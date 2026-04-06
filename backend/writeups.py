"""Task 2 writeup generator."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.prompts import ChallengeMeta, list_distfiles
from backend.solve_lifecycle import ChallengeResultRecord


def challenge_slug(name: str) -> str:
    normalized = name.strip().lower()
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    unicode_slug = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE).strip("-_").replace("_", "-")

    if normalized.isascii() and ascii_slug:
        return ascii_slug

    base = unicode_slug or "challenge"
    suffix = hashlib.blake2b(name.encode("utf-8"), digest_size=4).hexdigest()
    return f"{base}-{suffix}"


def run_dir_name(meta: ChallengeMeta) -> str:
    platform = meta.platform or "local"
    event_part = str(meta.event_id) if meta.event_id is not None else "local"
    return f"{platform}-{event_part}"


def _compact_text(value: str, limit: int = 160) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _parse_args(raw_args: Any) -> str:
    if isinstance(raw_args, dict):
        if "command" in raw_args:
            return str(raw_args["command"])
        return json.dumps(raw_args, ensure_ascii=False)
    if isinstance(raw_args, str):
        try:
            decoded = json.loads(raw_args)
        except json.JSONDecodeError:
            return raw_args
        return _parse_args(decoded)
    return str(raw_args)


def extract_recent_key_steps(log_path: str, limit: int = 6) -> list[str]:
    if not log_path:
        return []

    path = Path(log_path)
    if not path.exists():
        return []

    steps: list[str] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type") or event.get("event")
                if event_type not in {"tool_call", "tool_result"}:
                    continue

                step = event.get("step", "?")
                tool = event.get("tool", "?")
                if event_type == "tool_call":
                    args = _compact_text(_parse_args(event.get("args", "")))
                    if args:
                        steps.append(f"Step {step} 调用 `{tool}`：`{args}`")
                else:
                    result = _compact_text(str(event.get("result", "")))
                    if result:
                        steps.append(f"Step {step} 结果：{result}")
    except (OSError, UnicodeDecodeError):
        return []
    return steps[-limit:]


def write_writeup(
    meta: ChallengeMeta,
    challenge_dir: str | Path,
    record: ChallengeResultRecord,
    base_dir: str | Path,
) -> Path:
    challenge_dir = Path(challenge_dir)
    base_dir = Path(base_dir)
    writeup_dir = base_dir / run_dir_name(meta)
    writeup_dir.mkdir(parents=True, exist_ok=True)

    writeup_path = writeup_dir / f"{challenge_slug(meta.name)}.md"
    attachments = list_distfiles(str(challenge_dir))
    key_steps = extract_recent_key_steps(record["log_path"])

    reproduction_notes: list[str] = []
    if not record["confirmed"]:
        reproduction_notes.append("未自动提交，需人工确认。")
    if record["env_cleanup_status"] == "failed":
        reproduction_notes.append("平台环境可能仍处于占用状态。")
    if not reproduction_notes:
        reproduction_notes.append("无额外复现备注。")

    lines = [
        f"# {meta.name}",
        "",
        "## 题目基本信息",
        f"- 题目名称：{meta.name}",
        f"- 分类：{meta.category or '未知'}",
        f"- 分值：{meta.value or 0}",
        f"- 平台：{meta.platform or 'local'}",
        f"- 赛事标识：{meta.event_id if meta.event_id is not None else 'local'}",
        "",
        "## 附件与环境信息",
        f"- connection_info：{meta.connection_info or '无'}",
        "- 附件列表：" if attachments else "- 附件列表：无",
    ]
    if attachments:
        lines.extend(f"  - {name}" for name in attachments)
    lines.extend(
        [
            "",
            "## 最终结果",
            f"- solve_status：{record['solve_status']}",
            f"- flag：{record['flag'] or '未获得'}",
            f"- submit_status：{record['submit_status'] or '未提交'}",
            f"- submit_display：{record['submit_display'] or '无'}",
            f"- confirmed：{record['confirmed']}",
            f"- winner_model：{record['winner_model'] or '未知'}",
            "",
            "## 解题思路摘要",
            record["findings_summary"] or "暂无摘要。",
            "",
            "## 关键步骤与命令",
        ]
    )
    if key_steps:
        lines.extend(f"- {item}" for item in key_steps)
    else:
        lines.append("- 暂无可提取的关键步骤。")

    lines.extend(["", "## 复现备注"])
    lines.extend(f"- {note}" for note in reproduction_notes)
    lines.append("")

    writeup_path.write_text("\n".join(lines), encoding="utf-8")

    record["writeup_path"] = str(writeup_path)
    record["writeup_status"] = "generated"
    record["writeup_error"] = ""
    return writeup_path
