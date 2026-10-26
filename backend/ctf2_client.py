"""CTF² (dasctf) Agent-API client — drop-in replacement for CTFdClient.

协议情报来自 SecAutoMind internal/ctfplatform/ctf2.go（已实测）：
  Base    /api/open/v1/user/agent/
  列表    GET  competitions/{comp}/stages/{stage}/challenges/?limit=100
  详情    GET  .../challenges/{id}/
  环境起  POST .../challenges/{id}/environment/start/
  环境查  GET  .../challenges/{id}/environment/
  环境毁  DELETE .../challenges/{id}/environment/
  提交    POST .../challenges/{id}/submit/   {flag, confirmation:true}
  附件    GET  .../challenges/{id}/attachments/{file_id}/url/?limit=1 → {url}
  比赛    GET  /competitions/{comp}/?limit=1
  排行    GET  /competitions/{comp}/scoreboard/?limit=100
  认证    Authorization: Bearer <agent token>
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "SecAutoMind-ctf-agent/1.0"


@dataclass
class SubmitResult:
    status: str  # "correct" | "already_solved" | "incorrect" | "unknown"
    message: str
    display: str


@dataclass
class CTF2Client:
    base_url: str = "https://ctf2.dasctf.com"
    token: str = ""
    competition_id: str = ""
    stage_id: str = ""

    _client: httpx.AsyncClient | None = field(default=None, repr=False)
    _comp_ready: bool = False
    _challenge_ids: dict[str, str] = field(default_factory=dict)

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                follow_redirects=True,
                verify=False,
                timeout=30.0,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _ensure_context(self) -> None:
        """解析 competition_id + stage_id：显式 env 优先，否则 agent/me 自动发现。"""
        if self._comp_ready:
            return
        if not self.token:
            self.token = os.environ.get("CTF2_AGENT_TOKEN", "")
        if not self.competition_id:
            self.competition_id = os.environ.get("CTF2_COMPETITION_ID", "")
        if not self.stage_id:
            self.stage_id = os.environ.get("CTF2_STAGE_ID", "")
        if self.competition_id and self.stage_id:
            self._comp_ready = True
            return
        client = await self._ensure_client()
        me = await client.get("/api/open/v1/user/agent/me/", headers=self._headers())
        me.raise_for_status()
        me_data = me.json().get("data", {})
        comps = me_data.get("active_competitions") or []
        # 取 running + agent_stage_count>0 的第一个比赛
        comp = next((c for c in comps if c.get("status") == "running" and (c.get("agent_stage_count") or 0) > 0), None)
        if not comp and comps:
            comp = comps[0]
        if not comp:
            raise RuntimeError("CTF² agent/me 无可用比赛（需在平台创建 Agent 并允许参赛）")
        self.competition_id = self.competition_id or comp.get("id") or comp.get("competition_id") or ""
        # 查该比赛阶段
        comp_resp = await client.get(
            f"/api/open/v1/user/agent/competitions/{self.competition_id}/?limit=1",
            headers=self._headers(),
        )
        comp_resp.raise_for_status()
        stages = (comp_resp.json().get("data") or {}).get("stages") or []
        if not self.stage_id:
            # audience=agent 且 running 优先
            stage = next(
                (s for s in stages if s.get("audience") == "agent" and s.get("status") == "running"),
                None,
            )
            if not stage:
                stage = next((s for s in stages if s.get("audience") == "agent"), None)
            if not stage and stages:
                stage = stages[0]
            if not stage:
                raise RuntimeError(f"CTF² 比赛 {self.competition_id} 无 agent 阶段")
            self.stage_id = stage.get("id") or ""
        if not self.competition_id or not self.stage_id:
            raise RuntimeError("CTF² 无法确定 competition_id/stage_id")
        self._comp_ready = True
        logger.info("CTF² ctx: competition=%s stage=%s", self.competition_id, self.stage_id)

    async def _get(self, path: str) -> Any:
        await self._ensure_context()
        client = await self._ensure_client()
        resp = await client.get(path, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        await self._ensure_context()
        client = await self._ensure_client()
        resp = await client.post(path, json=body, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def _challenge_path(self, challenge_id: str) -> str:
        return (
            f"/api/open/v1/user/agent/competitions/{self.competition_id}"
            f"/stages/{self.stage_id}/challenges/{challenge_id}"
        )

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        """拉取题目列表（轻量 stub）。"""
        await self._ensure_context()
        client = await self._ensure_client()
        resp = await client.get(
            f"/api/open/v1/user/agent/competitions/{self.competition_id}"
            f"/stages/{self.stage_id}/challenges/?limit=100",
            headers=self._headers(),
        )
        resp.raise_for_status()
        items = (resp.json().get("data") or {}).get("items") or []
        stubs = []
        for it in items:
            self._challenge_ids[it["name"]] = it["id"]
            stubs.append({
                "id": it.get("id"),
                "name": it.get("name"),
                "category": it.get("category", ""),
                "value": it.get("points", 0),
                "difficulty": it.get("difficulty", ""),
                "has_attachments": it.get("has_attachments", False),
                "has_container": it.get("has_container", False),
                "is_solved": it.get("is_solved", False),
                "solves": it.get("solve_count", 0),
                "submission_type": it.get("submission_type", ""),
            })
        return stubs

    async def get_challenge_id(self, name: str) -> str:
        if name in self._challenge_ids:
            return self._challenge_ids[name]
        stubs = await self.fetch_challenge_stubs()
        for s in stubs:
            self._challenge_ids[s["name"]] = s["id"]
        if name not in self._challenge_ids:
            raise RuntimeError(f'Challenge "{name}" not found in CTF²')
        return self._challenge_ids[name]

    async def _get_detail(self, challenge_id: str) -> dict[str, Any]:
        data = await self._get(self._challenge_path(challenge_id) + "/")
        return data.get("data") or {}

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        challenge_id = await self.get_challenge_id(challenge_name)
        data = await self._post(
            self._challenge_path(challenge_id) + "/submit/",
            {"flag": flag, "confirmation": True},
        )
        d = data.get("data") or {}
        accepted = d.get("accepted", False)
        is_solved = d.get("is_solved", False)
        attempts_left = d.get("attempts_left", 0)
        submission_id = d.get("submission_id", "")

        if accepted or is_solved:
            return SubmitResult(
                "correct" if accepted else "already_solved",
                f"submission_id={submission_id} attempts_left={attempts_left}",
                f'CORRECT — "{flag}" accepted (attempts_left={attempts_left})'.strip(),
            )
        return SubmitResult(
            "incorrect",
            f"attempts_left={attempts_left} submission_id={submission_id}",
            f'INCORRECT — "{flag}" rejected (attempts_left={attempts_left})'.strip(),
        )

    async def fetch_all_challenges(self) -> list[dict[str, Any]]:
        stubs = await self.fetch_challenge_stubs()
        challenges = []
        for stub in stubs:
            if stub.get("is_solved"):
                continue
            detail = await self._get_detail(stub["id"])
            detail["_id"] = stub["id"]
            detail["_name"] = stub["name"]
            challenges.append(detail)
        return challenges

    async def fetch_solved_names(self) -> set[str]:
        try:
            stubs = await self.fetch_challenge_stubs()
            return {s["name"] for s in stubs if s.get("is_solved")}
        except Exception:
            logger.warning("Could not fetch solved challenges", exc_info=True)
            return set()

    async def ensure_environment(self, challenge_id: str) -> str:
        """启动/查询靶机环境，返回 access_url（或空）。"""
        try:
            await self._ensure_context()
            client = await self._ensure_client()
            # 先查询
            q = await client.get(self._challenge_path(challenge_id) + "/environment/", headers=self._headers())
            if q.status_code == 200:
                d = (q.json().get("data") or {})
                if d.get("access_ready") or d.get("status") == "running":
                    return d.get("access_url") or ""
            # 启动；409 = 已存在/创建中，稍候回查
            r = await client.post(self._challenge_path(challenge_id) + "/environment/start/", json={}, headers=self._headers())
            if r.status_code in (200, 201):
                d = (r.json().get("data") or {})
                if d.get("access_ready") or d.get("status") == "running":
                    return d.get("access_url") or ""
            for _ in range(6):  # 最多等 ~15s
                await asyncio.sleep(2.5)
                q = await client.get(self._challenge_path(challenge_id) + "/environment/", headers=self._headers())
                if q.status_code == 200:
                    d = (q.json().get("data") or {})
                    if d.get("access_ready") or d.get("status") == "running":
                        return d.get("access_url") or ""
            return ""
        except Exception as e:
            logger.warning("ensure_environment failed: %s", e)
            return ""

    async def acquire_target(self, challenge_id: str, max_wait_s: float = 180.0, poll_base_s: float = 3.0) -> str:
        """阻塞获取靶机 access_url，直到就绪或超时（返回 '' 表示失败）。

        与 `ensure_environment` 的关键区别（修复「有脑无手」SPOF）：
        - 先主动 `DELETE` 可能存在的旧环境，释放 Agent 唯一环境槽位，避免 409 卡死；
        - 指数退避轮询 `GET /environment/`，直到 `access_ready` / `status==running` 且有 `access_url`；
        - 全程记录 HTTP 状态码，不静默吞错；
        - 超时返回 ''，由调用方 fail-closed（绝不带空地址盲启 solver）。
        """
        try:
            await self._ensure_context()
            client = await self._ensure_client()
            env_path = self._challenge_path(challenge_id) + "/environment/"

            # 1) 主动释放旧环境（平台环境销毁是异步的，DELETE 立即返回不保证已销毁）
            try:
                del_resp = await client.delete(env_path, headers=self._headers())
                logger.info("[%s] pre-close env: HTTP %d", challenge_id, del_resp.status_code)
            except Exception as e:
                logger.warning("[%s] pre-close env failed: %s", challenge_id, e)

            # 2) 轮询直到就绪（指数退避，上限 10s）
            waited = 0.0
            delay = poll_base_s
            while waited <= max_wait_s:
                try:
                    q = await client.get(env_path, headers=self._headers())
                    if q.status_code == 200:
                        d = (q.json().get("data") or {})
                        if (d.get("access_ready") or d.get("status") == "running") and d.get("access_url"):
                            url = d.get("access_url") or ""
                            if url:
                                logger.info("[%s] env ready: %s", challenge_id, url)
                                return url
                except Exception as e:
                    logger.warning("[%s] env query failed: %s", challenge_id, e)

                # 未就绪则尝试启动（409=槽位被占/创建中，下一轮轮询回查）
                try:
                    r = await client.post(env_path + "start/", json={}, headers=self._headers())
                    logger.info("[%s] env start: HTTP %d", challenge_id, r.status_code)
                except Exception as e:
                    logger.warning("[%s] env start failed: %s", challenge_id, e)

                await asyncio.sleep(delay)
                waited += delay
                delay = min(delay * 1.5, 10.0)

            logger.warning("[%s] acquire_target 超时（%ss）未拿到 access_url", challenge_id, int(max_wait_s))
            return ""
        except Exception as e:
            logger.warning("acquire_target failed: %s", e)
            return ""

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        """下载题目附件 + 写 metadata.yml，返回题目目录。"""
        name = challenge.get("name") or challenge.get("_name") or f"challenge-{challenge.get('id','')}"
        slug = re.sub(r'[<>:"/\\|?*.\x00-\x1f]', "", name.lower().strip())
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug).strip("-") or "challenge"
        ch_dir = Path(output_dir) / slug
        ch_dir.mkdir(parents=True, exist_ok=True)

        challenge_id = challenge.get("id") or challenge.get("_id")
        # 详情（description/files/points 都在 detail.data）
        detail_data: dict[str, Any] = {}
        if challenge_id:
            try:
                data = await self._get(self._challenge_path(challenge_id) + "/")
                detail_data = data.get("data") or {}
            except Exception:
                detail_data = {}
        files = challenge.get("files") or []
        if detail_data.get("files"):
            files = detail_data.get("files") or files
        for f in files or []:
            fid = f.get("file_id")
            if not fid:
                continue
            try:
                url_data = await self._get(self._challenge_path(challenge_id) + f"/attachments/{fid}/url/?limit=1")
                url = (url_data.get("data") or {}).get("url")
                fname = (url_data.get("data") or {}).get("file_name") or f.get("file_name", "file")
                if url:
                    dist_dir = ch_dir / "distfiles"
                    dist_dir.mkdir(exist_ok=True)
                    client = await self._ensure_client()
                    resp = await client.get(url, timeout=60.0)
                    resp.raise_for_status()
                    safe = Path(fname).name
                    (dist_dir / safe).write_bytes(resp.content)
                    logger.info("Downloaded: %s (%d bytes)", fname, len(resp.content))
            except Exception as e:
                logger.warning("附件下载失败 %s: %s", f.get("file_name"), e)

        desc = detail_data.get("description") or challenge.get("description") or ""
        # CTF² 无 connection_info 字段：容器题经 environment/ 拿访问地址
        conn = challenge.get("connection_info") or ""
        has_container = challenge.get("has_container") or challenge.get("has_environment") or detail_data.get("has_container")
        if not conn and challenge_id and has_container:
            conn = await self.ensure_environment(challenge_id)
        meta = {
            "name": name,
            "category": challenge.get("category") or detail_data.get("category", ""),
            "description": desc.strip(),
            "value": challenge.get("value", challenge.get("points", 0)) or detail_data.get("points", 0),
            "connection_info": conn,
            "tags": [],
            "solves": challenge.get("solves", challenge.get("solve_count", 0)),
        }
        import yaml
        (ch_dir / "metadata.yml").write_text(
            yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
        )
        return str(ch_dir)

    async def close_environment(self, challenge_id: str) -> None:
        """销毁靶机环境（释放 Agent 环境槽位）。"""
        try:
            await self._ensure_context()
            client = await self._ensure_client()
            r = await client.delete(self._challenge_path(challenge_id) + "/environment/", headers=self._headers())
            logger.info("environment closed: %s (HTTP %d)", challenge_id, r.status_code)
        except Exception as e:
            logger.warning("close_environment failed %s: %s", challenge_id, e)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
