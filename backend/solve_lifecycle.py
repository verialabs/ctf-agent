"""Task 2 lifecycle helpers: normalized result records and writeup policy."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypedDict

from backend.deps import CoordinatorDeps, ReleasedEnvKey
from backend.prompts import ChallengeMeta
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


def _build_no_result_record() -> ChallengeResultRecord:
    return {
        "flag": None,
        "solve_status": "no_result",
        "submit_status": "",
        "submit_display": "",
        "confirmed": False,
        "winner_model": "",
        "findings_summary": "",
        "log_path": "",
        "writeup_path": "",
        "writeup_status": "skipped",
        "writeup_error": "",
        "env_cleanup_status": "skipped",
        "env_cleanup_error": "",
    }


def _released_env_key(
    *,
    challenge_name: str,
    challenge_dir: str,
    meta: ChallengeMeta,
) -> ReleasedEnvKey:
    platform = str(meta.platform or "local")
    event_id = "" if meta.event_id is None else str(meta.event_id)
    if meta.platform_challenge_id is not None:
        challenge_id = str(meta.platform_challenge_id)
    else:
        challenge_id = str(Path(challenge_dir).resolve())
    return (platform, event_id, challenge_id, str(meta.name or challenge_name))


async def finalize_swarm_result(
    *,
    deps: CoordinatorDeps,
    challenge_name: str,
    challenge_dir: str,
    meta: ChallengeMeta,
    swarm: Any,
    result: SolverResult | None,
) -> ChallengeResultRecord:
    if result is None:
        record = _build_no_result_record()
        deps.results[challenge_name] = record
        return record

    released_key = _released_env_key(
        challenge_name=challenge_name,
        challenge_dir=challenge_dir,
        meta=meta,
    )
    submit_status = getattr(swarm, "confirmed_submit_status", "")
    submit_display = getattr(swarm, "confirmed_submit_display", "")
    confirmed = submit_status in ("correct", "already_solved")
    record = build_result_record(
        result=result,
        submit_status=submit_status,
        submit_display=submit_display,
        confirmed=confirmed,
    )

    if (
        submit_status in ("correct", "already_solved")
        and meta.requires_env_start is True
        and deps.no_submit is False
        and released_key not in deps.released_envs
    ):
        try:
            await deps.ctfd.release_challenge_env(meta)
            deps.released_envs.add(released_key)
            record["env_cleanup_status"] = "released"
            record["env_cleanup_error"] = ""
        except Exception as exc:
            record["env_cleanup_status"] = "failed"
            record["env_cleanup_error"] = str(exc)

    if should_generate_writeup(deps.settings.writeup_mode, record):
        try:
            from backend.writeups import write_writeup

            writeup_path = write_writeup(meta, challenge_dir, record, deps.settings.writeup_dir)
            record["writeup_path"] = str(writeup_path)
            record["writeup_status"] = "generated"
            record["writeup_error"] = ""
        except Exception as exc:
            record["writeup_status"] = "failed"
            record["writeup_error"] = str(exc)
    else:
        record["writeup_status"] = "skipped"
        record["writeup_error"] = ""

    deps.results[challenge_name] = record
    return record
