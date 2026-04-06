"""Task 2 lifecycle helpers: normalized result records and writeup policy."""

from __future__ import annotations

from typing import Literal, TypedDict

from backend.solver_base import FLAG_FOUND, SolverResult

WriteupMode = Literal["off", "confirmed", "solved"]


class ChallengeResultRecord(TypedDict):
    flag: str | None
    solve_status: str
    submit_status: str
    submit_display: str
    confirmed: bool
    winner_model: str
    findings_summary: str
    log_path: str
    writeup_path: str
    writeup_status: str
    writeup_error: str
    env_cleanup_status: str
    env_cleanup_error: str


def build_result_record(
    *,
    result: SolverResult,
    submit_status: str = "",
    submit_display: str = "",
    confirmed: bool = False,
    winner_model: str | None = None,
    writeup_path: str = "",
    writeup_status: str = "pending",
    writeup_error: str = "",
    env_cleanup_status: str = "skipped",
    env_cleanup_error: str = "",
) -> ChallengeResultRecord:
    return {
        "flag": result.flag,
        "solve_status": result.status,
        "submit_status": submit_status,
        "submit_display": submit_display,
        "confirmed": confirmed,
        "winner_model": winner_model if winner_model is not None else result.model_spec,
        "findings_summary": result.findings_summary,
        "log_path": result.log_path,
        "writeup_path": writeup_path,
        "writeup_status": writeup_status,
        "writeup_error": writeup_error,
        "env_cleanup_status": env_cleanup_status,
        "env_cleanup_error": env_cleanup_error,
    }


def should_generate_writeup(mode: WriteupMode, record: ChallengeResultRecord) -> bool:
    if mode == "off":
        return False
    if mode == "confirmed":
        return bool(record["confirmed"])
    if mode == "solved":
        return record["solve_status"] == FLAG_FOUND
    return False
