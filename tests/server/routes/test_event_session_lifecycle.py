"""Session lifecycle events must reach SSE subscribers in the TUI/opencode shape.

``Session.create`` / ``Session.delete`` only publish on the in-process Bus; the
bridge in ``routes/event.py`` forwards them as ``session.updated {info}`` and
``session.deleted {info: {id}}`` (OPS-0255 / OPS-0257). ``session.updated``
published as ``{id, status}`` by the archive/restore/rename routes is enriched
with ``info`` so clients that only act on ``properties.info`` see it too.
"""

import pytest

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.bus.bus import Bus
from flocks.bus.events import SessionCreated, SessionDeleted
from flocks.server.routes import event as event_routes
from flocks.server.routes.event import EventBroadcaster
from flocks.session.session import SessionInfo


@pytest.fixture
def owner() -> AuthUser:
    return AuthUser(id="user-owner", username="owner", role="member")


@pytest.fixture
def session_info(owner: AuthUser) -> SessionInfo:
    return SessionInfo(
        id="session-lifecycle",
        projectID="project-1",
        directory="/tmp/project-1",
        title="lifecycle",
        ownerUserID=owner.id,
        ownerUsername=owner.username,
    )


@pytest.fixture
def broadcaster(monkeypatch: pytest.MonkeyPatch) -> EventBroadcaster:
    instance = EventBroadcaster()
    monkeypatch.setattr(EventBroadcaster, "_instance", instance)
    return instance


def _drain(queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_bridge_is_installed_once_on_import():
    assert event_routes._lifecycle_bridge_installed is True
    before = len(Bus._subscriptions.get(SessionCreated.type, []))
    event_routes.install_session_lifecycle_bridge()
    assert len(Bus._subscriptions.get(SessionCreated.type, [])) == before


@pytest.mark.asyncio
async def test_session_created_reaches_sse_as_session_updated_with_full_info(
    monkeypatch: pytest.MonkeyPatch, broadcaster: EventBroadcaster, owner: AuthUser, session_info: SessionInfo,
):
    async def _get_event_session(session_id: str):
        return session_info if session_id == session_info.id else None

    monkeypatch.setattr(event_routes, "_get_event_session", _get_event_session)
    stranger = AuthUser(id="user-stranger", username="stranger", role="member")
    owner_queue = await broadcaster.subscribe(owner)
    stranger_queue = await broadcaster.subscribe(stranger)

    await Bus.publish(SessionCreated, {"info": {"id": session_info.id, "title": session_info.title, "parentID": None, "projectID": "project-1"}})

    owner_events = [e for e in _drain(owner_queue) if e["type"] == "session.updated"]
    assert owner_events, "owner should receive session.updated for the new session"
    info = owner_events[0]["properties"]["info"]
    assert info["id"] == session_info.id
    assert info["title"] == "lifecycle"
    assert info["ownerUserID"] == owner.id
    assert "time" in info and "status" in info
    # per-subscriber read filtering still applies: a stranger cannot see a private session
    assert not [e for e in _drain(stranger_queue) if e["type"] == "session.updated"]


@pytest.mark.asyncio
async def test_session_deleted_reaches_sse_even_though_the_row_is_gone(
    monkeypatch: pytest.MonkeyPatch, broadcaster: EventBroadcaster, owner: AuthUser,
):
    async def _get_event_session(session_id: str):
        return None  # already deleted

    monkeypatch.setattr(event_routes, "_get_event_session", _get_event_session)
    queue = await broadcaster.subscribe(owner)
    stranger = AuthUser(id="user-stranger", username="stranger", role="member")
    stranger_queue = await broadcaster.subscribe(stranger)
    admin = AuthUser(id="user-admin", username="admin", role="admin")
    admin_queue = await broadcaster.subscribe(admin)

    # Deletion happens in the deleting user's request context.
    token = set_current_auth_user(owner)
    try:
        await Bus.publish(SessionDeleted, {"sessionID": "session-gone"})
    finally:
        reset_current_auth_user(token)

    expected = [{"type": "session.deleted", "properties": {"info": {"id": "session-gone"}}}]
    deleted = [e for e in _drain(queue) if e["type"] == "session.deleted"]
    assert deleted == expected
    assert [e for e in _drain(admin_queue) if e["type"] == "session.deleted"] == expected
    # No broadcast: other members never learn about the deleted session id.
    assert not [e for e in _drain(stranger_queue) if e["type"] == "session.deleted"]


@pytest.mark.asyncio
async def test_session_updated_id_status_is_enriched_with_info(
    monkeypatch: pytest.MonkeyPatch, broadcaster: EventBroadcaster, owner: AuthUser, session_info: SessionInfo,
):
    async def _get_event_session(session_id: str):
        return session_info if session_id == session_info.id else None

    monkeypatch.setattr(event_routes, "_get_event_session", _get_event_session)
    queue = await broadcaster.subscribe(owner)

    await event_routes.publish_event("session.updated", {"id": session_info.id, "status": "archived"})

    events = [e for e in _drain(queue) if e["type"] == "session.updated"]
    assert len(events) == 1
    props = events[0]["properties"]
    assert props["id"] == session_info.id and props["status"] == "archived"  # original keys kept
    assert props["info"]["id"] == session_info.id                              # TUI shape added


@pytest.mark.asyncio
async def test_session_updated_with_info_is_left_untouched(
    monkeypatch: pytest.MonkeyPatch, broadcaster: EventBroadcaster, owner: AuthUser, session_info: SessionInfo,
):
    async def _get_event_session(session_id: str):
        return session_info

    monkeypatch.setattr(event_routes, "_get_event_session", _get_event_session)
    queue = await broadcaster.subscribe(owner)
    payload = {"info": {"id": session_info.id, "title": "already shaped"}}

    await event_routes.publish_event("session.updated", payload)

    events = [e for e in _drain(queue) if e["type"] == "session.updated"]
    assert events[0]["properties"] == payload
