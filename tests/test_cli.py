from __future__ import annotations

import asyncio
import json
import tomllib
import urllib.request
from pathlib import Path

import yaml
from click.testing import CliRunner

from backend import cli
from backend.config import Settings


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
    assert "--lingxu-cookie-file" in result.output
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


def test_main_rejects_lingxu_without_cookie() -> None:
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
