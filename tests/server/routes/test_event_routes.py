import json
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from flocks.auth.context import AuthUser
from flocks.server.routes import event as event_routes
from flocks.server.routes.event import EventBroadcaster
from flocks.session.session import SessionInfo


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


@pytest.mark.asyncio
async def test_event_broadcaster_keeps_latest_event_when_drop_target_is_max_minus_one():
    broadcaster = EventBroadcaster(queue_maxsize=3, queue_drop_to=2)
    queue = await broadcaster.subscribe()

    for event_type in ("event.one", "event.two", "event.three", "event.latest"):
        await broadcaster.publish({"type": event_type, "properties": {}})

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert events[-1]["type"] == "event.latest"
    assert any(event["type"] == "server.events_dropped" for event in events)


@pytest.mark.asyncio
async def test_event_broadcaster_coalesces_accumulated_text_and_preserves_delta():
    broadcaster = EventBroadcaster(queue_maxsize=3)
    queue = await broadcaster.subscribe()
    first = {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": "part-1",
                "messageID": "message-1",
                "sessionID": "session-1",
                "type": "text",
                "text": "hello",
            },
            "delta": "hello",
        },
    }
    latest = {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": "part-1",
                "messageID": "message-1",
                "sessionID": "session-1",
                "type": "text",
                "text": "hello world",
            },
            "delta": " world",
        },
    }

    broadcaster._publish_to_queue(queue, first)
    broadcaster._publish_to_queue(queue, latest)

    assert queue.qsize() == 1
    event = queue.get_nowait()
    assert event["properties"]["part"]["text"] == "hello world"
    assert event["properties"]["delta"] == "hello world"


@pytest.mark.asyncio
async def test_event_broadcaster_coalesces_snapshots_across_interleaved_events():
    broadcaster = EventBroadcaster(queue_maxsize=4)
    queue = await broadcaster.subscribe()
    first = {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": "part-1",
                "messageID": "message-1",
                "sessionID": "session-1",
                "type": "text",
                "text": "hello",
            },
            "delta": "hello",
        },
    }
    control = {"type": "question.asked", "properties": {"requestID": "question-1"}}
    latest = {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": "part-1",
                "messageID": "message-1",
                "sessionID": "session-1",
                "type": "text",
                "text": "hello world",
            },
            "delta": " world",
        },
    }

    broadcaster._publish_to_queue(queue, first)
    broadcaster._publish_to_queue(queue, control)
    broadcaster._publish_to_queue(queue, latest)

    assert queue.qsize() == 2
    assert queue.get_nowait() == control
    snapshot = queue.get_nowait()
    assert snapshot["properties"]["part"]["text"] == "hello world"
    assert snapshot["properties"]["delta"] == "hello world"


@pytest.mark.asyncio
async def test_accumulated_text_queue_memory_tracks_latest_snapshot_not_full_history():
    broadcaster = EventBroadcaster(queue_maxsize=1000)
    queue = await broadcaster.subscribe()
    text = ""

    for _ in range(1000):
        delta = "x" * 100
        text += delta
        broadcaster._publish_to_queue(queue, {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "id": "part-1",
                    "messageID": "message-1",
                    "sessionID": "session-1",
                    "type": "text",
                    "text": text,
                },
                "delta": delta,
            },
        })

    assert queue.qsize() == 1
    payload = queue.get_nowait()
    assert payload["properties"]["delta"] == text
    assert len(json.dumps(payload)) < 250_000


@pytest.mark.asyncio
async def test_text_snapshot_does_not_evict_control_events():
    broadcaster = EventBroadcaster(queue_maxsize=3, queue_drop_to=1)
    queue = await broadcaster.subscribe()
    controls = [
        {"type": "permission.request", "properties": {"requestID": "permission-1"}},
        {"type": "question.asked", "properties": {"id": "question-1"}},
    ]
    for event in controls:
        broadcaster._publish_to_queue(queue, event)

    def _snapshot(part_id: str) -> dict:
        return {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "id": part_id,
                    "messageID": "message-1",
                    "sessionID": "session-1",
                    "type": "text",
                    "text": part_id,
                },
                "delta": part_id,
            },
        }

    broadcaster._publish_to_queue(queue, _snapshot("part-old"))
    broadcaster._publish_to_queue(queue, _snapshot("part-latest"))

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert [event["type"] for event in events[:2]] == [
        "permission.request",
        "question.asked",
    ]
    assert events[-1]["type"] == "server.events_dropped"


@pytest.mark.asyncio
async def test_snapshot_dropped_behind_full_control_queue_emits_deferred_marker():
    broadcaster = EventBroadcaster(queue_maxsize=3, queue_drop_to=1)
    queue = await broadcaster.subscribe()
    controls = [
        {"type": f"control.{index}", "properties": {"index": index}}
        for index in range(3)
    ]
    for event in controls:
        broadcaster._publish_to_queue(queue, event)

    broadcaster._publish_to_queue(queue, {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": "part-dropped",
                "messageID": "message-1",
                "sessionID": "session-1",
                "type": "text",
                "text": "dropped",
            },
            "delta": "dropped",
        },
    })

    assert queue.qsize() == 3
    assert await queue.get() == controls[0]
    assert await queue.get() == controls[1]
    assert await queue.get() == controls[2]
    marker = await queue.get()
    assert marker["type"] == "server.events_dropped"
    assert marker["properties"]["dropped"] == 1


@pytest.mark.asyncio
async def test_session_events_are_filtered_by_subscriber_user(monkeypatch: pytest.MonkeyPatch):
    broadcaster = EventBroadcaster()
    alice = AuthUser(id="user-alice", username="alice", role="member")
    bob = AuthUser(id="user-bob", username="bob", role="member")
    session = SessionInfo(
        id="session-private",
        projectID="project-1",
        directory="/tmp/project-1",
        ownerUserID=alice.id,
        ownerUsername=alice.username,
    )

    async def _get_event_session(session_id: str):
        assert session_id == session.id
        return session

    monkeypatch.setattr(event_routes, "_get_event_session", _get_event_session)
    alice_queue = await broadcaster.subscribe(alice)
    bob_queue = await broadcaster.subscribe(bob)

    private_event = {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": "part-1",
                "messageID": "message-1",
                "sessionID": session.id,
                "type": "text",
                "text": "private content",
            }
        },
    }
    await broadcaster.publish(private_event)

    assert alice_queue.get_nowait() == private_event
    assert bob_queue.empty()

    global_event = {"type": "workflow.updated", "properties": {"id": "workflow-1"}}
    await broadcaster.publish(global_event)
    assert alice_queue.get_nowait() == global_event
    assert bob_queue.get_nowait() == global_event


@pytest.mark.asyncio
async def test_deleted_session_event_can_still_resolve_its_owner(monkeypatch: pytest.MonkeyPatch):
    from flocks.session.session import Session
    from flocks.storage.storage import Storage

    deleted = SessionInfo(
        id="session-deleted",
        projectID="project-1",
        directory="/tmp/project-1",
        status="deleted",
        ownerUserID="user-owner",
        ownerUsername="owner",
    )
    monkeypatch.setattr(Session, "_all_sessions_cache", None)
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(Storage, "list_keys", AsyncMock(return_value=["session:project-1:session-deleted"]))
    monkeypatch.setattr(Storage, "get", AsyncMock(return_value=deleted))

    assert await event_routes._get_event_session(deleted.id) == deleted


@pytest.mark.asyncio
async def test_global_event_subscription_keeps_authenticated_user(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import global_ as global_routes

    user = AuthUser(id="user-alice", username="alice", role="member")
    request = Request({"type": "http", "method": "GET", "path": "/global/event", "headers": []})
    request.state.auth_user = user
    broadcaster = EventBroadcaster()
    subscribe = AsyncMock(wraps=broadcaster.subscribe)
    monkeypatch.setattr(broadcaster, "subscribe", subscribe)
    monkeypatch.setattr(global_routes.EventBroadcaster, "get", classmethod(lambda cls: broadcaster))

    response = await global_routes.get_global_events(request)

    subscribe.assert_awaited_once_with(user)
    await response.body_iterator.aclose()
