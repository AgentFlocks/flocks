"""Model mutation boundaries remain enforced below HTTP and tool dispatch."""

import asyncio
import json
import os
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session, SessionInfo
from flocks.storage.storage import Storage


OWNER = AuthUser(id="usr_owner", username="owner", role="member", status="active")
READER = AuthUser(id="usr_reader", username="reader", role="member", status="active")
ADMIN = AuthUser(id="usr_admin", username="admin", role="admin", status="active")


@contextmanager
def acting_as(user):
    token = set_current_auth_user(user)  # secret-guard: allow
    try:
        yield
    finally:
        reset_current_auth_user(token)


async def make_session(action, *, legacy=False):
    session = await Session.create(
        project_id="mutation_access_project", directory="/fixture",
        title="Retained source", owner_user_id=None if legacy else OWNER.id,
        owner_username=OWNER.username,
        metadata={"shared_read_access_user_ids": [READER.id]},
    )
    if action == "unarchive":
        assert await Session.archive(session.project_id, session.id)
        session = await Session.get(session.project_id, session.id)
    return session


async def mutate(action, session):
    if action == "update":
        return await Session.update(session.project_id, session.id, title="Changed")
    if action == "move":
        return await Session.move_to_project(
            session.project_id, session.id,
            target_project_id="mutation_target_project", target_directory="/target",
        )
    return await getattr(Session, action)(session.project_id, session.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["update", "delete", "archive", "unarchive", "move"])
@pytest.mark.parametrize("actor", [READER, ADMIN], ids=["shared-reader", "unrelated-admin"])
async def test_non_owner_cannot_mutate_readable_or_private_session(action, actor, monkeypatch):
    session = await make_session(action)
    stop = AsyncMock(return_value=True)
    monkeypatch.setattr(Session, "_stop_session_tree_for_archive", stop)
    from flocks.session.interaction_queue import InteractionQueue
    pause, resume = AsyncMock(), AsyncMock()
    monkeypatch.setattr(InteractionQueue, "pause", pause)
    monkeypatch.setattr(InteractionQueue, "resume", resume)

    with acting_as(actor):
        if actor is READER:
            assert await Session.get(session.project_id, session.id) is not None
        result = await mutate(action, session)
    assert result is None if action in {"update", "move"} else result is False
    stop.assert_not_awaited()
    pause.assert_not_awaited()
    resume.assert_not_awaited()
    assert not Session.is_lifecycle_transitioning(session.id)
    current = await Session.get(session.project_id, session.id)
    assert current is not None
    assert current.title == "Retained source"
    assert current.status == session.status
    assert current.owner_user_id == OWNER.id


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["update", "delete", "archive", "unarchive", "move"])
@pytest.mark.parametrize("kind", ["owner", "admin-legacy", "internal"])
async def test_authorized_model_mutations_remain_available(action, kind):
    session = await make_session(action, legacy=kind == "admin-legacy")
    actor = {"owner": OWNER, "admin-legacy": ADMIN, "internal": None}[kind]
    with acting_as(actor):
        result = await mutate(action, session)
    assert result


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["delete", "archive", "unarchive", "move"])
async def test_foreign_descendant_blocks_entire_tree_before_side_effects(action, monkeypatch):
    parent = await make_session(action)
    # Represent a pre-existing imported/malformed tree without relaxing create.
    child = await Session.create(
        project_id=parent.project_id, directory="/fixture", title="Foreign child",
        owner_user_id=READER.id, owner_username=READER.username,
    )
    child.parent_id = parent.id
    await Storage.set(f"session:{child.project_id}:{child.id}", child, "session")
    stop = AsyncMock(return_value=True)
    monkeypatch.setattr(Session, "_stop_session_tree_for_archive", stop)
    from flocks.session.interaction_queue import InteractionQueue
    pause, resume = AsyncMock(), AsyncMock()
    monkeypatch.setattr(InteractionQueue, "pause", pause)
    monkeypatch.setattr(InteractionQueue, "resume", resume)

    with acting_as(OWNER):
        result = await mutate(action, parent)
    assert result is None if action == "move" else result is False
    stop.assert_not_awaited()
    pause.assert_not_awaited()
    resume.assert_not_awaited()
    for session in (parent, child):
        current = await Session.get(session.project_id, session.id)
        assert current is not None and current.status == session.status
        assert not Session.is_lifecycle_transitioning(session.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("supply_owner", [False, True])
async def test_reader_cannot_attach_child_to_foreign_parent(supply_owner):
    parent = await make_session("create")
    options = {"owner_user_id": OWNER.id, "owner_username": OWNER.username} if supply_owner else {}
    with acting_as(READER):
        with pytest.raises(PermissionError):
            await Session.create(
                project_id=parent.project_id, directory="/fixture", parent_id=parent.id,
                **options,
            )
    assert await Session.children(parent.project_id, parent.id) == []


@pytest.mark.asyncio
async def test_owner_can_create_and_delete_own_tree():
    with acting_as(OWNER):
        parent = await make_session("create")
        child = await Session.create(
            project_id=parent.project_id, directory="/fixture", parent_id=parent.id,
        )
        assert child.owner_user_id == OWNER.id
        assert await Session.delete(parent.project_id, parent.id)
    assert await Session.get_by_id_unfiltered(parent.id) is None
    assert await Session.get_by_id_unfiltered(child.id) is None


@pytest.mark.asyncio
async def test_shared_reader_cannot_delete_through_session_tool():
    from flocks.tool.registry import ToolContext
    from flocks.tool.system.session_manage import _session_delete_impl

    session = await make_session("delete")
    await Message.create(session.id, MessageRole.USER, "Retained message")
    with acting_as(READER):
        result = await _session_delete_impl(
            ToolContext(session_id="caller", message_id="caller_message", agent="test"), session.id,
        )
    assert not result.success
    assert await Session.get_by_id_unfiltered(session.id) is not None
    assert len(await Message.list(session.id)) == 1


@pytest.mark.asyncio
async def test_restore_rejects_reader_before_project_restore(monkeypatch):
    from flocks.project.project import Project

    session = await make_session("unarchive")
    monkeypatch.setattr(Project, "registry_state", lambda *_args, **_kwargs: "removed")
    restore = AsyncMock(return_value=True)
    monkeypatch.setattr(Project, "restore", restore)
    with acting_as(READER):
        assert not await Session.restore(session.project_id, session.id, project_owner_id=OWNER.id)
    restore.assert_not_awaited()
    assert (await Session.get(session.project_id, session.id)).status == "archived"


@pytest.mark.asyncio
async def test_delete_rechecks_locked_rows_before_committing(monkeypatch):
    session = await make_session("delete")
    await Message.create(session.id, MessageRole.USER, "Retained message")
    original_mutate = Storage.mutate_many

    async def reassign_then_mutate(_cls, **kwargs):
        # Simulate another writer between the tree snapshot and delete's tx.
        key = f"session:{session.project_id}:{session.id}"
        current = await Storage.get(key, SessionInfo)
        current.owner_user_id = READER.id
        current.owner_username = READER.username
        await Storage.set(key, current, "session")
        return await original_mutate(**kwargs)

    monkeypatch.setattr(Storage, "mutate_many", classmethod(reassign_then_mutate))
    with acting_as(OWNER):
        assert not await Session.delete(session.project_id, session.id)
    # As stored: the other writer went around this process's session cache.
    current = await Storage.get(f"session:{session.project_id}:{session.id}", SessionInfo)
    assert current is not None and current.owner_user_id == READER.id
    assert len(await Message.list(session.id)) == 1
    assert not Session.is_lifecycle_transitioning(session.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["parent_id", "parentID", "id", "projectID"])
async def test_generic_update_cannot_change_session_topology(field):
    parent = await make_session("create")
    child = await Session.create(
        project_id=parent.project_id, directory="/fixture",
        owner_user_id=READER.id, owner_username=READER.username,
    )
    changed = parent.id if field.startswith("parent") else "other_identity"
    with acting_as(READER):
        with pytest.raises(PermissionError, match="topology"):
            await Session.update(child.project_id, child.id, **{field: changed})
        same = {"parent_id": None, "parentID": None, "id": child.id, "projectID": child.project_id}[field]
        assert await Session.update(child.project_id, child.id, **{field: same})
    current = await Session.get_by_id_unfiltered(child.id)
    assert current is not None and current.parent_id is None
    with acting_as(OWNER):
        assert await Session.archive(parent.project_id, parent.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["delete", "archive"])
@pytest.mark.parametrize("field", ["owner_user_id", "ownerUserID", "owner_username", "ownerUsername"])
async def test_application_identity_update_waits_for_lifecycle(action, field, monkeypatch):
    session = await make_session(action)
    stop_started, release_stop, identity_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def stop(*_args, **_kwargs):
        stop_started.set()
        await asyncio.wait_for(release_stop.wait(), 3)
        return True

    monkeypatch.setattr(Session, "_stop_session_tree_for_archive", stop)

    async def identity_update():
        identity_started.set()
        return await Session.update(
            session.project_id, session.id, allow_inactive=True,
            **{field: READER.id if field in {"owner_user_id", "ownerUserID"} else READER.username},
        )

    with acting_as(OWNER):
        lifecycle = asyncio.create_task(mutate(action, session))
        await asyncio.wait_for(stop_started.wait(), 2)
        with acting_as(None):
            identity = asyncio.create_task(identity_update())
        await asyncio.wait_for(identity_started.wait(), 2)
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(identity), 0.1)
            # Runner completion writes must not wait for the project guard
            # held by the lifecycle operation which is waiting for that runner.
            assert await asyncio.wait_for(
                Session.update(session.project_id, session.id, title="Runner completion"), 1,
            ) is None
        finally:
            release_stop.set()
        assert await asyncio.wait_for(lifecycle, 3)
        result = await asyncio.wait_for(identity, 3)
    assert result is None if action == "delete" else result is not None


@pytest.mark.asyncio
async def test_retained_session_count_does_not_report_denied_updates():
    session = await make_session("create")
    with acting_as(READER):
        assert await Session.retain_deleted_user_sessions(OWNER.id, OWNER.username) == 0
    assert (await Session.get_by_id_unfiltered(session.id)).owner_user_id == OWNER.id
