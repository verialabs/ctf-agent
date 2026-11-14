"""Solver result type, status constants, and solver protocol — shared across all backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Status constants
FLAG_FOUND = "flag_found"
GAVE_UP = "gave_up"
CANCELLED = "cancelled"
ERROR = "error"
QUOTA_ERROR = "quota_error"
POLICY_BLOCKED = "policy_blocked"  # provider safety filter stopped the turn

# Provider safety-filter messages (OpenAI cyber classifier, Claude usage-policy refusal)
_POLICY_BLOCK_MARKERS = (
    "flagged for possible cybersecurity risk",
    "cyber permissive safeguards",
    "violate our usage policy",
)


def is_policy_block(text: str | None) -> bool:
    """True if an error/result text is a provider safety-filter block."""
    t = (text or "").lower()
    return any(m in t for m in _POLICY_BLOCK_MARKERS)

# Flag confirmation markers from CTFd
CORRECT_MARKERS = ("CORRECT", "ALREADY SOLVED")


@dataclass
class SolverResult:
    flag: str | None
    status: str
    findings_summary: str
    step_count: int
    cost_usd: float
    log_path: str


class SolverProtocol(Protocol):
    """Common interface for all solver backends (Pydantic AI, Claude SDK, Codex)."""

    model_spec: str
    agent_name: str
    sandbox: object

    async def start(self) -> None: ...
    async def run_until_done_or_gave_up(self) -> SolverResult: ...
    def bump(self, insights: str) -> None: ...
    async def stop(self) -> None: ...
