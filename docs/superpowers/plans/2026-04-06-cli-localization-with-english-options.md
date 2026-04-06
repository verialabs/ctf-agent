# CLI 英文参数与中文说明收口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 HuntingBlade 的 CLI 收口为“参数名英文、帮助文本与 README 中文”，并补齐第三次任务的文档沉淀。

**Architecture:** 先用 `tests/test_cli.py` 固定最终对外接口，再最小化修改 `backend/cli.py` 与 `pyproject.toml`，最后同步 README 与历史计划文档，确保代码、测试和文档三者口径一致。

**Tech Stack:** Python 3.14, click, pytest, ruff, markdown

---

### Task 1: 用测试固定最终 CLI 规则

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 先写失败测试，固定“英文参数 + 中文帮助”**

```python
def test_main_help_uses_english_options_with_chinese_help() -> None:
    result = CliRunner().invoke(cli.main, ["--help"])

    assert result.exit_code == 0
    assert "CTF Agent 多模型题目求解入口。" in result.output
    assert "--platform" in result.output
    assert "--challenge" in result.output
    assert "--题目目录" not in result.output


def test_import_cmd_help_uses_english_options_with_chinese_help() -> None:
    result = CliRunner().invoke(cli.import_cmd, ["--help"])

    assert result.exit_code == 0
    assert "--name" in result.output
    assert "--attachment-dir" in result.output
    assert "--题目名称" not in result.output


def test_pyproject_exposes_ctf_import_script() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    assert data["project"]["scripts"]["ctf-import"] == "backend.cli:import_cmd"
```

- [ ] **Step 2: 运行测试，确认当前实现与目标不一致**

Run: `uv run pytest tests/test_cli.py -v`  
Expected: FAIL because help output or script exposure does not yet match the final English-option contract

- [ ] **Step 3: 写最小实现，让 CLI 接口符合最终规则**

```python
# backend/cli.py
@click.option("--platform", default=None, type=click.Choice(["ctfd", "lingxu-event-ctf"]), help="题目来源平台，默认使用 ctfd")
@click.option("--platform-url", default=None, help="平台根地址；使用凌虚赛事 CTF 时必填")
@click.option("--lingxu-event-id", default=None, type=int, help="凌虚赛事 ID；使用凌虚赛事 CTF 时必填")
@click.option("--lingxu-cookie", default=None, help="浏览器导出的凌虚 Cookie 原文")
@click.option("--lingxu-cookie-file", default=None, type=click.Path(dir_okay=False, path_type=Path), help="从文件读取凌虚 Cookie，适合避免命令历史泄露")
@click.option("--ctfd-url", default=None, help="CTFd 地址，优先于 .env 配置")
@click.option("--challenge", default=None, help="只求解单个本地题目目录")
@click.option("--msg-port", default=0, type=int, help="操作员消息端口，0 表示自动选择")
def main(
    platform: str | None,
    platform_url: str | None,
    lingxu_event_id: int | None,
    lingxu_cookie: str | None,
    lingxu_cookie_file: Path | None,
    ctfd_url: str | None,
    challenge: str | None,
    msg_port: int,
) -> None:
    """CTF Agent 多模型题目求解入口。

    不传 `--challenge` 时启动完整协调器，按 Ctrl+C 停止。
    """
```

```toml
[project.scripts]
ctf-solve = "backend.cli:main"
ctf-msg = "backend.cli:msg"
ctf-import = "backend.cli:import_cmd"
```

- [ ] **Step 4: 重新运行测试，确认接口收口完成**

Run: `uv run pytest tests/test_cli.py -v`  
Expected: PASS with all CLI tests green

- [ ] **Step 5: 提交这一小步**

```bash
git add backend/cli.py pyproject.toml tests/test_cli.py
git commit -m "fix: restore english cli options"
```

### Task 2: 同步 README 与历史计划文档

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-04-06-lingxu-event-ctf-integration.md`
- Create: `docs/superpowers/specs/2026-04-06-cli-localization-with-english-options-design.md`
- Create: `docs/superpowers/plans/2026-04-06-cli-localization-with-english-options.md`

- [ ] **Step 1: 先做残留搜索，找出错误的中文参数表述**

```bash
rg -n -- '--平台|--平台地址|--凌虚赛事ID|--凌虚Cookie|--凌虚Cookie文件|中文参数化|中文 CLI|Chinese CLI' README.md docs/superpowers/plans/2026-04-06-lingxu-event-ctf-integration.md docs/superpowers/specs/2026-04-06-lingxu-event-ctf-design.md docs/superpowers/specs/2026-04-06-manual-challenge-import-design.md docs/superpowers/plans/2026-04-06-manual-challenge-import-cli.md
```

- [ ] **Step 2: 更新 README，补齐凌虚赛事 CTF 与 `ctf-import` 的最终说明**

```markdown
## 凌虚赛事 CTF 接入

uv run ctf-solve \
  --platform lingxu-event-ctf \
  --platform-url https://match.example.com \
  --lingxu-event-id 42 \
  --lingxu-cookie-file .secrets/lingxu.cookie \
  --max-challenges 3 \
  --no-submit \
  -v

## 手动导题到本地

uv run ctf-import \
  --name "签到题" \
  --category misc \
  --description "阅读附件并找出 flag。" \
  --attachment ./downloads/task.zip \
  --output-dir ./challenges
```

- [ ] **Step 3: 修正旧的凌虚实现计划，并写入第三次任务 spec / plan**

```markdown
- `backend/cli.py` 的对外参数名保持英文，仅帮助文本、README 和示例说明使用中文。
- `tests/test_cli.py` 校验 `ctf-solve` 在保持英文参数的前提下接入凌虚平台选项。
```

```markdown
# HuntingBlade CLI 英文参数与中文说明收口设计

**目标**：在保留凌虚赛事 CTF 接入与 `ctf-import` 的前提下，把 CLI 固定为“参数名英文、帮助文本与 README 中文”。
```

```markdown
# CLI 英文参数与中文说明收口 Implementation Plan

**Goal:** 把 HuntingBlade 的 CLI 收口为“参数名英文、帮助文本与 README 中文”，并补齐第三次任务的文档沉淀。
```

- [ ] **Step 4: 自检，确保 README 和 docs 中不再残留中文参数名**

Run: `rg -n -- '--平台|--平台地址|--凌虚赛事ID|--凌虚Cookie|--凌虚Cookie文件|中文参数化|Chinese CLI' README.md docs/superpowers/plans/2026-04-06-lingxu-event-ctf-integration.md docs/superpowers/specs/2026-04-06-lingxu-event-ctf-design.md docs/superpowers/specs/2026-04-06-manual-challenge-import-design.md docs/superpowers/plans/2026-04-06-manual-challenge-import-cli.md`  
Expected: no output

- [ ] **Step 5: 提交这一小步**

```bash
git add README.md docs/superpowers/specs/2026-04-06-cli-localization-with-english-options-design.md docs/superpowers/plans/2026-04-06-cli-localization-with-english-options.md docs/superpowers/plans/2026-04-06-lingxu-event-ctf-integration.md
git commit -m "docs: sync chinese guidance with english cli options"
```

### Task 3: 做最终验证并准备合并到主线

**Files:**
- Modify: `backend/cli.py`
- Modify: `README.md`
- Modify: `tests/test_cli.py`
- Modify: `pyproject.toml`
- Modify: `docs/superpowers/plans/2026-04-06-lingxu-event-ctf-integration.md`
- Modify: `docs/superpowers/specs/2026-04-06-cli-localization-with-english-options-design.md`
- Modify: `docs/superpowers/plans/2026-04-06-cli-localization-with-english-options.md`

- [ ] **Step 1: 运行回归测试**

Run: `uv run pytest tests/test_platform_factory.py tests/test_coordinator_platform_flow.py tests/test_lingxu_event_ctf_client.py tests/test_challenge_import.py tests/test_prompts.py tests/test_cli.py -v`  
Expected: PASS with all selected tests passing

- [ ] **Step 2: 运行静态检查**

Run: `uv run ruff check backend/cli.py backend/challenge_import.py backend/prompts.py tests/test_cli.py tests/test_challenge_import.py tests/test_prompts.py tests/test_platform_factory.py pyproject.toml`  
Expected: PASS with no lint errors in touched files

- [ ] **Step 3: 跑 CLI smoke**

Run: `uv run ctf-import --help`  
Expected: 输出中文帮助，选项名为英文

Run: `uv run ctf-solve --help`  
Expected: 输出中文帮助，选项名为英文

Run: `uv run ctf-msg --help`  
Expected: 输出中文帮助，选项名为英文

- [ ] **Step 4: 提交第三个中文 commit**

```bash
git add backend/cli.py pyproject.toml tests/test_cli.py README.md docs/superpowers/specs/2026-04-06-cli-localization-with-english-options-design.md docs/superpowers/plans/2026-04-06-cli-localization-with-english-options.md docs/superpowers/plans/2026-04-06-lingxu-event-ctf-integration.md
git commit -m "fix: 恢复英文 CLI 参数并完善中文说明"
```

- [ ] **Step 5: 推送主分支并清理临时工作区**

```bash
git push origin main
git branch -D codex/lingxu-event-ctf-integration
git branch -D codex/manual-import-cli
git worktree remove /Users/d1a0y1bb/Desktop/HuntingBlade/.worktrees/manual-import-cli
git worktree remove /Users/d1a0y1bb/Desktop/HuntingBlade/.worktrees/main-finalize
```

## Spec Coverage Check

- CLI 参数英文化：Task 1 固定命令接口，覆盖 `ctf-solve`、`ctf-msg`、`ctf-import`。
- 中文帮助与 README：Task 2 同步 README、旧 plan 和第三次任务文档。
- 最终回归与交付：Task 3 覆盖测试、lint、help smoke、提交与推送。
