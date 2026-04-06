# 无总控整场模式与网关切换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 HuntingBlade 增加 `--coordinator none` 的无总控整场模式，切换项目默认 OpenAI 兼容网关，并更新 README 中的最新中文使用说明。

**Architecture:** 继续复用 `backend/agents/coordinator_loop.py` 的共享事件循环，不新建第二套 headless 编排器。CLI 只负责把 `none` 选项传给 `_run_coordinator()`，真正的 headless 执行入口放在协调器层薄封装里；README 与 `.env.example` 独立更新，避免配置说明和运行时逻辑耦合。

**Tech Stack:** Python 3.14, click, asyncio, pytest, rich, curl smoke validation

---

### Task 1: 先用测试定义 `--coordinator none` 的外部行为

**Files:**
- Modify: `tests/test_cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 在 CLI help 测试里补上 `none` 选项断言**

```python
def test_main_help_uses_english_options_with_chinese_help() -> None:
    result = CliRunner().invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "--coordinator" in result.output
    assert "claude|codex|none" in result.output
```

- [ ] **Step 2: 新增接收 `--coordinator none` 的测试**

```python
def test_main_accepts_headless_coordinator(monkeypatch, tmp_path: Path) -> None:
    cookie_file = tmp_path / "lingxu.cookie"
    cookie_file.write_text("sessionid=sid123", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_run_coordinator(
        settings,
        model_specs,
        challenges_dir,
        no_submit,
        coordinator_model,
        coordinator_backend,
        max_challenges,
        msg_port=0,
    ) -> None:
        captured["coordinator_backend"] = coordinator_backend

    monkeypatch.setattr(cli, "_run_coordinator", fake_run_coordinator)

    result = CliRunner().invoke(
        cli.main,
        [
            "--platform", "lingxu-event-ctf",
            "--platform-url", "https://lx.example.com",
            "--lingxu-event-id", "42",
            "--lingxu-cookie-file", str(cookie_file),
            "--coordinator", "none",
        ],
    )

    assert result.exit_code == 0
    assert captured["coordinator_backend"] == "none"
```

- [ ] **Step 3: 运行测试，确认先失败**

Run: `uv run pytest tests/test_cli.py -q`  
Expected: FAIL because `click.Choice(["claude", "codex"])` still rejects `none`

- [ ] **Step 4: 提交**

```bash
git add tests/test_cli.py
git commit -m "test: define headless coordinator cli behavior"
```

### Task 2: 用测试固定 headless 协调器接线

**Files:**
- Modify: `tests/test_coordinator_platform_flow.py`
- Test: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 新增 headless 分支测试**

```python
@pytest.mark.asyncio
async def test_run_headless_coordinator_uses_shared_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_event_loop(deps, ctfd, cost_tracker, turn_fn, status_interval=60):
        captured["deps"] = deps
        captured["ctfd"] = ctfd
        captured["cost_tracker"] = cost_tracker
        await turn_fn("warmup")
        return {"results": {}, "total_cost_usd": 0.0, "total_tokens": 0}
```

- [ ] **Step 2: 在测试里校验 headless 的 `turn_fn` 不触发任何 LLM 调用，只吞掉消息并返回**

```python
        captured["turn_result"] = await turn_fn("status")
        assert captured["turn_result"] is None
```

- [ ] **Step 3: 运行测试，确认先失败**

Run: `uv run pytest tests/test_coordinator_platform_flow.py -q`  
Expected: FAIL because headless coordinator 入口尚不存在

- [ ] **Step 4: 提交**

```bash
git add tests/test_coordinator_platform_flow.py
git commit -m "test: add headless coordinator coverage"
```

### Task 3: 实现 `--coordinator none` 的运行时逻辑

**Files:**
- Modify: `backend/cli.py`
- Create: `backend/agents/headless_coordinator.py`
- Modify: `backend/agents/coordinator_loop.py` (only if small helper extraction is needed)
- Test: `tests/test_cli.py`
- Test: `tests/test_coordinator_platform_flow.py`

- [ ] **Step 1: 把 CLI 选项扩成三选一**

```python
@click.option(
    "--coordinator",
    default="claude",
    type=click.Choice(["claude", "codex", "none"]),
    help="协调器后端；none 表示无总控整场模式",
)
```

- [ ] **Step 2: 新增 headless 协调器薄封装**

```python
async def run_headless_coordinator(
    settings: Settings,
    model_specs: list[str],
    challenges_root: str,
    no_submit: bool = False,
    msg_port: int = 0,
) -> dict[str, Any]:
    ctfd, cost_tracker, deps = build_deps(
        settings=settings,
        model_specs=model_specs,
        challenges_root=challenges_root,
        no_submit=no_submit,
    )
    deps.msg_port = msg_port

    async def _headless_turn(message: str) -> None:
        logger.info("Headless event: %s", message[:400])

    return await run_event_loop(
        deps=deps,
        ctfd=ctfd,
        cost_tracker=cost_tracker,
        turn_fn=_headless_turn,
    )
```

- [ ] **Step 3: 在 `_run_coordinator()` 中接入 `none` 分支**

```python
    if coordinator_backend == "codex":
        ...
    elif coordinator_backend == "none":
        from backend.agents.headless_coordinator import run_headless_coordinator

        results = await run_headless_coordinator(
            settings=settings,
            model_specs=model_specs,
            challenges_root=challenges_dir,
            no_submit=no_submit,
            msg_port=msg_port,
        )
    else:
        ...
```

- [ ] **Step 4: 调整启动提示，明确 headless 模式**

```python
    label = "none/headless" if coordinator_backend == "none" else coordinator_backend
    console.print(f"[bold]Starting coordinator ({label}, Ctrl+C to stop)...[/bold]\n")
```

- [ ] **Step 5: 运行针对性测试，确认通过**

Run: `uv run pytest tests/test_cli.py tests/test_coordinator_platform_flow.py -q`  
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/cli.py backend/agents/headless_coordinator.py tests/test_cli.py tests/test_coordinator_platform_flow.py
git commit -m "feat: add headless coordinator mode"
```

### Task 4: 切换项目默认网关配置

**Files:**
- Modify: `.env.example`
- Modify: `.env`

- [ ] **Step 1: 更新 `.env.example` 到新网关，占位符不写真实密钥**

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.masterjie.eu.cc/v1

AZURE_OPENAI_ENDPOINT=https://api.masterjie.eu.cc/v1
AZURE_OPENAI_API_KEY=sk-...
```

- [ ] **Step 2: 更新本地 `.env` 到新网关**

```dotenv
OPENAI_API_KEY=<user provided secret>
OPENAI_BASE_URL=https://api.masterjie.eu.cc/v1

AZURE_OPENAI_ENDPOINT=https://api.masterjie.eu.cc/v1
AZURE_OPENAI_API_KEY=<user provided secret>
```

- [ ] **Step 3: 用 HTTP smoke test 验证新网关**

Run:

```bash
curl -sS https://api.masterjie.eu.cc/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.4-mini","messages":[{"role":"user","content":"Reply with exactly: ok"}],"max_tokens":10}'
```

Expected: JSON response containing `"ok"`

- [ ] **Step 4: 提交**

```bash
git add .env.example
git commit -m "chore: update default gateway examples"
```

### Task 5: 更新 README 最新中文使用方法

**Files:**
- Modify: `README.md`
- Test: `README.md`

- [ ] **Step 1: 更新环境变量章节**

```md
OPENAI_BASE_URL=https://api.masterjie.eu.cc/v1
AZURE_OPENAI_ENDPOINT=https://api.masterjie.eu.cc/v1
```

- [ ] **Step 2: 新增三种协调模式说明**

```md
- `--coordinator claude`：适合你本机能稳定跑 Claude 协调器时使用
- `--coordinator codex`：适合你本机 `codex` 可用，且网关支持 `responses` 时使用
- `--coordinator none`：无总控整场模式，只做自动拉题、自动起 swarm、自动监控，适合稳定性优先
```

- [ ] **Step 3: 补充凌虚赛事 CTF 最新推荐命令**

```bash
uv run ctf-solve \
  --platform lingxu-event-ctf \
  --platform-url https://ctf.yunyansec.com \
  --lingxu-event-id 198 \
  --lingxu-cookie-file .secrets/lingxu.cookie \
  --coordinator none \
  --models azure/gpt-5.4 \
  --models azure/gpt-5.4-mini \
  --max-challenges 3 \
  --msg-port 9400 \
  --no-submit \
  -v
```
```

- [ ] **Step 4: 写清 Cookie 与自动行为**

```md
- Cookie 至少要有 `sessionid`；`csrftoken` 没有也可以
- 不传 `--challenge` 时会进入整场模式，自动读取题单、自动拉题、自动起 swarm
- 对需要环境的题目，平台适配器会在求解前自动尝试准备连接信息
```

- [ ] **Step 5: 运行文档断言**

Run: `rg -n -- '--coordinator none|api.masterjie.eu.cc/v1|sessionid|csrftoken' README.md .env.example`  
Expected: hits in both `README.md` and `.env.example`

- [ ] **Step 6: 提交**

```bash
git add README.md .env.example
git commit -m "docs: refresh readme for headless mode"
```

### Task 6: 最终验证

**Files:**
- Modify: none
- Test: `tests/test_cli.py`
- Test: `tests/test_coordinator_platform_flow.py`
- Test: project smoke commands

- [ ] **Step 1: 跑本次相关测试**

Run: `uv run pytest tests/test_cli.py tests/test_coordinator_platform_flow.py tests/test_lingxu_event_ctf_client.py -q`  
Expected: PASS

- [ ] **Step 2: 跑更大范围回归**

Run: `uv run pytest -q`  
Expected: PASS or only existing known non-blocking warnings

- [ ] **Step 3: 做 CLI smoke**

Run:

```bash
uv run ctf-solve --help
uv run ctf-msg --help
uv run ctf-import --help
```

Expected: all commands print help successfully

- [ ] **Step 4: 做 headless 命令 smoke**

Run:

```bash
uv run ctf-solve \
  --platform lingxu-event-ctf \
  --platform-url https://ctf.yunyansec.com \
  --lingxu-event-id 198 \
  --lingxu-cookie "$LINGXU_COOKIE" \
  --coordinator none \
  --models azure/gpt-5.4-mini \
  --max-challenges 1 \
  --msg-port 9400 \
  --no-submit \
  -v
```

Expected: enters event loop, validates access, initializes poller, auto-spawns at most one challenge

- [ ] **Step 5: 整理验证结果并准备交付**

Run: `git status --short`  
Expected: only intended files changed

## Spec Coverage Check

- `--coordinator none`：Task 1-3 覆盖 CLI 行为、headless 入口和共享事件循环复用。
- 网关切换：Task 4 覆盖 `.env` 与 `.env.example`。
- README 最新中文说明：Task 5 覆盖三种协调模式、凌虚 Cookie、自动拉题和推荐命令。
- 验证：Task 6 覆盖测试、help smoke 和整场模式 smoke。
