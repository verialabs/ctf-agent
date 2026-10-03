"""Claude Agent SDK solver — native tools with execution hooks.

Uses Claude's native Bash tool, but intercepts every command via a PreToolUse
hook and rewrites it to run inside the Docker sandbox via `docker exec`. Read,
Write, and Edit are blocked — the model uses bash for all file operations.
Flag submission is intercepted from bash commands matching `submit_flag <flag>`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
)

from backend.cost_tracker import CostTracker
from backend.ctfd import CTFdClient
from backend.loop_detect import LoopDetector
from backend.models import model_id_from_spec
from backend.output_types import solver_output_json_schema
from backend.prompts import ChallengeMeta, build_prompt, list_distfiles
from backend.sandbox import DockerSandbox
from backend.solver_base import CANCELLED, ERROR, FLAG_FOUND, GAVE_UP, QUOTA_ERROR, SolverResult
from backend.tracing import SolverTracer

logger = logging.getLogger(__name__)


class ClaudeSolver:
    """Claude Agent SDK solver using native tools redirected to Docker sandbox."""

    def __init__(
        self,
        model_spec: str,
        challenge_dir: str,
        meta: ChallengeMeta,
        ctfd: CTFdClient,
        cost_tracker: CostTracker,
        settings: object,
        cancel_event: asyncio.Event | None = None,
        no_submit: bool = False,
        submit_fn=None,
        message_bus=None,
        notify_coordinator=None,
    ) -> None:
        self.model_spec = model_spec
        self.model_id = model_id_from_spec(model_spec)
        self.challenge_dir = challenge_dir
        self.meta = meta
        self.ctfd = ctfd
        self.cost_tracker = cost_tracker
        self.settings = settings
        self.cancel_event = cancel_event or asyncio.Event()
        self.no_submit = no_submit
        self.submit_fn = submit_fn
        self.message_bus = message_bus
        self.notify_coordinator = notify_coordinator

        self.sandbox = DockerSandbox(
            image=getattr(settings, "sandbox_image", "ctf-sandbox"),
            challenge_dir=challenge_dir,
            memory_limit=getattr(settings, "container_memory_limit", "4g"),
        )
        self.loop_detector = LoopDetector()
        self.tracer = SolverTracer(meta.name, self.model_id)
        self.agent_name = f"{meta.name}/{self.model_id}"

        self._client: ClaudeSDKClient | None = None
        self._session_id: str | None = None
        self._container_id: str = ""
        self._step_count = 0
        self._flag: str | None = None
        self._confirmed = False
        self._findings = ""
        self._cost_usd = 0.0
        self._bump_insights: str | None = None

    async def start(self) -> None:
        await self.sandbox.start()

        self._container_id = self.sandbox.container_id

        arch_result = await self.sandbox.exec("uname -m", timeout_s=10)
        container_arch = arch_result.stdout.strip() or "unknown"

        distfile_names = list_distfiles(self.challenge_dir)
        sandbox_preamble = (
            "IMPORTANT: You are running inside a Docker sandbox. "
            "All files are under /challenge/ — distfiles at /challenge/distfiles/, "
            "workspace at /challenge/workspace/. Do NOT use any paths outside /challenge/. "
            "All bash commands run inside the container via docker exec. "
            "Use bash for everything: cat/head to read files, tee/echo> to write, find/grep to search. "
            "submit_flag 'FLAG' to submit. notify_coordinator 'MSG' to message the coordinator.\n\n"
        )
        system_prompt = sandbox_preamble + build_prompt(
            self.meta, distfile_names, container_arch=container_arch,
            has_named_tools=False,
        )

        # PreToolUse hook: rewrite Bash commands to run in the sandbox container.
        # Block Read/Write/Edit — model should use bash for file access.
        async def sandbox_redirect(input_data, tool_use_id, context):
            try:
                return await _sandbox_redirect_inner(input_data, tool_use_id, context)
            except Exception as e:
                logger.warning(f"[{self.agent_name}] PreToolUse hook error: {e}")
                return {}

        async def _sandbox_redirect_inner(input_data, tool_use_id, context):
            if input_data.get("hook_event_name") != "PreToolUse":
                return {}

            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            # Step counting and loop detection for all tools
            self._step_count += 1
            self.tracer.tool_call(tool_name, tool_input, self._step_count)
            loop_status = self.loop_detector.check(tool_name, str(tool_input)[:200])
            if loop_status == "break":
                self.tracer.event("loop_break", tool=tool_name, step=self._step_count)
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Loop detected — try a different approach.",
                    }
                }
            warn_msg = ""
            if loop_status == "warn":
                from backend.loop_detect import LOOP_WARNING_MESSAGE
                warn_msg = LOOP_WARNING_MESSAGE

            if tool_name == "Bash":
                command = tool_input.get("command", "")

                # Intercept submit_flag commands — handle submission directly
                flag_match = re.match(r"submit_flag\s+['\"]?(.+?)['\"]?\s*$", command.strip())
                if flag_match:
                    flag_val = flag_match.group(1).strip()
                    if self.no_submit:
                        result_msg = f'DRY RUN — would submit "{flag_val}"'
                    else:
                        if self.submit_fn:
                            display, confirmed = await self.submit_fn(flag_val)
                        else:
                            from backend.tools.core import do_submit_flag
                            display, confirmed = await do_submit_flag(self.ctfd, self.meta.name, flag_val)
                        result_msg = display
                        if confirmed:
                            self._confirmed = True
                            self._flag = flag_val
                            self.tracer.event("flag_confirmed", flag=flag_val, step=self._step_count)
                    # Rewrite to an echo so Bash returns the submission result
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "updatedInput": {
                                **tool_input,
                                "command": f"echo {shlex.quote(result_msg)}",
                            },
                        }
                    }

                # Intercept notify_coordinator commands
                notify_match = re.match(r"notify_coordinator\s+['\"]?(.+?)['\"]?\s*$", command.strip())
                if notify_match and self.notify_coordinator:
                    msg = notify_match.group(1).strip()
                    await self.notify_coordinator(msg)
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "updatedInput": {**tool_input, "command": "echo 'Message sent to coordinator.'"},
                        }
                    }

                # Rewrite command to run in the Docker container
                escaped = shlex.quote(command)
                rewritten = f"docker exec -i {self._container_id} bash -c {escaped}"

                result = {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "updatedInput": {
                            **tool_input,
                            "command": rewritten,
                        },
                    }
                }
                if warn_msg:
                    result["systemMessage"] = warn_msg
                return result

            if tool_name in ("WebFetch", "WebSearch"):
                return {"systemMessage": warn_msg} if warn_msg else {}

            # Everything else is denied — Glob/Grep/Read/Write/Edit/Agent/etc.
            # would run on the host filesystem, breaking sandbox isolation.
            # The model should use find/grep/cat/tee via bash instead.
            redirect_hint = ""
            if tool_name in ("Glob", "Grep"):
                redirect_hint = " Use `find` or `grep` via bash instead — those run in the container."
            elif tool_name in ("Read", "Write", "Edit", "NotebookEdit"):
                redirect_hint = " Use cat/head/tail to read, and tee/cat>file to write via bash."

            return {
                "systemMessage": f"{tool_name} is not available — all work happens inside the Docker container.{redirect_hint}" if redirect_hint else "",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"{tool_name} blocked — use bash for all operations inside the sandbox.",
                },
            }

        async def trace_post_tool(input_data, tool_use_id, context):
            try:
                return await _trace_post_tool_inner(input_data, tool_use_id, context)
            except Exception as e:
                logger.warning(f"[{self.agent_name}] PostToolUse hook error: {e}")
                return {}

        async def _trace_post_tool_inner(input_data, tool_use_id, context):
            if input_data.get("hook_event_name") != "PostToolUse":
                return {}
            response_str = str(input_data.get("tool_response", ""))[:2000]
            self.tracer.tool_result(input_data.get("tool_name", "?"), response_str[:500], self._step_count)

            if self._step_count % 5 == 0 and self.message_bus:
                from backend.tools.core import do_check_findings
                findings = await do_check_findings(self.message_bus, self.model_spec)
                if findings and "No new findings" not in findings:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PostToolUse",
                            "additionalContext": findings,
                        }
                    }
            return {}

        from backend.models import effort_from_spec
        effort = effort_from_spec(self.model_spec)

        options = ClaudeAgentOptions(
            model=self.model_id,
            system_prompt=system_prompt,
            effort=effort,
            # Clear CLAUDECODE to prevent nested-session rejection when run from coordinator
            env={"CLAUDECODE": ""},
            allowed_tools=["Bash", "WebFetch", "WebSearch"],
            permission_mode="bypassPermissions",
            output_format={"type": "json_schema", "schema": solver_output_json_schema()},
            hooks={
                "PreToolUse": [
                    HookMatcher(hooks=[sandbox_redirect]),
                ],
                "PostToolUse": [
                    HookMatcher(hooks=[trace_post_tool]),
                ],
            },
        )

        self._client = ClaudeSDKClient(options=options)
        await self._client.__aenter__()
        self.tracer.event("start", challenge=self.meta.name, model=self.model_id)
        logger.info(f"[{self.agent_name}] Claude SDK solver started")

    async def run_until_done_or_gave_up(self) -> SolverResult:
        if not self._client:
            await self.start()
        assert self._client is not None

        t0 = time.monotonic()
        cost_before = self._cost_usd
        steps_before = self._step_count

        try:
            if self._bump_insights:
                prompt = (
                    "Your previous attempt did not find the flag. "
                    f"Insights from other agents:\n\n{self._bump_insights}\n\n"
                    "Try a different approach. Do NOT repeat what was tried."
                )
                self._bump_insights = None
            elif self._session_id:
                prompt = "Continue solving. Try a different approach."
            else:
                prompt = "Solve this CTF challenge."

            await self._client.query(prompt)

            async for message in self._client.receive_response():
                if self.cancel_event.is_set():
                    break

                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            self._findings = block.text[:2000]

                elif isinstance(message, ResultMessage):
                    self._session_id = message.session_id
                    turn_cost = getattr(message, "total_cost_usd", 0.0)
                    self._cost_usd += turn_cost
                    msg_usage = getattr(message, "usage", None) or {}
                    if not isinstance(msg_usage, dict):
                        msg_usage = vars(msg_usage) if hasattr(msg_usage, "__dict__") else {}
                    self.cost_tracker.record_tokens(
                        self.agent_name, self.model_id,
                        input_tokens=msg_usage.get("input_tokens", 0),
                        output_tokens=msg_usage.get("output_tokens", 0),
                        cache_read_tokens=msg_usage.get("cache_read_input_tokens", msg_usage.get("cache_read_tokens", 0)),
                        provider_spec="claude-sdk",
                        duration_seconds=time.monotonic() - t0,
                    )

                    output = getattr(message, "structured_output", None)
                    if output and output.get("type") == "flag_found":
                        self._flag = output.get("flag")
                        self._findings = f"Flag found via {output.get('method', '?')}: {self._flag}"
                        if self.no_submit:
                            self._confirmed = True

            self.tracer.event("turn_complete", duration=round(time.monotonic() - t0, 1), cost=round(self._cost_usd, 4))

            # Also check if flag was confirmed via submit_flag in bash
            if self._confirmed and self._flag:
                return self._result(FLAG_FOUND)
            # Report per-run metrics so broken-solver detection works
            run_steps = self._step_count - steps_before
            run_cost = self._cost_usd - cost_before
            return self._result(GAVE_UP, run_steps=run_steps, run_cost=run_cost)

        except asyncio.CancelledError:
            return self._result(CANCELLED)
        except Exception as e:
            error_str = str(e)
            logger.error(f"[{self.agent_name}] Error: {e}", exc_info=True)
            self._findings = f"Error: {e}"
            self.tracer.event("error", error=error_str)
            if "quota" in error_str.lower() or "rate" in error_str.lower() or "overloaded" in error_str.lower():
                return self._result(QUOTA_ERROR)
            return self._result(ERROR)

    def bump(self, insights: str) -> None:
        self._bump_insights = insights
        self.loop_detector.reset()
        self.tracer.event("bump", insights=insights[:500])
        logger.info(f"[{self.agent_name}] Bumped with insights (session {self._session_id})")

    def _result(self, status: str, run_steps: int | None = None, run_cost: float | None = None) -> SolverResult:
        self.tracer.event("finish", status=status, flag=self._flag, confirmed=self._confirmed, cost_usd=round(self._cost_usd, 4))
        # Use per-run metrics if provided, so broken-solver detection works across bumps
        return SolverResult(
            flag=self._flag, status=status,
            findings_summary=self._findings[:2000],
            step_count=run_steps if run_steps is not None else self._step_count,
            cost_usd=run_cost if run_cost is not None else self._cost_usd,
            log_path=self.tracer.path,
        )

    async def stop(self) -> None:
        self.tracer.event("stop", step_count=self._step_count)
        self.tracer.close()
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None
        if self.sandbox:
            await self.sandbox.stop()
