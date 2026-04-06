# 手动导题与中文文档 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 HuntingBlade 增加 `ctf-import` 手动导题能力，并让目录型附件能被 prompt 递归枚举，同时保持 CLI 参数英文、帮助文本中文。

**Architecture:** 核心导题逻辑放在独立模块 `backend/challenge_import.py`，避免 CLI 入口承担文件系统细节。prompt 侧只改 `list_distfiles()` 的枚举行为；CLI 接线与 README 更新放在独立任务里处理，避免和核心导题逻辑耦合。

**Tech Stack:** Python 3.14, click, PyYAML, pathlib, shutil, pytest

---

### Task 1: 落地手动导题核心模块

**Files:**
- Create: `backend/challenge_import.py`
- Test: `tests/test_challenge_import.py`

- [ ] **Step 1: 先写失败测试，固定导题行为**

```python
def test_import_manual_challenge_writes_metadata_and_recursive_distfiles(tmp_path: Path) -> None:
    file_attachment = tmp_path / "task.zip"
    file_attachment.write_bytes(b"zip-data")

    attachment_dir = tmp_path / "bundle"
    (attachment_dir / "src").mkdir(parents=True)
    (attachment_dir / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    challenge_dir = import_manual_challenge(
        ManualChallengeImportSpec(
            name="登录器",
            category="web",
            description="分析登录逻辑并找到 flag。",
            output_dir=tmp_path / "challenges",
            connection_info="http://target.example.com",
            attachments=(file_attachment,),
            attachment_dirs=(attachment_dir,),
        )
    )

    assert (challenge_dir / "distfiles" / "src" / "main.py").exists()
```

- [ ] **Step 2: 运行测试，确认当前实现缺失**

Run: `uv run pytest tests/test_challenge_import.py -v`  
Expected: FAIL with `ModuleNotFoundError` for `backend.challenge_import`

- [ ] **Step 3: 写最小实现**

```python
@dataclass(slots=True)
class ManualChallengeImportSpec:
    name: str
    category: str
    description: str
    output_dir: Path
    connection_info: str = ""
    attachments: tuple[Path, ...] = ()
    attachment_dirs: tuple[Path, ...] = ()
    value: int = 0
    tags: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
```

```python
def import_manual_challenge(spec: ManualChallengeImportSpec) -> Path:
    spec = _validate_spec(spec)
    copy_plan = _build_copy_plan(spec)
    ...
    return challenge_dir
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_challenge_import.py -v`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/challenge_import.py tests/test_challenge_import.py
git commit -m "feat: add manual challenge import core"
```

### Task 2: 让 prompt 递归识别目录型附件

**Files:**
- Modify: `backend/prompts.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: 先写失败测试**

```python
def test_list_distfiles_returns_recursive_relative_paths(tmp_path: Path) -> None:
    distfiles = tmp_path / "distfiles"
    (distfiles / "nested").mkdir(parents=True)
    (distfiles / "top.txt").write_text("top\n", encoding="utf-8")
    (distfiles / "nested" / "inner.txt").write_text("inner\n", encoding="utf-8")

    assert list_distfiles(str(tmp_path)) == ["nested/inner.txt", "top.txt"]
```

- [ ] **Step 2: 运行测试，确认当前只列顶层文件**

Run: `uv run pytest tests/test_prompts.py -v`  
Expected: FAIL because nested file is missing from `list_distfiles()`

- [ ] **Step 3: 改最小实现**

```python
def list_distfiles(challenge_dir: str) -> list[str]:
    dist = Path(challenge_dir) / "distfiles"
    if not dist.exists():
        return []
    return sorted(
        path.relative_to(dist).as_posix()
        for path in dist.rglob("*")
        if path.is_file()
    )
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_prompts.py -v`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/prompts.py tests/test_prompts.py
git commit -m "feat: support nested distfiles in prompts"
```

### Task 3: 保存设计与实现文档

**Files:**
- Create: `docs/superpowers/specs/2026-04-06-manual-challenge-import-design.md`
- Create: `docs/superpowers/plans/2026-04-06-manual-challenge-import-cli.md`

- [ ] **Step 1: 写 spec**

```markdown
# HuntingBlade 手动导题与中文文档设计
...
```

- [ ] **Step 2: 写 plan**

```markdown
# 手动导题与中文文档 Implementation Plan
...
```

- [ ] **Step 3: 自检**

Run: `rg -n "TODO|TBD|implement later|fill in details" docs/superpowers/specs/2026-04-06-manual-challenge-import-design.md docs/superpowers/plans/2026-04-06-manual-challenge-import-cli.md`  
Expected: no output

- [ ] **Step 4: 提交**

```bash
git add docs/superpowers/specs/2026-04-06-manual-challenge-import-design.md docs/superpowers/plans/2026-04-06-manual-challenge-import-cli.md
git commit -m "docs: add manual import spec and plan"
```

## Spec Coverage Check

- 导题核心模块：Task 1 覆盖 metadata 落盘、递归复制、覆盖与回滚。
- 目录型附件提示：Task 2 覆盖 `list_distfiles()` 递归行为。
- 文档落盘：Task 3 保存该任务的 spec 与 plan。
