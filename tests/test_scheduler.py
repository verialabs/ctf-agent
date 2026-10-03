import asyncio
from types import SimpleNamespace

import pytest

from backend.agents.coordinator_loop import _fill_available_slots
from backend.deps import CoordinatorDeps
from backend.message_bus import CoordinatorEventBus
from backend.task_registry import ChallengeRegistry, TaskStatus


@pytest.mark.asyncio
async def test_fill_available_slots_is_priority_ordered_and_capacity_bounded(monkeypatch) -> None:
    registry = ChallengeRegistry()
    registry.register("hard", solves=1)
    registry.register("easy", solves=50)
    registry.register("urgent", priority=10)
    for name in ("hard", "easy", "urgent"):
        registry.queue(name)

    deps = CoordinatorDeps(
        ctfd=SimpleNamespace(),
        cost_tracker=SimpleNamespace(),
        settings=SimpleNamespace(),
        max_concurrent_challenges=2,
        task_registry=registry,
        event_bus=CoordinatorEventBus(),
    )
    blockers: list[asyncio.Event] = []
    started: list[str] = []

    async def fake_spawn(inner_deps, challenge_name: str) -> str:
        started.append(challenge_name)
        inner_deps.task_registry.start(challenge_name)
        blocker = asyncio.Event()
        blockers.append(blocker)
        inner_deps.swarms[challenge_name] = SimpleNamespace()
        inner_deps.swarm_tasks[challenge_name] = asyncio.create_task(blocker.wait())
        return "started"

    monkeypatch.setattr("backend.agents.coordinator_core.do_spawn_swarm", fake_spawn)
    await _fill_available_slots(deps)

    assert started == ["urgent", "easy"]
    assert registry.tasks["hard"].status == TaskStatus.QUEUED
    assert sum(not task.done() for task in deps.swarm_tasks.values()) == 2

    for task in deps.swarm_tasks.values():
        task.cancel()
    await asyncio.gather(*deps.swarm_tasks.values(), return_exceptions=True)
