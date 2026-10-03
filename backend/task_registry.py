"""In-memory challenge registry and work-conserving task queue."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from enum import StrEnum


class TaskStatus(StrEnum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    SOLVED = "solved"
    FAILED = "failed"


@dataclass
class ChallengeTask:
    name: str
    category: str = ""
    path: str = ""
    value: int = 0
    solves: int = 0
    priority: int = 0
    status: TaskStatus = TaskStatus.DISCOVERED
    attempts: int = 0
    last_error: str = ""
    updated_at: float = 0.0

    def as_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data


class ChallengeRegistry:
    """Single source of truth for challenge paths and scheduling state."""

    def __init__(self) -> None:
        self.tasks: dict[str, ChallengeTask] = {}

    def register(
        self,
        name: str,
        *,
        category: str = "",
        path: str = "",
        value: int | None = None,
        solves: int | None = None,
        priority: int | None = None,
    ) -> ChallengeTask:
        task = self.tasks.get(name)
        if task is None:
            task = ChallengeTask(name=name, updated_at=time.time())
            self.tasks[name] = task
        if category:
            task.category = category
        if path:
            task.path = path
        if value is not None:
            task.value = value
        if solves is not None:
            task.solves = solves
        if priority is not None:
            task.priority = priority
        task.updated_at = time.time()
        return task

    def queue(self, name: str, *, force: bool = False) -> ChallengeTask:
        task = self.register(name)
        if force or task.status not in (TaskStatus.RUNNING, TaskStatus.SOLVED):
            task.status = TaskStatus.QUEUED
            task.last_error = ""
            task.updated_at = time.time()
        return task

    def start(self, name: str, *, path: str = "", category: str = "") -> ChallengeTask:
        task = self.register(name, path=path, category=category)
        task.status = TaskStatus.RUNNING
        task.attempts += 1
        task.last_error = ""
        task.updated_at = time.time()
        return task

    def finish(self, name: str, status: TaskStatus, error: str = "") -> ChallengeTask:
        task = self.register(name)
        task.status = status
        task.last_error = error
        task.updated_at = time.time()
        return task

    def next_queued(self) -> ChallengeTask | None:
        queued = [task for task in self.tasks.values() if task.status == TaskStatus.QUEUED]
        if not queued:
            return None
        # Explicit priority first, then community solve count (easier first), then value/name.
        return min(queued, key=lambda task: (-task.priority, -task.solves, task.value, task.name))

    def snapshot(self) -> list[dict]:
        return [self.tasks[name].as_dict() for name in sorted(self.tasks)]
