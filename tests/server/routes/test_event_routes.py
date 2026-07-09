import pytest

from flocks.server.routes.event import EventBroadcaster


@pytest.mark.asyncio
async def test_event_broadcaster_compacts_overflowing_client_queue():
    broadcaster = EventBroadcaster(queue_maxsize=3, queue_drop_to=1)
    queue = await broadcaster.subscribe()

    for index in range(5):
        await broadcaster.publish({
            "type": "message.part.updated",
            "properties": {"index": index},
        })

    assert queue.qsize() <= 3

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert any(event["type"] == "server.events_dropped" for event in events)
    assert events[-1] == {
        "type": "message.part.updated",
        "properties": {"index": 4},
    }


@pytest.mark.asyncio
async def test_event_broadcaster_shutdown_does_not_block_on_full_queue():
    broadcaster = EventBroadcaster(queue_maxsize=2, queue_drop_to=0)
    queue = await broadcaster.subscribe()

    await broadcaster.publish({"type": "event.one", "properties": {}})
    await broadcaster.publish({"type": "event.two", "properties": {}})
    await broadcaster.shutdown()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert broadcaster.client_count == 0
    assert any(event["type"] == "server.shutting_down" for event in events)
