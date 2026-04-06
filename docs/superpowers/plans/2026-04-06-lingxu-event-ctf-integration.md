# 凌虚赛事 CTF 接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏现有 CTFd 工作流的前提下，为 HuntingBlade 增加“凌虚赛事 CTF”平台接入，让协调器能够用 Cookie 登录态拉题、为环境型题目做预处理、并向平台提交 flag。

**Architecture:** 采用“薄平台协议 + 平台工厂 + 独立凌虚客户端”的增量改造。协调器、poller、swarm、solver 继续沿用现有结构，但它们依赖的客户端能力从“CTFd 专用”提升为“竞赛平台最小能力集”；凌虚特有的 Cookie、CSRF、题目映射、环境启动与提交逻辑全部收敛到 `backend/platforms/lingxu_event_ctf.py`。V1 不做验证码登录，不做 check 模式自动化，只支持赛事 CTF 的 FLAG 题。

**Tech Stack:** Python 3.14, click, pydantic-settings, httpx, pytest, pytest-asyncio, PyYAML, markdownify

---

## 协作约束

- 唯一明确的共享修改点是 `backend/cli.py`。另一线程已经在推进 `ctf-import` 以及 CLI 中文帮助文本整理，本计划不要回退那部分改动。
- 本计划内部的配置字段继续保持英文命名，例如 `Settings.platform_url`、`Settings.lingxu_event_id`；`backend/cli.py` 的对外参数名也保持英文，仅帮助文本、README 和示例说明使用中文。
- 执行 Task 6 前，先确认另一线程的 CLI 改动已经落地或已完成 rebase。检查命令只看差异，不做覆盖：

```bash
git diff -- backend/cli.py
git log --oneline -- backend/cli.py
```

- 除 `backend/cli.py` 外，本计划新增的改动主要落在 `backend/platforms/`、`backend/poller.py`、`backend/agents/coordinator_*`、`backend/prompts.py` 和新增测试文件，与手动导入线程基本无交叉。

## 文件结构映射

### 新建文件

- `backend/platforms/__init__.py`
  - 导出平台协议、工厂和凌虚客户端。
- `backend/platforms/base.py`
  - 定义 `CompetitionPlatformClient` 最小协议和 `PlatformConfigError`。
- `backend/platforms/factory.py`
  - 根据 `Settings` 校验配置并构建 `CTFdClient` 或 `LingxuEventCTFClient`。
- `backend/platforms/lingxu_event_ctf.py`
  - 封装凌虚赛事 CTF 的 Cookie / CSRF、拉题、落盘、环境 preflight、flag 提交逻辑。
- `tests/test_platform_factory.py`
  - 校验平台配置和工厂分流逻辑。
- `tests/test_coordinator_platform_flow.py`
  - 校验通用 poller、协调器对 unsupported / preflight 的处理。
- `tests/test_lingxu_event_ctf_client.py`
  - 校验凌虚客户端的认证头、题目映射、环境启动、提交结果归一化。
- `tests/test_cli.py`
  - 校验 `ctf-solve` 在保持英文参数的前提下接入凌虚平台选项，并输出中文帮助与报错。

### 修改文件

- `backend/config.py`
  - 新增平台相关配置字段，保留原有 CTFd 配置兼容路径。
- `backend/ctfd.py`
  - 让 CTFd 客户端实现最小平台协议；增加 `validate_access()`、`prepare_challenge()` 空实现，并让 `submit_flag()` 接受 `ChallengeMeta | str`。
- `backend/deps.py`
  - 将 `ctfd` 的类型从 `CTFdClient` 放宽为 `CompetitionPlatformClient`，降低上层耦合。
- `backend/poller.py`
  - 让 poller 面向平台协议而不是 CTFd 具体实现。
- `backend/prompts.py`
  - 扩展 `ChallengeMeta`，让 metadata 能携带 `platform`、`event_id`、`platform_challenge_id`、`requires_env_start`、`unsupported_reason` 等字段。
- `backend/agents/coordinator_loop.py`
  - 启动时通过工厂创建平台客户端并校验登录态；poller 改用通用平台协议。
- `backend/agents/coordinator_core.py`
  - 拉题后对 unsupported 题跳过；在启动 swarm 前调用 `prepare_challenge()`；提交时改为优先使用 `ChallengeMeta`。
- `backend/agents/swarm.py`
  - `try_submit_flag()` 传 `self.meta` 给平台客户端，不再只传题目名。
- `backend/agents/claude_solver.py`
  - 手工拦截 `submit_flag` 时传 `self.meta`。
- `backend/agents/codex_solver.py`
  - 手工拦截 `submit_flag` 时传 `self.meta`。
- `backend/agents/solver.py`
  - 给 `SolverDeps` 补充 `challenge_ref`，让不走 swarm 去重路径时也能传完整题目引用。
- `backend/tools/core.py`
  - `do_submit_flag()` 接受 `ChallengeMeta | str`。
- `backend/tools/flag.py`
  - 直接提交 fallback 路径改用 `challenge_ref`。
- `backend/cli.py`
  - 在英文参数 CLI 体系上新增凌虚平台选项，把对应值写入 `Settings`，并提供中文帮助文本。
- `README.md`
  - 补一节“凌虚赛事 CTF 使用方式”，写清 Cookie 文件、限制范围、check 模式跳过和示例命令。

### 设计取舍

- V1 先保留大量内部变量名 `ctfd`，只把类型约束提升为平台协议。这样 diff 更小，避免一次性大面积重命名。
- 提交链路依旧通过 `SubmitResult` 归一化，solver 不需要知道底层是 CTFd 还是凌虚。
- 环境题的 `connection_info` 仍写回本地 `metadata.yml`，保持 prompt 构造逻辑稳定。

### Task 1: 建立平台协议、平台工厂和配置校验

**Files:**
- Create: `backend/platforms/__init__.py`
- Create: `backend/platforms/base.py`
- Create: `backend/platforms/factory.py`
- Modify: `backend/config.py`
- Test: `tests/test_platform_factory.py`

- [ ] **Step 1: 先写失败的配置校验测试**

```python
import pytest

from backend.config import Settings
from backend.ctfd import CTFdClient
from backend.platforms.base import PlatformConfigError
from backend.platforms.factory import create_platform_client, validate_platform_settings


def test_validate_platform_settings_requires_cookie_for_lingxu() -> None:
    settings = Settings(
        platform="lingxu-event-ctf",
        platform_url="https://lx.example.com",
        lingxu_event_id=42,
    )

    with pytest.raises(PlatformConfigError, match="lingxu_cookie"):
        validate_platform_settings(settings)


def test_create_platform_client_returns_ctfd_by_default() -> None:
    settings = Settings(ctfd_url="https://ctfd.example.com", ctfd_token="token-1")
    client = create_platform_client(settings)

    assert isinstance(client, CTFdClient)
    assert client.base_url == "https://ctfd.example.com"
    assert client.token == "token-1"
```

- [ ] **Step 2: 运行测试，确认它先失败**

Run: `uv run pytest tests/test_platform_factory.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.platforms'`

- [ ] **Step 3: 写最小实现，先把平台协议、工厂和配置字段落地**

```python
# backend/platforms/__init__.py
from backend.platforms.base import CompetitionPlatformClient, PlatformConfigError
from backend.platforms.factory import create_platform_client, validate_platform_settings

__all__ = [
    "CompetitionPlatformClient",
    "PlatformConfigError",
    "create_platform_client",
    "validate_platform_settings",
]
```

```python
# backend/platforms/base.py
from __future__ import annotations

from typing import Any, Protocol


class CompetitionPlatformClient(Protocol):
    async def validate_access(self) -> None: ...
    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]: ...
    async def fetch_all_challenges(self) -> list[dict[str, Any]]: ...
    async def fetch_solved_names(self) -> set[str]: ...
    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str: ...
    async def prepare_challenge(self, challenge_dir: str) -> None: ...
    async def submit_flag(self, challenge_ref: Any, flag: str) -> Any: ...
    async def close(self) -> None: ...


class PlatformConfigError(ValueError):
    """Raised when CLI / .env platform configuration is incomplete."""
```

```python
# backend/platforms/factory.py
from __future__ import annotations

from pathlib import Path

from backend.config import Settings
from backend.ctfd import CTFdClient
from backend.platforms.base import PlatformConfigError


def _read_cookie_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def validate_platform_settings(settings: Settings) -> None:
    platform = (settings.platform or "ctfd").strip()

    if platform == "ctfd":
        if not settings.ctfd_url:
            raise PlatformConfigError("ctfd_url is required when platform=ctfd")
        return

    if platform != "lingxu-event-ctf":
        raise PlatformConfigError(f"unsupported platform: {platform}")

    if not settings.platform_url:
        raise PlatformConfigError("platform_url is required when platform=lingxu-event-ctf")
    if not settings.lingxu_event_id:
        raise PlatformConfigError("lingxu_event_id is required when platform=lingxu-event-ctf")
    if not settings.lingxu_cookie and not settings.lingxu_cookie_file:
        raise PlatformConfigError("lingxu_cookie or lingxu_cookie_file is required when platform=lingxu-event-ctf")


def create_platform_client(settings: Settings):
    validate_platform_settings(settings)

    platform = (settings.platform or "ctfd").strip()
    if platform == "ctfd":
        return CTFdClient(
            base_url=settings.ctfd_url,
            token=settings.ctfd_token,
            username=settings.ctfd_user,
            password=settings.ctfd_pass,
        )

    from backend.platforms.lingxu_event_ctf import LingxuEventCTFClient

    cookie = settings.lingxu_cookie or _read_cookie_file(settings.lingxu_cookie_file)
    return LingxuEventCTFClient(
        base_url=settings.platform_url,
        event_id=settings.lingxu_event_id,
        cookie=cookie,
    )
```

```python
# backend/config.py
class Settings(BaseSettings):
    # platform selection
    platform: str = "ctfd"
    platform_url: str = ""
    lingxu_event_id: int = 0
    lingxu_cookie: str = ""
    lingxu_cookie_file: str = ""

    # CTFd
    ctfd_url: str = "http://localhost:8000"
    ctfd_user: str = "admin"
    ctfd_pass: str = "admin"
    ctfd_token: str = ""
```

- [ ] **Step 4: 重新运行测试，确认工厂与配置校验通过**

Run: `uv run pytest tests/test_platform_factory.py -v`  
Expected: PASS with `2 passed`

- [ ] **Step 5: 提交这一小步**

```bash
git add backend/platforms/__init__.py backend/platforms/base.py backend/platforms/factory.py backend/config.py tests/test_platform_factory.py
git commit -m "refactor: add competition platform factory"
```

### Task 2: 让 poller 与协调器依赖平台协议，而不是写死 CTFd

**Files:**
- Modify: `backend/deps.py`
- Modify: `backend/poller.py`
- Modify: `backend/agents/coordinator_loop.py`
- Test: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 先写失败的 poller / coordinator 测试**

```python
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from backend.agents.coordinator_loop import build_deps
from backend.config import Settings
from backend.poller import CompetitionPoller
from backend.prompts import ChallengeMeta


@dataclass
class FakePlatform:
    stubs: list[dict]
    solved: set[str]
    closed: bool = False

    async def validate_access(self) -> None:
        return None

    async def fetch_challenge_stubs(self) -> list[dict]:
        return list(self.stubs)

    async def fetch_all_challenges(self) -> list[dict]:
        return list(self.stubs)

    async def fetch_solved_names(self) -> set[str]:
        return set(self.solved)

    async def pull_challenge(self, challenge: dict, output_dir: str) -> str:
        raise AssertionError("not used in this test")

    async def prepare_challenge(self, challenge_dir: str) -> None:
        return None

    async def submit_flag(self, challenge_ref, flag: str):
        raise AssertionError("not used in this test")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_competition_poller_detects_new_and_solved_challenges() -> None:
    platform = FakePlatform(
        stubs=[{"id": 1, "name": "签到"}],
        solved=set(),
    )
    poller = CompetitionPoller(ctfd=platform, interval_s=0.01)
    await poller._seed()

    platform.stubs = [
        {"id": 1, "name": "签到"},
        {"id": 2, "name": "Pwn1"},
    ]
    platform.solved = {"签到"}

    await poller._poll_once()
    events = {(event.kind, event.challenge_name) for event in poller.drain_events()}

    assert events == {
        ("new_challenge", "Pwn1"),
        ("challenge_solved", "签到"),
    }


def test_build_deps_accepts_generic_platform(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenges" / "signin-1"
    challenge_dir.mkdir(parents=True)
    (challenge_dir / "metadata.yml").write_text(
        "name: 签到\ncategory: misc\ndescription: 1\nvalue: 100\n",
        encoding="utf-8",
    )

    platform = FakePlatform(stubs=[], solved=set())
    _, _, deps = build_deps(
        settings=Settings(),
        challenges_root=str(tmp_path / "challenges"),
        platform=platform,
    )

    assert deps.ctfd is platform
    assert deps.challenge_metas["签到"] == ChallengeMeta.from_yaml(challenge_dir / "metadata.yml")
```

- [ ] **Step 2: 运行测试，确认当前实现还不支持通用平台**

Run: `uv run pytest tests/test_coordinator_platform_flow.py -v`  
Expected: FAIL with `ImportError` or `AttributeError` mentioning `CompetitionPoller` or `build_deps() got an unexpected keyword argument 'platform'`

- [ ] **Step 3: 写最小实现，让 poller / coordinator 接入平台协议**

```python
# backend/deps.py
from backend.platforms.base import CompetitionPlatformClient


@dataclass
class SolverDeps:
    sandbox: DockerSandbox
    ctfd: CompetitionPlatformClient
    challenge_dir: str
    challenge_name: str
    challenge_ref: Any = None
    workspace_dir: str = ""
    use_vision: bool = False
```

```python
# backend/deps.py
@dataclass
class CoordinatorDeps:
    ctfd: CompetitionPlatformClient
    cost_tracker: CostTracker
    settings: Any
    model_specs: list[str] = field(default_factory=list)
```

```python
# backend/poller.py
from backend.platforms.base import CompetitionPlatformClient


@dataclass
class CompetitionPoller:
    """Polls any competition platform implementing the minimal protocol."""

    ctfd: CompetitionPlatformClient
    interval_s: float = 5.0


CTFdPoller = CompetitionPoller
```

```python
# backend/agents/coordinator_loop.py
from backend.platforms.base import CompetitionPlatformClient
from backend.platforms.factory import create_platform_client
from backend.poller import CompetitionPoller


def build_deps(
    settings: Settings,
    model_specs: list[str] | None = None,
    challenges_root: str = "challenges",
    no_submit: bool = False,
    challenge_dirs: dict[str, str] | None = None,
    challenge_metas: dict[str, ChallengeMeta] | None = None,
    platform: CompetitionPlatformClient | None = None,
) -> tuple[CompetitionPlatformClient, CostTracker, CoordinatorDeps]:
    ctfd = platform or create_platform_client(settings)
    cost_tracker = CostTracker()
    specs = model_specs or list(DEFAULT_MODELS)
    Path(challenges_root).mkdir(parents=True, exist_ok=True)
    deps = CoordinatorDeps(
        ctfd=ctfd,
        cost_tracker=cost_tracker,
        settings=settings,
        model_specs=specs,
        challenges_root=challenges_root,
        no_submit=no_submit,
        max_concurrent_challenges=getattr(settings, "max_concurrent_challenges", 10),
        challenge_dirs=challenge_dirs or {},
        challenge_metas=challenge_metas or {},
    )
    for d in Path(challenges_root).iterdir():
        meta_path = d / "metadata.yml"
        if meta_path.exists():
            meta = ChallengeMeta.from_yaml(meta_path)
            deps.challenge_dirs.setdefault(meta.name, str(d))
            deps.challenge_metas.setdefault(meta.name, meta)
    return ctfd, cost_tracker, deps
```

```python
# backend/agents/coordinator_loop.py
async def run_event_loop(
    deps: CoordinatorDeps,
    ctfd: CompetitionPlatformClient,
    cost_tracker: CostTracker,
    turn_fn: TurnFn,
    status_interval: int = 60,
) -> dict[str, Any]:
    await ctfd.validate_access()
    poller = CompetitionPoller(ctfd=ctfd, interval_s=5.0)
    await poller.start()
```

- [ ] **Step 4: 重新运行测试，确认 poller 与 build_deps 通过**

Run: `uv run pytest tests/test_coordinator_platform_flow.py -v`  
Expected: PASS with `2 passed`

- [ ] **Step 5: 提交这一小步**

```bash
git add backend/deps.py backend/poller.py backend/agents/coordinator_loop.py tests/test_coordinator_platform_flow.py
git commit -m "refactor: make coordinator platform-agnostic"
```

### Task 3: 实现凌虚客户端的 Cookie / CSRF 认证和题目列表读取

**Files:**
- Create: `backend/platforms/lingxu_event_ctf.py`
- Modify: `backend/ctfd.py`
- Test: `tests/test_lingxu_event_ctf_client.py`

- [ ] **Step 1: 先写失败的凌虚认证与列题测试**

```python
import httpx
import pytest

from backend.platforms.lingxu_event_ctf import LingxuEventCTFClient


@pytest.mark.asyncio
async def test_fetch_challenge_stubs_and_solved_names_use_cookie_session() -> None:
    seen_cookies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_cookies.append(request.headers["cookie"])
        assert request.url.path == "/event/42/ctf/"
        return httpx.Response(
            200,
            json={
                "count": 2,
                "results": [
                    {"id": 137, "name": "签到", "classify": "misc", "score": 100, "is_parse": True},
                    {"id": 204, "name": "Web1", "classify": "web", "score": 300, "is_parse": False},
                ],
            },
        )

    client = LingxuEventCTFClient(
        base_url="https://lx.example.com",
        event_id=42,
        cookie="sessionid=sid123; csrftoken=csrf456",
        transport=httpx.MockTransport(handler),
    )

    stubs = await client.fetch_challenge_stubs()
    solved = await client.fetch_solved_names()

    assert seen_cookies == [
        "sessionid=sid123; csrftoken=csrf456",
        "sessionid=sid123; csrftoken=csrf456",
    ]
    assert stubs == [
        {"id": 137, "name": "签到", "category": "misc", "value": 100},
        {"id": 204, "name": "Web1", "category": "web", "value": 300},
    ]
    assert solved == {"签到"}


@pytest.mark.asyncio
async def test_validate_access_fails_without_event_permission() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    client = LingxuEventCTFClient(
        base_url="https://lx.example.com",
        event_id=42,
        cookie="sessionid=sid123; csrftoken=csrf456",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="赛事 CTF 接口"):
        await client.validate_access()
```

- [ ] **Step 2: 运行测试，确认凌虚客户端还不存在**

Run: `uv run pytest tests/test_lingxu_event_ctf_client.py -v`  
Expected: FAIL with `ModuleNotFoundError` for `backend.platforms.lingxu_event_ctf`

- [ ] **Step 3: 写最小实现，先把认证和题目列表跑通**

```python
# backend/platforms/lingxu_event_ctf.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.ctfd import SubmitResult


@dataclass
class LingxuEventCTFClient:
    base_url: str
    event_id: int
    cookie: str
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    def _cookie_map(self) -> dict[str, str]:
        pairs = [part.strip() for part in self.cookie.split(";") if part.strip()]
        data: dict[str, str] = {}
        for pair in pairs:
            if "=" in pair:
                key, value = pair.split("=", 1)
                data[key.strip()] = value.strip()
        return data

    def _csrf_token(self) -> str:
        token = self._cookie_map().get("csrftoken", "")
        if not token:
            raise RuntimeError("Lingxu cookie missing csrftoken")
        return token

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                headers={"User-Agent": "Mozilla/5.0", "Cookie": self.cookie},
                follow_redirects=True,
                verify=False,
                timeout=30.0,
                transport=self.transport,
            )
        return self._client

    async def _get(self, path: str) -> Any:
        client = await self._ensure_client()
        resp = await client.get(path)
        if resp.status_code >= 400:
            raise RuntimeError(f"Lingxu GET {path} failed with HTTP {resp.status_code}")
        return resp.json()

    async def validate_access(self) -> None:
        if "sessionid" not in self._cookie_map():
            raise RuntimeError("Lingxu cookie missing sessionid")
        if "csrftoken" not in self._cookie_map():
            raise RuntimeError("Lingxu cookie missing csrftoken")
        try:
            await self._get(f"/event/{self.event_id}/ctf/")
        except Exception as exc:
            raise RuntimeError("无法访问凌虚赛事 CTF 接口，请检查 Cookie、赛事 ID 和报名状态") from exc

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        data = await self._get(f"/event/{self.event_id}/ctf/")
        rows = data.get("results", data)
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
        data = await self._get(f"/event/{self.event_id}/ctf/")
        rows = data.get("results", data)
        return {row["name"] for row in rows if row.get("is_parse")}

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        raise NotImplementedError("Implemented in Task 4")

    async def prepare_challenge(self, challenge_dir: str) -> None:
        return None

    async def submit_flag(self, challenge_ref, flag: str) -> SubmitResult:
        raise NotImplementedError("Implemented in Task 5")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
```

```python
# backend/ctfd.py
    async def validate_access(self) -> None:
        await self.fetch_challenge_stubs()

    async def prepare_challenge(self, challenge_dir: str) -> None:
        return None
```

- [ ] **Step 4: 重新运行测试，确认列题与登录态校验通过**

Run: `uv run pytest tests/test_lingxu_event_ctf_client.py -v`  
Expected: PASS with `2 passed`

- [ ] **Step 5: 提交这一小步**

```bash
git add backend/platforms/lingxu_event_ctf.py backend/ctfd.py tests/test_lingxu_event_ctf_client.py
git commit -m "feat: add Lingxu event CTF listing client"
```

### Task 4: 实现题目详情落盘、metadata 扩展字段和 unsupported 跳过

**Files:**
- Modify: `backend/platforms/lingxu_event_ctf.py`
- Modify: `backend/prompts.py`
- Modify: `backend/agents/coordinator_core.py`
- Modify: `tests/test_lingxu_event_ctf_client.py`
- Modify: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 先写失败的题目落盘与 unsupported 测试**

```python
import httpx
import pytest
import yaml

from backend.platforms.lingxu_event_ctf import LingxuEventCTFClient
from backend.prompts import ChallengeMeta


@pytest.mark.asyncio
async def test_pull_challenge_writes_metadata_and_downloads_attachment(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/event/42/ctf/137/info/":
            return httpx.Response(
                200,
                json={
                    "name": "SQL Injection",
                    "desc": "<p>Find the flag</p>",
                    "task_type": 3,
                    "link_path": "",
                    "answer_mode": 1,
                    "secondary_path": "",
                    "attachment": "/media/env/ctf/sql.zip",
                    "score": 250,
                    "parse_count": 8,
                    "is_parse": False,
                    "message": [],
                },
            )
        if request.url.path == "/media/env/ctf/sql.zip":
            return httpx.Response(200, content=b"zip-data")
        raise AssertionError(request.url.path)

    client = LingxuEventCTFClient(
        base_url="https://lx.example.com",
        event_id=42,
        cookie="sessionid=sid123; csrftoken=csrf456",
        transport=httpx.MockTransport(handler),
    )

    challenge_dir = await client.pull_challenge(
        {"id": 137, "name": "SQL Injection", "category": "web", "value": 250},
        str(tmp_path),
    )

    metadata = yaml.safe_load((tmp_path / "sql-injection-137" / "metadata.yml").read_text(encoding="utf-8"))
    meta = ChallengeMeta.from_yaml(tmp_path / "sql-injection-137" / "metadata.yml")

    assert challenge_dir == str(tmp_path / "sql-injection-137")
    assert metadata["platform"] == "lingxu-event-ctf"
    assert metadata["platform_url"] == "https://lx.example.com"
    assert metadata["event_id"] == 42
    assert metadata["platform_challenge_id"] == 137
    assert metadata["test_type"] == 3
    assert metadata["answer_mode"] == 1
    assert metadata["requires_env_start"] is False
    assert metadata["solves"] == 8
    assert meta.platform_challenge_id == 137
    assert (tmp_path / "sql-injection-137" / "distfiles" / "sql.zip").read_bytes() == b"zip-data"


@pytest.mark.asyncio
async def test_pull_challenge_marks_check_mode_as_unsupported(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "name": "Check Only",
                "desc": "skip me",
                "task_type": 2,
                "link_path": "https://check.example.com",
                "answer_mode": 2,
                "secondary_path": "",
                "attachment": "",
                "score": 50,
                "parse_count": 0,
                "is_parse": False,
                "message": [],
            },
        )

    client = LingxuEventCTFClient(
        base_url="https://lx.example.com",
        event_id=42,
        cookie="sessionid=sid123; csrftoken=csrf456",
        transport=httpx.MockTransport(handler),
    )

    await client.pull_challenge(
        {"id": 204, "name": "Check Only", "category": "misc", "value": 50},
        str(tmp_path),
    )

    metadata = yaml.safe_load((tmp_path / "check-only-204" / "metadata.yml").read_text(encoding="utf-8"))
    assert metadata["unsupported_reason"] == "check mode is not supported in v1"
```

```python
from pathlib import Path

import pytest
import yaml

from backend.agents.coordinator_core import do_spawn_swarm
from backend.config import Settings
from backend.cost_tracker import CostTracker
from backend.deps import CoordinatorDeps


class UnsupportedPlatform:
    async def validate_access(self) -> None:
        return None

    async def fetch_challenge_stubs(self):
        return [{"id": 204, "name": "Check Only"}]

    async def fetch_all_challenges(self):
        return [{"id": 204, "name": "Check Only"}]

    async def fetch_solved_names(self):
        return set()

    async def pull_challenge(self, challenge, output_dir: str) -> str:
        challenge_dir = Path(output_dir) / "check-only-204"
        challenge_dir.mkdir(parents=True, exist_ok=True)
        (challenge_dir / "metadata.yml").write_text(
            yaml.safe_dump(
                {
                    "name": "Check Only",
                    "category": "misc",
                    "description": "skip",
                    "platform": "lingxu-event-ctf",
                    "event_id": 42,
                    "platform_challenge_id": 204,
                    "unsupported_reason": "check mode is not supported in v1",
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return str(challenge_dir)

    async def prepare_challenge(self, challenge_dir: str) -> None:
        return None

    async def submit_flag(self, challenge_ref, flag: str):
        raise AssertionError("not used")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_do_spawn_swarm_skips_unsupported_challenge(tmp_path) -> None:
    deps = CoordinatorDeps(
        ctfd=UnsupportedPlatform(),
        cost_tracker=CostTracker(),
        settings=Settings(),
        model_specs=["codex/gpt-5.4-mini"],
        challenges_root=str(tmp_path),
    )

    message = await do_spawn_swarm(deps, "Check Only")

    assert "skipped" in message
    assert deps.swarms == {}
```

- [ ] **Step 2: 运行测试，确认当前实现还不会落盘 metadata 扩展字段**

Run: `uv run pytest tests/test_lingxu_event_ctf_client.py tests/test_coordinator_platform_flow.py -v`  
Expected: FAIL with `NotImplementedError` or missing `platform_challenge_id`

- [ ] **Step 3: 写最小实现，完成详情映射、附件下载和 unsupported 跳过**

```python
# backend/prompts.py
@dataclass
class ChallengeMeta:
    name: str = "Unknown"
    category: str = ""
    value: int = 0
    description: str = ""
    tags: list[str] = field(default_factory=list)
    connection_info: str = ""
    hints: list[dict[str, Any]] = field(default_factory=list)
    solves: int = 0
    platform: str = ""
    platform_url: str = ""
    event_id: int | None = None
    platform_challenge_id: int | None = None
    test_type: int | None = None
    answer_mode: int | None = None
    requires_env_start: bool = False
    unsupported_reason: str = ""

    @classmethod
    def from_yaml(cls, path: str | Path) -> ChallengeMeta:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(
            name=data.get("name", "Unknown"),
            category=data.get("category", ""),
            value=data.get("value", 0),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            connection_info=data.get("connection_info", ""),
            hints=data.get("hints", []),
            solves=data.get("solves", 0),
            platform=data.get("platform", ""),
            platform_url=data.get("platform_url", ""),
            event_id=data.get("event_id"),
            platform_challenge_id=data.get("platform_challenge_id"),
            test_type=data.get("test_type"),
            answer_mode=data.get("answer_mode"),
            requires_env_start=bool(data.get("requires_env_start", False)),
            unsupported_reason=data.get("unsupported_reason", ""),
        )
```

```python
# backend/platforms/lingxu_event_ctf.py
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from markdownify import markdownify as html2md


def _slugify(name: str) -> str:
    slug = re.sub(r'[<>:"/\\\\|?*.\\x00-\\x1f]', "", name.lower().strip())
    slug = re.sub(r"[\\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-") or "challenge"


class LingxuEventCTFClient:
    async def _download(self, raw_url: str) -> bytes:
        client = await self._ensure_client()
        resp = await client.get(raw_url)
        resp.raise_for_status()
        return resp.content

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        detail = await self._get(f"/event/{self.event_id}/ctf/{challenge['id']}/info/")
        name = detail.get("name") or challenge["name"]
        slug = f"{_slugify(name)}-{challenge['id']}"
        challenge_dir = Path(output_dir) / slug
        distfiles_dir = challenge_dir / "distfiles"
        distfiles_dir.mkdir(parents=True, exist_ok=True)

        description = detail.get("desc") or ""
        try:
            description = html2md(description, heading_style="atx", escape_asterisks=False).strip()
        except Exception:
            description = str(description).strip()

        attachment = detail.get("attachment") or ""
        if attachment:
            raw_url = attachment if attachment.startswith("http") else f"{self.base_url.rstrip('/')}/{attachment.lstrip('/')}"
            filename = Path(urlparse(raw_url).path).name or "attachment.bin"
            (distfiles_dir / filename).write_bytes(await self._download(raw_url))

        metadata = {
            "name": name,
            "category": challenge.get("category", ""),
            "description": description,
            "value": detail.get("score", challenge.get("value", 0)),
            "connection_info": detail.get("link_path") or "",
            "tags": [],
            "solves": detail.get("parse_count", 0),
            "platform": "lingxu-event-ctf",
            "platform_url": self.base_url.rstrip("/"),
            "event_id": self.event_id,
            "platform_challenge_id": challenge["id"],
            "test_type": detail.get("task_type"),
            "answer_mode": detail.get("answer_mode"),
            "requires_env_start": detail.get("task_type") == 1,
        }
        if detail.get("answer_mode") == 2:
            metadata["unsupported_reason"] = "check mode is not supported in v1"

        (challenge_dir / "metadata.yml").write_text(
            yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return str(challenge_dir)
```

```python
# backend/agents/coordinator_core.py
    if challenge_name not in deps.challenge_dirs:
        challenges = await deps.ctfd.fetch_all_challenges()
        ch_data = next((c for c in challenges if c.get("name") == challenge_name), None)
        if not ch_data:
            return f"Challenge '{challenge_name}' not found on platform"
        output_dir = str(Path(deps.challenges_root))
        ch_dir = await deps.ctfd.pull_challenge(ch_data, output_dir)
        deps.challenge_dirs[challenge_name] = ch_dir
        deps.challenge_metas[challenge_name] = ChallengeMeta.from_yaml(Path(ch_dir) / "metadata.yml")

    meta = deps.challenge_metas[challenge_name]
    if meta.unsupported_reason:
        logger.info("challenge_skipped_unsupported name=%s reason=%s", challenge_name, meta.unsupported_reason)
        return f"Challenge '{challenge_name}' skipped: {meta.unsupported_reason}"
```

- [ ] **Step 4: 重新运行测试，确认 metadata 映射和 unsupported 跳过都通过**

Run: `uv run pytest tests/test_lingxu_event_ctf_client.py tests/test_coordinator_platform_flow.py -v`  
Expected: PASS with `5 passed`

- [ ] **Step 5: 提交这一小步**

```bash
git add backend/platforms/lingxu_event_ctf.py backend/prompts.py backend/agents/coordinator_core.py tests/test_lingxu_event_ctf_client.py tests/test_coordinator_platform_flow.py
git commit -m "feat: materialize Lingxu challenges with platform metadata"
```

### Task 5: 实现环境型题 preflight 与基于 metadata 的 flag 提交流程

**Files:**
- Modify: `backend/platforms/lingxu_event_ctf.py`
- Modify: `backend/ctfd.py`
- Modify: `backend/agents/coordinator_core.py`
- Modify: `backend/agents/swarm.py`
- Modify: `backend/agents/solver.py`
- Modify: `backend/agents/claude_solver.py`
- Modify: `backend/agents/codex_solver.py`
- Modify: `backend/tools/core.py`
- Modify: `backend/tools/flag.py`
- Modify: `tests/test_lingxu_event_ctf_client.py`
- Modify: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 先写失败的 preflight 和提交测试**

```python
import httpx
import pytest
import yaml

from backend.platforms.lingxu_event_ctf import LingxuEventCTFClient
from backend.prompts import ChallengeMeta
from backend.tools.core import do_submit_flag


@pytest.mark.asyncio
async def test_prepare_challenge_runs_begin_run_addr_and_updates_connection_info(tmp_path) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("x-csrftoken")))
        if request.url.path.endswith("/begin/"):
            return httpx.Response(200, json={"status": 1, "msg": "开启成功"})
        if request.url.path.endswith("/run/"):
            return httpx.Response(200, json={"status": 1, "msg": "环境启动成功"})
        if request.url.path.endswith("/addr/"):
            return httpx.Response(
                200,
                json={
                    "domain_addr": "",
                    "ext_id": "10.10.10.10:31337",
                    "instance_id": "env-1",
                    "name": "Pwn1",
                },
            )
        raise AssertionError(request.url.path)

    challenge_dir = tmp_path / "pwn1-137"
    challenge_dir.mkdir()
    (challenge_dir / "metadata.yml").write_text(
        yaml.safe_dump(
            {
                "name": "Pwn1",
                "category": "pwn",
                "description": "1",
                "platform": "lingxu-event-ctf",
                "platform_url": "https://lx.example.com",
                "event_id": 42,
                "platform_challenge_id": 137,
                "test_type": 1,
                "answer_mode": 1,
                "requires_env_start": True,
                "connection_info": "",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    client = LingxuEventCTFClient(
        base_url="https://lx.example.com",
        event_id=42,
        cookie="sessionid=sid123; csrftoken=csrf456",
        transport=httpx.MockTransport(handler),
    )

    await client.prepare_challenge(str(challenge_dir))
    meta = yaml.safe_load((challenge_dir / "metadata.yml").read_text(encoding="utf-8"))

    assert meta["connection_info"] == "nc 10.10.10.10 31337"
    assert calls == [
        ("POST", "/event/42/ctf/137/begin/", "csrf456"),
        ("POST", "/event/42/ctf/137/run/", "csrf456"),
        ("GET", "/event/42/ctf/137/addr/", None),
    ]


@pytest.mark.asyncio
async def test_do_submit_flag_uses_challenge_meta_for_lingxu() -> None:
    class RecordingPlatform:
        def __init__(self) -> None:
            self.last_ref = None

        async def submit_flag(self, challenge_ref, flag: str):
            self.last_ref = challenge_ref
            return type("SubmitResult", (), {"status": "correct", "display": f"CORRECT {flag}"})()

    meta = ChallengeMeta(
        name="Pwn1",
        platform="lingxu-event-ctf",
        event_id=42,
        platform_challenge_id=137,
    )
    platform = RecordingPlatform()

    display, confirmed = await do_submit_flag(platform, meta, "flag{ok}")

    assert display == "CORRECT flag{ok}"
    assert confirmed is True
    assert platform.last_ref is meta
```

```python
from pathlib import Path

import pytest
import yaml

from backend.agents.coordinator_core import do_spawn_swarm
from backend.config import Settings
from backend.cost_tracker import CostTracker
from backend.deps import CoordinatorDeps


class PreflightFailPlatform:
    async def validate_access(self) -> None:
        return None

    async def fetch_challenge_stubs(self):
        return [{"id": 137, "name": "Pwn1"}]

    async def fetch_all_challenges(self):
        return [{"id": 137, "name": "Pwn1"}]

    async def fetch_solved_names(self):
        return set()

    async def pull_challenge(self, challenge, output_dir: str) -> str:
        challenge_dir = Path(output_dir) / "pwn1-137"
        challenge_dir.mkdir(parents=True, exist_ok=True)
        (challenge_dir / "metadata.yml").write_text(
            yaml.safe_dump(
                {
                    "name": "Pwn1",
                    "category": "pwn",
                    "description": "1",
                    "platform": "lingxu-event-ctf",
                    "event_id": 42,
                    "platform_challenge_id": 137,
                    "requires_env_start": True,
                    "connection_info": "",
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return str(challenge_dir)

    async def prepare_challenge(self, challenge_dir: str) -> None:
        raise RuntimeError("addr missing")

    async def submit_flag(self, challenge_ref, flag: str):
        raise AssertionError("not used")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_do_spawn_swarm_skips_when_preflight_fails(tmp_path) -> None:
    deps = CoordinatorDeps(
        ctfd=PreflightFailPlatform(),
        cost_tracker=CostTracker(),
        settings=Settings(),
        model_specs=["codex/gpt-5.4-mini"],
        challenges_root=str(tmp_path),
    )

    message = await do_spawn_swarm(deps, "Pwn1")

    assert "preflight_failed" in message
    assert deps.swarms == {}
```

- [ ] **Step 2: 运行测试，确认 preflight 和 metadata 提交现在还不通**

Run: `uv run pytest tests/test_lingxu_event_ctf_client.py tests/test_coordinator_platform_flow.py -v`  
Expected: FAIL with missing `connection_info` update or `platform.last_ref` not equal to `ChallengeMeta`

- [ ] **Step 3: 写最小实现，完成 preflight、metadata 提交和兼容 CTFd 的空实现**

```python
# backend/platforms/lingxu_event_ctf.py
from pathlib import Path
from typing import Any

import yaml

from backend.ctfd import SubmitResult


class LingxuEventCTFClient:
    def _write_json_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-CSRFToken": self._csrf_token(),
        }

    def _format_connection_info(self, data: dict[str, Any]) -> str:
        ext_id = str(data.get("ext_id") or "").strip()
        domain = str(data.get("domain_addr") or "").strip()
        if domain.startswith(("http://", "https://")):
            return domain
        if ext_id and ":" in ext_id and " " not in ext_id:
            host, port = ext_id.split(":", 1)
            return f"nc {host} {port}"
        if ext_id and domain:
            return f"{domain}\n{ext_id}"
        return ext_id or domain

    async def prepare_challenge(self, challenge_dir: str) -> None:
        meta_path = Path(challenge_dir) / "metadata.yml"
        metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        if not metadata.get("requires_env_start"):
            return
        if metadata.get("connection_info"):
            return

        challenge_id = metadata["platform_challenge_id"]
        client = await self._ensure_client()
        begin_resp = await client.post(
            f"/event/{self.event_id}/ctf/{challenge_id}/begin/",
            headers=self._write_json_headers(),
            json={},
        )
        begin_resp.raise_for_status()

        run_resp = await client.post(
            f"/event/{self.event_id}/ctf/{challenge_id}/run/",
            headers=self._write_json_headers(),
            json={},
        )
        run_resp.raise_for_status()

        addr_resp = await client.get(f"/event/{self.event_id}/ctf/{challenge_id}/addr/")
        addr_resp.raise_for_status()
        connection_info = self._format_connection_info(addr_resp.json())
        if not connection_info:
            raise RuntimeError("preflight addr returned empty connection info")

        metadata["connection_info"] = connection_info
        meta_path.write_text(
            yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    async def submit_flag(self, challenge_ref, flag: str) -> SubmitResult:
        challenge_id = getattr(challenge_ref, "platform_challenge_id", None)
        if not challenge_id and isinstance(challenge_ref, dict):
            challenge_id = challenge_ref.get("platform_challenge_id")
        if not challenge_id:
            raise RuntimeError("Lingxu submit requires platform_challenge_id")

        client = await self._ensure_client()
        resp = await client.post(
            f"/event/{self.event_id}/ctf/{challenge_id}/flag/",
            headers=self._write_json_headers(),
            json={"flag": flag.strip()},
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == 1:
            return SubmitResult("correct", str(data.get("score", "")), f'CORRECT — "{flag.strip()}" accepted.')
        message = data.get("error") or data.get("msg") or str(data)
        if "已提交了正确的Flag" in message:
            return SubmitResult("already_solved", message, f'ALREADY SOLVED — "{flag.strip()}" accepted. {message}')
        if "错误" in message:
            return SubmitResult("incorrect", message, f'INCORRECT — "{flag.strip()}" rejected. {message}')
        return SubmitResult("unknown", message, f"Unknown status: {message}")
```

```python
# backend/ctfd.py
from backend.prompts import ChallengeMeta


    async def submit_flag(self, challenge_ref: ChallengeMeta | str, flag: str) -> SubmitResult:
        challenge_name = challenge_ref.name if isinstance(challenge_ref, ChallengeMeta) else challenge_ref
        challenge_id = await self.get_challenge_id(challenge_name)
        resp = await self._post(
            "/challenges/attempt",
            {"challenge_id": challenge_id, "submission": flag},
        )
```

```python
# backend/agents/coordinator_core.py
    meta = deps.challenge_metas[challenge_name]
    if meta.requires_env_start and not meta.connection_info:
        try:
            logger.info("challenge_preflight_started name=%s", challenge_name)
            await deps.ctfd.prepare_challenge(deps.challenge_dirs[challenge_name])
            deps.challenge_metas[challenge_name] = ChallengeMeta.from_yaml(Path(deps.challenge_dirs[challenge_name]) / "metadata.yml")
        except Exception as exc:
            logger.warning("challenge_preflight_failed name=%s error=%s", challenge_name, exc)
            return f"Challenge '{challenge_name}' preflight_failed: {exc}"
```

```python
# backend/tools/core.py
async def do_submit_flag(ctfd, challenge_ref, flag: str) -> tuple[str, bool]:
    flag = flag.strip()
    if not flag:
        return "Empty flag — nothing to submit.", False

    try:
        result = await ctfd.submit_flag(challenge_ref, flag)
        is_confirmed = result.status in ("correct", "already_solved")
        return result.display, is_confirmed
    except Exception as e:
        return f"submit_flag error: {e}", False
```

```python
# backend/agents/swarm.py
            from backend.tools.core import do_submit_flag
            display, is_confirmed = await do_submit_flag(self.ctfd, self.meta, flag)
```

```python
# backend/agents/solver.py
        self.deps = SolverDeps(
            sandbox=self.sandbox,
            ctfd=ctfd,
            challenge_dir=challenge_dir,
            challenge_name=meta.name,
            challenge_ref=meta,
            workspace_dir="",
            use_vision=self.use_vision,
            cost_tracker=cost_tracker,
        )
```

```python
# backend/tools/flag.py
    if ctx.deps.submit_fn:
        display, is_confirmed = await ctx.deps.submit_fn(flag)
    else:
        challenge_ref = ctx.deps.challenge_ref or ctx.deps.challenge_name
        display, is_confirmed = await do_submit_flag(ctx.deps.ctfd, challenge_ref, flag)
```

```python
# backend/agents/claude_solver.py
display, confirmed = await do_submit_flag(self.ctfd, self.meta, flag_val)
```

```python
# backend/agents/codex_solver.py
display, is_confirmed = await do_submit_flag(self.ctfd, self.meta, flag)
```

- [ ] **Step 4: 重新运行测试，确认 preflight 和提交链路通过**

Run: `uv run pytest tests/test_lingxu_event_ctf_client.py tests/test_coordinator_platform_flow.py -v`  
Expected: PASS with `8 passed`

- [ ] **Step 5: 提交这一小步**

```bash
git add backend/platforms/lingxu_event_ctf.py backend/ctfd.py backend/agents/coordinator_core.py backend/agents/swarm.py backend/agents/solver.py backend/agents/claude_solver.py backend/agents/codex_solver.py backend/tools/core.py backend/tools/flag.py tests/test_lingxu_event_ctf_client.py tests/test_coordinator_platform_flow.py
git commit -m "feat: add Lingxu preflight and metadata-based submission"
```

### Task 6: 在英文参数 CLI 上接入凌虚平台，并补 README 与最终验证

**Files:**
- Modify: `backend/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 先写失败的英文参数 CLI 测试**

```python
from pathlib import Path

from click.testing import CliRunner

from backend.cli import main


def test_cli_rejects_lingxu_without_cookie() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "--platform", "lingxu-event-ctf",
            "--platform-url", "https://lx.example.com",
            "--lingxu-event-id", "42",
            "--no-submit",
        ],
    )

    assert result.exit_code != 0
    assert "lingxu_cookie" in result.output


def test_cli_accepts_lingxu_cookie_file(tmp_path: Path, monkeypatch) -> None:
    cookie_file = tmp_path / "lingxu.cookie"
    cookie_file.write_text("sessionid=sid123; csrftoken=csrf456", encoding="utf-8")

    async def fake_run_coordinator(*args, **kwargs):
        return None

    monkeypatch.setattr("backend.cli._run_coordinator", fake_run_coordinator)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--platform", "lingxu-event-ctf",
            "--platform-url", "https://lx.example.com",
            "--lingxu-event-id", "42",
            "--lingxu-cookie-file", str(cookie_file),
            "--no-submit",
        ],
    )

    assert result.exit_code == 0
```

- [ ] **Step 2: 运行测试，确认 CLI 还没有这些平台参数或帮助文本不符合预期**

Run: `uv run pytest tests/test_cli.py -v`  
Expected: FAIL because Lingxu options or help text are not yet wired through the English-option CLI

- [ ] **Step 3: 在保留中文帮助与 README 成果的前提下，把凌虚平台参数接进英文 CLI，并补 README**

```python
# backend/cli.py
@click.command()
@click.option("--platform", default=None, type=click.Choice(["ctfd", "lingxu-event-ctf"]), help="题目来源平台")
@click.option("--platform-url", default=None, help="平台根地址；凌虚模式必填")
@click.option("--lingxu-event-id", default=None, type=int, help="凌虚赛事 ID")
@click.option("--lingxu-cookie", default=None, help="浏览器导出的 Cookie 原文")
@click.option("--lingxu-cookie-file", type=click.Path(path_type=Path, dir_okay=False), default=None, help="包含 sessionid 与 csrftoken 的 Cookie 文件")
def main(
    platform: str | None,
    platform_url: str | None,
    lingxu_event_id: int | None,
    lingxu_cookie: str | None,
    lingxu_cookie_file: Path | None,
    # 其余现有英文参数保持不变
) -> None:
    _setup_logging(verbose)

    settings = Settings(sandbox_image=image)
    if platform:
        settings.platform = platform
    if platform_url:
        settings.platform_url = platform_url
    if lingxu_event_id is not None:
        settings.lingxu_event_id = lingxu_event_id
    if lingxu_cookie:
        settings.lingxu_cookie = lingxu_cookie
    if lingxu_cookie_file:
        settings.lingxu_cookie_file = str(lingxu_cookie_file)
    if ctfd_url:
        settings.ctfd_url = ctfd_url
    if ctfd_token:
        settings.ctfd_token = ctfd_token
    validate_platform_settings(settings)
```

~~~markdown
<!-- README.md -->
## 凌虚赛事 CTF 接入

当前支持范围：

- 仅支持 `赛事 CTF`
- 仅支持 `FLAG` 模式题
- 支持环境型、外链型、附件型
- `check` 模式会被识别并跳过，不会启动 swarm

使用前提：

1. 先在浏览器中登录凌虚平台并进入目标赛事。
2. 导出当前站点 Cookie，至少包含 `sessionid` 与 `csrftoken`。
3. 推荐把 Cookie 写入 `.secrets/lingxu.cookie`。

示例：

~~~bash
uv run ctf-solve \
  --platform lingxu-event-ctf \
  --platform-url https://match.example.com \
  --lingxu-event-id 42 \
  --lingxu-cookie-file .secrets/lingxu.cookie \
  --max-challenges 3 \
  --no-submit \
  -v
~~~
~~~

- [ ] **Step 4: 跑最终验证，确认 CLI、平台测试和静态检查都通过**

Run: `uv run pytest tests/test_platform_factory.py tests/test_coordinator_platform_flow.py tests/test_lingxu_event_ctf_client.py tests/test_cli.py -v`  
Expected: PASS with all tests passing

Run: `uv run ruff check backend tests`  
Expected: PASS with no lint errors

Run: `uv run ctf-solve --platform lingxu-event-ctf --platform-url https://match.example.com --lingxu-event-id 42 --lingxu-cookie-file .secrets/lingxu.cookie --max-challenges 1 --no-submit -v`  
Expected: 真实联调时日志包含 `platform_login_validated`、新题列表和 unsupported / preflight 相关日志；如果 Cookie 失效，应在启动期直接失败而不是进入 coordinator 主循环

- [ ] **Step 5: 提交这一小步**

```bash
git add backend/cli.py README.md tests/test_cli.py
git commit -m "feat: wire Lingxu event CTF through english CLI options"
```

## Spec Coverage Check

- 平台抽象层：Task 1 和 Task 2 落地 `CompetitionPlatformClient`、工厂和通用 poller。
- 凌虚认证模型：Task 3 落地 Cookie / CSRF、赛事访问校验。
- 题目落地与 metadata 扩展字段：Task 4 覆盖 `platform`、`event_id`、`platform_challenge_id`、`test_type`、`answer_mode`、`requires_env_start`、`unsupported_reason`。
- 环境题 preflight：Task 5 覆盖 `begin -> run -> addr -> metadata 更新`。
- 提交流程：Task 5 覆盖基于 `ChallengeMeta` 的提交，不再依赖纯题目名。
- CLI 与配置：Task 1 负责内部配置模型，Task 6 负责英文参数 CLI 接线与中文帮助文本。
- 日志与真实联调：Task 6 最终 smoke 命令要求观察启动校验、unsupported 和 preflight 日志。

## 风险提示

- `backend/cli.py` 与手动导入线程有共享改动面，必须在 Task 6 前先合并对方的中文帮助方案，再把凌虚参数增补进去。
- `tests/test_cli.py` 依赖最终的英文 CLI 参数名；如果另一线程对命名做了微调，优先对齐最终 CLI，而不是保留这里的字面值。
- 真实 Cookie 联调时不要把 `.secrets/lingxu.cookie` 提交进仓库；只在本地 smoke 使用。
