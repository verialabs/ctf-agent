"""
Solver 侧「双通道注入」回归测试（P1 质量底座）。

环境约束：本环境未安装 aiodocker / pydantic_ai / genai_prices，
无法 import 完整 solver。因此直接对注入的「落地点」DockerSandbox 做
真·行为测试（用 fake docker 记录容器创建时的 config），并对三个 solver
源码做契约断言（确保它们确实把 CTF_TARGET 透传给沙箱 env）。

覆盖 P0 修复的「双通道兜底」：即使 prompt 链路丢失地址，agent 也能从
容器环境变量 CTF_TARGET 读到靶机，从而不再出现「找到漏洞却无靶机可打」。
"""

import asyncio
import re
import sys
import types
from pathlib import Path

import pytest


# ---- 必须在 import backend.sandbox 之前注入 fake aiodocker ----
_CAPTURED: list = []


class _FakeContainer:
    id = "fakecontainer0000"

    async def start(self):
        return None

    async def show(self):
        return {"Id": self.id}


class _FakeContainers:
    def __init__(self, sink):
        self._sink = sink

    async def create(self, config):
        self._sink.append(config)
        return _FakeContainer()


class _FakeDocker:
    def __init__(self, sink):
        self.containers = _FakeContainers(sink)

    async def close(self):
        return None


def _install_fake_aiodocker():
    mod = types.ModuleType("aiodocker")
    mod.Docker = lambda: _FakeDocker(_CAPTURED)
    ex = types.ModuleType("aiodocker.exceptions")
    ex.DockerError = Exception
    mod.exceptions = ex
    sys.modules["aiodocker"] = mod
    sys.modules["aiodocker.exceptions"] = ex


_install_fake_aiodocker()

from backend.sandbox import DockerSandbox  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_capture():
    _CAPTURED.clear()
    yield
    _CAPTURED.clear()


def _run(coro):
    return asyncio.run(coro)


def test_sandbox_forwards_ctf_target_to_container_env():
    sb = DockerSandbox(
        image="ctf-sandbox",
        challenge_dir="/tmp/ctf-agent-nonexistent-challenge",
        env={"CTF_TARGET": "http://dec95825af6d76a1add7ff19.http-ctf2.dasctf.com:80"},
    )
    _run(sb.start())
    assert len(_CAPTURED) == 1, "containers.create 应恰好被调用一次"
    config = _CAPTURED[0]
    assert "Env" in config, "双通道注入失败：容器 config 缺少 Env 键"
    assert (
        "CTF_TARGET=http://dec95825af6d76a1add7ff19.http-ctf2.dasctf.com:80"
        in config["Env"]
    ), f"CTF_TARGET 未正确注入容器 Env：{config.get('Env')}"


def test_sandbox_empty_env_omits_container_env():
    sb = DockerSandbox(
        image="ctf-sandbox",
        challenge_dir="/tmp/ctf-agent-nonexistent-challenge",
        env={},
    )
    _run(sb.start())
    assert len(_CAPTURED) == 1
    config = _CAPTURED[0]
    assert "Env" not in config, "空 env 不应向容器注入 Env 键"


def test_solvers_inject_ctf_target_from_meta():
    """契约断言：三个 solver 必须把 meta.connection_info 透传为 CTF_TARGET。"""
    agents = Path(__file__).resolve().parent.parent / "backend" / "agents"
    # 允许空格差异，但结构必须一致
    pattern = re.compile(r'env=\{\s*"CTF_TARGET":\s*self\.meta\.connection_info\s*\}')
    for name in ("solver.py", "claude_solver.py", "codex_solver.py"):
        src = (agents / name).read_text(encoding="utf-8")
        assert pattern.search(src), f"{name} 未注入 CTF_TARGET 双通道（pattern 缺失）"
