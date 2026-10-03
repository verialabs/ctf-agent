from backend.task_registry import ChallengeRegistry, TaskStatus


def test_registry_prefers_priority_then_easy_tasks() -> None:
    registry = ChallengeRegistry()
    registry.register("hard", solves=1, value=500)
    registry.register("easy", solves=100, value=100)
    registry.register("urgent", solves=0, value=500, priority=10)
    for name in ("hard", "easy", "urgent"):
        registry.queue(name)

    assert registry.tasks["easy"].solves == 100
    assert registry.next_queued().name == "urgent"
    registry.start("urgent", path="/tmp/urgent", category="pwn")
    assert registry.next_queued().name == "easy"


def test_registry_tracks_reusable_slot_lifecycle_and_path() -> None:
    registry = ChallengeRegistry()
    registry.queue("one")
    task = registry.start("one", path="../pwn/one", category="pwn")
    assert task.status == TaskStatus.RUNNING
    assert task.attempts == 1
    assert task.path == "../pwn/one"

    registry.finish("one", TaskStatus.BLOCKED, "stuck")
    registry.queue("one", force=True)
    task = registry.start("one")
    assert task.attempts == 2
    assert task.path == "../pwn/one"

    registry.finish("one", TaskStatus.SOLVED)
    registry.queue("one")
    assert registry.tasks["one"].status == TaskStatus.SOLVED
