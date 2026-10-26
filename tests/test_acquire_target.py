"""回归测试：CTF²Client.acquire_target 的 409/异步销毁/超时行为。

复现 1024 快闪赛「有脑无手」根因：原 ensure_environment 在 409（环境槽位争用）
或异步销毁未就绪时静默返回 ''，导致 solver 带空地址盲打。acquire_target 改为
「先释放旧环境 + 指数退避轮询直到就绪」，超时返回 '' 由调用方 fail-closed。
"""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.ctf2_client import CTF2Client


class FakeResp:
    def __init__(self, status_code: int, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}

    def json(self) -> dict:
        return {"data": self._data}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """脚本化 CTF² 环境接口：handler(method, path) -> FakeResp。"""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, str]] = []
        self.deletes = 0

    async def get(self, path, headers=None):
        self.calls.append(("GET", path))
        return self.handler("GET", path)

    async def post(self, path, json=None, headers=None):
        self.calls.append(("POST", path))
        return self.handler("POST", path)

    async def delete(self, path, headers=None):
        self.deletes += 1
        self.calls.append(("DELETE", path))
        return self.handler("DELETE", path)


def _make_client(handler) -> CTF2Client:
    c = CTF2Client(token="test-token")
    c._comp_ready = True  # 跳过 agent/me 自动发现
    c._client = FakeClient(handler)
    return c


def _env_get_ready(url: str = "http://abc123.http-ctf2.dasctf.com:80"):
    return FakeResp(200, {"access_ready": True, "status": "running", "access_url": url})


@pytest.mark.asyncio
async def test_immediate_ready_skips_start():
    """已就绪时直接返回 URL，不应调用 POST /start/（避免无谓启动）。"""

    def handler(method, path):
        if method == "GET" and "/environment/" in path and not path.endswith("/start/"):
            return _env_get_ready()
        return FakeResp(404)

    c = _make_client(handler)
    url = await c.acquire_target("cid", max_wait_s=5)
    assert url == "http://abc123.http-ctf2.dasctf.com:80"
    start_calls = [p for (m, p) in c._client.calls if m == "POST" and p.endswith("/start/")]
    assert start_calls == [], "就绪时不应触发 start"


@pytest.mark.asyncio
async def test_409_slot_conflict_then_ready():
    """409 槽位争用（旧环境未释放）：轮询后就绪应返回 URL。"""
    state = {"gets": 0}

    def handler(method, path):
        if method == "DELETE":
            return FakeResp(404)  # 无旧环境
        if method == "GET" and "/environment/" in path and not path.endswith("/start/"):
            state["gets"] += 1
            if state["gets"] == 1:
                return FakeResp(200, {"status": "creating"})  # 未就绪
            return _env_get_ready()
        if method == "POST" and path.endswith("/start/"):
            return FakeResp(409)  # 槽位被占
        return FakeResp(404)

    c = _make_client(handler)
    url = await c.acquire_target("cid", max_wait_s=30)
    assert url == "http://abc123.http-ctf2.dasctf.com:80"


@pytest.mark.asyncio
async def test_preclose_called_on_existing_env():
    """存在旧环境时，acquire_target 应先 DELETE 释放槽位。"""

    def handler(method, path):
        if method == "DELETE":
            return FakeResp(200)  # 存在旧环境，已删除
        if method == "GET" and "/environment/" in path and not path.endswith("/start/"):
            return _env_get_ready()
        return FakeResp(404)

    c = _make_client(handler)
    await c.acquire_target("cid", max_wait_s=5)
    assert c._client.deletes == 1, "应先 DELETE 旧环境释放槽位"


@pytest.mark.asyncio
async def test_timeout_returns_empty():
    """始终未就绪（含 409）：超时返回 ''，交由调用方 fail-closed。"""

    def handler(method, path):
        if method == "GET" and "/environment/" in path and not path.endswith("/start/"):
            return FakeResp(200, {"status": "creating"})  # 永不就绪
        if method == "POST" and path.endswith("/start/"):
            return FakeResp(409)
        return FakeResp(404)

    c = _make_client(handler)
    url = await c.acquire_target("cid", max_wait_s=4)
    assert url == "", "超时必须返回空串，绝不假装成功"
