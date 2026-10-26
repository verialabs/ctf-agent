"""End-to-end integration tests for the CTF² client over real HTTP.

These run fully offline against MockCTF2Server (tests/mock_ctf2_server.py),
so the `acquire_target` / `submit_flag` path can be regression-tested during
the off-season when the live platform returns 40403 (no open track).

This is the "P3" deliverable: a no-platform regression harness that proves the
"有脑无手" SPOF fix (PR #9 / acquire_target) holds under real HTTP — including
the 409 slot-contention case that broke the 1024 flash-CTF.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# make repo root + tests/ importable regardless of pytest invocation dir
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tests"))

from backend.ctf2_client import CTF2Client  # noqa: E402
from backend.prompts import ChallengeMeta, build_prompt  # noqa: E402
from mock_ctf2_server import MockCTF2Server  # noqa: E402


def _client(base_url: str, token: str = "test") -> CTF2Client:
    # competition_id/stage_id set explicitly -> skips the /me auto-discovery call
    return CTF2Client(
        base_url=base_url, token=token,
        competition_id="comp1", stage_id="stage1",
    )


@pytest.fixture
def server():
    srv = MockCTF2Server(env_config={"web1": {"ready_after": 0, "start_first_status": 200}})
    srv.start()
    yield srv
    srv.stop()


@pytest.mark.asyncio
async def test_acquire_target_immediate_ready(server):
    c = _client(server.base_url)
    url = await c.acquire_target("web1", max_wait_s=10)
    assert url.startswith("http")
    # pre-close DELETE must have been issued (releases the single agent slot)
    assert any(m == "DELETE" and "environment" in p for m, p in server.requests)
    await c.close()


@pytest.mark.asyncio
async def test_acquire_target_slot_contention_409():
    srv = MockCTF2Server(env_config={"web1": {"ready_after": 2, "start_first_status": 409}})
    srv.start()
    c = None
    try:
        c = _client(srv.base_url)
        url = await c.acquire_target("web1", max_wait_s=20)
        assert url.startswith("http")
        # the transient 409 on the first start must be tolerated (P0 fix)
        assert any(code == 409 for _, _, code in srv.responses)
    finally:
        srv.stop()
        if c:
            await c.close()


@pytest.mark.asyncio
async def test_acquire_target_timeout_returns_empty():
    srv = MockCTF2Server(env_config={"web1": {"ready_after": 9999, "start_first_status": 200}})
    srv.start()
    c = None
    try:
        c = _client(srv.base_url)
        url = await c.acquire_target("web1", max_wait_s=3)
        # fail-closed: caller must NOT start solver with an empty target
        assert url == ""
    finally:
        srv.stop()
        if c:
            await c.close()


@pytest.mark.asyncio
async def test_submit_flag_accepted(server):
    c = _client(server.base_url)
    res = await c.submit_flag("第1024行", "DASCTF{integration-test}")
    assert res.status in ("correct", "already_solved")
    await c.close()


def test_build_prompt_includes_ctf_target():
    m = ChallengeMeta(
        name="t", category="web", description="d", value=100,
        connection_info="http://x:80", tags=[], solves=0,
    )
    p = build_prompt(m, [], "unknown", True)
    assert "FIRST ACTION REQUIRED" in p
    assert "CTF_TARGET" in p


def test_build_prompt_no_conn_no_first_action():
    m = ChallengeMeta(
        name="t", category="pwn", description="d", value=100,
        connection_info="", tags=[], solves=0,
    )
    p = build_prompt(m, [], "unknown", True)
    assert "FIRST ACTION REQUIRED" not in p
    assert "CTF_TARGET" not in p
