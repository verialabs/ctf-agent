# Policy, Memory, and Knowledge Control Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `poller -> coordinator -> swarm -> solver` 骨架上落地显式控制面，新增结构化状态、单题 Working Memory、跨题 Knowledge Store、规则优先的 Policy Engine，以及 provider-neutral 的 advisor 接口。

**Architecture:** 第一阶段先引入 `backend/control/` 包，承接运行时状态、动作模型、记忆、知识与策略，不重写 solver 和 swarm 主链路；第二阶段把 `coordinator_loop` 升级为“状态更新 + policy tick + action 执行”控制内核；第三阶段把 `azure`、`claude`、`codex` 三类 coordinator 收缩成 advisor 适配器。整个过程保持 `coordinator_core` 作为动作执行库，避免复制整场逻辑。

**Tech Stack:** Python 3.14, asyncio, dataclasses, pydantic-ai, Claude Agent SDK, Codex app-server, pytest, click, rich

---

## File Structure

- `backend/control/__init__.py`
  责任：导出控制面公共类型，避免调用方直接跨模块 import 实现细节。
- `backend/control/state.py`
  责任：定义 `CompetitionState`、`ChallengeState`、`SwarmState` 以及从 `deps + poller` 构建状态快照的帮助函数。
- `backend/control/actions.py`
  责任：定义 `SpawnSwarm`、`BumpSolver`、`BroadcastKnowledge`、`HoldChallenge`、`RetryChallenge`、`MarkChallengeSkipped` 等标准动作对象。
- `backend/control/working_memory.py`
  责任：定义单题 `ChallengeWorkingMemory`、trace 提炼规则、去重规则与摘要生成。
- `backend/control/knowledge_store.py`
  责任：定义 `KnowledgeEntry`、晋升规则、匹配规则和已应用记录。
- `backend/control/policy_engine.py`
  责任：根据状态、记忆和知识输出动作列表；规则先行，advisor 建议后置。
- `backend/control/advisor.py`
  责任：定义 provider-neutral 的 `AdvisorContext`、`AdvisorSuggestion`、`CoordinatorAdvisor` 协议。
- `backend/deps.py`
  责任：把 `runtime_state`、`working_memory_store`、`knowledge_store`、`policy_engine` 注入 coordinator deps。
- `backend/agents/coordinator_loop.py`
  责任：从事件泵升级为控制内核，负责状态更新、policy tick、动作执行、advisor 调用。
- `backend/agents/coordinator_core.py`
  责任：继续作为动作执行库；新增 action 执行桥接函数，但不回退成决策层。
- `backend/agents/azure_coordinator.py`
  责任：从直接调工具的 coordinator 演进为 Azure advisor 适配器。
- `backend/agents/claude_coordinator.py`
  责任：从 Claude SDK 总控演进为 Claude advisor 适配器。
- `backend/agents/codex_coordinator.py`
  责任：从 Codex app-server 总控演进为 Codex advisor 适配器。
- `tests/test_control_state.py`
  责任：验证结构化状态和动作模型。
- `tests/test_working_memory.py`
  责任：验证 trace 提炼、去重和摘要。
- `tests/test_knowledge_store.py`
  责任：验证知识晋升、匹配和已应用保护。
- `tests/test_policy_engine.py`
  责任：验证规则优先的动作生成。
- `tests/test_coordinator_platform_flow.py`
  责任：保留整场流回归，并覆盖新控制内核与 advisor 接线。
- `README.md`
  责任：补充新控制架构的运行方式和边界说明。

---

### Task 1: 建立 `backend/control` 包与状态、动作基础模型

**Files:**
- Create: `backend/control/__init__.py`
- Create: `backend/control/state.py`
- Create: `backend/control/actions.py`
- Create: `tests/test_control_state.py`
- Test: `tests/test_control_state.py`

- [ ] **Step 1: 先写状态与动作模型的失败测试**

```python
from backend.control.actions import BumpSolver, HoldChallenge, SpawnSwarm
from backend.control.state import ChallengeState, CompetitionState, SwarmState


def test_competition_state_counts_only_running_swarms() -> None:
    state = CompetitionState(
        known_challenges={"echo", "rsa"},
        known_solved={"echo"},
        swarms={
            "rsa": SwarmState(
                challenge_name="rsa",
                status="running",
                running_models=["azure/gpt-5.4"],
            ),
            "echo": SwarmState(
                challenge_name="echo",
                status="finished",
                running_models=[],
            ),
        },
    )

    assert state.active_swarm_count == 1


def test_spawn_and_bump_actions_expose_stable_kind() -> None:
    spawn = SpawnSwarm(challenge_name="rsa", priority=10, reason="new unsolved challenge")
    bump = BumpSolver(
        challenge_name="rsa",
        model_spec="azure/gpt-5.4",
        guidance="Switch to lattice attack",
        reason="stalled with open hypothesis",
    )
    hold = HoldChallenge(challenge_name="echo", reason="cooldown", retry_after_seconds=60)

    assert spawn.kind == "spawn_swarm"
    assert bump.kind == "bump_solver"
    assert hold.kind == "hold_challenge"
```

- [ ] **Step 2: 运行测试，确认当前缺少控制面模块**

Run: `uv run pytest tests/test_control_state.py -q`  
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.control'`

- [ ] **Step 3: 用最小实现补齐状态与动作模型**

```python
# backend/control/state.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ChallengeState:
    challenge_name: str
    status: Literal["unknown", "pending", "running", "solved", "skipped", "error"] = "unknown"
    category: str = ""
    value: float = 0.0
    requires_env_start: bool = False
    unsupported_reason: str = ""
    last_materialized_at: float | None = None


@dataclass
class SwarmState:
    challenge_name: str
    status: Literal["idle", "running", "finished", "cancelled", "error"] = "idle"
    running_models: list[str] = field(default_factory=list)
    last_bump_at: float | None = None
    bump_count: int = 0
    last_progress_at: float | None = None
    last_error: str = ""
    step_count: int = 0
    cost_usd: float = 0.0
    winner_model: str = ""
    applied_knowledge_ids: set[str] = field(default_factory=set)


@dataclass
class CompetitionState:
    known_challenges: set[str] = field(default_factory=set)
    known_solved: set[str] = field(default_factory=set)
    challenges: dict[str, ChallengeState] = field(default_factory=dict)
    swarms: dict[str, SwarmState] = field(default_factory=dict)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    global_cost_usd: float = 0.0
    last_poll_at: float | None = None
    operator_messages: list[str] = field(default_factory=list)

    @property
    def active_swarm_count(self) -> int:
        return sum(1 for swarm in self.swarms.values() if swarm.status == "running")
```

```python
# backend/control/actions.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpawnSwarm:
    challenge_name: str
    priority: int
    reason: str
    kind: str = "spawn_swarm"


@dataclass(frozen=True)
class BumpSolver:
    challenge_name: str
    model_spec: str
    guidance: str
    reason: str
    kind: str = "bump_solver"


@dataclass(frozen=True)
class BroadcastKnowledge:
    challenge_name: str
    message: str
    source: str
    knowledge_id: str = ""
    kind: str = "broadcast_knowledge"


@dataclass(frozen=True)
class HoldChallenge:
    challenge_name: str
    reason: str
    retry_after_seconds: int
    kind: str = "hold_challenge"


@dataclass(frozen=True)
class RetryChallenge:
    challenge_name: str
    reason: str
    kind: str = "retry_challenge"


@dataclass(frozen=True)
class MarkChallengeSkipped:
    challenge_name: str
    reason: str
    kind: str = "mark_challenge_skipped"
```

- [ ] **Step 4: 运行测试，确认基础模型通过**

Run: `uv run pytest tests/test_control_state.py -q`  
Expected: PASS

- [ ] **Step 5: 提交基础控制面骨架**

```bash
git add backend/control/__init__.py backend/control/state.py backend/control/actions.py tests/test_control_state.py
git commit -m "新增控制面状态与动作模型"
```

### Task 2: 把结构化状态接入 `CoordinatorDeps` 和 `coordinator_loop`

**Files:**
- Modify: `backend/deps.py`
- Modify: `backend/agents/coordinator_loop.py`
- Modify: `backend/control/state.py`
- Modify: `tests/test_control_state.py`
- Modify: `tests/test_coordinator_platform_flow.py`
- Test: `tests/test_control_state.py`
- Test: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 先写失败测试，锁定状态快照行为**

```python
from backend.control.state import build_runtime_state_snapshot
from types import SimpleNamespace


class DummyEvent:
    def __init__(self, cancelled: bool) -> None:
        self._cancelled = cancelled

    def is_set(self) -> bool:
        return self._cancelled


def test_build_runtime_state_snapshot_maps_running_swarm_and_result() -> None:
    deps = SimpleNamespace(
        results={"rsa": {"solve_status": "flag_found", "winner_model": "azure/gpt-5.4"}},
        swarms={},
        cost_tracker=SimpleNamespace(total_cost_usd=1.25),
    )
    deps.swarms["rsa"] = SimpleNamespace(
        cancel_event=DummyEvent(cancelled=False),
        solvers={
            "azure/gpt-5.4": SimpleNamespace(model_id="gpt-5.4", _step_count=[7]),
        },
    )
    poller = SimpleNamespace(
        known_challenges={"rsa", "echo"},
        known_solved={"echo"},
    )

    snapshot = build_runtime_state_snapshot(deps, poller, now=123.0)

    assert snapshot.global_cost_usd == 1.25
    assert snapshot.known_challenges == {"rsa", "echo"}
    assert snapshot.known_solved == {"echo"}
    assert snapshot.swarms["rsa"].status == "running"
    assert snapshot.swarms["rsa"].step_count == 7
    assert snapshot.results["rsa"]["winner_model"] == "azure/gpt-5.4"
```

- [ ] **Step 2: 运行测试，确认快照构建器尚未实现**

Run: `uv run pytest tests/test_control_state.py::test_build_runtime_state_snapshot_maps_running_swarm_and_result -q`  
Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: 实现状态快照并注入到 `CoordinatorDeps`**

```python
# backend/deps.py
from dataclasses import dataclass, field
from typing import Any

from backend.cost_tracker import CostTracker
from backend.platforms.base import CompetitionPlatformClient
from backend.control.state import CompetitionState


@dataclass
class CoordinatorDeps:
    ctfd: CompetitionPlatformClient
    cost_tracker: CostTracker
    settings: Any
    model_specs: list[str] = field(default_factory=list)
    challenges_root: str = "challenges"
    no_submit: bool = False
    max_concurrent_challenges: int = 10
    runtime_state: CompetitionState = field(default_factory=CompetitionState)
```

```python
# backend/control/state.py
def build_runtime_state_snapshot(deps: CoordinatorDeps, poller: CompetitionPoller, now: float) -> CompetitionState:
    snapshot = CompetitionState(
        known_challenges=set(poller.known_challenges),
        known_solved=set(poller.known_solved),
        results=dict(deps.results),
        global_cost_usd=deps.cost_tracker.total_cost_usd,
        last_poll_at=now,
    )
    for name, swarm in deps.swarms.items():
        status = "running" if not swarm.cancel_event.is_set() else "finished"
        snapshot.swarms[name] = SwarmState(
            challenge_name=name,
            status=status,
            running_models=sorted(swarm.solvers.keys()),
            step_count=sum(getattr(solver, "_step_count", [0])[0] for solver in swarm.solvers.values()),
            cost_usd=sum(
                getattr(deps.cost_tracker, "by_agent", {}).get(
                    f"{name}/{solver.model_id}",
                    type("Usage", (), {"cost_usd": 0.0})(),
                ).cost_usd
                for solver in swarm.solvers.values()
            ),
        )
    return snapshot
```

```python
# backend/agents/coordinator_loop.py
now = asyncio.get_event_loop().time()
deps.runtime_state = build_runtime_state_snapshot(deps, poller, now)
```

- [ ] **Step 4: 运行状态和整场流回归**

Run: `uv run pytest tests/test_control_state.py tests/test_coordinator_platform_flow.py -q`  
Expected: PASS

- [ ] **Step 5: 提交结构化状态接线**

```bash
git add backend/deps.py backend/control/state.py backend/agents/coordinator_loop.py tests/test_control_state.py tests/test_coordinator_platform_flow.py
git commit -m "接入控制面运行时状态快照"
```

### Task 3: 落地单题 `Working Memory` 与 trace 提炼

**Files:**
- Create: `backend/control/working_memory.py`
- Create: `tests/test_working_memory.py`
- Modify: `backend/deps.py`
- Modify: `backend/agents/coordinator_loop.py`
- Test: `tests/test_working_memory.py`
- Test: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 先写失败测试，锁定记忆提炼与去重规则**

```python
from backend.control.working_memory import ChallengeWorkingMemory, WorkingMemoryStore


def test_working_memory_dedupes_repeated_failed_hypothesis() -> None:
    store = WorkingMemoryStore()
    store.apply_trace_events(
        challenge_name="echo",
        events=[
            {"type": "tool_result", "tool": "submit_flag", "result": "INCORRECT"},
            {"type": "tool_result", "tool": "submit_flag", "result": "INCORRECT"},
            {"type": "bump", "insights": "Try format string offset 6"},
        ],
    )

    memory = store.get("echo")

    assert memory.failed_hypotheses == ["submit_flag returned INCORRECT"]
    assert memory.last_guidance == ["Try format string offset 6"]
```

```python
def test_working_memory_keeps_verified_findings_and_artifacts() -> None:
    store = WorkingMemoryStore()
    store.apply_trace_events(
        challenge_name="rsa",
        events=[
            {"type": "tool_result", "tool": "read_file", "result": "/challenge/distfiles/pub.pem"},
            {"type": "tool_result", "tool": "bash", "result": "platform rule: Lingxu env题需要先 begin/run/addr"},
            {"type": "flag_confirmed", "tool": "submit_flag"},
        ],
    )

    memory = store.get("rsa")

    assert "/challenge/distfiles/pub.pem" in memory.useful_artifacts
    assert "platform rule: Lingxu env题需要先 begin/run/addr" in memory.verified_findings
```

- [ ] **Step 2: 运行测试，确认 Working Memory 尚不存在**

Run: `uv run pytest tests/test_working_memory.py -q`  
Expected: FAIL with `ModuleNotFoundError` or missing symbols

- [ ] **Step 3: 实现 `WorkingMemoryStore` 与提炼逻辑**

```python
# backend/control/working_memory.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChallengeWorkingMemory:
    challenge_name: str
    attempted_actions: list[str] = field(default_factory=list)
    failed_hypotheses: list[str] = field(default_factory=list)
    open_hypotheses: list[str] = field(default_factory=list)
    verified_findings: list[str] = field(default_factory=list)
    useful_artifacts: list[str] = field(default_factory=list)
    last_guidance: list[str] = field(default_factory=list)

    def to_summary(self) -> str:
        return "\n".join(
            [
                f"failed_hypotheses={self.failed_hypotheses[:3]}",
                f"open_hypotheses={self.open_hypotheses[:3]}",
                f"verified_findings={self.verified_findings[:3]}",
                f"useful_artifacts={self.useful_artifacts[:3]}",
                f"last_guidance={self.last_guidance[-2:]}",
            ]
        )


class WorkingMemoryStore:
    def __init__(self) -> None:
        self._memories: dict[str, ChallengeWorkingMemory] = {}

    def get(self, challenge_name: str) -> ChallengeWorkingMemory:
        return self._memories.setdefault(challenge_name, ChallengeWorkingMemory(challenge_name))

    def apply_trace_events(self, challenge_name: str, events: list[dict]) -> ChallengeWorkingMemory:
        memory = self.get(challenge_name)
        for event in events:
            if event.get("type") == "tool_result" and event.get("tool") == "submit_flag":
                summary = f"{event['tool']} returned {event['result']}".strip()
                if summary not in memory.failed_hypotheses:
                    memory.failed_hypotheses.append(summary)
            if event.get("type") == "bump":
                insight = str(event.get("insights", "")).strip()
                if insight and insight not in memory.last_guidance:
                    memory.last_guidance.append(insight)
            if event.get("type") == "tool_result" and "/challenge/" in str(event.get("result", "")):
                artifact = str(event["result"]).strip()
                if artifact not in memory.useful_artifacts:
                    memory.useful_artifacts.append(artifact)
            if event.get("type") == "tool_result" and "platform rule:" in str(event.get("result", "")):
                finding = str(event["result"]).strip()
                if finding not in memory.verified_findings:
                    memory.verified_findings.append(finding)
        return memory
```

```python
# backend/deps.py
from dataclasses import dataclass, field
from typing import Any

from backend.cost_tracker import CostTracker
from backend.platforms.base import CompetitionPlatformClient
from backend.control.working_memory import WorkingMemoryStore


@dataclass
class CoordinatorDeps:
    ctfd: CompetitionPlatformClient
    cost_tracker: CostTracker
    settings: Any
    model_specs: list[str] = field(default_factory=list)
    challenges_root: str = "challenges"
    no_submit: bool = False
    max_concurrent_challenges: int = 10
    working_memory_store: WorkingMemoryStore = field(default_factory=WorkingMemoryStore)
```

```python
# backend/agents/coordinator_loop.py
def _load_recent_trace_events(trace_path: str, limit: int = 50) -> list[dict]:
    lines = Path(trace_path).read_text().strip().splitlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()]
```

- [ ] **Step 4: 在整场流里从 solver trace 回写 Working Memory**

```python
# backend/agents/coordinator_loop.py
for challenge_name, swarm in deps.swarms.items():
    for solver in swarm.solvers.values():
        trace_path = str(solver.tracer.path)
        trace_events = _load_recent_trace_events(trace_path)
        deps.working_memory_store.apply_trace_events(challenge_name, trace_events)
```

Run: `uv run pytest tests/test_working_memory.py tests/test_coordinator_platform_flow.py -q`  
Expected: PASS

- [ ] **Step 5: 提交 Working Memory 能力**

```bash
git add backend/control/working_memory.py backend/deps.py backend/agents/coordinator_loop.py tests/test_working_memory.py tests/test_coordinator_platform_flow.py
git commit -m "新增单题 Working Memory 提炼"
```

### Task 4: 落地 `Knowledge Store` 与知识晋升规则

**Files:**
- Create: `backend/control/knowledge_store.py`
- Create: `tests/test_knowledge_store.py`
- Modify: `backend/deps.py`
- Modify: `backend/control/working_memory.py`
- Modify: `backend/agents/coordinator_loop.py`
- Test: `tests/test_knowledge_store.py`
- Test: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 先写失败测试，锁定知识晋升与匹配边界**

```python
from backend.control.knowledge_store import KnowledgeStore
from backend.control.working_memory import ChallengeWorkingMemory


def test_promote_verified_platform_rule_from_memory() -> None:
    store = KnowledgeStore()
    memory = ChallengeWorkingMemory(
        challenge_name="hatephp",
        verified_findings=["platform rule: Lingxu env题需要先 begin/run/addr"],
    )

    promoted = store.promote_from_memory(
        challenge_name="hatephp",
        category="web",
        memory=memory,
    )

    assert len(promoted) == 1
    assert promoted[0].scope == "platform"
    assert promoted[0].kind == "platform_rule"
```

```python
def test_match_returns_category_knowledge_and_skips_applied_entry() -> None:
    store = KnowledgeStore()
    entry = store.upsert(
        scope="category",
        kind="exploit_pattern",
        content="php phar deserialization first",
        evidence="confirmed in two PHP challenges",
        confidence=0.9,
        source_challenge="hatephp",
        applicability={"category": "web"},
    )

    matched = store.match(
        category="web",
        challenge_name="web2",
        applied_ids={entry.id},
    )

    assert matched == []
```

- [ ] **Step 2: 运行测试，确认知识库尚未实现**

Run: `uv run pytest tests/test_knowledge_store.py -q`  
Expected: FAIL with `ModuleNotFoundError` or missing symbols

- [ ] **Step 3: 实现知识条目、晋升规则与匹配**

```python
# backend/control/knowledge_store.py
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str
    scope: str
    kind: str
    content: str
    evidence: str
    confidence: float
    source_challenge: str
    applicability: dict[str, str] = field(default_factory=dict)


class KnowledgeStore:
    def __init__(self) -> None:
        self._entries: dict[str, KnowledgeEntry] = {}

    def upsert(self, *, scope: str, kind: str, content: str, evidence: str, confidence: float, source_challenge: str, applicability: dict[str, str]) -> KnowledgeEntry:
        entry = KnowledgeEntry(
            id=f"knowledge-{uuid4().hex[:10]}",
            scope=scope,
            kind=kind,
            content=content,
            evidence=evidence,
            confidence=confidence,
            source_challenge=source_challenge,
            applicability=applicability,
        )
        self._entries[entry.id] = entry
        return entry

    def match(self, *, category: str, challenge_name: str, applied_ids: set[str]) -> list[KnowledgeEntry]:
        category = category.lower()
        return [
            entry
            for entry in self._entries.values()
            if entry.id not in applied_ids
            and entry.source_challenge != challenge_name
            and (
                entry.applicability.get("category", "").lower() == category
                or entry.applicability.get("platform") == "lingxu-event-ctf"
            )
        ]

    def promote_from_memory(self, *, challenge_name: str, category: str, memory: ChallengeWorkingMemory) -> list[KnowledgeEntry]:
        promoted: list[KnowledgeEntry] = []
        for finding in memory.verified_findings:
            if finding.startswith("platform rule:"):
                promoted.append(
                    self.upsert(
                        scope="platform",
                        kind="platform_rule",
                        content=finding,
                        evidence=f"verified in {challenge_name}",
                        confidence=0.8,
                        source_challenge=challenge_name,
                        applicability={"platform": "lingxu-event-ctf"},
                    )
                )
            elif finding.startswith("category rule:"):
                promoted.append(
                    self.upsert(
                        scope="category",
                        kind="exploit_pattern",
                        content=finding,
                        evidence=f"verified in {challenge_name}",
                        confidence=0.7,
                        source_challenge=challenge_name,
                        applicability={"category": category.lower()},
                    )
                )
        return promoted

    def summary_for(self, challenge_name: str, category: str, applied_ids: set[str] | None = None) -> str:
        matched = self.match(
            category=category,
            challenge_name=challenge_name,
            applied_ids=applied_ids or set(),
        )
        return "\n".join(entry.content for entry in matched[:3])
```

- [ ] **Step 4: 把知识库注入 `CoordinatorDeps` 并在 loop 中晋升知识**

```python
# backend/deps.py
from dataclasses import dataclass, field
from typing import Any

from backend.cost_tracker import CostTracker
from backend.platforms.base import CompetitionPlatformClient
from backend.control.knowledge_store import KnowledgeStore


@dataclass
class CoordinatorDeps:
    ctfd: CompetitionPlatformClient
    cost_tracker: CostTracker
    settings: Any
    model_specs: list[str] = field(default_factory=list)
    challenges_root: str = "challenges"
    no_submit: bool = False
    max_concurrent_challenges: int = 10
    knowledge_store: KnowledgeStore = field(default_factory=KnowledgeStore)
```

```python
# backend/agents/coordinator_loop.py
memory = deps.working_memory_store.get(challenge_name)
deps.knowledge_store.promote_from_memory(
    challenge_name=challenge_name,
    category=deps.runtime_state.challenges.get(challenge_name, ChallengeState(challenge_name)).category,
    memory=memory,
)
```

Run: `uv run pytest tests/test_knowledge_store.py tests/test_coordinator_platform_flow.py -q`  
Expected: PASS

- [ ] **Step 5: 提交跨题知识层**

```bash
git add backend/control/knowledge_store.py backend/deps.py backend/control/working_memory.py backend/agents/coordinator_loop.py tests/test_knowledge_store.py tests/test_coordinator_platform_flow.py
git commit -m "新增跨题 Knowledge Store 与晋升规则"
```

### Task 5: 落地规则优先的 `Policy Engine`

**Files:**
- Create: `backend/control/policy_engine.py`
- Create: `tests/test_policy_engine.py`
- Modify: `backend/deps.py`
- Test: `tests/test_policy_engine.py`

- [ ] **Step 1: 先写失败测试，锁定 spawn、bump、knowledge broadcast 三类动作**

```python
from backend.control.actions import BroadcastKnowledge, BumpSolver, SpawnSwarm
from backend.control.knowledge_store import KnowledgeStore
from backend.control.policy_engine import PolicyEngine
from backend.control.state import ChallengeState, CompetitionState, SwarmState
from backend.control.working_memory import WorkingMemoryStore


def test_policy_engine_spawns_unsolved_challenge_when_capacity_available() -> None:
    state = CompetitionState(
        known_challenges={"echo"},
        known_solved=set(),
        challenges={"echo": ChallengeState(challenge_name="echo", status="pending", category="pwn")},
        swarms={},
    )

    engine = PolicyEngine(max_concurrent_challenges=3, bump_cooldown_seconds=60, stall_seconds=120)
    actions = engine.plan_tick(
        competition=state,
        working_memory_store=WorkingMemoryStore(),
        knowledge_store=KnowledgeStore(),
        now=100.0,
    )

    assert actions == [SpawnSwarm(challenge_name="echo", priority=100, reason="unsolved without active swarm")]
```

```python
def test_policy_engine_bumps_stalled_swarm_once_cooldown_expires() -> None:
    state = CompetitionState(
        challenges={"rsa": ChallengeState(challenge_name="rsa", status="running", category="crypto")},
        swarms={
            "rsa": SwarmState(
                challenge_name="rsa",
                status="running",
                running_models=["azure/gpt-5.4"],
                last_bump_at=0.0,
                last_progress_at=0.0,
            )
        },
    )
    memories = WorkingMemoryStore()
    memories.get("rsa").open_hypotheses.append("Try common modulus attack")

    engine = PolicyEngine(max_concurrent_challenges=3, bump_cooldown_seconds=30, stall_seconds=60)
    actions = engine.plan_tick(
        competition=state,
        working_memory_store=memories,
        knowledge_store=KnowledgeStore(),
        now=100.0,
    )

    assert actions == [
        BumpSolver(
            challenge_name="rsa",
            model_spec="azure/gpt-5.4",
            guidance="Retry with open hypothesis: Try common modulus attack",
            reason="stalled swarm with reusable hypothesis",
        )
    ]
```

```python
def test_policy_engine_broadcasts_matched_knowledge_once_per_swarm() -> None:
    state = CompetitionState(
        known_challenges={"hatephp"},
        challenges={"hatephp": ChallengeState(challenge_name="hatephp", status="running", category="web")},
        swarms={
            "hatephp": SwarmState(
                challenge_name="hatephp",
                status="running",
                running_models=["azure/gpt-5.4"],
                applied_knowledge_ids=set(),
            )
        },
    )
    store = KnowledgeStore()
    entry = store.upsert(
        scope="category",
        kind="exploit_pattern",
        content="category rule: php phar deserialization first",
        evidence="confirmed in prior web challenge",
        confidence=0.9,
        source_challenge="older-web",
        applicability={"category": "web"},
    )

    engine = PolicyEngine(max_concurrent_challenges=3, bump_cooldown_seconds=60, stall_seconds=120)
    actions = engine.plan_tick(
        competition=state,
        working_memory_store=WorkingMemoryStore(),
        knowledge_store=store,
        now=100.0,
    )

    assert actions == [
        BroadcastKnowledge(
            challenge_name="hatephp",
            message="category rule: php phar deserialization first",
            source="older-web",
            knowledge_id=entry.id,
        )
    ]
```

- [ ] **Step 2: 运行测试，确认策略引擎尚未实现**

Run: `uv run pytest tests/test_policy_engine.py -q`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 实现规则优先的最小策略引擎**

```python
# backend/control/policy_engine.py
from __future__ import annotations

from dataclasses import dataclass

from backend.control.actions import BroadcastKnowledge, BumpSolver, SpawnSwarm


@dataclass
class PolicyEngine:
    max_concurrent_challenges: int
    bump_cooldown_seconds: int
    stall_seconds: int

    def plan_tick(self, *, competition, working_memory_store, knowledge_store, now: float):
        actions = []

        if competition.active_swarm_count < self.max_concurrent_challenges:
            unsolved_without_swarm = sorted(
                name
                for name in competition.known_challenges - competition.known_solved
                if name not in competition.swarms
            )
            if unsolved_without_swarm:
                target = unsolved_without_swarm[0]
                actions.append(SpawnSwarm(challenge_name=target, priority=100, reason="unsolved without active swarm"))

        for name, swarm in competition.swarms.items():
            memory = working_memory_store.get(name)
            stalled = swarm.last_progress_at is not None and now - swarm.last_progress_at >= self.stall_seconds
            cooled = swarm.last_bump_at is None or now - swarm.last_bump_at >= self.bump_cooldown_seconds
            if swarm.status == "running" and stalled and cooled and memory.open_hypotheses and swarm.running_models:
                actions.append(
                    BumpSolver(
                        challenge_name=name,
                        model_spec=swarm.running_models[0],
                        guidance=f"Retry with open hypothesis: {memory.open_hypotheses[0]}",
                        reason="stalled swarm with reusable hypothesis",
                    )
                )

            challenge = competition.challenges.get(name)
            if challenge:
                matched = knowledge_store.match(
                    category=challenge.category,
                    challenge_name=name,
                    applied_ids=swarm.applied_knowledge_ids,
                )
                if matched:
                    entry = matched[0]
                    actions.append(
                        BroadcastKnowledge(
                            challenge_name=name,
                            message=entry.content,
                            source=entry.source_challenge,
                            knowledge_id=entry.id,
                        )
                    )
        return actions

    def apply_advisor_suggestions(self, suggestions, competition, now: float):
        actions = []
        for suggestion in suggestions:
            if suggestion.action_hint == "bump_solver" and suggestion.challenge_name in competition.swarms:
                actions.append(
                    BumpSolver(
                        challenge_name=suggestion.challenge_name,
                        model_spec=suggestion.model_spec or competition.swarms[suggestion.challenge_name].running_models[0],
                        guidance=suggestion.guidance,
                        reason=suggestion.reason or "advisor suggested bump",
                    )
                )
            if suggestion.action_hint == "broadcast_knowledge" and suggestion.challenge_name in competition.swarms:
                actions.append(
                    BroadcastKnowledge(
                        challenge_name=suggestion.challenge_name,
                        message=suggestion.message or suggestion.guidance,
                        source="advisor",
                    )
                )
        return actions
```

- [ ] **Step 4: 把 `PolicyEngine` 注入 `CoordinatorDeps`**

```python
# backend/deps.py
from dataclasses import dataclass, field
from typing import Any

from backend.cost_tracker import CostTracker
from backend.platforms.base import CompetitionPlatformClient
from backend.control.policy_engine import PolicyEngine


@dataclass
class CoordinatorDeps:
    ctfd: CompetitionPlatformClient
    cost_tracker: CostTracker
    settings: Any
    model_specs: list[str] = field(default_factory=list)
    challenges_root: str = "challenges"
    no_submit: bool = False
    max_concurrent_challenges: int = 10
    policy_engine: PolicyEngine = field(
        default_factory=lambda: PolicyEngine(
            max_concurrent_challenges=10,
            bump_cooldown_seconds=60,
            stall_seconds=180,
        )
    )
```

Run: `uv run pytest tests/test_policy_engine.py -q`  
Expected: PASS

- [ ] **Step 5: 提交策略引擎**

```bash
git add backend/control/policy_engine.py backend/deps.py tests/test_policy_engine.py
git commit -m "新增规则优先的 Policy Engine"
```

### Task 6: 把 `coordinator_loop` 升级为“状态更新 + policy tick + action 执行”

**Files:**
- Modify: `backend/agents/coordinator_loop.py`
- Modify: `backend/agents/coordinator_core.py`
- Modify: `backend/control/actions.py`
- Modify: `tests/test_coordinator_platform_flow.py`
- Test: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 先写失败测试，锁定 policy 产出的 action 会被执行**

```python
@pytest.mark.asyncio
async def test_run_event_loop_executes_policy_actions_before_llm_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from types import SimpleNamespace
    from backend.control.knowledge_store import KnowledgeStore
    from backend.control.state import CompetitionState
    from backend.control.working_memory import WorkingMemoryStore

    executed: list[str] = []

    async def fake_spawn_swarm(deps, challenge_name: str) -> str:
        executed.append(f"spawn:{challenge_name}")
        return f"spawned:{challenge_name}"

    monkeypatch.setattr("backend.agents.coordinator_core.do_spawn_swarm", fake_spawn_swarm)

    class FakePolicyEngine:
        def __init__(self, actions):
            self._actions = actions

        def plan_tick(self, **kwargs):
            return list(self._actions)

    class FakePlatform:
        async def validate_access(self) -> None:
            return None

        async def fetch_challenge_stubs(self):
            return [{"name": "echo"}]

        async def fetch_solved_names(self):
            return set()

    async def fake_turn_fn(message: str) -> None:
        return None

    deps = SimpleNamespace(
        policy_engine=FakePolicyEngine([SpawnSwarm(challenge_name="echo", priority=100, reason="test")]),
        cost_tracker=SimpleNamespace(total_cost_usd=0.0),
        swarms={},
        swarm_tasks={},
        results={},
        challenge_dirs={},
        challenge_metas={},
        coordinator_inbox=asyncio.Queue(),
        operator_inbox=asyncio.Queue(),
        msg_port=0,
        model_specs=["azure/gpt-5.4"],
        no_submit=True,
        settings=SimpleNamespace(all_solved_policy="exit", all_solved_idle_seconds=5),
        runtime_state=CompetitionState(known_challenges={"echo"}, known_solved=set()),
        working_memory_store=WorkingMemoryStore(),
        knowledge_store=KnowledgeStore(),
    )

    result = await run_event_loop(
        deps=deps,
        ctfd=FakePlatform(),
        cost_tracker=deps.cost_tracker,
        turn_fn=fake_turn_fn,
        status_interval=3600,
    )

    assert executed == ["spawn:echo"]
```

- [ ] **Step 2: 运行测试，确认 loop 尚未执行结构化动作**

Run: `uv run pytest tests/test_coordinator_platform_flow.py::test_run_event_loop_executes_policy_actions_before_llm_turn -q`  
Expected: FAIL because `run_event_loop()` still only forwards messages

- [ ] **Step 3: 实现 action 执行桥接**

```python
# backend/agents/coordinator_core.py
from backend.control.actions import BumpSolver, BroadcastKnowledge, MarkChallengeSkipped, RetryChallenge, SpawnSwarm


async def execute_action(deps: CoordinatorDeps, action) -> str:
    if action.kind == "spawn_swarm":
        return await do_spawn_swarm(deps, action.challenge_name)
    if action.kind == "bump_solver":
        return await do_bump_agent(deps, action.challenge_name, action.model_spec, action.guidance)
    if action.kind == "broadcast_knowledge":
        return await do_broadcast(deps, action.challenge_name, action.message)
    if action.kind == "retry_challenge":
        return await do_spawn_swarm(deps, action.challenge_name)
    if action.kind == "mark_challenge_skipped":
        _record_skipped_challenge(deps, action.challenge_name, action.reason)
        return f"Challenge '{action.challenge_name}' skipped: {action.reason}"
    return f"Unhandled action: {action}"
```

```python
# backend/agents/coordinator_loop.py
actions = deps.policy_engine.plan_tick(
    competition=deps.runtime_state,
    working_memory_store=deps.working_memory_store,
    knowledge_store=deps.knowledge_store,
    now=now,
)
for action in actions:
    result_text = await execute_action(deps, action)
    logger.info("Policy action executed: %s -> %s", action, result_text)
```

- [ ] **Step 4: 运行整场回归，确认旧行为没有被打断**

Run: `uv run pytest tests/test_coordinator_platform_flow.py -q`  
Expected: PASS

- [ ] **Step 5: 提交控制内核升级**

```bash
git add backend/agents/coordinator_loop.py backend/agents/coordinator_core.py backend/control/actions.py tests/test_coordinator_platform_flow.py
git commit -m "升级 coordinator loop 为 policy tick 控制内核"
```

### Task 7: 把 provider-specific coordinator 收缩为 advisor 接口

**Files:**
- Create: `backend/control/advisor.py`
- Modify: `backend/agents/azure_coordinator.py`
- Modify: `backend/agents/claude_coordinator.py`
- Modify: `backend/agents/codex_coordinator.py`
- Modify: `backend/agents/coordinator_loop.py`
- Modify: `tests/test_coordinator_platform_flow.py`
- Test: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 先写失败测试，锁定 advisor 接口输出结构**

```python
from backend.control.advisor import AdvisorContext, AdvisorSuggestion


def test_advisor_suggestion_is_structured_and_provider_neutral() -> None:
    suggestion = AdvisorSuggestion(
        action_hint="bump_solver",
        challenge_name="rsa",
        model_spec="azure/gpt-5.4",
        guidance="Try Wiener's attack first",
        reason="private exponent likely small",
    )

    assert suggestion.action_hint == "bump_solver"
    assert suggestion.challenge_name == "rsa"
```

```python
@pytest.mark.asyncio
async def test_azure_advisor_returns_suggestions_without_direct_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    advisor = AzureCoordinatorAdvisor(settings=make_settings(), model_spec="azure/gpt-5.4")
    suggestions = await advisor.suggest(
        AdvisorContext(
            competition_summary="1 active swarm, 2 unsolved challenges",
            challenge_name="rsa",
            memory_summary="Open hypothesis: common modulus",
            knowledge_summary="category rule: try common modulus first",
        )
    )

    assert suggestions
    assert all(s.action_hint in {"none", "spawn_swarm", "bump_solver", "broadcast_knowledge"} for s in suggestions)
```

- [ ] **Step 2: 运行测试，确认 advisor 抽象尚不存在**

Run: `uv run pytest tests/test_coordinator_platform_flow.py -q`  
Expected: FAIL with missing `AdvisorSuggestion` or missing advisor classes

- [ ] **Step 3: 定义 advisor 协议与结构化建议对象**

```python
# backend/control/advisor.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AdvisorContext:
    competition_summary: str
    challenge_name: str = ""
    memory_summary: str = ""
    knowledge_summary: str = ""


@dataclass(frozen=True)
class AdvisorSuggestion:
    action_hint: str
    challenge_name: str
    model_spec: str = ""
    guidance: str = ""
    message: str = ""
    reason: str = ""


class CoordinatorAdvisor(Protocol):
    async def suggest(self, context: AdvisorContext) -> list[AdvisorSuggestion]:
        raise NotImplementedError
```

- [ ] **Step 4: 让三类 coordinator 适配 `CoordinatorAdvisor`**

```python
# backend/agents/azure_coordinator.py
def parse_advisor_suggestions(text: str, default_challenge: str) -> list[AdvisorSuggestion]:
    suggestions: list[AdvisorSuggestion] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= 4:
            suggestions.append(
                AdvisorSuggestion(
                    action_hint=parts[0],
                    challenge_name=parts[1] or default_challenge,
                    model_spec=parts[2],
                    guidance=parts[3],
                    reason=parts[4] if len(parts) >= 5 else "",
                )
            )
    return suggestions


class AzureCoordinatorAdvisor:
    async def suggest(self, context: AdvisorContext) -> list[AdvisorSuggestion]:
        async with self._agent.run_stream(self._build_prompt(context), deps=self.deps) as result:
            text = (await result.get_output()).strip()
        return parse_advisor_suggestions(text, default_challenge=context.challenge_name)
```

```python
# backend/agents/coordinator_loop.py
def _summarize_competition_state(state: CompetitionState) -> str:
    return (
        f"known={sorted(state.known_challenges)}\n"
        f"solved={sorted(state.known_solved)}\n"
        f"active_swarms={state.active_swarm_count}\n"
        f"cost_usd={state.global_cost_usd:.4f}"
    )


advisor_suggestions = await advisor.suggest(
    AdvisorContext(
        competition_summary=_summarize_competition_state(deps.runtime_state),
        challenge_name=challenge_name,
        memory_summary=deps.working_memory_store.get(challenge_name).to_summary(),
        knowledge_summary=deps.knowledge_store.summary_for(challenge_name, category),
    )
)
actions.extend(deps.policy_engine.apply_advisor_suggestions(advisor_suggestions, deps.runtime_state, now))
```

Run: `uv run pytest tests/test_coordinator_platform_flow.py -q`  
Expected: PASS

- [ ] **Step 5: 提交 provider-neutral advisor 重构**

```bash
git add backend/control/advisor.py backend/agents/azure_coordinator.py backend/agents/claude_coordinator.py backend/agents/codex_coordinator.py backend/agents/coordinator_loop.py tests/test_coordinator_platform_flow.py
git commit -m "将多种 coordinator 收缩为 advisor 适配器"
```

### Task 8: README、设计图和整体验证收口

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-04-07-policy-memory-knowledge-control-architecture-design.md`
- Test: `README.md`
- Test: `uv run pytest -q`

- [ ] **Step 1: 更新 README 的架构图和控制层说明**

```md
## 架构设计

- `Platform State`：平台事实、题目状态、并发槽位
- `Policy Engine`：spawn / bump / broadcast / hold 的规则优先决策
- `Working Memory`：单题短期记忆，记录失败路径、开放假设与有效 artifact
- `Knowledge Store`：跨题可复用知识，带晋升规则与已应用保护
- `Solver Runtime`：单模型 ReAct 工具执行
```

- [ ] **Step 2: 把 spec 中的落地状态补成“已实现/未实现”**

```md
## 实现状态

- [x] 显式 Runtime State
- [x] Working Memory
- [x] Knowledge Store
- [x] Policy Engine
- [x] Advisor 接口
```

- [ ] **Step 3: 运行全量测试回归**

Run: `uv run pytest -q`  
Expected: PASS with all existing tests and new control-plane tests green

- [ ] **Step 4: 手工 smoke 一次 Azure 总控整场启动**

Run:

```bash
uv run ctf-solve \
  --platform lingxu-event-ctf \
  --platform-url https://ctf.yunyansec.com \
  --lingxu-event-id 198 \
  --coordinator azure \
  --models azure/gpt-5.4 \
  --models azure/gpt-5.4-mini \
  --max-challenges 3 \
  --all-solved-policy exit \
  --writeup-mode confirmed \
  --writeup-dir writeups \
  --msg-port 9400 \
  -v
```

Expected: 启动日志出现 `Starting coordinator (azure)`，并完成至少一轮 `policy tick` 与 `/responses` 流式调用，无 output validation 错误

- [ ] **Step 5: 提交文档与收口改动**

```bash
git add README.md docs/superpowers/specs/2026-04-07-policy-memory-knowledge-control-architecture-design.md
git commit -m "更新控制架构文档与实现状态"
```

---

## Self-Review

### Spec coverage

- `Runtime State`：Task 1, Task 2
- `Working Memory`：Task 3
- `Knowledge Store`：Task 4
- `Policy Engine`：Task 5
- `Action Executor`：Task 6
- `Advisor` 化 coordinator：Task 7
- 文档与整体验证：Task 8

无遗漏；spec 的阶段 1 到阶段 4 都有对应任务。

### Placeholder scan

- 全文没有 `TODO`、`TBD`、`implement later`、`similar to Task N`
- 每个代码步骤都给了明确文件路径、代码骨架和测试命令
- 每个任务都包含 commit 步骤

### Type consistency

- `CompetitionState / ChallengeState / SwarmState` 在 Task 1 定义，并在 Task 2 到 Task 7 一致使用
- `SpawnSwarm / BumpSolver / BroadcastKnowledge / HoldChallenge / RetryChallenge / MarkChallengeSkipped` 在 Task 1 定义，并在 Task 5 到 Task 7 一致使用
- `AdvisorContext / AdvisorSuggestion / CoordinatorAdvisor` 只在 Task 7 引入，前文未提前引用未定义类型
