from backend.control.working_memory import WorkingMemoryStore


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


def test_working_memory_ignores_successful_submit_flag_results() -> None:
    store = WorkingMemoryStore()
    store.apply_trace_events(
        challenge_name="web",
        events=[
            {"type": "tool_result", "tool": "submit_flag", "result": "INCORRECT"},
            {"type": "tool_result", "tool": "submit_flag", "result": "CORRECT"},
            {"type": "tool_result", "tool": "submit_flag", "result": "accepted"},
        ],
    )

    memory = store.get("web")

    assert memory.failed_hypotheses == ["submit_flag returned INCORRECT"]


def test_working_memory_ignores_invalid_event_shapes() -> None:
    store = WorkingMemoryStore()
    store.apply_trace_events(
        challenge_name="misc",
        events=[
            {"type": "tool_result", "tool": "submit_flag", "result": "INCORRECT"},
            "bad-line",
            123,
            None,
            {"type": "bump", "insights": "Check argv parsing"},
        ],
    )

    memory = store.get("misc")
    assert memory.failed_hypotheses == ["submit_flag returned INCORRECT"]
    assert memory.last_guidance == ["Check argv parsing"]
