from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import backend.agents.coordinator_core as coordinator_core
import backend.agents.coordinator_loop as coordinator_loop
from backend.config import Settings
from backend.cost_tracker import CostTracker
from backend.ctfd import CTFdClient, SubmitResult
from backend.deps import CoordinatorDeps
from backend.platforms.lingxu_event_ctf import LingxuEventCTFClient
from backend.poller import CompetitionPoller, CTFdPoller
from backend.prompts import ChallengeMeta


class FakePlatform:
    def __init__(
        self,
        stub_snapshots: Sequence[list[dict[str, Any]]] | None = None,
        solved_snapshots: Sequence[set[str]] | None = None,
        all_challenges: list[dict[str, Any]] | None = None,
        events: list[str] | None = None,
        supports_challenge_materialization: bool = True,
    ) -> None:
        self._stub_snapshots = list(stub_snapshots or [[]])
        self._solved_snapshots = list(solved_snapshots or [set()])
        self._all_challenges = list(all_challenges or [])
        self._stub_index = 0
        self._solved_index = 0
        self.events = events if events is not None else []
        self.supports_challenge_materialization = supports_challenge_materialization

    async def validate_access(self) -> None:
        self.events.append("validate_access")

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        snapshot = self._stub_snapshots[min(self._stub_index, len(self._stub_snapshots) - 1)]
        self._stub_index += 1
        return snapshot

    async def fetch_all_challenges(self) -> list[dict[str, Any]]:
        return list(self._all_challenges)

    async def fetch_solved_names(self) -> set[str]:
        snapshot = self._solved_snapshots[min(self._solved_index, len(self._solved_snapshots) - 1)]
        self._solved_index += 1
        return snapshot

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        return output_dir

    async def prepare_challenge(self, challenge_dir: str) -> None:
        return None

    async def submit_flag(self, challenge_ref: Any, flag: str) -> dict[str, Any]:
        return {"status": "incorrect", "challenge_ref": challenge_ref, "flag": flag}

    async def close(self) -> None:
        self.events.append("close")


def make_settings(**overrides: Any) -> Settings:
    values = {
        "platform": "ctfd",
        "platform_url": "",
        "lingxu_event_id": 0,
        "lingxu_cookie": "",
        "lingxu_cookie_file": "",
        "ctfd_url": "https://ctfd.example.com",
        "ctfd_user": "admin",
        "ctfd_pass": "password",
        "ctfd_token": "token-1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_competition_poller_detects_new_and_solved_challenges() -> None:
    platform = FakePlatform(
        stub_snapshots=[
            [{"name": "warmup"}],
            [{"name": "warmup"}, {"name": "pwn-100"}],
        ],
        solved_snapshots=[set(), {"warmup"}],
    )
    poller = CompetitionPoller(ctfd=platform, interval_s=60.0)

    await poller.start()
    try:
        assert poller.known_challenges == {"warmup"}
        assert poller.known_solved == set()

        await poller._poll_once()

        events = poller.drain_events()
        assert [(event.kind, event.challenge_name) for event in events] == [
            ("new_challenge", "pwn-100"),
            ("challenge_solved", "warmup"),
        ]
        assert poller.known_challenges == {"warmup", "pwn-100"}
        assert poller.known_solved == {"warmup"}
    finally:
        await poller.stop()


@pytest.mark.asyncio
async def test_ctfd_poller_alias_accepts_legacy_ctfd_keyword() -> None:
    platform = FakePlatform(
        stub_snapshots=[[{"name": "warmup"}]],
        solved_snapshots=[set()],
    )

    poller = CTFdPoller(ctfd=platform, interval_s=60.0)

    await poller.start()
    try:
        assert poller.known_challenges == {"warmup"}
        assert poller.known_solved == set()
    finally:
        await poller.stop()


def test_build_deps_accepts_platform_override_and_preloads_metadata(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "web-200"
    challenge_dir.mkdir()
    (challenge_dir / "metadata.yml").write_text(
        "\n".join(
            [
                "name: Local Warmup",
                "category: web",
                "value: 200",
                "description: local copy",
            ]
        ),
        encoding="utf-8",
    )
    platform = FakePlatform()

    returned_platform, cost_tracker, deps = coordinator_loop.build_deps(
        make_settings(),
        challenges_root=str(tmp_path),
        platform=platform,
    )

    assert returned_platform is platform
    assert deps.ctfd is platform
    assert deps.cost_tracker is cost_tracker
    assert deps.challenge_dirs == {"Local Warmup": str(challenge_dir)}
    assert deps.challenge_metas["Local Warmup"].category == "web"
    assert deps.challenge_metas["Local Warmup"].value == 200


@pytest.mark.asyncio
async def test_build_deps_default_ctfd_client_supports_validate_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctfd, _cost_tracker, _deps = coordinator_loop.build_deps(make_settings())

    assert isinstance(ctfd, CTFdClient)

    called: list[str] = []

    async def fake_fetch_challenge_stubs() -> list[dict[str, Any]]:
        called.append("fetch_challenge_stubs")
        return [{"name": "warmup"}]

    monkeypatch.setattr(ctfd, "fetch_challenge_stubs", fake_fetch_challenge_stubs)

    await ctfd.validate_access()

    assert called == ["fetch_challenge_stubs"]


@pytest.mark.asyncio
async def test_run_event_loop_validates_platform_before_starting_poller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    platform = FakePlatform(events=events)
    deps = CoordinatorDeps(
        ctfd=platform,
        cost_tracker=CostTracker(),
        settings=make_settings(),
        model_specs=[],
    )

    class FakePoller:
        def __init__(self, ctfd: FakePlatform, interval_s: float) -> None:
            assert interval_s == 5.0
            self.ctfd = ctfd
            self.interval_s = interval_s
            self.known_challenges: set[str] = set()
            self.known_solved: set[str] = set()

        async def start(self) -> None:
            events.append("poller_start")

        async def stop(self) -> None:
            events.append("poller_stop")

        async def get_event(self, timeout: float = 1.0) -> None:
            return None

        def drain_events(self) -> list[Any]:
            return []

    async def fake_start_msg_server(inbox: asyncio.Queue, port: int = 0) -> None:
        assert port == deps.msg_port
        return None

    async def fake_turn_fn(message: str) -> None:
        events.append("turn_fn")
        raise asyncio.CancelledError()

    monkeypatch.setattr(coordinator_loop, "CompetitionPoller", FakePoller)
    monkeypatch.setattr(coordinator_loop, "_start_msg_server", fake_start_msg_server)

    result = await coordinator_loop.run_event_loop(
        deps=deps,
        ctfd=platform,
        cost_tracker=deps.cost_tracker,
        turn_fn=fake_turn_fn,
    )

    assert result["results"] == {}
    assert events[:3] == ["validate_access", "poller_start", "turn_fn"]
    assert "poller_stop" in events
    assert events[-1] == "close"


@pytest.mark.asyncio
async def test_auto_spawn_one_skips_platforms_without_materialization_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = FakePlatform(supports_challenge_materialization=False)
    deps = CoordinatorDeps(
        ctfd=platform,
        cost_tracker=CostTracker(),
        settings=make_settings(),
        model_specs=[],
    )

    called: list[str] = []

    async def fake_do_spawn_swarm(_deps: CoordinatorDeps, challenge_name: str) -> str:
        called.append(challenge_name)
        return "unexpected"

    monkeypatch.setattr(coordinator_core, "do_spawn_swarm", fake_do_spawn_swarm)

    await coordinator_loop._auto_spawn_one(deps, "warmup")

    assert called == []


@pytest.mark.asyncio
async def test_auto_spawn_one_calls_spawn_when_platform_supports_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = LingxuEventCTFClient(
        base_url="https://lx.example.com",
        event_id=42,
        cookie="sessionid=sid123; csrftoken=csrf456",
    )
    deps = CoordinatorDeps(
        ctfd=platform,
        cost_tracker=CostTracker(),
        settings=make_settings(),
        model_specs=[],
    )

    called: list[str] = []

    async def fake_do_spawn_swarm(_deps: CoordinatorDeps, challenge_name: str) -> str:
        called.append(challenge_name)
        return "spawned"

    monkeypatch.setattr(coordinator_core, "do_spawn_swarm", fake_do_spawn_swarm)

    await coordinator_loop._auto_spawn_one(deps, "warmup")

    assert called == ["warmup"]


@pytest.mark.asyncio
async def test_do_spawn_swarm_returns_stable_message_when_materialization_is_unavailable() -> None:
    class NotImplementedPlatform(FakePlatform):
        async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
            raise NotImplementedError("Implemented in Task 4")

    challenge_name = "warmup"
    platform = NotImplementedPlatform(
        all_challenges=[
            {
                "name": challenge_name,
                "category": "misc",
                "value": 100,
            }
        ]
    )
    deps = CoordinatorDeps(
        ctfd=platform,
        cost_tracker=CostTracker(),
        settings=make_settings(),
        model_specs=[],
        challenges_root="challenges",
    )

    result = await coordinator_core.do_spawn_swarm(deps, challenge_name)

    assert result == f"Challenge '{challenge_name}' materialization is not available for this platform yet"


@pytest.mark.asyncio
async def test_do_spawn_swarm_skips_unsupported_materialized_challenge(tmp_path: Path) -> None:
    challenge_name = "check-mode"

    class UnsupportedPlatform(FakePlatform):
        async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
            challenge_dir = Path(output_dir) / "check-mode-204"
            challenge_dir.mkdir(parents=True, exist_ok=True)
            (challenge_dir / "distfiles").mkdir(exist_ok=True)
            (challenge_dir / "metadata.yml").write_text(
                "\n".join(
                    [
                        f"name: {challenge_name}",
                        "category: web",
                        "value: 100",
                        "description: unsupported",
                        "platform: lingxu-event-ctf",
                        "platform_challenge_id: 204",
                        "unsupported_reason: check mode is not supported in v1",
                    ]
                ),
                encoding="utf-8",
            )
            return str(challenge_dir)

    platform = UnsupportedPlatform(
        all_challenges=[
            {
                "name": challenge_name,
                "category": "web",
                "value": 100,
            }
        ]
    )
    deps = CoordinatorDeps(
        ctfd=platform,
        cost_tracker=CostTracker(),
        settings=make_settings(),
        model_specs=[],
        challenges_root=str(tmp_path),
    )

    result = await coordinator_core.do_spawn_swarm(deps, challenge_name)

    assert result == f"Challenge '{challenge_name}' skipped: check mode is not supported in v1"
    assert deps.swarms == {}


@pytest.mark.asyncio
async def test_do_spawn_swarm_returns_preflight_failed_when_prepare_raises(tmp_path: Path) -> None:
    challenge_name = "env-task"

    class PreflightFailPlatform(FakePlatform):
        async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
            challenge_dir = Path(output_dir) / "env-task-137"
            challenge_dir.mkdir(parents=True, exist_ok=True)
            (challenge_dir / "distfiles").mkdir(exist_ok=True)
            (challenge_dir / "metadata.yml").write_text(
                "\n".join(
                    [
                        f"name: {challenge_name}",
                        "category: pwn",
                        "value: 300",
                        "description: env task",
                        "platform: lingxu-event-ctf",
                        "platform_challenge_id: 137",
                        "requires_env_start: true",
                        "connection_info: ''",
                    ]
                ),
                encoding="utf-8",
            )
            return str(challenge_dir)

        async def prepare_challenge(self, challenge_dir: str) -> None:
            raise RuntimeError("docker boot timeout")

    platform = PreflightFailPlatform(
        all_challenges=[
            {
                "name": challenge_name,
                "category": "pwn",
                "value": 300,
            }
        ]
    )
    deps = CoordinatorDeps(
        ctfd=platform,
        cost_tracker=CostTracker(),
        settings=make_settings(),
        model_specs=[],
        challenges_root=str(tmp_path),
    )

    result = await coordinator_core.do_spawn_swarm(deps, challenge_name)

    assert result == f"Challenge '{challenge_name}' preflight_failed: docker boot timeout"
    assert deps.swarms == {}


@pytest.mark.asyncio
async def test_do_spawn_swarm_skips_unsupported_before_preflight(tmp_path: Path) -> None:
    challenge_name = "unsupported-env"

    class UnsupportedEnvPlatform(FakePlatform):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.prepare_calls: list[str] = []

        async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
            challenge_dir = Path(output_dir) / "unsupported-env-137"
            challenge_dir.mkdir(parents=True, exist_ok=True)
            (challenge_dir / "distfiles").mkdir(exist_ok=True)
            (challenge_dir / "metadata.yml").write_text(
                "\n".join(
                    [
                        f"name: {challenge_name}",
                        "category: pwn",
                        "value: 300",
                        "description: unsupported env task",
                        "platform: lingxu-event-ctf",
                        "platform_challenge_id: 137",
                        "requires_env_start: true",
                        "connection_info: ''",
                        "unsupported_reason: check mode is not supported in v1",
                    ]
                ),
                encoding="utf-8",
            )
            return str(challenge_dir)

        async def prepare_challenge(self, challenge_dir: str) -> None:
            self.prepare_calls.append(challenge_dir)
            raise RuntimeError("prepare_challenge should not be called")

    platform = UnsupportedEnvPlatform(
        all_challenges=[
            {
                "name": challenge_name,
                "category": "pwn",
                "value": 300,
            }
        ]
    )
    deps = CoordinatorDeps(
        ctfd=platform,
        cost_tracker=CostTracker(),
        settings=make_settings(),
        model_specs=[],
        challenges_root=str(tmp_path),
    )

    result = await coordinator_core.do_spawn_swarm(deps, challenge_name)

    assert result == f"Challenge '{challenge_name}' skipped: check mode is not supported in v1"
    assert platform.prepare_calls == []
    assert deps.swarms == {}


@pytest.mark.asyncio
async def test_do_submit_flag_prefers_challenge_meta_over_name() -> None:
    challenge_name = "env-task"

    class RecordingPlatform(FakePlatform):
        def __init__(self) -> None:
            super().__init__()
            self.seen_refs: list[Any] = []

        async def submit_flag(self, challenge_ref: Any, flag: str) -> SubmitResult:
            self.seen_refs.append(challenge_ref)
            return SubmitResult(
                status="correct",
                message="accepted",
                display=f'CORRECT — "{flag}" accepted. accepted',
            )

    platform = RecordingPlatform()
    deps = CoordinatorDeps(
        ctfd=platform,
        cost_tracker=CostTracker(),
        settings=make_settings(platform="lingxu-event-ctf"),
        model_specs=[],
    )
    meta = ChallengeMeta(
        name=challenge_name,
        platform="lingxu-event-ctf",
        platform_url="https://lx.example.com",
        event_id=42,
        platform_challenge_id=137,
    )
    deps.challenge_metas[challenge_name] = meta

    result = await coordinator_core.do_submit_flag(deps, challenge_name, "FLAG{real}")

    assert platform.seen_refs == [meta]
    assert result == 'CORRECT — "FLAG{real}" accepted. accepted'
