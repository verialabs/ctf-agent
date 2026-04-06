from __future__ import annotations

import asyncio
import json
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from backend import cli
from backend.config import Settings
from backend.solver_base import FLAG_FOUND, SolverResult


def test_import_cmd_help_uses_english_options_with_chinese_help() -> None:
    result = CliRunner().invoke(cli.import_cmd, ["--help"])

    assert result.exit_code == 0
    assert "把手工整理的题目信息导入为本地题目目录。" in result.output
    assert "--name" in result.output
    assert "--attachment-dir" in result.output
    assert "附件目录，会递归拷贝其中的文件" in result.output
    assert "--题目名称" not in result.output


def test_main_help_uses_english_options_with_chinese_help() -> None:
    result = CliRunner().invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "CTF Agent 多模型题目求解入口。" in result.output
    assert "不传 `--challenge` 时启动完整协调器" in result.output
    assert "--challenge" in result.output
    assert "--platform" in result.output
    assert "claude" in result.output
    assert "codex" in result.output
    assert "none" in result.output
    assert "--lingxu-cookie-file" in result.output
    assert "--all-solved-policy" in result.output
    assert "--all-solved-idle-seconds" in result.output
    assert "--writeup-mode" in result.output
    assert "--writeup-dir" in result.output
    assert "--题目目录" not in result.output


def test_msg_help_uses_english_options_with_chinese_help() -> None:
    result = CliRunner().invoke(cli.msg, ["--help"])

    assert result.exit_code == 0
    assert "向运行中的协调器发送消息。" in result.output
    assert "--port" in result.output
    assert "--host" in result.output
    assert "--端口" not in result.output


def test_pyproject_exposes_ctf_import_script() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    assert data["project"]["scripts"]["ctf-import"] == "backend.cli:import_cmd"


def test_import_cmd_writes_local_challenge_directory(tmp_path: Path) -> None:
    attachment = tmp_path / "note.txt"
    attachment.write_text("payload\n", encoding="utf-8")
    output_dir = tmp_path / "challenges"

    result = CliRunner().invoke(
        cli.import_cmd,
        [
            "--name",
            "demo",
            "--category",
            "misc",
            "--description",
            "desc",
            "--attachment",
            str(attachment),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert "导入成功：" in result.output
    metadata = yaml.safe_load((output_dir / "demo" / "metadata.yml").read_text(encoding="utf-8"))
    assert metadata["category"] == "misc"


def test_main_accepts_lingxu_cookie_file_and_runs_coordinator(monkeypatch, tmp_path: Path) -> None:
    cookie_file = tmp_path / "lingxu.cookie"
    cookie_file.write_text("sessionid=sid123; csrftoken=csrf456", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_run_coordinator(
        settings,
        model_specs,
        challenges_dir,
        no_submit,
        coordinator_model,
        coordinator_backend,
        max_challenges,
        msg_port=0,
    ) -> None:
        captured["settings"] = settings
        captured["model_specs"] = model_specs
        captured["challenges_dir"] = challenges_dir
        captured["no_submit"] = no_submit
        captured["coordinator_model"] = coordinator_model
        captured["coordinator_backend"] = coordinator_backend
        captured["max_challenges"] = max_challenges
        captured["msg_port"] = msg_port

    monkeypatch.setattr(cli, "_run_coordinator", fake_run_coordinator)

    result = CliRunner().invoke(
        cli.main,
        [
            "--platform",
            "lingxu-event-ctf",
            "--platform-url",
            "https://lx.example.com",
            "--lingxu-event-id",
            "42",
            "--lingxu-cookie-file",
            str(cookie_file),
            "--models",
            "codex/gpt-5.4",
            "--msg-port",
            "9500",
        ],
    )

    assert result.exit_code == 0
    settings = captured["settings"]
    assert settings.platform == "lingxu-event-ctf"
    assert settings.platform_url == "https://lx.example.com"
    assert settings.lingxu_event_id == 42
    assert settings.lingxu_cookie_file == str(cookie_file)
    assert captured["model_specs"] == ["codex/gpt-5.4"]
    assert captured["msg_port"] == 9500


def test_main_accepts_headless_coordinator(monkeypatch, tmp_path: Path) -> None:
    cookie_file = tmp_path / "lingxu.cookie"
    cookie_file.write_text("sessionid=sid123", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_run_coordinator(
        settings,
        model_specs,
        challenges_dir,
        no_submit,
        coordinator_model,
        coordinator_backend,
        max_challenges,
        msg_port=0,
    ) -> None:
        captured["settings"] = settings
        captured["coordinator_backend"] = coordinator_backend
        captured["msg_port"] = msg_port

    monkeypatch.setattr(cli, "_run_coordinator", fake_run_coordinator)

    result = CliRunner().invoke(
        cli.main,
        [
            "--platform",
            "lingxu-event-ctf",
            "--platform-url",
            "https://lx.example.com",
            "--lingxu-event-id",
            "42",
            "--lingxu-cookie-file",
            str(cookie_file),
            "--coordinator",
            "none",
            "--msg-port",
            "9600",
        ],
    )

    assert result.exit_code == 0
    assert captured["coordinator_backend"] == "none"
    assert captured["msg_port"] == 9600


def test_main_rejects_lingxu_without_cookie(monkeypatch) -> None:
    monkeypatch.setenv("LINGXU_COOKIE", "")
    monkeypatch.setenv("LINGXU_COOKIE_FILE", "")

    result = CliRunner().invoke(
        cli.main,
        [
            "--platform",
            "lingxu-event-ctf",
            "--platform-url",
            "https://lx.example.com",
            "--lingxu-event-id",
            "42",
        ],
    )

    assert result.exit_code != 0
    assert "lingxu_cookie" in result.output


def test_main_accepts_challenge_option(monkeypatch, tmp_path: Path) -> None:
    challenge_dir = tmp_path / "demo"
    challenge_dir.mkdir()
    (challenge_dir / "metadata.yml").write_text("name: demo\n", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_run_single(settings, challenge_dir, model_specs, no_submit, max_challenges):
        captured["challenge_dir"] = challenge_dir
        captured["no_submit"] = no_submit
        captured["max_challenges"] = max_challenges

    def fake_asyncio_run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(cli, "_run_single", fake_run_single)
    monkeypatch.setattr(cli.asyncio, "run", fake_asyncio_run)

    result = CliRunner().invoke(cli.main, ["--challenge", str(challenge_dir), "--no-submit"])

    assert result.exit_code == 0
    assert captured["challenge_dir"] == str(challenge_dir)
    assert captured["no_submit"] is True
    assert captured["max_challenges"] == 10


def test_main_uses_environment_values_for_all_solved_and_writeup_options_when_cli_omits_them(
    monkeypatch, tmp_path: Path
) -> None:
    challenge_dir = tmp_path / "demo"
    challenge_dir.mkdir()
    (challenge_dir / "metadata.yml").write_text("name: demo\n", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_run_single(settings, challenge_dir, model_specs, no_submit, max_challenges):
        captured["settings"] = settings
        captured["challenge_dir"] = challenge_dir

    def fake_asyncio_run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setenv("ALL_SOLVED_POLICY", "idle")
    monkeypatch.setenv("ALL_SOLVED_IDLE_SECONDS", "45")
    monkeypatch.setenv("WRITEUP_MODE", "confirmed")
    monkeypatch.setenv("WRITEUP_DIR", "env-notes")
    monkeypatch.setattr(cli, "_run_single", fake_run_single)
    monkeypatch.setattr(cli.asyncio, "run", fake_asyncio_run)

    result = CliRunner().invoke(cli.main, ["--challenge", str(challenge_dir)])

    assert result.exit_code == 0
    settings = captured["settings"]
    assert captured["challenge_dir"] == str(challenge_dir)
    assert settings.all_solved_policy == "idle"
    assert settings.all_solved_idle_seconds == 45
    assert settings.writeup_mode == "confirmed"
    assert settings.writeup_dir == "env-notes"
    assert "All-solved policy: idle" in result.output
    assert "Idle timeout: 45 seconds" in result.output
    assert "Writeup mode: confirmed" in result.output
    assert "Writeup dir: env-notes" in result.output


def test_main_writes_all_solved_and_writeup_options_to_settings(monkeypatch, tmp_path: Path) -> None:
    challenge_dir = tmp_path / "demo"
    challenge_dir.mkdir()
    (challenge_dir / "metadata.yml").write_text("name: demo\n", encoding="utf-8")
    writeup_dir = Path("notes")
    captured: dict[str, object] = {}

    async def fake_run_single(settings, challenge_dir, model_specs, no_submit, max_challenges):
        captured["settings"] = settings
        captured["challenge_dir"] = challenge_dir

    def fake_asyncio_run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(cli, "_run_single", fake_run_single)
    monkeypatch.setattr(cli.asyncio, "run", fake_asyncio_run)

    result = CliRunner().invoke(
        cli.main,
        [
            "--challenge",
            str(challenge_dir),
            "--all-solved-policy",
            "idle",
            "--all-solved-idle-seconds",
            "120",
            "--writeup-mode",
            "solved",
            "--writeup-dir",
            str(writeup_dir),
        ],
    )

    assert result.exit_code == 0
    settings = captured["settings"]
    assert captured["challenge_dir"] == str(challenge_dir)
    assert settings.all_solved_policy == "idle"
    assert settings.all_solved_idle_seconds == 120
    assert settings.writeup_mode == "solved"
    assert settings.writeup_dir == str(writeup_dir)
    assert "All-solved policy: idle" in result.output
    assert "Idle timeout: 120 seconds" in result.output
    assert "Writeup mode: solved" in result.output
    assert f"Writeup dir: {writeup_dir}" in result.output


def test_main_rejects_non_positive_idle_seconds_for_idle_policy(monkeypatch, tmp_path: Path) -> None:
    challenge_dir = tmp_path / "demo"
    challenge_dir.mkdir()
    (challenge_dir / "metadata.yml").write_text("name: demo\n", encoding="utf-8")
    called = False

    async def fake_run_single(settings, challenge_dir, model_specs, no_submit, max_challenges):
        nonlocal called
        called = True

    def fake_asyncio_run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(cli, "_run_single", fake_run_single)
    monkeypatch.setattr(cli.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(cli, "validate_platform_settings", lambda settings: None)

    result = CliRunner().invoke(
        cli.main,
        [
            "--challenge",
            str(challenge_dir),
            "--all-solved-policy",
            "idle",
            "--all-solved-idle-seconds",
            "0",
        ],
    )

    assert result.exit_code != 0
    assert "--all-solved-idle-seconds 必须大于 0" in result.output
    assert called is False


def test_settings_allow_non_positive_idle_seconds_when_policy_is_not_idle() -> None:
    settings = Settings(all_solved_policy="wait", all_solved_idle_seconds=0)

    assert settings.all_solved_policy == "wait"
    assert settings.all_solved_idle_seconds == 0


def test_settings_reject_non_positive_idle_seconds_when_policy_is_idle() -> None:
    with pytest.raises(ValidationError):
        Settings(all_solved_policy="idle", all_solved_idle_seconds=0)


def _make_cli_settings(**overrides: Any) -> Settings:
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


def _write_single_challenge_metadata(challenge_dir: Path, **overrides: Any) -> None:
    metadata = {
        "name": "demo",
        "category": "misc",
        "description": "single challenge regression",
        "value": 100,
        "platform": "lingxu-event-ctf",
        "event_id": 198,
        "platform_challenge_id": 42,
        "requires_env_start": False,
    }
    metadata.update(overrides)
    (challenge_dir / "metadata.yml").write_text(yaml.safe_dump(metadata), encoding="utf-8")


def _make_solver_result(
    *,
    flag: str = "flag{demo}",
    status: str = FLAG_FOUND,
    model_spec: str = "codex/gpt-5.4",
) -> SolverResult:
    return SolverResult(
        flag=flag,
        status=status,
        findings_summary="Recovered the real flag.",
        step_count=4,
        cost_usd=0.42,
        log_path="trace.jsonl",
        model_spec=model_spec,
    )


def _install_single_run_swarm(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: SolverResult | None,
    confirmed_submit_status: str = "",
    confirmed_submit_display: str = "",
) -> None:
    import backend.agents.swarm as swarm_module

    class StubChallengeSwarm:
        def __init__(self, **kwargs: Any) -> None:
            self.challenge_dir = kwargs["challenge_dir"]
            self.meta = kwargs["meta"]
            self.confirmed_submit_status = confirmed_submit_status
            self.confirmed_submit_display = confirmed_submit_display
            self.confirmed_submit_message = ""
            self.confirmed_flag = result.flag if result is not None else None

        async def run(self) -> SolverResult | None:
            return result

    monkeypatch.setattr(swarm_module, "ChallengeSwarm", StubChallengeSwarm)


class _FakeSingleRunPlatform:
    def __init__(self) -> None:
        self.released: list[Any] = []
        self.closed = False

    async def release_challenge_env(self, challenge_ref: Any) -> None:
        self.released.append(challenge_ref)

    async def close(self) -> None:
        self.closed = True


def test_run_single_generates_writeup_in_solved_mode(monkeypatch, tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_single_challenge_metadata(challenge_dir, name="writeup-demo")
    fake_platform = _FakeSingleRunPlatform()
    writeup_calls: list[dict[str, Any]] = []

    async def fake_cleanup_orphan_containers() -> None:
        return None

    def fake_configure_semaphore(limit: int) -> None:
        return None

    def fake_write_writeup(meta, challenge_dir, record, writeup_dir) -> Path:
        path = Path(writeup_dir) / f"{meta.name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {meta.name}\n{record['flag']}\n", encoding="utf-8")
        writeup_calls.append({"meta": meta, "challenge_dir": challenge_dir, "record": record, "path": path})
        return path

    import backend.sandbox as sandbox_module

    monkeypatch.setattr(cli, "create_platform_client", lambda settings: fake_platform)
    monkeypatch.setattr(sandbox_module, "cleanup_orphan_containers", fake_cleanup_orphan_containers)
    monkeypatch.setattr(sandbox_module, "configure_semaphore", fake_configure_semaphore)
    monkeypatch.setattr("backend.writeups.write_writeup", fake_write_writeup)
    _install_single_run_swarm(monkeypatch, result=_make_solver_result(flag="flag{writeup}"))

    settings = _make_cli_settings(writeup_mode="solved", writeup_dir=str(tmp_path / "writeups"))

    asyncio.run(
        cli._run_single(
            settings=settings,
            challenge_dir=str(challenge_dir),
            model_specs=["codex/gpt-5.4"],
            no_submit=False,
            max_challenges=1,
        )
    )

    assert len(writeup_calls) == 1
    assert writeup_calls[0]["challenge_dir"] == str(challenge_dir)
    assert writeup_calls[0]["record"]["solve_status"] == FLAG_FOUND
    assert writeup_calls[0]["record"]["writeup_status"] == "generated"
    assert writeup_calls[0]["path"].exists()
    assert fake_platform.released == []
    assert fake_platform.closed is True


def test_run_single_releases_platform_env_after_confirmed_submit(monkeypatch, tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_single_challenge_metadata(
        challenge_dir,
        name="release-demo",
        requires_env_start=True,
        platform_challenge_id=314,
    )
    fake_platform = _FakeSingleRunPlatform()

    async def fake_cleanup_orphan_containers() -> None:
        return None

    def fake_configure_semaphore(limit: int) -> None:
        return None

    import backend.sandbox as sandbox_module

    monkeypatch.setattr(cli, "create_platform_client", lambda settings: fake_platform)
    monkeypatch.setattr(sandbox_module, "cleanup_orphan_containers", fake_cleanup_orphan_containers)
    monkeypatch.setattr(sandbox_module, "configure_semaphore", fake_configure_semaphore)
    _install_single_run_swarm(
        monkeypatch,
        result=_make_solver_result(flag="flag{release}"),
        confirmed_submit_status="correct",
        confirmed_submit_display='CORRECT — "flag{release}" accepted. accepted',
    )

    settings = _make_cli_settings(writeup_mode="off")

    asyncio.run(
        cli._run_single(
            settings=settings,
            challenge_dir=str(challenge_dir),
            model_specs=["codex/gpt-5.4"],
            no_submit=False,
            max_challenges=1,
        )
    )

    assert len(fake_platform.released) == 1
    released_meta = fake_platform.released[0]
    assert released_meta.name == "release-demo"
    assert released_meta.platform_challenge_id == 314
    assert released_meta.requires_env_start is True
    assert fake_platform.closed is True


def test_run_single_no_submit_skips_release_and_still_generates_solved_writeup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    _write_single_challenge_metadata(
        challenge_dir,
        name="dry-run-demo",
        requires_env_start=True,
        platform_challenge_id=2718,
    )
    fake_platform = _FakeSingleRunPlatform()
    writeup_calls: list[dict[str, Any]] = []

    async def fake_cleanup_orphan_containers() -> None:
        return None

    def fake_configure_semaphore(limit: int) -> None:
        return None

    def fake_write_writeup(meta, challenge_dir, record, writeup_dir) -> Path:
        path = Path(writeup_dir) / f"{meta.name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record["flag"] or "", encoding="utf-8")
        writeup_calls.append({"record": record, "path": path})
        return path

    import backend.sandbox as sandbox_module

    monkeypatch.setattr(cli, "create_platform_client", lambda settings: fake_platform)
    monkeypatch.setattr(sandbox_module, "cleanup_orphan_containers", fake_cleanup_orphan_containers)
    monkeypatch.setattr(sandbox_module, "configure_semaphore", fake_configure_semaphore)
    monkeypatch.setattr("backend.writeups.write_writeup", fake_write_writeup)
    _install_single_run_swarm(
        monkeypatch,
        result=_make_solver_result(flag="flag{dry-run}"),
        confirmed_submit_status="correct",
        confirmed_submit_display='CORRECT — "flag{dry-run}" accepted. accepted',
    )

    settings = _make_cli_settings(writeup_mode="solved", writeup_dir=str(tmp_path / "writeups"))

    asyncio.run(
        cli._run_single(
            settings=settings,
            challenge_dir=str(challenge_dir),
            model_specs=["codex/gpt-5.4"],
            no_submit=True,
            max_challenges=1,
        )
    )

    assert fake_platform.released == []
    assert len(writeup_calls) == 1
    assert writeup_calls[0]["record"]["solve_status"] == FLAG_FOUND
    assert writeup_calls[0]["record"]["confirmed"] is True
    assert writeup_calls[0]["path"].exists()
    assert fake_platform.closed is True


def test_run_single_uses_platform_factory_for_platform_client(monkeypatch, tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    (challenge_dir / "metadata.yml").write_text(
        "\n".join(
            [
                "name: demo",
                "category: misc",
                "description: just a test",
                "value: 100",
                "solves: 0",
                "platform: lingxu-event-ctf",
                "platform_challenge_id: 42",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakePlatformClient:
        closed = False

        async def close(self) -> None:
            self.closed = True

    fake_platform = FakePlatformClient()

    def fake_create_platform_client(settings: Settings) -> FakePlatformClient:
        captured["factory_settings"] = settings
        return fake_platform

    async def fake_cleanup_orphan_containers() -> None:
        return None

    def fake_configure_semaphore(limit: int) -> None:
        captured["semaphore_limit"] = limit

    class FakeSwarm:
        def __init__(self, **kwargs) -> None:
            captured["swarm_kwargs"] = kwargs

        async def run(self):
            return None

    import backend.agents.swarm as swarm_module
    import backend.sandbox as sandbox_module

    monkeypatch.setattr(cli, "create_platform_client", fake_create_platform_client)
    monkeypatch.setattr(sandbox_module, "cleanup_orphan_containers", fake_cleanup_orphan_containers)
    monkeypatch.setattr(sandbox_module, "configure_semaphore", fake_configure_semaphore)
    monkeypatch.setattr(swarm_module, "ChallengeSwarm", FakeSwarm)

    settings = Settings(
        _env_file=None,
        platform="lingxu-event-ctf",
        platform_url="https://lx.example.com",
        lingxu_event_id=42,
        lingxu_cookie="sessionid=sid123; csrftoken=csrf456",
    )

    asyncio.run(
        cli._run_single(
            settings=settings,
            challenge_dir=str(challenge_dir),
            model_specs=["codex/gpt-5.4"],
            no_submit=True,
            max_challenges=2,
        )
    )

    assert captured["factory_settings"] is settings
    assert captured["semaphore_limit"] == 2
    assert captured["swarm_kwargs"]["ctfd"] is fake_platform
    assert fake_platform.closed is True


def test_msg_accepts_english_port_and_host(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"queued": "收到"}).encode("utf-8")

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = request.data
        captured["headers"] = dict(request.header_items())
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = CliRunner().invoke(
        cli.msg,
        [
            "--port",
            "9500",
            "--host",
            "127.0.0.2",
            "测试消息",
        ],
    )

    assert result.exit_code == 0
    assert captured["url"] == "http://127.0.0.2:9500/msg"
    assert captured["timeout"] == 5
    assert captured["headers"]["Content-type"] == "application/json"
    assert json.loads(captured["body"]) == {"message": "测试消息"}
