"""Shared coordinator event loop — used by both Claude SDK and Codex coordinators."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.cost_tracker import CostTracker
from backend.deps import CoordinatorDeps
from backend.models import DEFAULT_MODELS
from backend.platforms.base import CompetitionPlatformClient
from backend.platforms.factory import create_platform_client
from backend.poller import CompetitionPoller
from backend.prompts import ChallengeMeta
from backend.solver_base import FLAG_FOUND

logger = logging.getLogger(__name__)

# Callable type for a coordinator turn: (message) -> None
TurnFn = Callable[[str], Coroutine[Any, Any, None]]


def build_deps(
    settings: Settings,
    model_specs: list[str] | None = None,
    challenges_root: str = "challenges",
    no_submit: bool = False,
    challenge_dirs: dict[str, str] | None = None,
    challenge_metas: dict[str, ChallengeMeta] | None = None,
    platform: CompetitionPlatformClient | None = None,
) -> tuple[CompetitionPlatformClient, CostTracker, CoordinatorDeps]:
    """Create platform client, cost tracker, and coordinator deps."""
    ctfd = platform or create_platform_client(settings)
    cost_tracker = CostTracker()
    specs = model_specs or list(DEFAULT_MODELS)
    Path(challenges_root).mkdir(parents=True, exist_ok=True)

    deps = CoordinatorDeps(
        ctfd=ctfd,
        cost_tracker=cost_tracker,
        settings=settings,
        model_specs=specs,
        challenges_root=challenges_root,
        no_submit=no_submit,
        max_concurrent_challenges=getattr(settings, "max_concurrent_challenges", 10),
        challenge_dirs=challenge_dirs or {},
        challenge_metas=challenge_metas or {},
    )

    # Pre-load already-pulled challenges
    for d in Path(challenges_root).iterdir():
        meta_path = d / "metadata.yml"
        if meta_path.exists():
            meta = ChallengeMeta.from_yaml(meta_path)
            if meta.name not in deps.challenge_dirs:
                deps.challenge_dirs[meta.name] = str(d)
                deps.challenge_metas[meta.name] = meta

    return ctfd, cost_tracker, deps


def _effective_solved_names(deps: CoordinatorDeps, known_solved: set[str]) -> set[str]:
    """Return the solved-name view used by all-solved exit policies."""
    effective = set(known_solved)
    effective |= {
        name
        for name, record in deps.results.items()
        if record.get("solve_status") == "skipped"
    }
    if deps.no_submit:
        effective |= {
            name
            for name, record in deps.results.items()
            if record.get("solve_status") == FLAG_FOUND
        }
    return effective


def _evaluate_all_solved_policy(
    *,
    deps: CoordinatorDeps,
    known_challenges: set[str],
    known_solved: set[str],
    active_swarms: int,
    now: float,
    idle_since: float | None,
) -> tuple[bool, float | None]:
    """Decide whether the coordinator should exit after all challenges are solved."""
    policy = getattr(deps.settings, "all_solved_policy", "wait")
    solved_names = _effective_solved_names(deps, known_solved)
    all_solved = bool(known_challenges) and known_challenges <= solved_names and active_swarms == 0

    if policy == "wait":
        return False, None
    if not all_solved:
        return False, None
    if policy == "exit":
        return True, now

    if idle_since is None:
        return False, now
    if now - idle_since >= getattr(deps.settings, "all_solved_idle_seconds", 300):
        return True, idle_since
    return False, idle_since


async def run_event_loop(
    deps: CoordinatorDeps,
    ctfd: CompetitionPlatformClient,
    cost_tracker: CostTracker,
    turn_fn: TurnFn,
    status_interval: int = 60,
) -> dict[str, Any]:
    """Run the shared coordinator event loop.

    Args:
        deps: Coordinator dependencies (shared state).
        ctfd: Competition platform client (for poller).
        cost_tracker: Cost tracker.
        turn_fn: Async function that sends a message to the coordinator LLM.
        status_interval: Seconds between status updates.
    """
    await ctfd.validate_access()

    poller = CompetitionPoller(ctfd=ctfd, interval_s=5.0)
    await poller.start()

    # Start operator message HTTP endpoint
    msg_server = await _start_msg_server(deps.operator_inbox, deps.msg_port)

    logger.info(
        "Coordinator starting: %d models, %d challenges, %d solved",
        len(deps.model_specs),
        len(poller.known_challenges),
        len(poller.known_solved),
    )

    solved_names = _effective_solved_names(deps, poller.known_solved)
    unsolved = poller.known_challenges - solved_names
    initial_msg = (
        f"CTF is LIVE. {len(poller.known_challenges)} challenges, "
        f"{len(solved_names)} solved.\n"
        f"Unsolved: {sorted(unsolved) if unsolved else 'NONE'}\n"
        "Fetch challenges and spawn swarms for all unsolved."
    )

    try:
        await turn_fn(initial_msg)

        # Auto-spawn swarms for unsolved challenges if coordinator LLM didn't
        await _auto_spawn_unsolved(deps, poller)

        last_status = asyncio.get_event_loop().time()
        idle_since: float | None = None

        while True:
            events = []
            evt = await poller.get_event(timeout=5.0)
            if evt:
                events.append(evt)
            events.extend(poller.drain_events())

            # Auto-kill swarms for solved challenges
            for evt in events:
                if evt.kind == "challenge_solved" and evt.challenge_name in deps.swarms:
                    swarm = deps.swarms[evt.challenge_name]
                    if not swarm.cancel_event.is_set():
                        swarm.kill()
                        logger.info("Auto-killed swarm for: %s", evt.challenge_name)

            parts: list[str] = []
            for evt in events:
                if evt.kind == "new_challenge":
                    parts.append(f"NEW CHALLENGE: '{evt.challenge_name}' appeared. Spawn a swarm.")
                    # Auto-spawn for new challenges
                    await _auto_spawn_one(deps, evt.challenge_name)
                elif evt.kind == "challenge_solved":
                    parts.append(f"SOLVED: '{evt.challenge_name}' — swarm auto-killed.")

            # Detect finished swarms
            for name, task in list(deps.swarm_tasks.items()):
                if task.done():
                    parts.append(f"SOLVER FINISHED: Swarm for '{name}' completed. Check results or retry.")
                    deps.swarm_tasks.pop(name, None)

            # Drain solver-to-coordinator messages
            while True:
                try:
                    solver_msg = deps.coordinator_inbox.get_nowait()
                    parts.append(f"SOLVER MESSAGE: {solver_msg}")
                except asyncio.QueueEmpty:
                    break

            # Drain operator messages
            while True:
                try:
                    op_msg = deps.operator_inbox.get_nowait()
                    parts.append(f"OPERATOR MESSAGE: {op_msg}")
                    logger.info("Operator message: %s", op_msg[:200])
                except asyncio.QueueEmpty:
                    break

            # Periodic status update — only when there are active swarms or other events
            now = asyncio.get_event_loop().time()
            if now - last_status >= status_interval:
                last_status = now
                active = [n for n, t in deps.swarm_tasks.items() if not t.done()]
                solved_set = _effective_solved_names(deps, poller.known_solved)
                unsolved_set = poller.known_challenges - solved_set
                status_line = (
                    f"STATUS: {len(solved_set)} solved, {len(unsolved_set)} unsolved, "
                    f"{len(active)} active swarms. Cost: ${cost_tracker.total_cost_usd:.2f}"
                )
                # Only send to coordinator if there's something happening
                if active or parts:
                    parts.append(status_line)
                else:
                    logger.info(f"Event -> coordinator: {status_line}")

            if parts:
                msg = "\n\n".join(parts)
                logger.info("Event -> coordinator: %s", msg[:200])
                await turn_fn(msg)

            now = asyncio.get_event_loop().time()
            active_count = sum(1 for task in deps.swarm_tasks.values() if not task.done())
            should_exit, idle_since = _evaluate_all_solved_policy(
                deps=deps,
                known_challenges=poller.known_challenges,
                known_solved=poller.known_solved,
                active_swarms=active_count,
                now=now,
                idle_since=idle_since,
            )
            if should_exit:
                logger.info(
                    "All challenges solved; policy=%s, active_swarms=%d, exiting coordinator loop",
                    getattr(deps.settings, "all_solved_policy", "wait"),
                    active_count,
                )
                break

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Coordinator shutting down...")
    except Exception as e:
        logger.error("Coordinator fatal: %s", e, exc_info=True)
    finally:
        if msg_server:
            msg_server.close()
            await msg_server.wait_closed()
        await poller.stop()
        for swarm in deps.swarms.values():
            swarm.kill()
        for task in deps.swarm_tasks.values():
            task.cancel()
        if deps.swarm_tasks:
            await asyncio.gather(*deps.swarm_tasks.values(), return_exceptions=True)
        cost_tracker.log_summary()
        try:
            await ctfd.close()
        except Exception:
            pass

    return {
        "results": deps.results,
        "total_cost_usd": cost_tracker.total_cost_usd,
        "total_tokens": cost_tracker.total_tokens,
    }


async def _auto_spawn_one(deps: CoordinatorDeps, challenge_name: str) -> None:
    """Auto-spawn a swarm for a single challenge if not already running."""
    if challenge_name in deps.swarms:
        return
    if not getattr(deps.ctfd, "supports_challenge_materialization", True):
        return
    active = sum(1 for t in deps.swarm_tasks.values() if not t.done())
    if active >= deps.max_concurrent_challenges:
        return
    try:
        from backend.agents.coordinator_core import do_spawn_swarm
        result = await do_spawn_swarm(deps, challenge_name)
        logger.info(f"Auto-spawn {challenge_name}: {result[:100]}")
    except Exception as e:
        logger.warning(f"Auto-spawn failed for {challenge_name}: {e}")


async def _auto_spawn_unsolved(deps: CoordinatorDeps, poller) -> None:
    """Auto-spawn swarms for all unsolved challenges that don't have active swarms."""
    unsolved = poller.known_challenges - _effective_solved_names(deps, poller.known_solved)
    for name in sorted(unsolved):
        await _auto_spawn_one(deps, name)


async def _start_msg_server(inbox: asyncio.Queue, port: int = 0) -> asyncio.Server | None:
    """Start a tiny HTTP server that accepts operator messages via POST."""

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # Read HTTP request
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n", b""):
                    break
                if b":" in line:
                    k, v = line.decode().split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            method = request_line.decode().split()[0] if request_line else ""
            content_length = int(headers.get("content-length", 0))

            if method == "POST" and content_length > 0:
                body = await asyncio.wait_for(reader.read(content_length), timeout=5)
                try:
                    data = json.loads(body)
                    message = data.get("message", body.decode())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    message = body.decode("utf-8", errors="replace")

                inbox.put_nowait(message)
                resp = json.dumps({"ok": True, "queued": message[:200]})
                writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(resp)}\r\n\r\n{resp}".encode())
            else:
                resp = json.dumps({"error": "POST with JSON body required", "usage": "POST {\"message\": \"...\"}"})
                writer.write(f"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: {len(resp)}\r\n\r\n{resp}".encode())

            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    try:
        server = await asyncio.start_server(_handle, "127.0.0.1", port)
        actual_port = server.sockets[0].getsockname()[1]
        logger.info(f"Operator message endpoint listening on http://127.0.0.1:{actual_port}")
        return server
    except OSError as e:
        logger.warning(f"Could not start operator message endpoint: {e}")
        return None
