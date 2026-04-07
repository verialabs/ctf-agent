from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from backend.deps import CoordinatorDeps
    from backend.poller import CompetitionPoller


@dataclass
class ChallengeState:
    challenge_name: str
    status: Literal["unknown", "pending", "running", "solved", "skipped", "error"] = "unknown"
    category: str = ""
    value: float = 0.0
    requires_env_start: bool = False
    unsupported_reason: str = ""
    last_materialized_at: float | None = None


@dataclass
class SwarmState:
    challenge_name: str
    status: Literal["idle", "running", "finished", "cancelled", "error"] = "idle"
    running_models: list[str] = field(default_factory=list)
    last_bump_at: float | None = None
    bump_count: int = 0
    last_progress_at: float | None = None
    last_error: str = ""
    step_count: int = 0
    cost_usd: float = 0.0
    winner_model: str = ""
    applied_knowledge_ids: set[str] = field(default_factory=set)


@dataclass
class CompetitionState:
    known_challenges: set[str] = field(default_factory=set)
    known_solved: set[str] = field(default_factory=set)
    challenges: dict[str, ChallengeState] = field(default_factory=dict)
    swarms: dict[str, SwarmState] = field(default_factory=dict)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    global_cost_usd: float = 0.0
    last_poll_at: float | None = None
    operator_messages: list[str] = field(default_factory=list)

    @property
    def active_swarm_count(self) -> int:
        return sum(1 for swarm in self.swarms.values() if swarm.status == "running")


def _solver_step_count(solver: Any) -> int:
    step_count = getattr(solver, "_step_count", 0)
    if isinstance(step_count, list):
        return int(step_count[0]) if step_count else 0
    try:
        return int(step_count)
    except (TypeError, ValueError):
        return 0


def _solver_cost_usd(solver: Any, deps: CoordinatorDeps) -> float:
    cost = getattr(solver, "_cost_usd", None)
    if cost is not None:
        try:
            return float(cost)
        except (TypeError, ValueError):
            return 0.0
    agent_name = getattr(solver, "agent_name", "")
    if agent_name:
        usage = deps.cost_tracker.by_agent.get(agent_name)
        if usage:
            return float(usage.cost_usd)
    return 0.0


def build_runtime_state_snapshot(
    deps: CoordinatorDeps,
    poller: CompetitionPoller,
    now: float,
) -> CompetitionState:
    swarms: dict[str, SwarmState] = {}
    for name, swarm in deps.swarms.items():
        task = deps.swarm_tasks.get(name)
        status = "running"
        if task and task.done():
            status = "finished"
        elif swarm.cancel_event.is_set():
            status = "cancelled"

        running_models = sorted(swarm.solvers.keys())
        step_count = 0
        cost_usd = 0.0
        for solver in swarm.solvers.values():
            step_count += _solver_step_count(solver)
            cost_usd += _solver_cost_usd(solver, deps)

        swarms[name] = SwarmState(
            challenge_name=name,
            status=status,
            running_models=running_models,
            step_count=step_count,
            cost_usd=cost_usd,
        )

    return CompetitionState(
        known_challenges=set(poller.known_challenges),
        known_solved=set(poller.known_solved),
        swarms=swarms,
        results=dict(deps.results),
        global_cost_usd=deps.cost_tracker.total_cost_usd,
        last_poll_at=now,
    )
