"""Minimal in-process mock of the CTF² (dasctf) Agent API.

Used by tests/test_ctf2_mock_integration.py so the `acquire_target` + `submit`
path can be exercised end-to-end over *real* HTTP without the live platform
(which is behind a 40403 gate during the off-season).

Implements the subset of routes the client actually hits:
  GET  /api/open/v1/user/agent/me/
  GET  /api/open/v1/user/agent/competitions/{comp}/?limit=1
  GET  .../stages/{stage}/challenges/?limit=100
  GET  .../stages/{stage}/challenges/{id}/
  POST .../stages/{stage}/challenges/{id}/environment/start/
  GET  .../stages/{stage}/challenges/{id}/environment/
  DELETE .../stages/{stage}/challenges/{id}/environment/
  POST .../stages/{stage}/challenges/{id}/submit/
  GET  .../stages/{stage}/challenges/{id}/attachments/{fid}/url/?limit=1
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockCTF2Server:
    def __init__(
        self,
        env_config: dict[str, dict] | None = None,
        challenges: dict[str, dict] | None = None,
    ):
        self.env_config = dict(env_config or {})
        self.challenges = dict(challenges or self._default_challenges())
        self.requests: list[tuple[str, str]] = []
        self.responses: list[tuple[str, str, int]] = []
        self._lock = threading.Lock()
        self._env_ready_after: dict[str, int] = {
            cid: int(cfg.get("ready_after", 0)) for cid, cfg in self.env_config.items()
        }
        self._start_calls: dict[str, int] = {}
        self.port = 0
        self.base_url = ""
        self._httpd = None
        self._thread = None

    @staticmethod
    def _default_challenges() -> dict[str, dict]:
        return {
            "web1": {
                "id": "web1", "name": "第1024行", "category": "web",
                "points": 148, "difficulty": "hard", "has_attachments": False,
                "has_container": True, "is_solved": False, "solve_count": 0,
                "submission_type": "flag",
            },
            "pwn1": {
                "id": "pwn1", "name": "pwn1", "category": "pwn",
                "points": 200, "has_container": False, "is_solved": False,
                "solve_count": 0,
            },
        }

    def _access_url(self, cid: str) -> str:
        return f"http://127.0.0.1:{self.port}/target/{cid}"

    def start(self) -> None:
        state = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _record(self, method: str, code: int = 200):
                with state._lock:
                    state.requests.append((method, self.path))
                    state.responses.append((method, self.path, code))

            def _send(self, code: int, payload: dict):
                self._record(self.command, code)
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                # 显式关闭连接，避免 httpx 在 Windows 上复用已关闭的连接
                # （http.server 默认 HTTP/1.0，配合 httpx keep-alive 偶发
                #  WinError 10054 / httpx.ReadError，导致集成测试随机失败）
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._route()

            def do_POST(self):
                self._route()

            def do_DELETE(self):
                self._route()

            def _route(self):
                path = self.path.split("?")[0]
                if path.endswith("/"):
                    path = path[:-1]
                BASE = "/api/open/v1/user/agent"
                if path == f"{BASE}/me":
                    return self._send(200, {"data": {"active_competitions": [
                        {"id": "comp1", "status": "running", "agent_stage_count": 1}]}})
                if path == f"{BASE}/competitions/comp1":
                    return self._send(200, {"data": {"stages": [
                        {"id": "stage1", "audience": "agent", "status": "running"}]}})
                P = f"{BASE}/competitions/comp1/stages/stage1/challenges"
                if path == P:
                    return self._send(200, {"data": {"items": list(state.challenges.values())}})
                if not path.startswith(P + "/"):
                    return self._send(404, {"detail": "no route"})
                rest = path[len(P) + 1:]
                parts = rest.split("/")
                cid = parts[0]
                if cid not in state.challenges:
                    return self._send(404, {"detail": "no such challenge"})
                if len(parts) == 1:
                    return self._send(200, {"data": state.challenges[cid]})
                if parts[1] == "environment":
                    return state._env_route(self, cid)
                if parts[1] == "submit" and len(parts) == 2:
                    return self._send(200, {"data": {
                        "accepted": True, "is_solved": True,
                        "attempts_left": 0, "submission_id": "sub_1"}})
                if parts[1] == "attachments" and len(parts) >= 4 and parts[-1] == "url":
                    return self._send(200, {"data": {
                        "url": f"http://127.0.0.1:{state.port}/att/{cid}",
                        "file_name": "att.zip"}})
                return self._send(404, {"detail": "no route"})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._wait_until_ready()

    def _wait_until_ready(self, timeout: float = 5.0) -> None:
        """Block until the server actually accepts and serves a request.

        Without this, a client may connect in the race window between
        thread.start() and serve_forever() picking up the backlog, which
        surfaces as an intermittent httpx.ReadError under the full suite.
        """
        url = self.base_url + "/api/open/v1/user/agent/me"
        deadline = time.time() + timeout
        last: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=0.5) as r:
                    if r.status == 200:
                        return
            except Exception as e:  # noqa: BLE001 - any failure means "not ready yet"
                last = e
                time.sleep(0.02)
        raise RuntimeError(f"mock CTF² server not ready on {self.base_url}: {last}")

    def _env_route(self, handler, cid: str) -> None:
        cfg = self.env_config.get(cid, {})
        method = handler.command
        if method == "DELETE":
            return handler._send(200, {"data": {"status": "deleted"}})
        if method == "POST":
            n = self._start_calls.get(cid, 0)
            self._start_calls[cid] = n + 1
            if cfg.get("start_first_status") == 409 and n == 0:
                return handler._send(409, {"detail": "environment already exists"})
            return handler._send(200, {"data": {"status": "starting"}})
        # GET
        ra = self._env_ready_after.get(cid, cfg.get("ready_after", 0))
        if ra > 0:
            self._env_ready_after[cid] = ra - 1
            return handler._send(200, {"data": {"access_ready": False, "status": "starting"}})
        return handler._send(200, {"data": {
            "access_ready": True, "status": "running",
            "access_url": self._access_url(cid)}})

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
