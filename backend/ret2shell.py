"""Ret2Shell platform adapter with the interface used by the solver swarm."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

from backend.ctfd import CTFdClient, SubmitResult

logger = logging.getLogger(__name__)


@dataclass
class Ret2ShellClient(CTFdClient):
    """Compatibility adapter for Ret2Shell's ``/api/game`` endpoints."""

    game_id: int = 0
    auto_start_instances: bool = True
    _resolved_game_id: int = field(default=0, init=False, repr=False)
    _challenge_ids: dict[str, int] = field(default_factory=dict, init=False)
    _challenge_names: dict[int, str] = field(default_factory=dict, init=False)

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                follow_redirects=False,
                verify=False,
                timeout=30.0,
                headers={"User-Agent": "ctf-agent/ret2shell"},
            )
        return self._client

    def _base_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        client = await self._ensure_client()
        response = await client.request(
            method,
            f"/api/{path.lstrip('/')}",
            params=params,
            json=json_body,
            headers=self._base_headers(),
        )
        rotated = response.headers.get("Set-Token")
        if rotated:
            self.token = rotated
        if response.status_code == 401:
            raise RuntimeError(
                "Ret2Shell authentication failed (401). Set RET2SHELL_TOKEN to a current "
                "Bearer session token from this platform."
            )
        response.raise_for_status()
        return response

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._request(method, path, params=params, json_body=json_body)
        return response.json() if response.content else None

    async def _game(self) -> int:
        if self._resolved_game_id:
            return self._resolved_game_id
        if self.game_id:
            self._resolved_game_id = self.game_id
            return self._resolved_game_id

        payload = await self._json("GET", "game", params={"page": "1", "page_size": "100"})
        games = payload[0] if isinstance(payload, list) and len(payload) == 2 else payload
        now = int(time.time())
        live = [
            game
            for game in games
            if game.get("host_type") == 1
            and int(game.get("start_at", 0)) <= now <= int(game.get("end_at", 0))
        ]
        if len(live) != 1:
            choices = ", ".join(f"{g.get('id')}:{g.get('name')}" for g in live) or "none"
            raise RuntimeError(
                "Ret2Shell game is ambiguous. Set RET2SHELL_GAME_ID or pass --game-id. "
                f"Live games: {choices}"
            )
        self._resolved_game_id = int(live[0]["id"])
        logger.info("Auto-selected Ret2Shell game %s (%s)", live[0]["name"], self._resolved_game_id)
        return self._resolved_game_id

    @staticmethod
    def _unwrap_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list) and len(payload) == 2 and isinstance(payload[0], list):
            return payload[0]
        return payload if isinstance(payload, list) else []

    def _normalize_challenge(self, item: dict[str, Any]) -> dict[str, Any]:
        tags = item.get("tag") or []
        primary = next((tag.get("name", "") for tag in tags if tag.get("primary")), "")
        category = primary or item.get("bucket") or "Misc"
        challenge_id = int(item["id"])
        name = str(item.get("name") or f"challenge-{challenge_id}")
        self._challenge_ids[name] = challenge_id
        self._challenge_names[challenge_id] = name
        return {
            "id": challenge_id,
            "name": name,
            "category": category,
            "value": int(item.get("score") or 0),
            "solves": int(item.get("solves") or 0),
            "description": item.get("content") or "",
            "connection_info": item.get("connection_info") or "",
            "tags": [tag.get("name", "") for tag in tags if tag.get("name")],
            "_ret2shell": item,
        }

    async def validate_auth(self) -> dict[str, Any]:
        return await self._json("GET", "account/profile")

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        game_id = await self._game()
        payload = await self._json("GET", f"game/{game_id}/challenge")
        return [self._normalize_challenge(item) for item in self._unwrap_list(payload)]

    async def fetch_all_challenges(self) -> list[dict[str, Any]]:
        game_id = await self._game()
        stubs = await self.fetch_challenge_stubs()
        details: list[dict[str, Any]] = []
        for stub in stubs:
            payload = await self._json("GET", f"game/{game_id}/challenge/{stub['id']}")
            details.append(self._normalize_challenge(payload))
        return details

    async def fetch_solved_names(self) -> set[str]:
        game_id = await self._game()
        if not self._challenge_names:
            await self.fetch_challenge_stubs()
        submissions = await self._json("GET", f"game/{game_id}/solve")
        return {
            self._challenge_names[int(item["challenge_id"])]
            for item in submissions
            if item.get("solved") is True and int(item["challenge_id"]) in self._challenge_names
        }

    async def get_challenge_id(self, name: str) -> int:
        if name not in self._challenge_ids:
            await self.fetch_challenge_stubs()
        if name not in self._challenge_ids:
            raise RuntimeError(f'Ret2Shell challenge "{name}" not found')
        return self._challenge_ids[name]

    async def get_connection_info(self, challenge_name: str) -> str:
        """Return (and, when configured, start) the named challenge instance."""
        challenge_id = await self.get_challenge_id(challenge_name)
        return await self._connection_info(challenge_id)

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        game_id = await self._game()
        challenge_id = await self.get_challenge_id(challenge_name)
        path = f"game/{game_id}/challenge/{challenge_id}/submit"
        submission = await self._json("POST", path, json_body={"content": flag})
        for _ in range(10):
            if submission.get("solved") is not None:
                break
            await asyncio.sleep(1)
            submission = await self._json(
                "GET", path, params={"id": str(submission["id"])}
            )
        solved = submission.get("solved")
        message = str(submission.get("result") or "")
        if solved is True:
            return SubmitResult("correct", message, f'CORRECT — "{flag}" accepted. {message}'.strip())
        if solved is False:
            return SubmitResult("incorrect", message, f'INCORRECT — "{flag}" rejected. {message}'.strip())
        return SubmitResult("unknown", message, f"Submission {submission.get('id')} is still pending")

    async def _existing_instance(self, challenge_id: int) -> dict[str, Any] | None:
        game_id = await self._game()
        instances = await self._json("GET", f"game/{game_id}/instance")
        return next(
            (x for x in instances if int(x.get("challenge_id", -1)) == challenge_id),
            None,
        )

    def _format_connection_info(self, instance: dict[str, Any] | None) -> str:
        if not instance or str(instance.get("state", "")).lower() != "running":
            return ""
        mapped = instance.get("exposed_ports") or []
        addresses = [str(port.get("address") or "").strip() for port in mapped]
        addresses = [address for address in addresses if address]
        if addresses:
            return "\n".join(addresses)

        traffic = str(instance.get("traffic") or "").strip()
        ports = instance.get("ports") or []
        if not traffic or not ports:
            return ""
        parsed = urlsplit(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return "\n".join(
            f"{scheme}://{parsed.netloc}/api/traffic/{traffic}?port={int(port)}"
            for port in ports
        )

    async def _connection_info(self, challenge_id: int) -> str:
        instance = await self._existing_instance(challenge_id)
        connection_info = self._format_connection_info(instance)
        if connection_info:
            return connection_info
        if instance and not self.auto_start_instances:
            return ""
        if not self.auto_start_instances:
            return ""
        game_id = await self._game()
        path = f"game/{game_id}/challenge/{challenge_id}/instance"
        if instance is None:
            try:
                await self._json("POST", path, json_body={})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (400, 404, 412):
                    return ""
                if exc.response.status_code != 409:
                    raise
        for _ in range(15):
            await asyncio.sleep(2)
            connection_info = self._format_connection_info(
                await self._existing_instance(challenge_id)
            )
            if connection_info:
                return connection_info
        return ""

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        game_id = await self._game()
        challenge_id = int(challenge["id"])
        name = str(challenge.get("name") or f"challenge-{challenge_id}")
        slug = re.sub(r'[<>:"/\\|?*.\x00-\x1f]', "", name.lower().strip())
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug).strip("-") or "challenge"
        challenge_dir = Path(output_dir) / slug
        dist_dir = challenge_dir / "distfiles"
        challenge_dir.mkdir(parents=True, exist_ok=True)

        files = await self._json("GET", f"game/{game_id}/challenge/{challenge_id}/file")
        for item in files:
            filename = Path(str(item.get("file") or "file")).name
            dist_dir.mkdir(exist_ok=True)
            destination = dist_dir / filename
            if destination.exists():
                continue
            response = await self._request(
                "GET",
                f"game/{game_id}/challenge/{challenge_id}/file",
                params={"folder": str(item.get("folder") or ""), "file": str(item.get("file") or "")},
            )
            destination.write_bytes(response.content)
            logger.info("Downloaded Ret2Shell attachment: %s (%d bytes)", filename, len(response.content))

        connection_info = challenge.get("connection_info") or ""
        if not connection_info:
            connection_info = await self._connection_info(challenge_id)

        tags = challenge.get("tags") or []
        metadata = {
            "name": name,
            "category": challenge.get("category") or "Misc",
            "description": str(challenge.get("description") or "").strip(),
            "value": int(challenge.get("value") or 0),
            "connection_info": connection_info,
            "tags": tags,
            "solves": int(challenge.get("solves") or 0),
        }
        (challenge_dir / "metadata.yml").write_text(
            yaml.dump(metadata, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return str(challenge_dir)


def create_competition_client(settings) -> CTFdClient:
    """Construct the configured competition-platform client."""
    platform = str(getattr(settings, "ctf_platform", "ctfd")).lower()
    if platform == "ret2shell":
        token = getattr(settings, "ret2shell_token", "") or settings.ctfd_token
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return Ret2ShellClient(
            base_url=settings.ctfd_url,
            token=token,
            game_id=int(getattr(settings, "ret2shell_game_id", 0)),
            auto_start_instances=bool(getattr(settings, "ret2shell_auto_start_instances", True)),
        )
    if platform == "ctfplus":
        from backend.ctfplus import CTFPlusClient

        token = getattr(settings, "ctfplus_token", "") or settings.ctfd_token
        return CTFPlusClient(
            base_url=settings.ctfd_url,
            token=token,
            competition_id=str(getattr(settings, "ctfplus_competition_id", "")),
            problem_bank_id=str(getattr(settings, "ctfplus_problem_bank_id", "")),
        )
    return CTFdClient(
        base_url=settings.ctfd_url,
        token=settings.ctfd_token,
        username=settings.ctfd_user,
        password=settings.ctfd_pass,
    )
