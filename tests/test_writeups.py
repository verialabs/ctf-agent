from __future__ import annotations

import json
from pathlib import Path

from backend.prompts import ChallengeMeta
from backend.solve_lifecycle import build_result_record, should_generate_writeup
from backend.solver_base import FLAG_FOUND, GAVE_UP, SolverResult
from backend.writeups import extract_recent_key_steps, run_dir_name, write_writeup


def test_build_result_record_preserves_required_fields() -> None:
    result = SolverResult(
        flag="flag{demo}",
        status=FLAG_FOUND,
        findings_summary="Used SQL injection to dump the admin token.",
        step_count=7,
        cost_usd=1.25,
        log_path="/tmp/trace.jsonl",
        model_spec="openai/gpt-5.3-codex",
    )

    record = build_result_record(
        result=result,
        submit_status="correct",
        submit_display="CORRECT",
        confirmed=True,
        writeup_path="writeups/lingxu-event-ctf-198/echo.md",
        writeup_status="generated",
        writeup_error="",
        env_cleanup_status="failed",
        env_cleanup_error="container still running",
    )

    assert record == {
        "flag": "flag{demo}",
        "solve_status": FLAG_FOUND,
        "submit_status": "correct",
        "submit_display": "CORRECT",
        "confirmed": True,
        "winner_model": "openai/gpt-5.3-codex",
        "findings_summary": "Used SQL injection to dump the admin token.",
        "log_path": "/tmp/trace.jsonl",
        "writeup_path": "writeups/lingxu-event-ctf-198/echo.md",
        "writeup_status": "generated",
        "writeup_error": "",
        "env_cleanup_status": "failed",
        "env_cleanup_error": "container still running",
    }


def test_should_generate_writeup_follows_mode_and_result_state() -> None:
    solved_record = {
        "flag": "flag{demo}",
        "solve_status": FLAG_FOUND,
        "submit_status": "correct",
        "submit_display": "CORRECT",
        "confirmed": True,
        "winner_model": "openai/gpt-5.3-codex",
        "findings_summary": "Solved.",
        "log_path": "/tmp/trace.jsonl",
        "writeup_path": "",
        "writeup_status": "pending",
        "writeup_error": "",
        "env_cleanup_status": "skipped",
        "env_cleanup_error": "",
    }
    unsolved_record = dict(solved_record, solve_status=GAVE_UP, confirmed=False)

    assert should_generate_writeup("off", solved_record) is False
    assert should_generate_writeup("confirmed", solved_record) is True
    assert should_generate_writeup("confirmed", unsolved_record) is False
    assert should_generate_writeup("solved", solved_record) is True
    assert should_generate_writeup("solved", unsolved_record) is False


def test_write_writeup_outputs_chinese_markdown_with_trace_summary(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    distfiles = challenge_dir / "distfiles"
    distfiles.mkdir(parents=True)
    (distfiles / "echo.py").write_text("print('hello')\n", encoding="utf-8")
    (distfiles / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")

    trace_path = tmp_path / "trace.jsonl"
    trace_events = [
        {"event": "tool_call", "tool": "bash", "step": 1, "args": {"command": "nc example.com 31337"}},
        {"event": "tool_result", "tool": "bash", "step": 1, "result": "banner"},
        {"event": "tool_call", "tool": "bash", "step": 2, "args": {"command": "python solve.py"}},
        {"event": "tool_result", "tool": "bash", "step": 2, "result": "flag{demo}"},
    ]
    trace_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in trace_events),
        encoding="utf-8",
    )

    meta = ChallengeMeta(
        name="Echo",
        category="web",
        value=100,
        description="Find the echo flaw.",
        connection_info="nc example.com 31337",
        platform="lingxu-event-ctf",
        event_id=198,
    )
    record = build_result_record(
        result=SolverResult(
            flag="flag{demo}",
            status=FLAG_FOUND,
            findings_summary="通过回显点执行命令并读取 flag。",
            step_count=2,
            cost_usd=0.5,
            log_path=str(trace_path),
            model_spec="openai/gpt-5.3-codex",
        ),
        submit_status="correct",
        submit_display="CORRECT",
        confirmed=False,
        env_cleanup_status="failed",
        env_cleanup_error="sandbox busy",
    )

    writeup_path = write_writeup(meta, challenge_dir, record, tmp_path / "writeups")

    assert writeup_path == tmp_path / "writeups" / "lingxu-event-ctf-198" / "echo.md"
    content = writeup_path.read_text(encoding="utf-8")

    assert "题目基本信息" in content
    assert "附件与环境信息" in content
    assert "最终结果" in content
    assert "解题思路摘要" in content
    assert "关键步骤与命令" in content
    assert "echo.py" in content
    assert "Dockerfile" in content
    assert "nc example.com 31337" in content
    assert "python solve.py" in content
    assert "未自动提交，需人工确认" in content
    assert "平台环境可能仍处于占用状态" in content


def test_write_writeup_keeps_distinct_paths_for_chinese_titles(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "distfiles").mkdir(parents=True)

    record = build_result_record(
        result=SolverResult(
            flag="flag{demo}",
            status=FLAG_FOUND,
            findings_summary="ok",
            step_count=1,
            cost_usd=0.1,
            log_path="",
            model_spec="openai/gpt-5.3-codex",
        )
    )

    first_path = write_writeup(
        ChallengeMeta(name="签到题", platform="local"),
        challenge_dir,
        dict(record),
        tmp_path / "writeups",
    )
    second_path = write_writeup(
        ChallengeMeta(name="签到题🔥", platform="local"),
        challenge_dir,
        dict(record),
        tmp_path / "writeups",
    )

    assert first_path != second_path
    assert first_path.name != "challenge.md"
    assert second_path.name != "challenge.md"


def test_run_dir_name_avoids_duplicate_event_ctf_segment() -> None:
    meta = ChallengeMeta(name="Echo", platform="lingxu-event-ctf", event_id=198)

    assert run_dir_name(meta) == "lingxu-event-ctf-198"


def test_extract_recent_key_steps_handles_invalid_trace_file(tmp_path: Path) -> None:
    trace_path = tmp_path / "broken.jsonl"
    trace_path.write_bytes(b"\xff\xfe\x00")

    assert extract_recent_key_steps(str(trace_path)) == []


def test_write_writeup_still_generates_markdown_when_trace_is_unreadable(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "challenge"
    (challenge_dir / "distfiles").mkdir(parents=True)

    trace_path = tmp_path / "broken.jsonl"
    trace_path.write_bytes(b"\xff\xfe\x00")

    record = build_result_record(
        result=SolverResult(
            flag="flag{demo}",
            status=FLAG_FOUND,
            findings_summary="trace unavailable",
            step_count=1,
            cost_usd=0.1,
            log_path=str(trace_path),
            model_spec="openai/gpt-5.3-codex",
        )
    )

    writeup_path = write_writeup(
        ChallengeMeta(name="损坏日志题", platform="lingxu-event-ctf", event_id=198),
        challenge_dir,
        record,
        tmp_path / "writeups",
    )

    assert writeup_path.exists()
    assert "暂无可提取的关键步骤" in writeup_path.read_text(encoding="utf-8")
