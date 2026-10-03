"""Generate a reproducible default Markdown write-up for a solved challenge."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from backend.prompts import ChallengeMeta, list_distfiles
from backend.solver_base import SolverResult

_SOLUTION_EXTENSIONS = {
    ".c", ".cpp", ".go", ".java", ".js", ".md", ".py", ".rb", ".sage", ".sh", ".txt",
}
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|authorization|cookie|token|password)"
        r"\s*[:=]\s*['\"]?([^\s'\"]+)['\"]?"
    ),
    re.compile(r"\b(?:sk|ctfd)_[A-Za-z0-9_-]{12,}\b"),
)


def _slug(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "-", value.strip(), flags=re.UNICODE).strip("-.")
    return value or "challenge"


def _redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


def _markdown_text(text: str) -> str:
    """Keep solver text readable without allowing it to break fenced sections."""
    cleaned = _redact(text.strip()).replace("```", "` ` `")
    return cleaned or "_没有保存可用摘要。_"


def _copy_solution_files(workspace_dir: str, destination: Path) -> list[str]:
    """Preserve small text solution files from the winning ephemeral workspace."""
    root = Path(workspace_dir)
    if not workspace_dir or not root.is_dir():
        return []

    copied: list[str] = []
    total_bytes = 0
    for source in sorted(root.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in _SOLUTION_EXTENSIONS:
            continue
        relative = source.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if any(word in source.name.lower() for word in ("secret", "token", "credential", ".env")):
            continue
        size = source.stat().st_size
        if size > 1_000_000 or total_bytes + size > 10_000_000:
            continue
        try:
            content = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_redact(content), encoding="utf-8")
        copied.append(target.relative_to(destination.parent).as_posix())
        total_bytes += size
    return copied


def generate_default_writeup(
    *,
    meta: ChallengeMeta,
    result: SolverResult,
    winner_model: str,
    model_specs: list[str],
    findings: dict[str, str],
    challenge_dir: str,
    workspace_dir: str,
    output_root: str = "writeups",
    verified: bool = True,
) -> Path:
    """Create README.md and preserve solution files. Returns the README path."""
    output_dir = Path(output_root) / _slug(meta.category or "Uncategorized") / _slug(meta.name)
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    copied = _copy_solution_files(workspace_dir, artifacts_dir)

    collaborators = []
    for model, summary in findings.items():
        if summary:
            collaborators.append(f"### {model}\n\n{_markdown_text(summary)}")
    if not collaborators:
        collaborators.append("_没有保存其他模型的有效发现。_")

    attachments = list_distfiles(challenge_dir)
    attachment_lines = (
        "\n".join(f"- `{name}`" for name in attachments) or "_本题没有附件或附件未记录。_"
    )
    artifact_lines = "\n".join(f"- [{path}]({path})" for path in copied) or "_未发现可保存的解题脚本。_"
    status = "VERIFIED（平台已确认）" if verified else "CANDIDATE（未向平台确认）"
    trace_name = Path(result.log_path).name if result.log_path else "未记录"

    document = f"""# {meta.name}

## 基本信息

| 字段 | 内容 |
|---|---|
| 分类 | {meta.category or 'Unknown'} |
| 分值 | {meta.value or 'Unknown'} |
| 状态 | {status} |
| 解题模型 | {winner_model} |
| 参与模型 | {', '.join(model_specs)} |
| 解题步骤数 | {result.step_count} |
| 模型成本 | ${result.cost_usd:.4f} |
| 生成时间 | {datetime.now(UTC).isoformat(timespec='seconds')} |
| Trace | `{trace_name}` |

## 题目描述

{_markdown_text(meta.description)}

## 附件

{attachment_lines}

## 核心思路与解题过程

### 获胜模型：{winner_model}

{_markdown_text(result.findings_summary)}

## 协作记录

{chr(10).join(collaborators)}

## 保存的解题文件

{artifact_lines}

## 复现步骤

1. 将题目附件放在原始目录结构中。
2. 检查上方保存的脚本及其依赖。
3. 在隔离环境中运行对应脚本。
4. 确认脚本输出下方 Flag；若题目依赖在线实例，还需使用比赛提供的新实例地址。

> 自动草稿基于真实 solver 摘要与获胜工作区生成。正式提交前应人工检查命令、补齐环境版本，并确认脚本可以从干净环境复现。

## Flag

```text
{result.flag or '未记录'}
```

## 总结

- 首个成功模型：`{winner_model}`
- 平台验证状态：`{status}`
- 本文档已自动移除常见 API Key、Token、Cookie 与密码形式的敏感值。
"""
    readme = output_dir / "README.md"
    readme.write_text(document, encoding="utf-8")
    return readme
