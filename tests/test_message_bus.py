import pytest

from backend.message_bus import CoordinatorEvent, CoordinatorEventBus, CoordinatorEventType


@pytest.mark.asyncio
async def test_coordinator_event_bus_is_typed_bounded_and_drainable() -> None:
    bus = CoordinatorEventBus(max_history=2)
    await bus.publish(CoordinatorEvent(CoordinatorEventType.TASK_QUEUED, "a"))
    await bus.publish(CoordinatorEvent(CoordinatorEventType.TASK_STARTED, "a"))
    await bus.publish(CoordinatorEvent(CoordinatorEventType.HELP_REQUEST, "a", source="solver"))

    assert [event.kind for event in bus.history] == [
        CoordinatorEventType.TASK_STARTED,
        CoordinatorEventType.HELP_REQUEST,
    ]
    assert len(bus.drain()) == 3
    assert bus.drain() == []
