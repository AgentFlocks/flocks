import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.server.routes import event as event_routes
from flocks.server.routes.event import EventBroadcaster
from flocks.session.session import SessionInfo

_ADMIN = AuthUser(id="user-admin", username="admin", role="admin")
_ALICE = AuthUser(id="user-alice", username="alice", role="member")
_BOB = AuthUser(id="user-bob", username="bob", role="member")


def _snapshot(text: str, delta: str, part_type: str = "text") -> dict:
    return {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": "part-1",
                "messageID": "message-1",
                "sessionID": "session-1",
                "type": part_type,
                "text": text,
            },
            "delta": delta,
        },
    }


@pytest.mark.asyncio
async def test_event_broadcaster_compacts_overflowing_client_queue():
    broadcaster = EventBroadcaster(queue_maxsize=3, queue_drop_to=1)
    queue = await broadcaster.subscribe(_ADMIN)

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
    queue = await broadcaster.subscribe(_ADMIN)

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
    queue = await broadcaster.subscribe(_ADMIN)

    for event_type in ("event.one", "event.two", "event.three", "event.latest"):
        await broadcaster.publish({"type": event_type, "properties": {}})

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert events[-1]["type"] == "event.latest"
    assert any(event["type"] == "server.events_dropped" for event in events)


@pytest.mark.asyncio
async def test_event_broadcaster_preserves_low_backlog_snapshot_cadence():
    broadcaster = EventBroadcaster(queue_maxsize=10, queue_coalesce_at=3)
    queue = await broadcaster.subscribe(_ADMIN)
    first = _snapshot("hello", "hello")
    latest = _snapshot("hello world", " world")

    broadcaster._publish_to_queue(queue, first)
    broadcaster._publish_to_queue(queue, latest)

    assert queue.qsize() == 2
    assert queue.get_nowait() == first
    assert queue.get_nowait() == latest


@pytest.mark.parametrize("part_type", ["text", "reasoning"])
@pytest.mark.asyncio
async def test_event_broadcaster_coalesces_adjacent_snapshots_at_high_water(part_type: str):
    broadcaster = EventBroadcaster(queue_maxsize=10, queue_coalesce_at=3)
    queue = await broadcaster.subscribe(_ADMIN)
    snapshots = [
        _snapshot("h", "h", part_type),
        _snapshot("he", "e", part_type),
        _snapshot("hel", "l", part_type),
        _snapshot("hello", "lo", part_type),
    ]

    for snapshot in snapshots:
        broadcaster._publish_to_queue(queue, snapshot)

    assert queue.qsize() == 3
    assert queue.get_nowait() == snapshots[0]
    assert queue.get_nowait() == snapshots[1]
    merged = queue.get_nowait()
    assert merged["properties"]["part"]["text"] == "hello"
    assert merged["properties"]["delta"] == "llo"


@pytest.mark.asyncio
async def test_event_broadcaster_does_not_coalesce_across_control_events():
    broadcaster = EventBroadcaster(queue_maxsize=10, queue_coalesce_at=2)
    queue = await broadcaster.subscribe(_ADMIN)
    first = _snapshot("hello", "hello")
    control = {"type": "question.asked", "properties": {"requestID": "question-1"}}
    latest = _snapshot("hello world", " world")

    broadcaster._publish_to_queue(queue, first)
    broadcaster._publish_to_queue(queue, control)
    broadcaster._publish_to_queue(queue, latest)

    assert queue.qsize() == 3
    assert queue.get_nowait() == first
    assert queue.get_nowait() == control
    assert queue.get_nowait() == latest


@pytest.mark.asyncio
async def test_message_finish_is_an_ordering_barrier_at_high_water():
    broadcaster = EventBroadcaster(queue_maxsize=10, queue_coalesce_at=2)
    queue = await broadcaster.subscribe(_ADMIN)
    first = _snapshot("hello", "hello")
    finish = {
        "type": "message.updated",
        "properties": {
            "info": {
                "id": "message-1",
                "sessionID": "session-1",
                "finish": "stop",
            },
        },
    }
    later = _snapshot("hello world", " world")

    broadcaster._publish_to_queue(queue, first)
    broadcaster._publish_to_queue(queue, finish)
    broadcaster._publish_to_queue(queue, later)

    assert queue.qsize() == 3
    assert queue.get_nowait() == first
    assert queue.get_nowait() == finish
    assert queue.get_nowait() == later


@pytest.mark.asyncio
async def test_high_backlog_snapshot_memory_remains_bounded():
    broadcaster = EventBroadcaster(queue_maxsize=1000, queue_coalesce_at=4)
    queue = await broadcaster.subscribe(_ADMIN)
    text = ""

    for _ in range(1000):
        delta = "x" * 100
        text += delta
        broadcaster._publish_to_queue(queue, _snapshot(text, delta))

    assert queue.qsize() == 4
    payloads = [queue.get_nowait() for _ in range(queue.qsize())]
    assert "".join(payload["properties"]["delta"] for payload in payloads) == text
    assert payloads[-1]["properties"]["part"]["text"] == text
    assert len(json.dumps(payloads)) < 250_000


@pytest.mark.asyncio
async def test_text_snapshot_does_not_evict_control_events():
    broadcaster = EventBroadcaster(queue_maxsize=3, queue_drop_to=1)
    queue = await broadcaster.subscribe(_ADMIN)
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
    queue = await broadcaster.subscribe(_ADMIN)
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


@pytest.mark.asyncio
async def test_subscribe_requires_authenticated_user():
    broadcaster = EventBroadcaster()

    with pytest.raises(ValueError):
        await broadcaster.subscribe(None)
    assert broadcaster.client_count == 0


@pytest.mark.asyncio
async def test_non_session_event_only_reaches_publishing_user():
    broadcaster = EventBroadcaster()
    alice_tab_1 = await broadcaster.subscribe(_ALICE)
    alice_tab_2 = await broadcaster.subscribe(_ALICE)
    bob_queue = await broadcaster.subscribe(_BOB)
    admin_queue = await broadcaster.subscribe(_ADMIN)

    event = {"type": "workflow.created", "properties": {"id": "workflow-1", "name": "alice flow"}}
    token = set_current_auth_user(_ALICE)
    try:
        await broadcaster.publish(event)
    finally:
        reset_current_auth_user(token)

    assert alice_tab_1.get_nowait() == event
    assert alice_tab_2.get_nowait() == event
    assert bob_queue.empty()
    assert admin_queue.empty()


@pytest.mark.asyncio
async def test_non_session_event_without_user_context_reaches_admins_only():
    broadcaster = EventBroadcaster()
    alice_queue = await broadcaster.subscribe(_ALICE)
    admin_queue = await broadcaster.subscribe(_ADMIN)

    event = {"type": "contracts.webui.pages.updated", "properties": {"id": "page-1", "hash": "abc"}}
    await broadcaster.publish(event)

    assert admin_queue.get_nowait() == event
    assert alice_queue.empty()


@pytest.mark.asyncio
async def test_non_session_event_from_api_token_service_reaches_admins_only():
    from flocks.server.auth import _build_api_token_user

    service_user = _build_api_token_user()
    broadcaster = EventBroadcaster()
    alice_queue = await broadcaster.subscribe(_ALICE)
    admin_queue = await broadcaster.subscribe(_ADMIN)
    tui_queue = await broadcaster.subscribe(service_user)

    event = {"type": "hub.scene_suites.changed", "properties": {"suiteId": "soc", "action": "install"}}
    token = set_current_auth_user(service_user)
    try:
        await broadcaster.publish(event)
    finally:
        reset_current_auth_user(token)

    assert admin_queue.get_nowait() == event
    assert tui_queue.get_nowait() == event
    assert alice_queue.empty()


@pytest.mark.asyncio
async def test_publish_event_routes_to_context_user(monkeypatch: pytest.MonkeyPatch):
    broadcaster = EventBroadcaster()
    monkeypatch.setattr(EventBroadcaster, "_instance", broadcaster)
    alice_queue = await broadcaster.subscribe(_ALICE)
    bob_queue = await broadcaster.subscribe(_BOB)

    token = set_current_auth_user(_BOB)
    try:
        await event_routes.publish_event("tui.toast.show", {"message": "for bob only"})
    finally:
        reset_current_auth_user(token)

    assert bob_queue.get_nowait()["type"] == "tui.toast.show"
    assert alice_queue.empty()


def _session(session_id: str, owner: AuthUser, **metadata) -> SessionInfo:
    return SessionInfo(
        id=session_id,
        projectID="project-1",
        directory="/tmp/project-1",
        ownerUserID=owner.id,
        ownerUsername=owner.username,
        metadata=metadata,
    )


async def _publish_tui_as(user: AuthUser, tui_event) -> None:
    from flocks.server.routes import tui as tui_routes

    auth_ctx = set_current_auth_user(user)
    try:
        await tui_routes.publish_tui_event_endpoint(tui_event)
    finally:
        reset_current_auth_user(auth_ctx)


@pytest.fixture
def tui_broadcaster(monkeypatch: pytest.MonkeyPatch) -> EventBroadcaster:
    from flocks.server.routes import tui as tui_routes

    broadcaster = EventBroadcaster()
    monkeypatch.setattr(EventBroadcaster, "_instance", broadcaster)
    monkeypatch.setattr(tui_routes, "_control_events", asyncio.Queue())
    return broadcaster


@pytest.mark.asyncio
async def test_tui_publish_with_shared_session_id_only_reaches_publisher(
    monkeypatch: pytest.MonkeyPatch,
    tui_broadcaster: EventBroadcaster,
):
    from flocks.server.routes import tui as tui_routes

    shared = _session("session-shared", _ALICE, shared_local=True)
    monkeypatch.setattr(event_routes, "_get_event_session", AsyncMock(return_value=shared))
    alice_queue = await tui_broadcaster.subscribe(_ALICE)
    bob_queue = await tui_broadcaster.subscribe(_BOB)
    admin_queue = await tui_broadcaster.subscribe(_ADMIN)

    await _publish_tui_as(_ALICE, tui_routes.EventTuiCommandExecute(
        properties={"command": "session.interrupt", "sessionID": shared.id},
    ))

    received = alice_queue.get_nowait()
    assert received["type"] == "tui.command.execute"
    assert received["properties"]["sessionID"] == shared.id
    assert bob_queue.empty()
    assert admin_queue.empty()


@pytest.mark.asyncio
async def test_tui_publish_cannot_target_another_users_session(
    monkeypatch: pytest.MonkeyPatch,
    tui_broadcaster: EventBroadcaster,
):
    from flocks.server.routes import tui as tui_routes

    bob_private = _session("session-bob", _BOB)
    monkeypatch.setattr(event_routes, "_get_event_session", AsyncMock(return_value=bob_private))
    alice_queue = await tui_broadcaster.subscribe(_ALICE)
    bob_queue = await tui_broadcaster.subscribe(_BOB)

    await _publish_tui_as(_ALICE, tui_routes.EventTuiSessionSelect(
        properties={"sessionID": bob_private.id},
    ))
    await _publish_tui_as(_ALICE, tui_routes.EventTuiCommandExecute(
        properties={"command": "app.exit", "sessionID": bob_private.id},
    ))

    assert [alice_queue.get_nowait()["type"] for _ in range(2)] == [
        "tui.session.select",
        "tui.command.execute",
    ]
    assert bob_queue.empty()


@pytest.mark.parametrize("event_type", [
    "tui.prompt.append",
    "tui.command.execute",
    "tui.toast.show",
    "tui.session.select",
])
@pytest.mark.asyncio
async def test_tui_events_from_api_token_with_session_id_reach_admins_only(
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
):
    from flocks.server.auth import _build_api_token_user

    service_user = _build_api_token_user()
    alice_session = _session("session-alice", _ALICE, shared_local=True)
    monkeypatch.setattr(event_routes, "_get_event_session", AsyncMock(return_value=alice_session))
    broadcaster = EventBroadcaster()
    alice_queue = await broadcaster.subscribe(_ALICE)
    admin_queue = await broadcaster.subscribe(_ADMIN)
    tui_queue = await broadcaster.subscribe(service_user)

    event = {"type": event_type, "properties": {"sessionID": alice_session.id}}
    auth_ctx = set_current_auth_user(service_user)
    try:
        await broadcaster.publish(event)
    finally:
        reset_current_auth_user(auth_ctx)

    assert admin_queue.get_nowait() == event
    assert tui_queue.get_nowait() == event
    assert alice_queue.empty()


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
