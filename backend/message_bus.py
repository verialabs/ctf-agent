"""Per-challenge message bus for inter-agent communication."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass
class Finding:
    model: str
    content: str
    timestamp: float = field(default_factory=time.time)


MAX_FINDINGS = 200


class CoordinatorEventType(StrEnum):
    TASK_QUEUED = "task_queued"
    TASK_STARTED = "task_started"
    TASK_BLOCKED = "task_blocked"
    TASK_SOLVED = "task_solved"
    TASK_FAILED = "task_failed"
    HELP_REQUEST = "help_request"
    HELP_ACCEPTED = "help_accepted"
    FLAG_CANDIDATE = "flag_candidate"


@dataclass(frozen=True)
class CoordinatorEvent:
    kind: CoordinatorEventType
    challenge: str
    source: str = "coordinator"
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class CoordinatorEventBus:
    """Typed coordinator events with a bounded audit trail."""

    max_history: int = 500
    history: list[CoordinatorEvent] = field(default_factory=list)
    _queue: asyncio.Queue[CoordinatorEvent] = field(default_factory=asyncio.Queue)

    async def publish(self, event: CoordinatorEvent) -> None:
        self.history.append(event)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history :]
        self._queue.put_nowait(event)

    def drain(self) -> list[CoordinatorEvent]:
        events: list[CoordinatorEvent] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                return events


@dataclass
class ChallengeMessageBus:
    """Append-only shared findings list with per-model cursors."""

    findings: list[Finding] = field(default_factory=list)
    cursors: dict[str, int] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def post(self, model: str, content: str) -> None:
        """Post a finding from a solver."""
        async with self._lock:
            self.findings.append(Finding(model=model, content=content))
            if len(self.findings) > MAX_FINDINGS:
                trim = len(self.findings) - MAX_FINDINGS
                self.findings = self.findings[trim:]
                self.cursors = {k: max(0, v - trim) for k, v in self.cursors.items()}

    async def check(self, model: str) -> list[Finding]:
        """Get unread findings from other models. Advances the cursor."""
        async with self._lock:
            cursor = self.cursors.get(model, 0)
            unread = [f for f in self.findings[cursor:] if f.model != model]
            self.cursors[model] = len(self.findings)
            return unread

    async def broadcast(self, content: str, source: str = "coordinator") -> None:
        """Coordinator broadcasts a message to all solvers."""
        await self.post(source, content)

    def format_unread(self, findings: list[Finding]) -> str:
        """Format findings for injection into a solver prompt."""
        if not findings:
            return ""
        parts = [f"[{f.model}] {f.content}" for f in findings]
        return "**Findings from other agents:**\n\n" + "\n\n".join(parts)
