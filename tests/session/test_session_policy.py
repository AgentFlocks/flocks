"""Unit tests for the unified SessionPolicy."""

from __future__ import annotations

from flocks.auth.context import AuthUser
from flocks.session.session import SessionInfo
from flocks.session.policy import SessionPolicy


def _make_user(user_id: str = "usr_1", username: str = "alice", role: str = "member") -> AuthUser:
    return AuthUser(id=user_id, username=username, role=role, status="active", must_reset_password=False)


def _make_session(**overrides) -> SessionInfo:
    defaults = dict(
        project_id="proj",
        directory="/tmp",
        title="t",
        owner_user_id="usr_1",
        owner_username="alice",
    )
    defaults.update(overrides)
    return SessionInfo(**defaults)


def test_is_owner_by_user_id():
    user = _make_user()
    session = _make_session()
    assert SessionPolicy.is_owner(session, user) is True


def test_is_owner_by_username_fallback():
    user = _make_user(user_id="usr_999")  # id differs
    session = _make_session()
    assert SessionPolicy.is_owner(session, user) is True


def test_can_read_admin_sees_everything():
    admin = _make_user(user_id="usr_admin", username="root", role="admin")
    session = _make_session(owner_user_id="usr_other", owner_username="bob")
    assert SessionPolicy.can_read(session, admin) is True


def test_can_read_private_hides_from_other_member():
    bob = _make_user(user_id="usr_bob", username="bob")
    session = _make_session()
    assert SessionPolicy.can_read(session, bob) is False


def test_can_delete_requires_admin_or_owner():
    owner = _make_user()
    admin = _make_user(user_id="usr_admin", username="root", role="admin")
    stranger = _make_user(user_id="usr_x", username="x")
    session = _make_session()
    assert SessionPolicy.can_delete(session, owner) is True
    assert SessionPolicy.can_delete(session, admin) is True
    assert SessionPolicy.can_delete(session, stranger) is False


def test_can_read_requires_owner_or_admin():
    owner = _make_user()
    admin = _make_user(user_id="usr_admin", username="root", role="admin")
    stranger = _make_user(user_id="usr_x", username="x")
    session = _make_session()
    assert SessionPolicy.can_read(session, owner) is True
    assert SessionPolicy.can_read(session, admin) is True
    assert SessionPolicy.can_read(session, stranger) is False
