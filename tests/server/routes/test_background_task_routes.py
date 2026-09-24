"""Regression tests for /api/background-task routes.

Before the fix ``_get_background_manager`` called ``Instance.get()`` (a method
that does not exist), so every background-task route answered 500. The routes
also exposed every task to every signed-in user; now visibility follows the
parent session's ``SessionPolicy`` and cancelling requires write access.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.server.routes import background_task as bg_routes
from flocks.task.background import BackgroundManager, BackgroundTask

OWNER = AuthUser(id="u-owner", username="owner", role="member", status="active", must_reset_password=False)
OTHER = AuthUser(id="u-other", username="other", role="member", status="active", must_reset_password=False)
ADMIN = AuthUser(id="u-admin", username="admin", role="admin", status="active", must_reset_password=False)


def _client(user: AuthUser) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def user_auth(request, call_next):
        request.state.auth_user = user
        return await call_next(request)

    app.include_router(bg_routes.router, prefix="/api/background-task")
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch):
    mgr = BackgroundManager()
    mgr._tasks["bg_1"] = BackgroundTask(id="bg_1", status="running", description="d", prompt="p", agent="rex", parent_session_id="ses_owner", session_id="ses_child")
    monkeypatch.setattr("flocks.task.background.get_background_manager", lambda: mgr)

    sessions = {
        "ses_owner": SimpleNamespace(id="ses_owner", owner_user_id="u-owner"),
        "ses_child": SimpleNamespace(id="ses_child", owner_user_id="u-owner"),
    }

    async def get_by_id(session_id):
        return sessions.get(session_id)

    monkeypatch.setattr(bg_routes.Session, "get_by_id", staticmethod(get_by_id))

    def can_read(session, user=None, **_):
        return user is not None and (user.role == "admin" or session.owner_user_id == user.id)

    def can_write(session, user=None):
        return user is not None and session.owner_user_id == user.id

    monkeypatch.setattr(bg_routes.SessionPolicy, "can_read", classmethod(lambda cls, s, u=None, **k: can_read(s, u)))
    monkeypatch.setattr(bg_routes.SessionPolicy, "can_write", classmethod(lambda cls, s, u=None: can_write(s, u)))
    return mgr


def test_manager_resolves_and_list_is_scoped_to_readable_sessions(manager):
    assert bg_routes._get_background_manager() is manager
    assert [t["id"] for t in _client(OWNER).get("/api/background-task").json()] == ["bg_1"]
    assert _client(OTHER).get("/api/background-task").json() == []
    assert [t["id"] for t in _client(ADMIN).get("/api/background-task").json()] == ["bg_1"]


def test_get_task_403_for_foreign_user_and_404_for_unknown(manager):
    assert _client(OWNER).get("/api/background-task/bg_1").status_code == 200
    forbidden = _client(OTHER).get("/api/background-task/bg_1")
    assert forbidden.status_code == 403 and forbidden.json()["detail"] == "Not allowed to view this task"
    assert _client(OWNER).get("/api/background-task/bg_missing").status_code == 404


@pytest.mark.asyncio
async def test_cancel_requires_write_access(manager):
    from fastapi import HTTPException

    async def _sleep():
        await asyncio.sleep(30)

    handle = asyncio.get_running_loop().create_task(_sleep())
    manager._task_handles["bg_1"] = handle

    # admin can read the task but does not own the session -> cannot cancel
    with pytest.raises(HTTPException) as refused:
        await bg_routes.cancel_background_task("bg_1", user=ADMIN)
    assert refused.value.status_code == 403
    assert manager.get_task("bg_1").status == "running"

    resp = await bg_routes.cancel_background_task("bg_1", user=OWNER)
    assert resp["cancelled"] == 1 and manager.get_task("bg_1").status == "cancelled"
    await asyncio.sleep(0)
    assert handle.cancelled() or handle.done()
