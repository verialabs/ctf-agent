from pathlib import Path

import httpx
import pytest

from backend.ret2shell import Ret2ShellClient, create_competition_client


def _client(handler) -> Ret2ShellClient:
    client = Ret2ShellClient(base_url="https://ctf.example", token="valid", game_id=37)
    client._client = httpx.AsyncClient(
        base_url="https://ctf.example", transport=httpx.MockTransport(handler)
    )
    return client


def test_factory_accepts_token_copied_with_bearer_prefix() -> None:
    settings = type("Settings", (), {
        "ctf_platform": "ret2shell",
        "ret2shell_token": "Bearer abc.def.ghi",
        "ctfd_token": "",
        "ctfd_url": "https://ctf.example",
        "ret2shell_game_id": 37,
        "ret2shell_auto_start_instances": True,
    })()
    client = create_competition_client(settings)
    assert isinstance(client, Ret2ShellClient)
    assert client.token == "abc.def.ghi"


@pytest.mark.asyncio
async def test_fetches_and_normalizes_ret2shell_challenges() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer valid"
        if request.url.path == "/api/game/37/challenge":
            return httpx.Response(200, json=[[
                {
                    "id": 9,
                    "name": "Cipher",
                    "content": "desc",
                    "score": 100,
                    "tag": [{"name": "Crypto", "primary": True}],
                    "score_rule": {"initial": 100, "minimum": 10, "decay": 5},
                    "bucket": None,
                }
            ], 1])
        if request.url.path == "/api/game/37/solve":
            return httpx.Response(200, json=[{"challenge_id": 9, "solved": True}])
        raise AssertionError(request.url)

    client = _client(handler)
    try:
        challenges = await client.fetch_challenge_stubs()
        solved = await client.fetch_solved_names()
    finally:
        await client.close()

    assert challenges[0]["category"] == "Crypto"
    assert challenges[0]["value"] == 100
    assert solved == {"Cipher"}


@pytest.mark.asyncio
async def test_submits_and_polls_ret2shell_flag() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/api/game/37/challenge":
            return httpx.Response(200, json=[[
                {"id": 9, "name": "Cipher", "score": 100, "tag": [], "score_rule": {}, "bucket": None}
            ], 1])
        if request.url.path.endswith("/submit") and request.method == "POST":
            assert request.read() == b'{"content":"flag{ok}"}'
            return httpx.Response(200, json={"id": 55, "challenge_id": 9, "solved": None})
        if request.url.path.endswith("/submit") and request.method == "GET":
            calls += 1
            return httpx.Response(
                200, json={"id": 55, "challenge_id": 9, "solved": True, "result": "accepted"}
            )
        raise AssertionError(request.url)

    client = _client(handler)
    try:
        result = await client.submit_flag("Cipher", "flag{ok}")
    finally:
        await client.close()
    assert calls == 1
    assert result.status == "correct"


@pytest.mark.asyncio
async def test_pulls_attachment_and_writes_metadata(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/file") and not request.url.query:
            return httpx.Response(200, json=[{"folder": "public", "file": "solve.py"}])
        if request.url.path.endswith("/file"):
            return httpx.Response(200, content=b"print('solve')\n")
        if request.url.path == "/api/game/37/instance":
            return httpx.Response(200, json=[{
                "challenge_id": 9,
                "state": "Running",
                "exposed_ports": [{"name": "tcp", "address": "nc host 31337"}],
            }])
        raise AssertionError(request.url)

    client = _client(handler)
    challenge = {"id": 9, "name": "Cipher", "category": "Crypto", "description": "desc"}
    try:
        output = Path(await client.pull_challenge(challenge, str(tmp_path)))
    finally:
        await client.close()
    assert (output / "distfiles" / "solve.py").read_text() == "print('solve')\n"
    assert "nc host 31337" in (output / "metadata.yml").read_text()


@pytest.mark.asyncio
async def test_get_connection_info_by_challenge_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/game/37/challenge":
            return httpx.Response(200, json=[[
                {"id": 9, "name": "only_open", "score": 150, "tag": [], "bucket": None}
            ], 1])
        if request.url.path == "/api/game/37/instance":
            return httpx.Response(200, json=[{
                "challenge_id": 9,
                "state": "Running",
                "exposed_ports": [{"address": "nc example.test 31337"}],
            }])
        raise AssertionError(request.url)

    client = _client(handler)
    try:
        connection_info = await client.get_connection_info("only_open")
    finally:
        await client.close()

    assert connection_info == "nc example.test 31337"


def test_formats_wsrx_fallback_when_direct_ports_are_unavailable() -> None:
    client = Ret2ShellClient(base_url="https://ctf.example", token="valid", game_id=37)

    connection_info = client._format_connection_info({
        "state": "Running",
        "traffic": "opaque-traffic-id",
        "ports": [9999],
        "exposed_ports": None,
    })

    assert connection_info == "wss://ctf.example/api/traffic/opaque-traffic-id?port=9999"
