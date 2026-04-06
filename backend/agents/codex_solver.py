"""Codex solver — drives `codex app-server` via JSON-RPC 2.0 over stdio.

Protocol shapes verified against codex-cli 0.116.0 schema:
- thread/start returns {thread: {id, ...}, ...}
- turn/start takes {threadId, input: UserInput[]}
- Dynamic tool calls arrive as item/tool/call server requests with DynamicToolCallParams
  {tool, arguments, callId, threadId, turnId}
- Client responds with DynamicToolCallResponse {contentItems: [{type, text}], success}
- Token usage via thread/tokenUsage/updated notification
- Turn completion via turn/completed notification with {threadId, turn: Turn}
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import time
from typing import Any

from backend.cost_tracker import CostTracker
from backend.ctfd import CTFdClient
from backend.loop_detect import LoopDetector
from backend.models import model_id_from_spec, supports_vision
from backend.output_types import solver_output_json_schema
from backend.prompts import ChallengeMeta, build_prompt, list_distfiles
from backend.sandbox import DockerSandbox
from backend.solver_base import CANCELLED, ERROR, FLAG_FOUND, GAVE_UP, QUOTA_ERROR, SolverResult
from backend.tools.core import (
    do_bash,
    do_list_files,
    do_read_file,
    do_view_image,
    do_web_fetch,
    do_webhook_create,
    do_webhook_get_requests,
    do_write_file,
)
from backend.tracing import SolverTracer

logger = logging.getLogger(__name__)

_rpc_counter = itertools.count(1)

# Per-model reasoning effort (only for models that support it)
REASONING_EFFORT: dict[str, str] = {
    "gpt-5.3-codex": "xhigh",
}


def _next_id() -> int:
    return next(_rpc_counter)


# DynamicToolSpec[] for thread/start
SANDBOX_TOOLS = [
    {
        "name": "bash",
        "description": "Execute a bash command in the Docker sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 60},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the sandbox container.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "write_file",
        "description": "Write a file into the sandbox container.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    },
    {
        "name": "list_files",
        "description": "List files in a directory in the sandbox.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "default": "/challenge/distfiles"}}},
    },
    {
        "name": "submit_flag",
        "description": "Submit a flag to CTFd. Returns CORRECT, ALREADY SOLVED, or INCORRECT.",
        "inputSchema": {"type": "object", "properties": {"flag": {"type": "string"}}, "required": ["flag"]},
    },
    {
        "name": "web_fetch",
        "description": "Fetch a URL from the host network.",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}, "method": {"type": "string", "default": "GET"}, "body": {"type": "string", "default": ""}}, "required": ["url"]},
    },
    {
        "name": "webhook_create",
        "description": "Create a webhook.site token for out-of-band HTTP callbacks (XSS, SSRF, bot challenges).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "webhook_get_requests",
        "description": "Retrieve HTTP requests received by a webhook.site token.",
        "inputSchema": {"type": "object", "properties": {"uuid": {"type": "string"}}, "required": ["uuid"]},
    },
    {
        "name": "view_image",
        "description": "View an image file from the sandbox for visual/steg analysis.",
        "inputSchema": {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]},
    },
    {
        "name": "notify_coordinator",
        "description": "Send a strategic message to the coordinator (e.g. flag format discovery, shared vulnerability, request for help).",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
    },
]


class CodexSolver:
    """Codex solver speaking the actual app-server JSON-RPC 2.0 protocol."""

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
        self.message_bus = message_bus
        self.notify_coordinator = notify_coordinator
        self.ctfd = ctfd
        self.cost_tracker = cost_tracker
        self.settings = settings
        self.cancel_event = cancel_event or asyncio.Event()
        self.no_submit = no_submit
        self.submit_fn = submit_fn

        self.sandbox = DockerSandbox(
            image=getattr(settings, "sandbox_image", "ctf-sandbox"),
            challenge_dir=challenge_dir,
            memory_limit=getattr(settings, "container_memory_limit", "4g"),
        )
        self.use_vision = supports_vision(model_spec)
        self.loop_detector = LoopDetector()
        self.tracer = SolverTracer(meta.name, self.model_id)
        self.agent_name = f"{meta.name}/{self.model_id}"

        self._proc: asyncio.subprocess.Process | None = None
        self._thread_id: str | None = None
        self._step_count = 0
        self._flag: str | None = None
        self._confirmed = False
        self._findings = ""
        self._cost_usd = 0.0
        self._bump_insights: str | None = None
        self._structured_output: dict | None = None
        self._turn_error: str | None = None
        self._compact_requested = False
        self._pending_responses: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._turn_done: asyncio.Event = asyncio.Event()

    async def start(self) -> None:
        await self.sandbox.start()

        arch_result = await self.sandbox.exec("uname -m", timeout_s=10)
        container_arch = arch_result.stdout.strip() or "unknown"

        distfile_names = list_distfiles(self.challenge_dir)
        system_prompt = build_prompt(
            self.meta, distfile_names, container_arch=container_arch,
            has_named_tools=True,
        )

        self._proc = await asyncio.create_subprocess_exec(
            "codex", "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        self._reader_task = asyncio.create_task(self._read_loop())

        # Initialize handshake: send initialize request, then initialized notification
        await self._rpc("initialize", {
            "clientInfo": {"name": "ctf-agent", "version": "2.0.0"},
            "capabilities": {"experimentalApi": True},
        })
        await self._send_notification("initialized", {})

        # thread/start — personality is enum, system prompt in baseInstructions
        # Prepend sandbox path reminder to prevent models from using host paths
        tool_names = [t["name"] for t in SANDBOX_TOOLS]
        sandbox_preamble = (
            "IMPORTANT: You are running inside a Docker sandbox. "
            "All files are under /challenge/ — distfiles at /challenge/distfiles/, "
            "workspace at /challenge/workspace/. Do NOT use any paths outside /challenge/. "
            f"Your tools: {', '.join(tool_names)}. Use these for ALL operations.\n\n"
        )
        thread_params = {
            "model": self.model_id,
            "personality": "pragmatic",
            "baseInstructions": sandbox_preamble + system_prompt,
            "cwd": "/challenge",
            "approvalPolicy": "on-request",
            "sandbox": "read-only",
            "serviceTier": "flex",
            "dynamicTools": SANDBOX_TOOLS,
        }
        # Reasoning effort for models that support it
        reasoning = REASONING_EFFORT.get(self.model_id)
        if reasoning:
            thread_params["reasoningEffort"] = reasoning
        resp = await self._rpc("thread/start", thread_params)
        # ThreadStartResponse: result.thread.id
        self._thread_id = resp.get("result", {}).get("thread", {}).get("id", "")

        self.tracer.event("start", challenge=self.meta.name, model=self.model_id)
        logger.info(f"[{self.agent_name}] Codex solver started (thread={self._thread_id})")

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        assert self._proc and self._proc.stdin
        msg_id = _next_id()
        msg: dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            msg["params"] = params

        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending_responses[msg_id] = future

        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=300)
        finally:
            self._pending_responses.pop(msg_id, None)

    async def _respond_to_request(self, request_id: int, result: Any) -> None:
        """Send a JSON-RPC response to a server request (e.g. item/tool/call)."""
        assert self._proc and self._proc.stdin
        resp = {"id": request_id, "result": result}
        self._proc.stdin.write((json.dumps(resp) + "\n").encode())
        await self._proc.stdin.drain()

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        assert self._proc and self._proc.stdin
        msg: dict[str, Any] = {"method": method}
        if params:
            msg["params"] = params
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        """Read JSON-RPC messages: responses, notifications, and server requests."""
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                self._turn_done.set()
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_id = msg.get("id")
            if msg_id is not None and ("result" in msg or "error" in msg):
                future = self._pending_responses.pop(msg_id, None)
                if future and not future.done():
                    if "error" in msg:
                        err = msg["error"]
                        logger.error(f"[{self.agent_name}] RPC error: {err}")
                        future.set_exception(RuntimeError(f"Codex RPC error: {err}"))
                    else:
                        future.set_result(msg)
                continue

            method = msg.get("method", "")
            params = msg.get("params", {})

            # Server request: dynamic tool call
            if method == "item/tool/call" and msg_id is not None:
                await self._handle_tool_call(msg_id, params)

            # Notification: item completed — assistant text arrives here
            elif method == "item/completed":
                item = params.get("item", params)
                if item.get("type") == "agentMessage":
                    text = item.get("text", "")
                    phase = item.get("phase")  # "commentary" | "final_answer" | null
                    if text:
                        self._findings = text[:2000]
                        if phase != "commentary" and text.lstrip()[:1] == "{":
                            try:
                                parsed = json.loads(text)
                                if isinstance(parsed, dict) and "type" in parsed:
                                    self._structured_output = parsed
                            except (json.JSONDecodeError, ValueError):
                                pass

            # Notification: turn completed — signals the turn is done
            elif method == "turn/completed":
                turn = params.get("turn", {})
                status = turn.get("status", "")
                if status == "failed":
                    error = turn.get("error", {})
                    if isinstance(error, dict):
                        # Include all error fields for robust quota classification
                        parts = [error.get("message", "unknown error")]
                        codex_info = error.get("codexErrorInfo", {})
                        if isinstance(codex_info, dict):
                            parts.append(str(codex_info))
                        additional = error.get("additionalDetails")
                        if additional:
                            parts.append(str(additional))
                        error_msg = " | ".join(parts)
                    else:
                        error_msg = str(error)
                    self._turn_error = error_msg
                    logger.error(f"[{self.agent_name}] Turn failed: {error_msg}")
                    self.tracer.event("turn_failed", error=error_msg, step=self._step_count)
                    self._findings = f"Turn failed: {error_msg}"
                    self._structured_output = None
                else:
                    self._turn_error = None
                self._turn_done.set()

            # Notification: token usage updated
            # params: {threadId, turnId, tokenUsage: {last: TokenUsageBreakdown, total: TokenUsageBreakdown}}
            elif method == "thread/tokenUsage/updated":
                token_usage = params.get("tokenUsage", {})
                last = token_usage.get("last", {})
                total = token_usage.get("total", {})

                # Proactive compaction at 70% context window (only for small-context models like spark)
                context_window = token_usage.get("modelContextWindow")
                total_tokens = total.get("totalTokens", 0)
                if (
                    context_window
                    and context_window < 200_000
                    and total_tokens > context_window * 0.7
                    and not self._compact_requested
                ):
                    self._compact_requested = True
                    logger.info(f"[{self.agent_name}] Requesting compaction ({total_tokens}/{context_window} tokens)")
                    try:
                        await self._rpc("thread/compact/start", {"threadId": self._thread_id})
                        self.tracer.event("compact_requested", tokens=total_tokens, window=context_window)
                    except Exception as e:
                        logger.warning(f"[{self.agent_name}] Compaction request failed: {e}")

                self.cost_tracker.record_tokens(
                    self.agent_name, self.model_id,
                    input_tokens=last.get("inputTokens", 0),
                    output_tokens=last.get("outputTokens", 0),
                    cache_read_tokens=last.get("cachedInputTokens", 0),
                    provider_spec="codex",
                )
                agent_usage = self.cost_tracker.by_agent.get(self.agent_name)
                self._cost_usd = agent_usage.cost_usd if agent_usage else 0.0
                self.tracer.usage(
                    total.get("inputTokens", 0),
                    total.get("outputTokens", 0),
                    total.get("cachedInputTokens", 0),
                    self._cost_usd,
                )

    async def _handle_tool_call(self, request_id: int, params: dict) -> None:
        """Handle item/tool/call server request. Params are DynamicToolCallParams."""
        tool_name = params.get("tool", "")
        try:
            args = params.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
        except Exception:
            args = {}

        self._step_count += 1
        self.tracer.tool_call(tool_name, args, self._step_count)

        loop_status = self.loop_detector.check(tool_name, args)
        if loop_status == "break":
            self.tracer.event("loop_break", tool=tool_name, step=self._step_count)
            result = "Loop detected — try a completely different approach."
        else:
            result = await self._exec_tool(tool_name, args)
            if loop_status == "warn" and isinstance(result, str):
                from backend.loop_detect import LOOP_WARNING_MESSAGE
                result = f"{result}\n\n{LOOP_WARNING_MESSAGE}"

        # Build content items — handle image tuples from view_image
        if isinstance(result, tuple):
            image_bytes, mime_type = result
            data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"
            content_items = [{"type": "inputImage", "imageUrl": data_url}]
            self.tracer.tool_result(tool_name, f"image:{mime_type}:{len(image_bytes)}b", self._step_count)
        else:
            result_text = str(result)
            self.tracer.tool_result(tool_name, result_text[:500], self._step_count)

            if self._step_count % 5 == 0 and self.message_bus:
                from backend.tools.core import do_check_findings
                findings = await do_check_findings(self.message_bus, self.model_spec)
                if findings and "No new findings" not in findings:
                    result_text = f"{result_text}\n\n---\n{findings}"

            content_items = [{"type": "inputText", "text": result_text}]

        await self._respond_to_request(request_id, {
            "contentItems": content_items,
            "success": True,
        })

    async def _exec_tool(self, name: str, args: dict) -> str | tuple[bytes, str]:
        if name == "bash":
            return await do_bash(self.sandbox, args.get("command", ""), args.get("timeout_seconds", 60))
        elif name == "read_file":
            return str(await do_read_file(self.sandbox, args.get("path", "")))
        elif name == "write_file":
            return await do_write_file(self.sandbox, args.get("path", ""), args.get("content", ""))
        elif name == "list_files":
            return await do_list_files(self.sandbox, args.get("path", "/challenge/distfiles"))
        elif name == "submit_flag":
            flag = args.get("flag", "")
            if self.no_submit:
                return f'DRY RUN — would submit "{flag}"'
            if self.submit_fn:
                display, is_confirmed = await self.submit_fn(flag)
            else:
                from backend.tools.core import do_submit_flag
                display, is_confirmed = await do_submit_flag(self.ctfd, self.meta, flag)
            if is_confirmed:
                self._confirmed = True
                self._flag = flag
            return display
        elif name == "web_fetch":
            return await do_web_fetch(args.get("url", ""), args.get("method", "GET"), args.get("body", ""))
        elif name == "webhook_create":
            return await do_webhook_create()
        elif name == "webhook_get_requests":
            return await do_webhook_get_requests(args.get("uuid", ""))
        elif name == "view_image":
            return await do_view_image(self.sandbox, args.get("filename", ""), use_vision=self.use_vision)
        elif name == "notify_coordinator":
            if self.notify_coordinator:
                await self.notify_coordinator(args.get("message", ""))
                return "Message sent to coordinator."
            return "No coordinator connected."
        return f"Unknown tool: {name}"

    async def run_until_done_or_gave_up(self) -> SolverResult:
        if not self._proc:
            await self.start()
        assert self._thread_id

        t0 = time.monotonic()
        if self._bump_insights:
            prompt_text = (
                "Your previous attempt did not find the flag. "
                f"Insights from other agents:\n\n{self._bump_insights}\n\n"
                "Try a different approach."
            )
            self._bump_insights = None
        elif self._step_count == 0:
            prompt_text = "Solve this CTF challenge."
        else:
            prompt_text = "Continue solving. Try a different approach."

        try:
            self._turn_done.clear()
            self._structured_output = None
            self._turn_error = None
            await self._rpc("turn/start", {
                "threadId": self._thread_id,
                "input": [{"type": "text", "text": prompt_text}],
                "outputSchema": solver_output_json_schema(),
            })

            await self._turn_done.wait()

            duration = time.monotonic() - t0
            self.tracer.event("turn_complete", duration=round(duration, 1), steps=self._step_count)

            if self._turn_error:
                err = self._turn_error.lower()
                # Context overflow is terminal — don't fallback, just error
                if "context_length" in err or "context window" in err:
                    return self._result(ERROR)
                if any(k in err for k in ("quota", "rate", "capacity", "usage")):
                    return self._result(QUOTA_ERROR)
                return self._result(ERROR)

            if self._structured_output and self._structured_output.get("type") == "flag_found":
                self._flag = self._structured_output.get("flag")
                self._findings = f"Flag found via {self._structured_output.get('method', '?')}: {self._flag}"
                if self.no_submit:
                    self._confirmed = True

            if self._confirmed and self._flag:
                return self._result(FLAG_FOUND)
            return self._result(GAVE_UP)

        except asyncio.CancelledError:
            return self._result(CANCELLED)
        except Exception as e:
            error_str = str(e)
            logger.error(f"[{self.agent_name}] Error: {e}", exc_info=True)
            self._findings = f"Error: {e}"
            self.tracer.event("error", error=error_str)
            if "quota" in error_str.lower() or "rate" in error_str.lower():
                return self._result(QUOTA_ERROR)
            return self._result(ERROR)

    def bump(self, insights: str) -> None:
        self._bump_insights = insights
        self.loop_detector.reset()
        self.tracer.event("bump", insights=insights[:500])

    def _result(self, status: str) -> SolverResult:
        self.tracer.event("finish", status=status, flag=self._flag, confirmed=self._confirmed)
        return SolverResult(
            flag=self._flag, status=status,
            findings_summary=self._findings[:2000],
            step_count=self._step_count,
            cost_usd=self._cost_usd, log_path=self.tracer.path,
        )

    async def stop(self) -> None:
        self.tracer.event("stop", step_count=self._step_count)
        self.tracer.close()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self.sandbox:
            await self.sandbox.stop()
