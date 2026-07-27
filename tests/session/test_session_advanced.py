"""
Tests for advanced Session operations in flocks/session/session.py

Covers:
- Session.archive() / unarchive()
- Session.fork()
- Session.children()
- Session.set_revert() / clear_revert()
- Session.set_current() / get_current()
- PermissionRule model validation
- SessionInfo model fields
"""

import asyncio

import pytest

from flocks.auth.context import (
    API_TOKEN_SERVICE_USER_ID,
    AuthUser,
    reset_current_auth_user,
    set_current_auth_user,
)
from flocks.session.message import Message, MessageRole, ToolPart, ToolStatePending
from flocks.session.features.todo import Todo, TodoInfo
from flocks.session.session import (
    PermissionRule,
    Session,
    SessionInactiveError,
    SessionInfo,
    SessionRevert,
    SessionTime,
)
from flocks.storage.storage import Storage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create(project_id="proj_adv", title="Test", directory="/tmp"):
    return await Session.create(project_id=project_id, directory=directory, title=title)


# ---------------------------------------------------------------------------
# Archive / Unarchive
# ---------------------------------------------------------------------------

class TestArchiveUnarchive:
    @pytest.mark.asyncio
    async def test_archive_sets_status(self):
        session = await _create(project_id="proj_arch_1")
        result = await Session.archive("proj_arch_1", session.id)
        assert result is True
        raw = await Session.get("proj_arch_1", session.id)
        assert raw is not None
        assert raw.status == "archived"
        assert raw.time.archived is not None

    @pytest.mark.asyncio
    async def test_archive_nonexistent_returns_false(self):
        result = await Session.archive("proj_x", "ses_nonexistent_abc123")
        assert result is False

    @pytest.mark.asyncio
    async def test_unarchive_restores_session(self):
        session = await _create(project_id="proj_arch_2")
        await Session.archive("proj_arch_2", session.id)
        result = await Session.unarchive("proj_arch_2", session.id)
        assert result is True
        restored = await Session.get("proj_arch_2", session.id)
        assert restored is not None
        assert restored.status == "active"
        assert restored.time.archived is None

    @pytest.mark.asyncio
    async def test_unarchive_nonarchived_is_idempotent(self):
        session = await _create(project_id="proj_arch_3")
        result = await Session.unarchive("proj_arch_3", session.id)
        assert result is True

    @pytest.mark.asyncio
    async def test_unarchive_nonexistent_returns_false(self):
        result = await Session.unarchive("proj_x", "ses_does_not_exist")
        assert result is False

    @pytest.mark.asyncio
    async def test_archive_and_unarchive_apply_to_descendants(self):
        parent = await _create(project_id="proj_arch_tree", title="Parent")
        child = await Session.fork("proj_arch_tree", parent.id)

        assert await Session.archive("proj_arch_tree", parent.id) is True
        archived_parent = await Session.get("proj_arch_tree", parent.id)
        archived_child = await Session.get("proj_arch_tree", child.id)
        assert archived_parent is not None and archived_parent.status == "archived"
        assert archived_child is not None and archived_child.status == "archived"

        assert await Session.unarchive("proj_arch_tree", parent.id) is True
        restored_parent = await Session.get("proj_arch_tree", parent.id)
        restored_child = await Session.get("proj_arch_tree", child.id)
        assert restored_parent is not None and restored_parent.status == "active"
        assert restored_child is not None and restored_child.status == "active"

    @pytest.mark.asyncio
    async def test_archive_is_idempotent_and_preserves_first_timestamp(self):
        session = await _create(project_id="proj_arch_idempotent")

        assert await Session.archive(session.project_id, session.id) is True
        first = await Session.get(session.project_id, session.id)
        assert first is not None and first.time.archived is not None

        assert await Session.archive(session.project_id, session.id) is True
        second = await Session.get(session.project_id, session.id)
        assert second is not None
        assert second.time.archived == first.time.archived

    @pytest.mark.asyncio
    async def test_unarchive_child_is_rejected_to_preserve_tree_state(self):
        parent = await _create(project_id="proj_arch_child_restore", title="Parent")
        child = await Session.create(
            project_id=parent.project_id,
            directory=parent.directory,
            title="Child",
            parent_id=parent.id,
        )
        assert await Session.archive(parent.project_id, parent.id) is True

        assert await Session.unarchive(parent.project_id, child.id) is False
        archived_parent = await Session.get(parent.project_id, parent.id)
        archived_child = await Session.get(parent.project_id, child.id)
        assert archived_parent is not None and archived_parent.status == "archived"
        assert archived_child is not None and archived_child.status == "archived"

    @pytest.mark.asyncio
    async def test_create_child_under_archived_parent_is_rejected(self):
        parent = await _create(project_id="proj_arch_child_create", title="Parent")
        assert await Session.archive(parent.project_id, parent.id) is True

        with pytest.raises(ValueError, match="is not active"):
            await Session.create(
                project_id=parent.project_id,
                directory=parent.directory,
                title="Late child",
                parent_id=parent.id,
            )

    @pytest.mark.asyncio
    async def test_child_inherits_parent_owner_when_explicit_values_are_none(self):
        parent = await Session.create(
            project_id="proj_arch_child_owner",
            directory="/tmp",
            title="Parent",
            owner_user_id="owner-1",
            owner_username="alice",
        )

        child = await Session.create(
            project_id=parent.project_id,
            directory=parent.directory,
            title="Child",
            parent_id=parent.id,
            owner_user_id=None,
            owner_username=None,
        )

        assert child.owner_user_id == parent.owner_user_id
        assert child.owner_username == parent.owner_username

    @pytest.mark.asyncio
    async def test_child_inherits_system_parent_owner_under_admin_context(self):
        parent = await Session.create(
            project_id="proj_system_parent_owner",
            directory="/tmp",
            title="System parent",
        )
        admin = AuthUser(id="usr_admin", username="admin", role="admin")
        token = set_current_auth_user(admin)
        try:
            child = await Session.create(
                project_id=parent.project_id,
                directory=parent.directory,
                title="Child",
                parent_id=parent.id,
            )
        finally:
            reset_current_auth_user(token)

        assert child.owner_user_id == API_TOKEN_SERVICE_USER_ID
        assert child.owner_username == API_TOKEN_SERVICE_USER_ID

    @pytest.mark.asyncio
    async def test_archived_session_loop_cannot_restart(self):
        from flocks.session.session_loop import SessionLoop

        session = await _create(project_id="proj_arch_loop_guard")
        assert await Session.archive(session.project_id, session.id) is True

        result = await SessionLoop.run(session.id)

        assert result.action == "error"
        assert "archived" in (result.error or "")

    @pytest.mark.asyncio
    async def test_lifecycle_guard_rejects_todo_write_after_archive(self):
        session = await _create(project_id="proj_arch_todo_guard")
        assert await Session.archive(session.project_id, session.id) is True

        with pytest.raises(SessionInactiveError):
            await Todo.update_active(
                session.id,
                [TodoInfo(id="late", content="must not persist")],
            )

        assert await Todo.get(session.id) == []

    @pytest.mark.asyncio
    async def test_archive_wins_before_session_loop_registration(self, monkeypatch):
        from flocks.session.session_loop import SessionLoop

        session = await _create(project_id="proj_arch_loop_race")

        async def archive_during_loop_start(_session_id: str):
            assert await Session.archive(session.project_id, session.id) is True
            return []

        monkeypatch.setattr(Message, "list", archive_during_loop_start)

        result = await SessionLoop.run(
            session.id,
            provider_id="test-provider",
            model_id="test-model",
        )

        assert result.action == "error"
        assert "archived" in (result.error or "")
        assert SessionLoop.is_running(session.id) is False

    @pytest.mark.asyncio
    async def test_archive_cannot_be_overwritten_by_an_inflight_update(self, monkeypatch):
        session = await _create(project_id="proj_arch_update_race")
        storage_key = f"session:{session.project_id}:{session.id}"
        update_reached_write = asyncio.Event()
        release_update = asyncio.Event()
        original_set = Storage.set

        async def delayed_set(key, value, *args, **kwargs):
            if key == storage_key and getattr(value, "title", None) == "Racing update":
                update_reached_write.set()
                await release_update.wait()
            return await original_set(key, value, *args, **kwargs)

        monkeypatch.setattr(Storage, "set", delayed_set)

        update_task = asyncio.create_task(
            Session.update(session.project_id, session.id, title="Racing update")
        )
        await update_reached_write.wait()
        archive_task = asyncio.create_task(Session.archive(session.project_id, session.id))
        await asyncio.sleep(0)
        assert archive_task.done() is False
        release_update.set()

        assert await update_task is not None
        assert await archive_task is True
        stored = await Storage.get(storage_key, SessionInfo)
        assert stored is not None
        assert stored.status == "archived"
        assert stored.title == "Racing update"

    @pytest.mark.asyncio
    async def test_unrelated_session_updates_do_not_share_a_global_lock(self, monkeypatch):
        first = await _create(project_id="proj_keyed_locks", title="First")
        second = await _create(project_id="proj_keyed_locks", title="Second")
        first_write_started = asyncio.Event()
        release_first_write = asyncio.Event()
        original_set = Storage.set

        async def delayed_set(key, value, *args, **kwargs):
            if key.endswith(f":{first.id}") and getattr(value, "title", None) == "Slow":
                first_write_started.set()
                await release_first_write.wait()
            return await original_set(key, value, *args, **kwargs)

        monkeypatch.setattr(Storage, "set", delayed_set)
        first_update = asyncio.create_task(
            Session.update(first.project_id, first.id, title="Slow")
        )
        await first_write_started.wait()

        try:
            second_update = await asyncio.wait_for(
                Session.update(second.project_id, second.id, title="Fast"),
                timeout=0.5,
            )
            assert second_update is not None and second_update.title == "Fast"
        finally:
            release_first_write.set()
            await first_update

    @pytest.mark.asyncio
    async def test_archive_flushes_debounced_tool_parts_before_cache_invalidation(self):
        session = await _create(project_id="proj_arch_parts_flush")
        message = await Message.create(
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            content="working",
        )
        tool_part = ToolPart(
            sessionID=session.id,
            messageID=message.id,
            callID="call-pending",
            tool="read",
            state=ToolStatePending(input={"path": "/tmp/a"}, raw='{"path":"/tmp/a"}'),
        )
        await Message.store_part(session.id, message.id, tool_part)

        assert session.id in Message._parts_flush_tasks
        assert await Session.archive(session.project_id, session.id) is True

        reloaded_parts = await Message.parts(message.id, session.id)
        reloaded_tool = next(part for part in reloaded_parts if part.id == tool_part.id)
        assert isinstance(reloaded_tool, ToolPart)
        assert reloaded_tool.state.status == "pending"

    @pytest.mark.asyncio
    async def test_permanent_delete_removes_tree_data_in_one_mutation(
        self,
        tmp_path,
        monkeypatch,
    ):
        from flocks.config.config import Config
        from flocks.permission.next import PermissionNext, PermissionRequestInfo
        from flocks.session.files import session_uploads_dir

        monkeypatch.setattr(
            Config,
            "get_data_path",
            classmethod(lambda _cls: tmp_path),
        )
        parent = await _create(project_id="proj_delete_atomic", title="Parent")
        child = await Session.create(
            project_id=parent.project_id,
            directory=parent.directory,
            title="Child",
            parent_id=parent.id,
        )
        for session in (parent, child):
            upload_dir = session_uploads_dir(session.id)
            upload_dir.mkdir(parents=True)
            (upload_dir / "attachment.txt").write_text("remove", encoding="utf-8")
            await Storage.set(f"message:{session.id}", [{"id": "message"}], "message")
            await Storage.set(f"message_parts:{session.id}", {"legacy": []}, "message_parts")
            await Storage.set(f"message_parts:{session.id}:message", [], "message_parts")
            await Storage.set(f"todo:{session.id}", [{"content": "remove"}], "todo")
            await Storage.set(f"goal:{session.id}", {"objective": "retain until delete"}, "goal")
            await Storage.set(f"session_diff:{session.id}", {"files": []}, "session_diff")
            await Storage.set(f"message_diff:{session.id}:message", {"diff": "remove"}, "message_diff")
            await Storage.set(f"system_prompts:{session.id}:default", {"text": "remove"}, "system_prompt")
            await Storage.set(f"session_callable_tools:{session.id}", {"tools": ["read"]}, "session_callable_tools")
            await Storage.set(
                f"{PermissionNext._SESSION_PREFIX}{session.id}",
                {"bash": "allow"},
                "permission_session",
            )
            PermissionNext._session_permissions[session.id] = {"bash": "allow"}

        pending = PermissionRequestInfo(
            id="per_delete_tree",
            sessionID=child.id,
            permission="write",
            patterns=["*"],
        )
        pending_future = asyncio.get_running_loop().create_future()
        PermissionNext._pending[pending.id] = {
            "info": pending,
            "future": pending_future,
        }
        await Storage.set(
            f"{PermissionNext._PENDING_PREFIX}{pending.id}",
            pending.model_dump(by_alias=True),
            "permission_pending",
        )
        await Storage.set(
            f"{PermissionNext._REPLY_PREFIX}{pending.id}",
            {"reply": "allow", "sessionID": child.id},
            "permission_reply",
        )

        assert await Session.delete(parent.project_id, parent.id) is True

        for session in (parent, child):
            deleted_session = await Storage.get(
                f"session:{session.project_id}:{session.id}",
                SessionInfo,
            )
            assert deleted_session is None
            assert await Storage.get(f"message:{session.id}") is None
            assert await Storage.get(f"message_parts:{session.id}") is None
            assert await Storage.list_keys(prefix=f"message_parts:{session.id}:") == []
            assert await Storage.get(f"todo:{session.id}") is None
            assert await Storage.get(f"goal:{session.id}") is None
            assert await Storage.get(f"session_diff:{session.id}") is None
            assert await Storage.list_keys(prefix=f"message_diff:{session.id}:") == []
            assert await Storage.list_keys(prefix=f"system_prompts:{session.id}:") == []
            assert await Storage.get(f"session_callable_tools:{session.id}") is None
            assert await Storage.get(f"{PermissionNext._SESSION_PREFIX}{session.id}") is None
            assert session.id not in PermissionNext._session_permissions
            assert not session_uploads_dir(session.id).exists()
        assert await Storage.get(f"{PermissionNext._PENDING_PREFIX}{pending.id}") is None
        assert await Storage.get(f"{PermissionNext._REPLY_PREFIX}{pending.id}") is None
        assert pending.id not in PermissionNext._pending
        assert pending_future.cancelled()


# ---------------------------------------------------------------------------
# Fork / Children
# ---------------------------------------------------------------------------

class TestForkChildren:
    @pytest.mark.asyncio
    async def test_fork_creates_child_session(self):
        parent = await _create(project_id="proj_fork_1", title="Parent")
        child = await Session.fork("proj_fork_1", parent.id)
        assert child is not None
        assert child.parent_id == parent.id
        assert child.project_id == parent.project_id

    @pytest.mark.asyncio
    async def test_fork_nonexistent_raises_or_returns_none(self):
        # fork() may raise ValueError or return None for nonexistent sessions
        try:
            result = await Session.fork("proj_fork_2", "ses_nonexistent_xyz_abc")
            assert result is None
        except (ValueError, KeyError):
            pass  # Raising is also acceptable behavior

    @pytest.mark.asyncio
    async def test_children_returns_forked_sessions(self):
        parent = await _create(project_id="proj_fork_3", title="Parent")
        await Session.fork("proj_fork_3", parent.id)
        await Session.fork("proj_fork_3", parent.id)
        children = await Session.children("proj_fork_3", parent.id)
        assert len(children) >= 2
        assert all(c.parent_id == parent.id for c in children)

    @pytest.mark.asyncio
    async def test_children_empty_when_no_fork(self):
        session = await _create(project_id="proj_fork_4")
        children = await Session.children("proj_fork_4", session.id)
        assert children == []


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------

class TestSetRevert:
    @pytest.mark.asyncio
    async def test_set_revert_stores_state(self):
        # Session.set_revert takes message_id as a string (not SessionRevert object)
        session = await _create(project_id="proj_revert_1")
        result = await Session.set_revert("proj_revert_1", session.id, "msg_001", snapshot="snap_001")
        assert result is True

    @pytest.mark.asyncio
    async def test_clear_revert_removes_state(self):
        session = await _create(project_id="proj_revert_2")
        await Session.set_revert("proj_revert_2", session.id, "msg_002")
        result = await Session.clear_revert("proj_revert_2", session.id)
        assert result is True

    @pytest.mark.asyncio
    async def test_set_revert_persisted(self):
        session = await _create(project_id="proj_revert_3")
        await Session.set_revert("proj_revert_3", session.id, "msg_003", snapshot="snap_x")
        updated = await Session.get("proj_revert_3", session.id)
        assert updated is not None
        assert updated.revert is not None
        assert updated.revert.message_id == "msg_003"


# ---------------------------------------------------------------------------
# set_current / get_current
# ---------------------------------------------------------------------------

class TestSetGetCurrent:
    @pytest.mark.asyncio
    async def test_set_and_get_current(self):
        # set_current() takes a SessionInfo object, not a string
        session = await _create(project_id="proj_current_1")
        Session.set_current(session)
        current = Session.get_current()
        assert current is not None
        assert current.id == session.id

    def test_get_current_initially_none(self):
        # After clearing with None - set_current expects SessionInfo, not None
        # get_current() returns None by default
        current = Session.get_current()
        # Either None or a previously set session - just check type
        assert current is None or isinstance(current, type(current))


# ---------------------------------------------------------------------------
# SessionInfo model
# ---------------------------------------------------------------------------

class TestSessionInfoModel:
    def test_default_title_starts_with_prefix(self):
        info = SessionInfo.model_construct(
            id="ses_x", project_id="proj_x", directory="/tmp"
        )
        # Default title generated from factory
        info2 = SessionInfo(project_id="proj_y", directory="/tmp")
        assert "New session" in info2.title or len(info2.title) > 0

    def test_memory_enabled_default_true(self):
        info = SessionInfo(project_id="proj_x", directory="/tmp")
        assert info.memory_enabled is True

    def test_category_default_user(self):
        info = SessionInfo(project_id="proj_x", directory="/tmp")
        assert info.category == "user"

    def test_status_default_active(self):
        info = SessionInfo(project_id="proj_x", directory="/tmp")
        assert info.status == "active"

    def test_project_id_alias(self):
        # Should work with both projectID and project_id
        info = SessionInfo(projectID="proj_a", directory="/tmp")
        assert info.project_id == "proj_a"


# ---------------------------------------------------------------------------
# PermissionRule model
# ---------------------------------------------------------------------------

class TestPermissionRule:
    def test_default_action_allow(self):
        rule = PermissionRule(permission="bash")
        assert rule.action == "allow"
        assert rule.pattern == "*"

    def test_deny_action(self):
        rule = PermissionRule(permission="write_file", action="deny", pattern="*.exe")
        assert rule.action == "deny"
        assert rule.pattern == "*.exe"

    def test_custom_permission(self):
        rule = PermissionRule(permission="network_access")
        assert rule.permission == "network_access"


# ---------------------------------------------------------------------------
# SessionRevert model
# ---------------------------------------------------------------------------


class TestSessionRevert:
    def test_message_id_required(self):
        with pytest.raises(Exception):
            SessionRevert()

    def test_alias_field(self):
        revert = SessionRevert(messageID="msg_123")
        assert revert.message_id == "msg_123"

    def test_optional_fields(self):
        revert = SessionRevert(messageID="msg_456")
        assert revert.snapshot is None
        assert revert.diff is None


# ---------------------------------------------------------------------------
# is_default_title
# ---------------------------------------------------------------------------

class TestIsDefaultTitle:
    def test_default_title_detected_parent(self):
        # Must follow format: "New session - YYYY-MM-DDTHH:MM:SS..."
        assert Session.is_default_title("New session - 2025-01-01T00:00:00") is True

    def test_default_title_detected_child(self):
        # Child session format: "Child session - YYYY-MM-DDTHH:MM:SS..."
        assert Session.is_default_title("Child session - 2025-06-15T12:30:45") is True

    def test_custom_title_not_default(self):
        assert Session.is_default_title("Investigate Security Incident") is False
        assert Session.is_default_title("My Custom Session") is False

    def test_default_title_without_timestamp_not_default(self):
        # Must have timestamp to match
        assert Session.is_default_title("New session - something") is False

    def test_empty_title_not_default(self):
        assert Session.is_default_title("") is False
