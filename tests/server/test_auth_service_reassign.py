"""Tests for the operator-facing :py:meth:`AuthService.reassign_orphan_sessions`.

The startup-time migration (`migrate_legacy_sessions_to_admin`) is
guarded by a one-shot marker, so it cannot be re-run after a member
account is added later.  ``reassign_orphan_sessions`` is the unguarded
sibling exposed via ``flocks admin reassign-orphan-sessions``; the tests
below exercise the dry-run path, the role check and the actual rewrite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.asyncio


async def _setup_admin():
    """Idempotently ensure a bootstrap admin exists for these tests.

    The autouse server-test isolation fixture (see tests/server/conftest.py)
    rebuilds the AuthService singleton between tests, so the ``has_users``
    branch will short-circuit on first call and run for real on subsequent
    tests — both paths return the same admin record.
    """
    from flocks.auth.service import AuthService

    if not await AuthService.has_users():
        await AuthService.bootstrap_admin(username="admin", password="Password123!")
    users = await AuthService.list_users()
    return next(u for u in users if u.role == "admin")


def _stub_session(session_id: str, owner: str | None) -> SimpleNamespace:
    """Mimic ``SessionInfo`` enough for the helper to consume it."""
    return SimpleNamespace(
        id=session_id,
        project_id="proj",
        owner_user_id=owner,
        owner_username=None if owner is None else "owner",
    )


async def test_reassign_orphan_sessions_skips_owned_and_rewrites_orphans(monkeypatch):
    """Only sessions with an empty ``owner_user_id`` are touched."""
    from flocks.auth import service as auth_service_module
    from flocks.session import session as session_module

    admin = await _setup_admin()

    listed = [
        _stub_session("ses_owned", owner="usr_owned"),
        _stub_session("ses_orphan_a", owner=None),
        _stub_session("ses_orphan_b", owner=""),
    ]
    update_calls: list[dict] = []

    async def _list_all():
        return listed

    async def _update(*, project_id, session_id, owner_user_id, owner_username):
        update_calls.append({
            "project_id": project_id,
            "session_id": session_id,
            "owner_user_id": owner_user_id,
            "owner_username": owner_username,
        })

    monkeypatch.setattr(session_module.Session, "list_all", staticmethod(_list_all))
    monkeypatch.setattr(session_module.Session, "update", staticmethod(_update))

    summary = await auth_service_module.AuthService.reassign_orphan_sessions(admin.id)

    assert summary == {"scanned": 3, "orphaned": 2, "reassigned": 2, "failed": 0}
    rewritten_ids = {c["session_id"] for c in update_calls}
    assert rewritten_ids == {"ses_orphan_a", "ses_orphan_b"}
    for call in update_calls:
        assert call["owner_user_id"] == admin.id
        assert call["owner_username"] == admin.username


async def test_reassign_orphan_sessions_dry_run_writes_nothing(monkeypatch):
    from flocks.auth import service as auth_service_module
    from flocks.session import session as session_module

    admin = await _setup_admin()

    async def _list_all():
        return [_stub_session("ses_orphan", owner=None)]

    async def _update(**_kwargs):
        raise AssertionError("dry_run must not call Session.update")

    monkeypatch.setattr(session_module.Session, "list_all", staticmethod(_list_all))
    monkeypatch.setattr(session_module.Session, "update", staticmethod(_update))

    summary = await auth_service_module.AuthService.reassign_orphan_sessions(
        admin.id, dry_run=True
    )
    assert summary == {"scanned": 1, "orphaned": 1, "reassigned": 0, "failed": 0}


async def test_reassign_orphan_sessions_continues_on_partial_failure(monkeypatch):
    """A single Session.update failure must not abort the whole pass."""
    from flocks.auth import service as auth_service_module
    from flocks.session import session as session_module

    admin = await _setup_admin()

    listed = [
        _stub_session("ses_a", owner=None),
        _stub_session("ses_b", owner=None),
        _stub_session("ses_c", owner=None),
    ]
    update_calls: list[str] = []

    async def _list_all():
        return listed

    async def _update(*, project_id, session_id, owner_user_id, owner_username):
        update_calls.append(session_id)
        if session_id == "ses_b":
            raise RuntimeError("storage write failed")

    monkeypatch.setattr(session_module.Session, "list_all", staticmethod(_list_all))
    monkeypatch.setattr(session_module.Session, "update", staticmethod(_update))

    summary = await auth_service_module.AuthService.reassign_orphan_sessions(admin.id)

    assert summary == {"scanned": 3, "orphaned": 3, "reassigned": 2, "failed": 1}
    # All three orphans were attempted; the failing one did not stop the loop.
    assert update_calls == ["ses_a", "ses_b", "ses_c"]


async def test_reassign_orphan_sessions_refuses_unknown_user():
    from flocks.auth.service import AuthService

    await AuthService.init()
    with pytest.raises(ValueError, match="管理员"):
        await AuthService.reassign_orphan_sessions("usr_does_not_exist")
