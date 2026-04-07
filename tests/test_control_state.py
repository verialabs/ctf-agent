from backend.control.actions import BumpSolver, HoldChallenge, SpawnSwarm
from backend.control.state import ChallengeState, CompetitionState, SwarmState


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
