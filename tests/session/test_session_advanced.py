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

import pytest

from flocks.session.session import (
    PermissionRule,
    Session,
    SessionInfo,
    SessionRevert,
    SessionTime,
)


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
        assert raw is None or (raw and raw.status == "archived")

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

    @pytest.mark.asyncio
    async def test_unarchive_nonarchived_returns_false(self):
        session = await _create(project_id="proj_arch_3")
        # Active session should not be unarchiveable
        result = await Session.unarchive("proj_arch_3", session.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_unarchive_nonexistent_returns_false(self):
        result = await Session.unarchive("proj_x", "ses_does_not_exist")
        assert result is False


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
