"""Lingxu event CTF client backed by browser session cookies."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from markdownify import markdownify as html2md

from backend.ctfd import SubmitResult

USER_AGENT = "Mozilla/5.0"
logger = logging.getLogger(__name__)


@dataclass
class LingxuEventCTFClient:
    base_url: str
    event_id: int
    cookie: str
    supports_challenge_materialization: bool = True
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    def _cookie_map(self) -> dict[str, str]:
        cookie_map: dict[str, str] = {}
        for part in self.cookie.split(";"):
            entry = part.strip()
            if not entry or "=" not in entry:
                continue
            key, value = entry.split("=", 1)
            cookie_map[key.strip()] = value.strip()
        return cookie_map

    def _csrf_token(self) -> str:
        token = self._cookie_map().get("csrftoken", "")
        if not token:
            raise RuntimeError("Lingxu cookie missing csrftoken")
        return token

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                headers={
                    "User-Agent": USER_AGENT,
                    "Cookie": self.cookie,
                },
                follow_redirects=True,
                verify=False,
                timeout=30.0,
                transport=self.transport,
            )
        return self._client

    async def _get(self, path: str) -> Any:
        client = await self._ensure_client()
        response = await client.get(path)
        if response.status_code >= 400:
            raise RuntimeError(f"Lingxu GET {path} failed with HTTP {response.status_code}")
        return response.json()

    async def _post(self, path: str, *, data: dict[str, Any] | None = None) -> tuple[httpx.Response, Any]:
        client = await self._ensure_client()
        response = await client.post(path, data=data, headers=self._write_json_headers())
        try:
            payload: Any = response.json()
        except Exception:
            payload = response.text
        return response, payload

    def _write_json_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "X-CSRFToken": self._csrf_token(),
            "X-Requested-With": "XMLHttpRequest",
        }

    def _extract_message(self, payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("error", "msg", "message", "detail"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, list):
                    text = " ".join(str(item).strip() for item in value if str(item).strip())
                    if text:
                        return text
                if value:
                    return str(value).strip()
            return ""
        if isinstance(payload, str):
            return payload.strip()
        return str(payload).strip() if payload else ""

    def _normalize_success_payload(self, status: str, flag: str, message: str) -> SubmitResult:
        if status == "correct":
            return SubmitResult("correct", message, f'CORRECT — "{flag}" accepted. {message}'.strip())
        if status == "already_solved":
            return SubmitResult("already_solved", message, f'ALREADY SOLVED — "{flag}" accepted. {message}'.strip())
        if status == "incorrect":
            return SubmitResult("incorrect", message, f'INCORRECT — "{flag}" rejected. {message}'.strip())
        return SubmitResult("unknown", message, f'UNKNOWN — "{flag}" submission status unclear. {message}'.strip())

    def _status_from_message(self, message: str) -> str | None:
        if "已提交了正确的Flag" in message:
            return "already_solved"
        if "flag错误" in message.casefold():
            return "incorrect"
        return None

    def _normalize_connection_target(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
            return text
        if text.startswith("nc "):
            return text
        host_port = re.fullmatch(r"([^:\s]+):(\d+)", text)
        if host_port:
            return f"nc {host_port.group(1)} {host_port.group(2)}"
        return text

    def _format_connection_info(self, payload: Any) -> str:
        lines: list[str] = []

        def add_line(value: Any) -> None:
            normalized = self._normalize_connection_target(value)
            if normalized and normalized not in lines:
                lines.append(normalized)

        if isinstance(payload, dict):
            add_line(payload.get("domain_addr"))
            ext_id = payload.get("ext_id")
        else:
            ext_id = payload

        if isinstance(ext_id, str):
            add_line(ext_id)
        elif isinstance(ext_id, list):
            for entry in ext_id:
                if isinstance(entry, dict):
                    for key in ("map_ip", "ext_ip", "ip"):
                        if entry.get(key):
                            add_line(entry.get(key))
                            break
                else:
                    add_line(entry)
        elif ext_id is not None:
            add_line(ext_id)

        return "\n".join(lines)

    def _platform_challenge_id_from_ref(self, challenge_ref: Any) -> int:
        if hasattr(challenge_ref, "platform_challenge_id"):
            challenge_id = challenge_ref.platform_challenge_id
            if challenge_id is not None:
                return int(challenge_id)
        if isinstance(challenge_ref, dict):
            challenge_id = challenge_ref.get("platform_challenge_id")
            if challenge_id is not None:
                return int(challenge_id)
        if isinstance(challenge_ref, int):
            return challenge_ref
        if isinstance(challenge_ref, str) and challenge_ref.isdigit():
            return int(challenge_ref)
        raise RuntimeError("Lingxu submit requires platform_challenge_id")

    def _event_id_from_ref(self, challenge_ref: Any) -> int:
        if hasattr(challenge_ref, "event_id"):
            event_id = challenge_ref.event_id
            if event_id is not None:
                return int(event_id)
        if isinstance(challenge_ref, dict):
            event_id = challenge_ref.get("event_id")
            if event_id is not None:
                return int(event_id)
        return self.event_id

    def _challenge_rows(self, payload: Any) -> list[dict[str, Any]]:
        rows = payload.get("results", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise RuntimeError("Lingxu challenge list payload is invalid")
        return [row for row in rows if isinstance(row, dict)]

    async def validate_access(self) -> None:
        try:
            cookie_map = self._cookie_map()
            if "sessionid" not in cookie_map:
                raise RuntimeError("Lingxu cookie missing sessionid")
            self._csrf_token()
            await self._get(f"/event/{self.event_id}/ctf/")
        except Exception as exc:
            raise RuntimeError("无法访问凌虚赛事 CTF 接口，请检查 Cookie、赛事 ID 和报名状态") from exc

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        payload = await self._get(f"/event/{self.event_id}/ctf/")
        rows = self._challenge_rows(payload)
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "category": row.get("classify") or "",
                "value": row.get("score", 0),
            }
            for row in rows
        ]

    async def fetch_all_challenges(self) -> list[dict[str, Any]]:
        return await self.fetch_challenge_stubs()

    async def fetch_solved_names(self) -> set[str]:
        payload = await self._get(f"/event/{self.event_id}/ctf/")
        rows = self._challenge_rows(payload)
        return {row["name"] for row in rows if row.get("is_parse")}

    def _slugify(self, name: str) -> str:
        slug = re.sub(r'[<>:"/\\|?*.\x00-\x1f]', "", name.lower().strip())
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug or "challenge"

    def _to_markdown(self, description: Any) -> str:
        text = str(description or "")
        if not text:
            return ""
        try:
            return html2md(text, heading_style="atx", escape_asterisks=False).strip()
        except Exception:
            plain = re.sub(r"<[^>]+>", "", text)
            return unescape(plain).strip()

    async def _download(self, source_url: str, dest: Path) -> None:
        client = await self._ensure_client()
        response = await client.get(source_url, follow_redirects=True)
        if response.status_code >= 400:
            raise RuntimeError(f"Lingxu download failed with HTTP {response.status_code}: {source_url}")
        dest.write_bytes(response.content)

    def _build_metadata(self, challenge: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
        score = detail.get("score")
        metadata = {
            "name": challenge.get("name") or f"challenge-{challenge['id']}",
            "category": challenge.get("category", ""),
            "description": self._to_markdown(detail.get("desc")),
            "value": score if score is not None else challenge.get("value", 0),
            "connection_info": detail.get("link_path") or "",
            "solves": detail.get("parse_count", 0),
            "platform": "lingxu-event-ctf",
            "platform_url": self.base_url.rstrip("/"),
            "event_id": self.event_id,
            "platform_challenge_id": challenge["id"],
            "test_type": detail.get("task_type"),
            "answer_mode": detail.get("answer_mode"),
            "requires_env_start": detail.get("task_type") == 1,
            "unsupported_reason": "",
        }
        if detail.get("answer_mode") == 2:
            metadata["unsupported_reason"] = "check mode is not supported in v1"
        return metadata

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        challenge_id = challenge["id"]
        detail = await self._get(f"/event/{self.event_id}/ctf/{challenge_id}/info/")

        challenge_dir = Path(output_dir) / f"{self._slugify(challenge.get('name', 'challenge'))}-{challenge_id}"
        distfiles_dir = challenge_dir / "distfiles"
        challenge_dir.mkdir(parents=True, exist_ok=True)
        distfiles_dir.mkdir(exist_ok=True)

        attachment = detail.get("attachment")
        if attachment:
            attachment_url = urljoin(f"{self.base_url.rstrip('/')}/", str(attachment))
            filename = Path(urlparse(attachment_url).path).name or "attachment"
            await self._download(attachment_url, distfiles_dir / filename)
            logger.info("Downloaded Lingxu attachment %s for challenge %s", filename, challenge_id)

        metadata = self._build_metadata(challenge, detail)
        (challenge_dir / "metadata.yml").write_text(
            yaml.dump(metadata, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return str(challenge_dir)

    async def prepare_challenge(self, challenge_dir: str) -> None:
        metadata_path = Path(challenge_dir) / "metadata.yml"
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        if not metadata.get("requires_env_start"):
            return None
        if str(metadata.get("connection_info") or "").strip():
            return None

        challenge_id = metadata.get("platform_challenge_id")
        if challenge_id is None:
            raise RuntimeError("Lingxu preflight requires platform_challenge_id")

        event_id = metadata.get("event_id") or self.event_id

        begin_response, begin_payload = await self._post(f"/event/{event_id}/ctf/{challenge_id}/begin/")
        begin_message = self._extract_message(begin_payload)
        if begin_response.status_code >= 400:
            raise RuntimeError(begin_message or f"preflight begin failed with HTTP {begin_response.status_code}")
        if isinstance(begin_payload, dict):
            if begin_payload.get("error"):
                raise RuntimeError(begin_message or "preflight begin failed")
            begin_status = begin_payload.get("status")
            if begin_status not in (None, 1, 2):
                raise RuntimeError(begin_message or f"preflight begin failed with status {begin_status}")

        run_response, run_payload = await self._post(f"/event/{event_id}/ctf/{challenge_id}/run/")
        run_message = self._extract_message(run_payload)
        if run_response.status_code >= 400:
            raise RuntimeError(run_message or f"preflight run failed with HTTP {run_response.status_code}")
        if isinstance(run_payload, dict) and (run_payload.get("error") or run_payload.get("status") == 3):
            raise RuntimeError(run_message or "preflight run failed")

        addr_payload = await self._get(f"/event/{event_id}/ctf/{challenge_id}/addr/")
        connection_info = self._format_connection_info(addr_payload)
        if not connection_info:
            raise RuntimeError("preflight addr returned empty connection info")

        metadata["connection_info"] = connection_info
        metadata_path.write_text(
            yaml.dump(metadata, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return None

    async def submit_flag(self, challenge_ref: Any, flag: str) -> SubmitResult:
        normalized_flag = flag.strip()
        challenge_id = self._platform_challenge_id_from_ref(challenge_ref)
        event_id = self._event_id_from_ref(challenge_ref)

        response, payload = await self._post(
            f"/event/{event_id}/ctf/{challenge_id}/flag/",
            data={"flag": normalized_flag},
        )
        message = self._extract_message(payload)

        if response.status_code == 200 and isinstance(payload, dict):
            if payload.get("status") == 1:
                return self._normalize_success_payload("correct", normalized_flag, message)
            if payload.get("status") == 2:
                return self._normalize_success_payload("incorrect", normalized_flag, message)

        message_status = self._status_from_message(message)
        if message_status is not None:
            return self._normalize_success_payload(message_status, normalized_flag, message)

        if response.status_code >= 400 and not message:
            message = f"HTTP {response.status_code}"
        return self._normalize_success_payload("unknown", normalized_flag, message)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
