"""Codex coordinator — drives `codex app-server` via JSON-RPC for coordination."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

from backend.agents.coordinator_core import (
    do_broadcast,
    do_bump_agent,
    do_check_swarm_status,
    do_fetch_challenges,
    do_get_solve_status,
    do_kill_swarm,
    do_read_solver_trace,
    do_spawn_swarm,
    do_submit_flag,
)
from backend.agents.coordinator_loop import build_deps, run_event_loop
from backend.config import Settings
from backend.deps import CoordinatorDeps
from backend.executables import resolve_executable

logger = logging.getLogger(__name__)

_rpc_counter = itertools.count(1)

COORDINATOR_PROMPT = """\
You are a CTF competition coordinator running for the ENTIRE duration of a live competition.
Your job is to maximize the number of challenges solved while minimizing cost.

Strategy:
- Spawn swarms for unsolved challenges, prioritizing by solve count (easy first)
- Respect the configured challenge-slot limit; queued work is started automatically as slots free up
- Use read_solver_trace to monitor what each solver is doing and where it's stuck
- When agents are stuck, read their traces, then craft targeted bumps with specific technical guidance
- Use broadcast to share cross-solver insights (e.g. flag format discovery, shared vulnerabilities)

CRITICAL RULES:
- Help stuck solvers with evidence-based bumps. The scheduler retries exhausted swarms up to
  the configured attempt limit, then marks the task blocked and reuses the slot.
- Kill a swarm only when the challenge was solved elsewhere or an operator requests it.
- When a solver seems stuck, bump it with very specific technical guidance based on
  its trace. Tell it exactly what to try next — specific tools, techniques, approaches.
- Prefer useful parallelism over duplicate work and unnecessary cost.

You will receive event messages. Respond with tool calls to manage the competition.
"""

COORDINATOR_TOOLS = [
    {
        "name": "fetch_challenges",
        "description": "List all challenges with category, points, solve count, and status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_solve_status",
        "description": "Check which challenges are solved and which swarms are running.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "spawn_swarm",
        "description": "Launch all solver models on a challenge.",
        "inputSchema": {
            "type": "object",
            "properties": {"challenge_name": {"type": "string"}},
            "required": ["challenge_name"],
        },
    },
    {
        "name": "check_swarm_status",
        "description": "Get per-agent progress for a swarm.",
        "inputSchema": {
            "type": "object",
            "properties": {"challenge_name": {"type": "string"}},
            "required": ["challenge_name"],
        },
    },
    {
        "name": "submit_flag",
        "description": "Submit a flag to CTFd.",
        "inputSchema": {
            "type": "object",
            "properties": {"challenge_name": {"type": "string"}, "flag": {"type": "string"}},
            "required": ["challenge_name", "flag"],
        },
    },
    {
        "name": "kill_swarm",
        "description": "Cancel all agents for a challenge.",
        "inputSchema": {
            "type": "object",
            "properties": {"challenge_name": {"type": "string"}},
            "required": ["challenge_name"],
        },
    },
    {
        "name": "bump_agent",
        "description": "Send targeted insights to a stuck agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "challenge_name": {"type": "string"},
                "model_spec": {"type": "string"},
                "insights": {"type": "string"},
            },
            "required": ["challenge_name", "model_spec", "insights"],
        },
    },
    {
        "name": "broadcast",
        "description": "Broadcast a strategic hint to ALL solvers on a challenge.",
        "inputSchema": {
            "type": "object",
            "properties": {"challenge_name": {"type": "string"}, "message": {"type": "string"}},
            "required": ["challenge_name", "message"],
        },
    },
    {
        "name": "read_solver_trace",
        "description": "Read recent trace events from a specific solver.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "challenge_name": {"type": "string"},
                "model_spec": {"type": "string"},
                "last_n": {"type": "integer", "default": 20},
            },
            "required": ["challenge_name", "model_spec"],
        },
    },
]


class CodexCoordinator:
    """Coordinator using Codex App Server JSON-RPC."""

    def __init__(self, deps: CoordinatorDeps, model: str = "gpt-5.5") -> None:
        self.deps = deps
        self.model = model
        self._proc: asyncio.subprocess.Process | None = None
        self._thread_id: str | None = None
        self._pending_responses: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._turn_done: asyncio.Event = asyncio.Event()
        self._turn_error: str | None = None

    async def start(self) -> None:
        codex_executable = resolve_executable("codex", env_var="CODEX_EXECUTABLE")
        self._proc = await asyncio.create_subprocess_exec(
            codex_executable, "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

        await self._rpc("initialize", {
            "clientInfo": {"name": "ctf-coordinator", "version": "2.0.0"},
            "capabilities": {"experimentalApi": True},
        })
        await self._send_notification("initialized", {})

        resp = await self._rpc("thread/start", {
            "model": self.model,
            "personality": "pragmatic",
            "baseInstructions": COORDINATOR_PROMPT,
            "cwd": ".",
            "approvalPolicy": "on-request",
            "sandbox": "read-only",
            "dynamicTools": COORDINATOR_TOOLS,
        })
        self._thread_id = resp.get("result", {}).get("thread", {}).get("id", "")
        logger.info(f"Codex coordinator started (thread={self._thread_id}, model={self.model})")

    async def turn(self, message: str) -> None:
        """Send a message and wait for the model to finish its turn."""
        if not self._proc or not self._thread_id:
            await self.start()

        self._turn_done.clear()
        self._turn_error = None

        await self._rpc("turn/start", {
            "threadId": self._thread_id,
            "input": [{"type": "text", "text": message}],
        })

        try:
            await asyncio.wait_for(self._turn_done.wait(), timeout=120)
        except TimeoutError:
            logger.warning("Codex coordinator turn timed out")

        if self._turn_error:
            logger.warning(f"Codex coordinator turn error: {self._turn_error}")

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None

    # --- JSON-RPC transport ---

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        assert self._proc and self._proc.stdin
        msg_id = next(_rpc_counter)
        msg: dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            msg["params"] = params

        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending_responses[msg_id] = future

        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=300)
        finally:
            self._pending_responses.pop(msg_id, None)

    async def _respond_to_request(self, request_id: int, result: Any) -> None:
        assert self._proc and self._proc.stdin
        resp = {"id": request_id, "result": result}
        self._proc.stdin.write((json.dumps(resp) + "\n").encode())
        await self._proc.stdin.drain()

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        assert self._proc and self._proc.stdin
        msg: dict[str, Any] = {"method": method}
        if params:
            msg["params"] = params
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
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

            # RPC response
            if msg_id is not None and ("result" in msg or "error" in msg):
                future = self._pending_responses.pop(msg_id, None)
                if future and not future.done():
                    if "error" in msg:
                        future.set_exception(RuntimeError(f"Codex RPC error: {msg['error']}"))
                    else:
                        future.set_result(msg)
                continue

            method = msg.get("method", "")
            params = msg.get("params", {})

            # Dynamic tool call
            if method == "item/tool/call" and msg_id is not None:
                await self._handle_tool_call(msg_id, params)

            # Turn completed
            elif method == "turn/completed":
                turn = params.get("turn", {})
                if turn.get("status") == "failed":
                    self._turn_error = str(turn.get("error", "unknown"))
                self._turn_done.set()

    async def _handle_tool_call(self, request_id: int, params: dict) -> None:
        tool_name = params.get("tool", "")
        try:
            args = params.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
        except Exception:
            args = {}

        logger.debug(f"Coordinator tool call: {tool_name}({args})")

        try:
            result = await self._dispatch_tool(tool_name, args)
        except Exception as e:
            result = f"Error: {e}"

        await self._respond_to_request(request_id, {
            "contentItems": [{"type": "inputText", "text": str(result)}],
            "success": True,
        })

    async def _dispatch_tool(self, name: str, args: dict) -> str:
        deps = self.deps
        if name == "fetch_challenges":
            return await do_fetch_challenges(deps)
        elif name == "get_solve_status":
            return await do_get_solve_status(deps)
        elif name == "spawn_swarm":
            return await do_spawn_swarm(deps, args["challenge_name"])
        elif name == "check_swarm_status":
            return await do_check_swarm_status(deps, args["challenge_name"])
        elif name == "submit_flag":
            return await do_submit_flag(deps, args["challenge_name"], args["flag"])
        elif name == "kill_swarm":
            return await do_kill_swarm(deps, args["challenge_name"])
        elif name == "bump_agent":
            return await do_bump_agent(deps, args["challenge_name"], args["model_spec"], args["insights"])
        elif name == "broadcast":
            return await do_broadcast(deps, args["challenge_name"], args["message"])
        elif name == "read_solver_trace":
            return await do_read_solver_trace(deps, args["challenge_name"], args["model_spec"], args.get("last_n", 20))
        else:
            return f"Unknown tool: {name}"


async def run_codex_coordinator(
    settings: Settings,
    model_specs: list[str] | None = None,
    challenges_root: str = "challenges",
    no_submit: bool = False,
    coordinator_model: str | None = None,
    msg_port: int = 0,
    allowed_categories: set[str] | None = None,
) -> dict[str, Any]:
    """Run the Codex coordinator with the shared event loop."""
    ctfd, cost_tracker, deps = build_deps(
        settings, model_specs, challenges_root, no_submit, allowed_categories=allowed_categories,
    )
    deps.msg_port = msg_port

    resolved_model = coordinator_model or "gpt-5.5"
    coordinator = CodexCoordinator(deps, model=resolved_model)
    await coordinator.start()

    async def turn_fn(msg: str) -> None:
        logger.debug(f"Coordinator query: {msg[:200]}")
        await coordinator.turn(msg)
        logger.info("Codex coordinator turn done")

    try:
        return await run_event_loop(deps, ctfd, cost_tracker, turn_fn)
    finally:
        await coordinator.stop()
