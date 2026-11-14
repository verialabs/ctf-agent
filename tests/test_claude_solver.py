import asyncio
from typing import Any

from backend.agents.claude_solver import _HOST_SAFE_TOOLS, ClaudeSolver


class RecordingTracer:
    def __init__(self):
        self.events = []

    def event(self, event_type, **data):
        self.events.append((event_type, data))


def make_solver(*, no_submit, submit_fn=None):
    solver: Any = object.__new__(ClaudeSolver)
    solver.no_submit = no_submit
    solver.submit_fn = submit_fn
    solver._confirmed = False
    solver._flag = None
    solver._findings = ""
    solver.tracer = RecordingTracer()
    return solver


def test_no_submit_flag_command_marks_flag_confirmed():
    solver = make_solver(no_submit=True)

    display = asyncio.run(solver._submit_flag_command("ECW{test}", step=10))

    assert display == 'DRY RUN — would submit "ECW{test}"'
    assert solver._confirmed is True
    assert solver._flag == "ECW{test}"
    assert solver._findings == "Flag found via submit_flag: ECW{test}"
    assert solver.tracer.events == [
        (
            "flag_confirmed",
            {"flag": "ECW{test}", "step": 10, "dry_run": True},
        )
    ]


def test_live_flag_command_uses_swarm_submission_callback():
    calls = []

    async def submit(flag):
        calls.append(flag)
        return f'CORRECT — "{flag}" accepted.', True

    solver = make_solver(no_submit=False, submit_fn=submit)

    display = asyncio.run(solver._submit_flag_command("ECW{live}", step=4))

    assert display == 'CORRECT — "ECW{live}" accepted.'
    assert calls == ["ECW{live}"]
    assert solver._confirmed is True
    assert solver._flag == "ECW{live}"


def test_structured_output_is_the_only_extra_host_safe_tool():
    assert "StructuredOutput" in _HOST_SAFE_TOOLS
    assert "Read" not in _HOST_SAFE_TOOLS
    assert "Write" not in _HOST_SAFE_TOOLS
    assert "Edit" not in _HOST_SAFE_TOOLS
