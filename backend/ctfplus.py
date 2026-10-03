"""CTF+ platform adapter for ctfplus.cn competitions."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from backend.ctfd import CTFdClient, SubmitResult

logger = logging.getLogger(__name__)


@dataclass
class CTFPlusClient(CTFdClient):
    """Compatibility adapter for CTF+ JSON endpoints.

    CTF+ uses ``/api/<module>/<action>`` JSON endpoints and a browser token stored
    as ``mario-token`` in localStorage. The token is sent verbatim in the
    ``Authorization`` header, matching the site's axios interceptor.
    """

    competition_id: str = ""
    problem_bank_id: str = ""
    _challenge_ids: dict[str, str] = field(default_factory=dict, init=False)
    _challenge_names: dict[str, str] = field(default_factory=dict, init=False)
    _challenge_details: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                follow_redirects=False,
                verify=False,
                timeout=30.0,
                headers={
                    "User-Agent": "Mozilla/5.0 ctf-agent/ctfplus",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Origin": self.base_url.rstrip("/"),
                    "Referer": f"{self.base_url.rstrip('/')}/competition/hall?detailCompetitionId={self.competition_id}",
                },
            )
        return self._client

    def _base_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = self.token
        if self.problem_bank_id:
            headers["X-Problem-Bank-Id"] = self.problem_bank_id
        return headers

    async def _api(self, path: str, body: dict[str, Any] | None = None) -> Any:
        client = await self._ensure_client()
        response = await client.post(
            f"/api/{path.lstrip('/')}",
            json=body or {},
            headers=self._base_headers(),
        )
        if response.status_code == 401:
            raise RuntimeError("CTF+ authentication failed (401). Set CTFPLUS_TOKEN to the current mario-token.")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("code", 200) != 200:
            raise RuntimeError(f"CTF+ API {path} failed: {payload.get('msg') or payload}")
        return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload

    async def validate_auth(self) -> dict[str, Any]:
        return await self._api("user/getMe", {})

    async def _competition_detail(self) -> dict[str, Any]:
        if not self.competition_id:
            raise RuntimeError("CTF+ competition ID is required. Pass --competition-id or set CTFPLUS_COMPETITION_ID.")
        return await self._api("competition/getCompetitionDetail", {"competitionId": self.competition_id})

    async def _self_team_id(self) -> str:
        try:
            data = await self._api("competition/getCompetitionSelfTeam", {"competitionId": self.competition_id})
        except Exception:
            return ""
        if isinstance(data, dict):
            team = data.get("team") if isinstance(data.get("team"), dict) else data
            return str(team.get("id") or team.get("teamId") or "")
        return ""

    async def _problem_list_payload(self) -> Any:
        bodies = [
            {"competitionId": self.competition_id, "page": {"page": 1, "size": 500}},
            {"competitionId": self.competition_id, "page": 1, "size": 500},
            {"competitionId": self.competition_id, "page": 1, "pageSize": 500},
            {"page": 1, "pageSize": 500},
            {"page": {"page": 1, "size": 500}},
        ]
        last_error: Exception | None = None
        for body in bodies:
            try:
                return await self._api("problem/getProblemList", body)
            except Exception as exc:
                last_error = exc
        detail = await self._competition_detail()
        if isinstance(detail, dict) and detail.get("canEnter") is False:
            logger.info("CTF+ competition is not enterable yet: %s", detail.get("entryStatus"))
            return []
        if last_error:
            raise last_error
        return []

    @staticmethod
    def _best_list(payload: Any) -> list[dict[str, Any]]:
        candidates: list[list[dict[str, Any]]] = []

        def walk(value: Any) -> None:
            if isinstance(value, list) and all(isinstance(x, dict) for x in value):
                if any(("problem" in x or "problemBase" in x or "name" in x or "title" in x) for x in value):
                    candidates.append(value)
            elif isinstance(value, dict):
                for child in value.values():
                    walk(child)

        walk(payload)
        return max(candidates, key=len, default=[])

    @staticmethod
    def _doc_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n".join(filter(None, (CTFPlusClient._doc_text(x) for x in value))).strip()
        if isinstance(value, dict):
            if "text" in value:
                return str(value.get("text") or "")
            return CTFPlusClient._doc_text(value.get("content"))
        return str(value).strip()

    def _normalize_challenge(self, item: dict[str, Any]) -> dict[str, Any]:
        raw_problem = item.get("problem") if isinstance(item.get("problem"), dict) else None
        base = item.get("problemBase") if isinstance(item.get("problemBase"), dict) else None
        specific = item.get("specificProblem") if isinstance(item.get("specificProblem"), dict) else None
        source = base or raw_problem or item
        specific_id = specific.get("id") if specific else ""
        problem_id = str(
            item.get("problemId")
            or item.get("id")
            or source.get("id")
            or source.get("problemId")
            or specific_id
            or ""
        )
        name = str(source.get("name") or source.get("title") or item.get("name") or f"problem-{problem_id}")
        category = str(
            source.get("category")
            or item.get("category")
            or source.get("type")
            or source.get("problemType")
            or "Misc"
        )
        value = int(source.get("score") or source.get("point") or item.get("score") or item.get("value") or 0)
        solves = int(source.get("solves") or source.get("solveCount") or source.get("acCount") or item.get("solves") or 0)
        description = self._doc_text(
            source.get("description")
            or source.get("content")
            or source.get("detail")
            or item.get("description")
            or item.get("content")
        )
        normalized = {
            "id": problem_id,
            "name": name,
            "category": category,
            "value": value,
            "solves": solves,
            "description": description,
            "connection_info": str(item.get("connection_info") or item.get("container") or ""),
            "tags": [str(x.get("name") if isinstance(x, dict) else x) for x in (source.get("tags") or item.get("tags") or [])],
            "_ctfplus": item,
        }
        self._challenge_ids[name] = problem_id
        self._challenge_names[problem_id] = name
        self._challenge_details[problem_id] = normalized
        return normalized

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        payload = await self._problem_list_payload()
        return [self._normalize_challenge(item) for item in self._best_list(payload)]

    async def fetch_all_challenges(self) -> list[dict[str, Any]]:
        stubs = await self.fetch_challenge_stubs()
        team_id = await self._self_team_id()
        sem = asyncio.Semaphore(8)

        async def _detail_for(stub: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                try:
                    detail = await self._problem_detail(stub["id"], team_id)
                except Exception as exc:
                    logger.warning("Could not fetch CTF+ detail for %s: %s", stub.get("name"), exc)
                    detail = stub.get("_ctfplus") or {}
            merged = dict(stub)
            merged["_ctfplus_detail"] = detail
            if isinstance(detail, dict):
                merged["description"] = self._doc_text(
                    detail.get("description") or detail.get("content") or merged.get("description")
                )
            return merged

        return list(await asyncio.gather(*(_detail_for(stub) for stub in stubs)))

    async def fetch_solved_names(self) -> set[str]:
        stubs = await self.fetch_challenge_stubs()
        solved: set[str] = set()
        for stub in stubs:
            raw = stub.get("_ctfplus") or {}
            if raw.get("finish") or raw.get("isSolved") or raw.get("solved"):
                solved.add(stub["name"])
        return solved

    async def get_challenge_id(self, name: str) -> str:
        if name not in self._challenge_ids:
            await self.fetch_challenge_stubs()
        if name not in self._challenge_ids:
            raise RuntimeError(f'CTF+ challenge "{name}" not found')
        return self._challenge_ids[name]

    async def _problem_detail(self, problem_id: str, team_id: str = "") -> dict[str, Any]:
        attempts = [
            ("problem/getDetailInfo", {"id": problem_id, **({"teamId": team_id} if team_id else {})}),
            ("problem/getProblemDetailInfo", {"id": problem_id}),
            ("problem/getProblemInfo", {"problemId": problem_id}),
        ]
        last_error: Exception | None = None
        for path, body in attempts:
            try:
                data = await self._api(path, body)
                return data if isinstance(data, dict) else {"data": data}
            except Exception as exc:
                last_error = exc
        raise last_error or RuntimeError(f"Could not fetch CTF+ problem detail for {problem_id}")

    async def get_connection_info(self, challenge_name: str) -> str:
        problem_id = await self.get_challenge_id(challenge_name)
        try:
            data = await self._api("challenge/start", {"problemId": problem_id, "vpnCount": 0})
        except Exception:
            try:
                data = await self._api("challenge/pentest/runtime/detail", {"problemId": problem_id})
            except Exception:
                return ""
        return self._extract_connection_info(data)

    @staticmethod
    def _extract_connection_info(value: Any) -> str:
        found: list[str] = []

        def walk(v: Any) -> None:
            if isinstance(v, str):
                if re.search(r"(?i)(https?://|nc\\s+|[\\w.-]+:\\d+)", v):
                    found.append(v.strip())
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, dict):
                for key in ("url", "address", "connectionInfo", "connection_info", "host", "port", "containerUrl"):
                    if key in v:
                        walk(v[key])
                if "host" in v and "port" in v:
                    found.append(f"{v['host']}:{v['port']}")
                for x in v.values():
                    walk(x)

        walk(value)
        return "\n".join(dict.fromkeys(x for x in found if x))

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        problem_id = await self.get_challenge_id(challenge_name)
        data = await self._api("challenge/submit", {"problemId": problem_id, "answer": flag})
        text = str(data)
        lowered = text.lower()
        ok = any(token in lowered for token in ("true", "correct", "accepted", "success", "通过", "正确"))
        bad = any(token in lowered for token in ("false", "incorrect", "wrong", "错误", "失败"))
        message = data.get("msg", "") if isinstance(data, dict) else text
        if ok and not bad:
            return SubmitResult("correct", message, f'CORRECT — "{flag}" accepted. {message}'.strip())
        if bad:
            return SubmitResult("incorrect", message, f'INCORRECT — "{flag}" rejected. {message}'.strip())
        return SubmitResult("unknown", message, f"Unknown CTF+ submission result: {text}")

    @staticmethod
    def _slug(name: str) -> str:
        slug = re.sub(r'[<>:"/\\|?*.\x00-\x1f]', "", name.lower().strip())
        slug = re.sub(r"[\s_]+", "-", slug)
        return re.sub(r"-+", "-", slug).strip("-") or "challenge"

    @staticmethod
    def _file_urls(detail: Any) -> list[str]:
        urls: list[str] = []

        def add(candidate: Any) -> None:
            if isinstance(candidate, str):
                if candidate.startswith(("http://", "https://", "/")):
                    urls.append(candidate)
            elif isinstance(candidate, list):
                for x in candidate:
                    add(x)
            elif isinstance(candidate, dict):
                for key in ("url", "href", "link", "downloadUrl", "fileUrl", "path"):
                    if key in candidate:
                        add(candidate[key])
                for x in candidate.values():
                    add(x)

        add(detail)
        return list(dict.fromkeys(urls))

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        problem_id = str(challenge["id"])
        name = str(challenge.get("name") or f"problem-{problem_id}")
        challenge_dir = Path(output_dir) / self._slug(name)
        dist_dir = challenge_dir / "distfiles"
        challenge_dir.mkdir(parents=True, exist_ok=True)

        team_id = await self._self_team_id()
        try:
            detail = await self._problem_detail(problem_id, team_id)
        except Exception:
            detail = challenge.get("_ctfplus") or {}

        client = await self._ensure_client()
        for raw_url in self._file_urls(detail):
            url = urljoin(self.base_url.rstrip("/") + "/", raw_url)
            filename = Path(urlparse(url).path).name or "attachment.bin"
            dist_dir.mkdir(exist_ok=True)
            destination = dist_dir / filename
            if destination.exists():
                continue
            try:
                response = await client.get(url, headers=self._base_headers())
                response.raise_for_status()
                destination.write_bytes(response.content)
                logger.info("Downloaded CTF+ attachment: %s (%d bytes)", filename, len(response.content))
            except Exception as exc:
                logger.warning("Could not download CTF+ attachment %s: %s", url, exc)

        connection_info = challenge.get("connection_info") or self._extract_connection_info(detail)
        metadata = {
            "name": name,
            "category": challenge.get("category") or "Misc",
            "description": self._doc_text(
                detail.get("description") if isinstance(detail, dict) else ""
            )
            or challenge.get("description", ""),
            "value": int(challenge.get("value") or 0),
            "connection_info": connection_info,
            "tags": challenge.get("tags") or [],
            "solves": int(challenge.get("solves") or 0),
            "ctfplus_problem_id": problem_id,
            "ctfplus_competition_id": self.competition_id,
        }
        (challenge_dir / "metadata.yml").write_text(
            yaml.dump(metadata, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return str(challenge_dir)
