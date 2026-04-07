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
    step_count = getattr(solver, "_step_count", None)
    if step_count is None:
        step_count = getattr(solver, "step_count", 0)
    if isinstance(step_count, list):
        return int(step_count[0]) if step_count else 0
    if isinstance(step_count, tuple):
        return int(step_count[0]) if step_count else 0
    try:
        return int(step_count)
    except (TypeError, ValueError):
        return 0


def _solver_cost_usd(solver: Any, deps: CoordinatorDeps) -> float:
    agent_name = getattr(solver, "agent_name", "")
    if agent_name:
        usage = deps.cost_tracker.by_agent.get(agent_name)
        if usage:
            return float(usage.cost_usd)
    return 0.0


def _status_from_result(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    solve_status = str(record.get("solve_status", "")).lower()
    if solve_status in {"cancelled"}:
        return "cancelled"
    if solve_status in {"error", "quota_error"}:
        return "error"
    if solve_status in {"flag_found", "gave_up", "no_result", "skipped"}:
        return "finished"
    return None


def _challenge_status_from_result(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    solve_status = str(record.get("solve_status", "")).lower()
    if solve_status == "skipped":
        return "skipped"
    if solve_status in {"flag_found"}:
        return "solved"
    if solve_status in {"error", "quota_error"}:
        return "error"
    return None


def _challenge_status(
    *,
    challenge_name: str,
    swarm_state: SwarmState | None,
    known_challenges: set[str],
    known_solved: set[str],
    record: dict[str, Any] | None,
    prior_state: ChallengeState | None,
) -> Literal["unknown", "pending", "running", "solved", "skipped", "error"]:
    result_status = _challenge_status_from_result(record)
    if result_status is not None:
        return result_status
    if challenge_name in known_solved:
        return "solved"
    if swarm_state is not None and swarm_state.status == "running":
        return "running"
    if challenge_name in known_challenges:
        if prior_state and prior_state.status in {"pending", "running"}:
            return prior_state.status if swarm_state is not None else "pending"
        return "pending"
    if prior_state is not None:
        return prior_state.status
    return "unknown"


def build_runtime_state_snapshot(
    deps: CoordinatorDeps,
    poller: CompetitionPoller,
    now: float,
) -> CompetitionState:
    previous_state = deps.runtime_state or CompetitionState()
    previous_swarms = previous_state.swarms
    previous_challenges = previous_state.challenges
    terminal_names: set[str] = {
        name
        for name, swarm in previous_swarms.items()
        if swarm.status in {"finished", "cancelled", "error"}
    }
    terminal_names.update(
        {
            name
            for name, record in deps.results.items()
            if _status_from_result(record) in {"finished", "cancelled", "error"}
        }
    )
    swarm_names = set(deps.swarms)
    swarm_names.update(terminal_names)

    swarms: dict[str, SwarmState] = {}
    for name in swarm_names:
        swarm = deps.swarms.get(name)
        result_status = _status_from_result(deps.results.get(name))
        prior_state = previous_swarms.get(name)
        status = prior_state.status if prior_state else "idle"

        if swarm is not None:
            task = deps.swarm_tasks.get(name)
            if task is not None:
                status = result_status or ("finished" if task.done() else "running")
            else:
                if swarm.cancel_event.is_set():
                    if result_status:
                        status = result_status
                    elif prior_state and prior_state.status in {"finished", "cancelled", "error"}:
                        status = prior_state.status
                    else:
                        status = "cancelled"
                else:
                    status = result_status or "running"
        else:
            if result_status:
                status = result_status
            elif prior_state and prior_state.status in {"finished", "cancelled", "error"}:
                status = prior_state.status
            else:
                continue

        if swarm is not None:
            step_count = 0
            cost_usd = 0.0
            for solver in swarm.solvers.values():
                step_count += _solver_step_count(solver)
                cost_usd += _solver_cost_usd(solver, deps)
            running_models = sorted(swarm.solvers.keys()) if status == "running" else []
        elif prior_state:
            step_count = prior_state.step_count
            cost_usd = prior_state.cost_usd
            running_models = [] if status != "running" else list(prior_state.running_models)
        else:
            step_count = 0
            cost_usd = 0.0
            running_models = []

        last_progress_at = prior_state.last_progress_at if prior_state else None
        if status == "running":
            if prior_state is None:
                if step_count > 0 or running_models:
                    last_progress_at = now
            elif (
                prior_state.status != "running"
                or step_count > prior_state.step_count
                or (last_progress_at is None and (step_count > 0 or running_models))
            ):
                last_progress_at = now

        swarms[name] = SwarmState(
            challenge_name=name,
            status=status,
            running_models=running_models,
            last_bump_at=prior_state.last_bump_at if prior_state else None,
            bump_count=prior_state.bump_count if prior_state else 0,
            last_progress_at=last_progress_at,
            last_error=prior_state.last_error if prior_state else "",
            step_count=step_count,
            cost_usd=cost_usd,
            winner_model=(
                str(deps.results.get(name, {}).get("winner_model", ""))
                or (prior_state.winner_model if prior_state else "")
            ),
            applied_knowledge_ids=set(prior_state.applied_knowledge_ids) if prior_state else set(),
        )

    challenge_names = set(poller.known_challenges)
    challenge_names.update(deps.challenge_metas.keys())
    challenge_names.update(deps.challenge_dirs.keys())
    challenge_names.update(previous_challenges.keys())
    challenge_names.update(swarms.keys())
    challenge_names.update(deps.results.keys())

    challenges: dict[str, ChallengeState] = {}
    for name in challenge_names:
        prior_state = previous_challenges.get(name)
        meta = deps.challenge_metas.get(name)
        value = prior_state.value if prior_state else 0.0
        if meta is not None and getattr(meta, "value", None) is not None:
            value = float(meta.value)

        last_materialized_at = prior_state.last_materialized_at if prior_state else None
        if name in deps.challenge_dirs and last_materialized_at is None:
            last_materialized_at = now

        challenges[name] = ChallengeState(
            challenge_name=name,
            status=_challenge_status(
                challenge_name=name,
                swarm_state=swarms.get(name),
                known_challenges=set(poller.known_challenges),
                known_solved=set(poller.known_solved),
                record=deps.results.get(name),
                prior_state=prior_state,
            ),
            category=(
                str(getattr(meta, "category", "")).strip()
                or (prior_state.category if prior_state else "")
            ),
            value=value,
            requires_env_start=(
                bool(getattr(meta, "requires_env_start", False))
                if meta is not None
                else (prior_state.requires_env_start if prior_state else False)
            ),
            unsupported_reason=(
                str(getattr(meta, "unsupported_reason", "")).strip()
                or (prior_state.unsupported_reason if prior_state else "")
            ),
            last_materialized_at=last_materialized_at,
        )

    return CompetitionState(
        known_challenges=set(poller.known_challenges),
        known_solved=set(poller.known_solved),
        challenges=challenges,
        swarms=swarms,
        results=dict(deps.results),
        global_cost_usd=deps.cost_tracker.total_cost_usd,
        last_poll_at=now,
    )
