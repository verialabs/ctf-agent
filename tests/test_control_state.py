import asyncio

from backend.config import Settings
from backend.control.actions import (
    BroadcastKnowledge,
    BumpSolver,
    HoldChallenge,
    MarkChallengeSkipped,
    RetryChallenge,
    SpawnSwarm,
)
from backend.control.state import (
    ChallengeState,
    CompetitionState,
    SwarmState,
    build_runtime_state_snapshot,
)
from backend.cost_tracker import AgentUsage, CostTracker
from backend.deps import CoordinatorDeps
from backend.prompts import ChallengeMeta


def test_competition_state_counts_only_running_swarms() -> None:
    state = CompetitionState(
        known_challenges={"echo", "rsa"},
        known_solved={"echo"},
        swarms={
            "rsa": SwarmState(
                challenge_name="rsa",
                status="running",
                running_models=["azure/gpt-5.4"],
            ),
            "echo": SwarmState(
                challenge_name="echo",
                status="finished",
                running_models=[],
            ),
        },
    )

    assert state.active_swarm_count == 1


def test_spawn_and_bump_actions_expose_stable_kind() -> None:
    spawn = SpawnSwarm(challenge_name="rsa", priority=10, reason="new unsolved challenge")
    bump = BumpSolver(
        challenge_name="rsa",
        model_spec="azure/gpt-5.4",
        guidance="Switch to lattice attack",
        reason="stalled with open hypothesis",
    )
    hold = HoldChallenge(challenge_name="echo", reason="cooldown", retry_after_seconds=60)

    assert spawn.kind == "spawn_swarm"
    assert bump.kind == "bump_solver"
    assert hold.kind == "hold_challenge"


def test_additional_actions_expose_stable_kind() -> None:
    broadcast = BroadcastKnowledge(
        challenge_name="rsa",
        message="Applying lattice knowledge",
        source="policy",
        knowledge_id="k-42",
    )
    retry = RetryChallenge(challenge_name="rsa", reason="force retry after cooldown")
    skip = MarkChallengeSkipped(challenge_name="echo", reason="not relevant")

    assert broadcast.kind == "broadcast_knowledge"
    assert retry.kind == "retry_challenge"
    assert skip.kind == "mark_challenge_skipped"


def test_build_runtime_state_snapshot_maps_runtime_fields() -> None:
    class StubPlatform:
        pass

    class StubPoller:
        def __init__(self) -> None:
            self.known_challenges = {"alpha", "bravo"}
            self.known_solved = {"alpha"}

    class StubSolver:
        def __init__(self, model_spec: str, step_count: int, cost_usd: float) -> None:
            self.model_spec = model_spec
            self.agent_name = f"solver/{model_spec}"
            self._step_count = step_count
            self._cost_usd = cost_usd

    class StubSwarm:
        def __init__(self, solvers: dict[str, StubSolver]) -> None:
            self.cancel_event = asyncio.Event()
            self.solvers = solvers

    class StubTask:
        def done(self) -> bool:
            return False

    cost_tracker = CostTracker()
    cost_tracker.by_agent["solver/azure/gpt-5.4"] = AgentUsage(cost_usd=1.2)
    cost_tracker.by_agent["solver/codex/gpt-5.4-mini"] = AgentUsage(cost_usd=0.4)
    deps = CoordinatorDeps(
        ctfd=StubPlatform(),
        cost_tracker=cost_tracker,
        settings=Settings(_env_file=None),
        model_specs=[],
    )
    deps.results["alpha"] = {"solve_status": "running"}
    deps.swarms["alpha"] = StubSwarm(
        solvers={
            "azure/gpt-5.4": StubSolver("azure/gpt-5.4", step_count=3, cost_usd=9.9),
            "codex/gpt-5.4-mini": StubSolver("codex/gpt-5.4-mini", step_count=2, cost_usd=8.8),
        }
    )
    deps.swarm_tasks["alpha"] = StubTask()

    snapshot = build_runtime_state_snapshot(deps, StubPoller(), now=123.4)

    assert snapshot.known_challenges == {"alpha", "bravo"}
    assert snapshot.known_solved == {"alpha"}
    assert snapshot.results == deps.results
    assert snapshot.global_cost_usd == 1.6
    assert snapshot.last_poll_at == 123.4
    assert snapshot.swarms["alpha"].status == "running"
    assert snapshot.swarms["alpha"].running_models == ["azure/gpt-5.4", "codex/gpt-5.4-mini"]
    assert snapshot.swarms["alpha"].step_count == 5
    assert snapshot.swarms["alpha"].cost_usd == 1.6


def test_build_runtime_state_snapshot_handles_missing_step_count() -> None:
    class StubPlatform:
        pass

    class StubPoller:
        def __init__(self) -> None:
            self.known_challenges = {"alpha"}
            self.known_solved = set()

    class StubSolver:
        def __init__(self, model_spec: str) -> None:
            self.model_spec = model_spec
            self.agent_name = f"solver/{model_spec}"

    class StubSwarm:
        def __init__(self, solvers: dict[str, StubSolver]) -> None:
            self.cancel_event = asyncio.Event()
            self.solvers = solvers

    class StubTask:
        def done(self) -> bool:
            return False

    cost_tracker = CostTracker()
    cost_tracker.by_agent["solver/azure/gpt-5.4"] = AgentUsage(cost_usd=0.3)
    deps = CoordinatorDeps(
        ctfd=StubPlatform(),
        cost_tracker=cost_tracker,
        settings=Settings(_env_file=None),
        model_specs=[],
    )
    deps.swarms["alpha"] = StubSwarm(
        solvers={"azure/gpt-5.4": StubSolver("azure/gpt-5.4")}
    )
    deps.swarm_tasks["alpha"] = StubTask()

    snapshot = build_runtime_state_snapshot(deps, StubPoller(), now=33.0)

    assert snapshot.swarms["alpha"].step_count == 0
    assert snapshot.swarms["alpha"].cost_usd == 0.3


def test_build_runtime_state_snapshot_preserves_terminal_status_without_task() -> None:
    class StubPlatform:
        pass

    class StubPoller:
        def __init__(self) -> None:
            self.known_challenges = {"alpha"}
            self.known_solved = {"alpha"}

    class StubSolver:
        def __init__(self, model_spec: str) -> None:
            self.model_spec = model_spec
            self.agent_name = f"solver/{model_spec}"
            self._step_count = 3

    class StubSwarm:
        def __init__(self, solvers: dict[str, StubSolver]) -> None:
            self.cancel_event = asyncio.Event()
            self.cancel_event.set()
            self.solvers = solvers

    cost_tracker = CostTracker()
    cost_tracker.by_agent["solver/azure/gpt-5.4"] = AgentUsage(cost_usd=0.5)
    deps = CoordinatorDeps(
        ctfd=StubPlatform(),
        cost_tracker=cost_tracker,
        settings=Settings(_env_file=None),
        model_specs=[],
    )
    deps.results["alpha"] = {"solve_status": "flag_found"}
    deps.runtime_state = CompetitionState(
        swarms={
            "alpha": SwarmState(
                challenge_name="alpha",
                status="finished",
                running_models=[],
                step_count=7,
                cost_usd=0.9,
            )
        }
    )
    deps.swarms["alpha"] = StubSwarm(
        solvers={"azure/gpt-5.4": StubSolver("azure/gpt-5.4")}
    )

    snapshot = build_runtime_state_snapshot(deps, StubPoller(), now=88.0)

    assert snapshot.swarms["alpha"].status == "finished"
    assert snapshot.swarms["alpha"].running_models == []


def test_build_runtime_state_snapshot_preserves_policy_fields_and_challenge_metadata() -> None:
    class StubPlatform:
        pass

    class StubPoller:
        def __init__(self) -> None:
            self.known_challenges = {"alpha"}
            self.known_solved = set()

    class StubSolver:
        def __init__(self, model_spec: str, step_count: int) -> None:
            self.model_spec = model_spec
            self.agent_name = f"solver/{model_spec}"
            self._step_count = step_count

    class StubSwarm:
        def __init__(self, solvers: dict[str, StubSolver]) -> None:
            self.cancel_event = asyncio.Event()
            self.solvers = solvers

    class StubTask:
        def done(self) -> bool:
            return False

    cost_tracker = CostTracker()
    cost_tracker.by_agent["solver/azure/gpt-5.4"] = AgentUsage(cost_usd=0.75)
    deps = CoordinatorDeps(
        ctfd=StubPlatform(),
        cost_tracker=cost_tracker,
        settings=Settings(_env_file=None),
        model_specs=[],
    )
    deps.challenge_metas["alpha"] = ChallengeMeta(
        name="alpha",
        category="web",
        value=300,
        requires_env_start=True,
    )
    deps.runtime_state = CompetitionState(
        challenges={
            "alpha": ChallengeState(
                challenge_name="alpha",
                status="running",
                category="web",
                value=300,
                requires_env_start=True,
            )
        },
        swarms={
            "alpha": SwarmState(
                challenge_name="alpha",
                status="running",
                running_models=["azure/gpt-5.4"],
                last_bump_at=11.0,
                last_progress_at=15.0,
                step_count=2,
                cost_usd=0.2,
                applied_knowledge_ids={"k-1"},
            )
        },
    )
    deps.swarms["alpha"] = StubSwarm(
        solvers={"azure/gpt-5.4": StubSolver("azure/gpt-5.4", step_count=5)}
    )
    deps.swarm_tasks["alpha"] = StubTask()

    snapshot = build_runtime_state_snapshot(deps, StubPoller(), now=42.0)

    assert snapshot.challenges["alpha"].status == "running"
    assert snapshot.challenges["alpha"].category == "web"
    assert snapshot.challenges["alpha"].value == 300
    assert snapshot.challenges["alpha"].requires_env_start is True
    assert snapshot.swarms["alpha"].last_bump_at == 11.0
    assert snapshot.swarms["alpha"].last_progress_at == 42.0
    assert snapshot.swarms["alpha"].applied_knowledge_ids == {"k-1"}


def test_build_runtime_state_snapshot_retains_terminal_state_without_swarm() -> None:
    class StubPlatform:
        pass

    class StubPoller:
        def __init__(self) -> None:
            self.known_challenges = {"alpha"}
            self.known_solved = {"alpha"}

    deps = CoordinatorDeps(
        ctfd=StubPlatform(),
        cost_tracker=CostTracker(),
        settings=Settings(_env_file=None),
        model_specs=[],
    )
    deps.results["alpha"] = {"solve_status": "flag_found"}
    deps.runtime_state = CompetitionState(
        swarms={
            "alpha": SwarmState(
                challenge_name="alpha",
                status="finished",
                running_models=[],
                step_count=7,
                cost_usd=0.9,
            )
        }
    )

    snapshot = build_runtime_state_snapshot(deps, StubPoller(), now=90.0)

    assert snapshot.swarms["alpha"].status == "finished"
    assert snapshot.swarms["alpha"].step_count == 7
    assert snapshot.swarms["alpha"].cost_usd == 0.9
