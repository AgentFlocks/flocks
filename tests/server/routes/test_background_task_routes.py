"""Legacy /api/background-task routes (2026-10-11 defect report, REG-001).

They used to call a non-existent ``Instance.get()`` and answered every
request with 500.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.server.auth import require_user
from flocks.server.routes import background_task as background_routes
from flocks.session.session import SessionInfo
from flocks.task.background import BackgroundTask, get_background_manager


@pytest.fixture(autouse=True)
def _fresh_managers():
    from flocks.task import background

    background._manager_state.invalidate_all()
    yield
    background._manager_state.invalidate_all()


def _add_task(task_id: str, *, session_id: str | None, parent_session_id: str | None = None, handle=None):
    # Scheduled tasks run outside any request's instance: this manager is the
    # "default" one, not the instance a request resolves to.
    manager = get_background_manager()
    task = BackgroundTask(
        id=task_id,
        status="running",
        description="nightly report",
        prompt="summarize",
        agent="rex",
        session_id=session_id,
        parent_session_id=parent_session_id,
    )
    manager._tasks[task_id] = task
    if handle is not None:
        manager._task_handles[task_id] = handle
    return task


@pytest.mark.asyncio
async def test_list_get_and_cancel_answer_instead_of_500(client, session_id: str) -> None:
    sleeper = asyncio.create_task(asyncio.sleep(30))
    _add_task("bg_routes_running", session_id=session_id, handle=sleeper)
    try:
        listed = await client.get("/api/background-task")
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == ["bg_routes_running"]

        detail = await client.get("/api/background-task/bg_routes_running")
        assert detail.status_code == 200
        assert detail.json()["sessionId"] == session_id

        cancelled = await client.post("/api/background-task/bg_routes_running/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancelled"] == 1
        await asyncio.sleep(0)
        assert sleeper.cancelled()
        assert (await client.get("/api/background-task/bg_routes_running")).json()["status"] == "cancelled"

        again = await client.post("/api/background-task/bg_routes_running/cancel")
        assert again.status_code == 409
    finally:
        sleeper.cancel()


@pytest.mark.asyncio
async def test_unknown_task_is_404(client) -> None:
    listed = await client.get("/api/background-task")
    assert listed.status_code == 200
    assert listed.json() == []

    detail = await client.get("/api/background-task/ledger-nonexistent")
    assert detail.status_code == 404
    cancel = await client.post("/api/background-task/ledger-nonexistent/cancel")
    assert cancel.status_code == 404


def _user(user_id: str, *, role: str = "member") -> AuthUser:
    return AuthUser(id=user_id, username=user_id, role=role, status="active", must_reset_password=False)


def _client(user: AuthUser) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_user] = lambda: user
    app.include_router(background_routes.router, prefix="/api/background-task")
    return TestClient(app, raise_server_exceptions=True)


def _session(session_id: str, owner: str | None) -> SessionInfo:
    return SessionInfo(
        id=session_id,
        project_id="project",
        directory="/tmp",
        title="owned",
        owner_user_id=owner,
        owner_username=owner,
    )


@pytest.fixture()
def sessions(monkeypatch: pytest.MonkeyPatch) -> dict[str, SessionInfo]:
    from flocks.session.session import Session

    stored = {
        "ses_owner": _session("ses_owner", "owner-id"),
        "ses_ownerless": _session("ses_ownerless", None),
    }
    monkeypatch.setattr(Session, "get_by_id_unfiltered", AsyncMock(side_effect=lambda sid: stored.get(sid)))
    return stored


def test_other_users_tasks_stay_hidden(sessions) -> None:
    _add_task("bg_owned", session_id="ses_owner")
    other = _client(_user("other-id"))

    assert other.get("/api/background-task").json() == []
    assert other.get("/api/background-task/bg_owned").status_code == 404
    assert other.post("/api/background-task/bg_owned/cancel").status_code == 404

    owner = _client(_user("owner-id"))
    assert [item["id"] for item in owner.get("/api/background-task").json()] == ["bg_owned"]


def test_parent_session_decides_before_the_task_has_its_own(sessions) -> None:
    _add_task("bg_pending", session_id=None, parent_session_id="ses_owner")

    assert _client(_user("owner-id")).get("/api/background-task/bg_pending").status_code == 200
    assert _client(_user("other-id")).get("/api/background-task/bg_pending").status_code == 404


def test_ownerless_and_sessionless_tasks_are_admin_only(sessions) -> None:
    _add_task("bg_system", session_id="ses_ownerless")
    _add_task("bg_sessionless", session_id=None)

    member_ids = [item["id"] for item in _client(_user("member-id")).get("/api/background-task").json()]
    admin_ids = [item["id"] for item in _client(_user("admin-id", role="admin")).get("/api/background-task").json()]

    assert member_ids == []
    assert sorted(admin_ids) == ["bg_sessionless", "bg_system"]


def test_tasks_of_a_session_that_is_gone_stay_hidden_even_from_admins(sessions) -> None:
    """v2: a finished task keeps its prompt and output in memory after its
    private session is deleted, and an admin could not read that session
    while it existed. A failed read looks the same (``None``)."""
    _add_task("bg_orphan", session_id="ses_deleted")
    admin = _client(_user("admin-id", role="admin"))

    assert admin.get("/api/background-task").json() == []
    assert admin.get("/api/background-task/bg_orphan").status_code == 404
    assert admin.post("/api/background-task/bg_orphan/cancel").status_code == 404


def test_a_missing_own_session_does_not_fall_back_to_the_parent(sessions) -> None:
    """The parent session stands in only before the task has a session of its own."""
    _add_task("bg_child_gone", session_id="ses_deleted", parent_session_id="ses_owner")

    assert _client(_user("owner-id")).get("/api/background-task/bg_child_gone").status_code == 404


def test_cancel_of_a_scheduled_task_run_goes_through_the_task_manager(sessions, monkeypatch) -> None:
    """Cancelling only the background run made the task manager queue the execution again."""
    from types import SimpleNamespace

    from flocks.task.manager import TaskManager
    from flocks.task.store import TaskStore

    _add_task("bg_scheduled", session_id="ses_owner")
    lookups: list[str] = []

    async def active_for_session(session_id: str):
        lookups.append(session_id)
        return [SimpleNamespace(id="txe_scheduled")]

    cancel_execution = AsyncMock()
    monkeypatch.setattr(TaskStore, "list_active_executions_for_session", active_for_session)
    monkeypatch.setattr(TaskManager, "cancel_execution", cancel_execution)

    response = _client(_user("owner-id")).post("/api/background-task/bg_scheduled/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["taskExecutionIds"] == ["txe_scheduled"]
    assert lookups == ["ses_owner"]
    cancel_execution.assert_awaited_once_with("txe_scheduled")


@pytest.mark.asyncio
async def test_cancelling_a_finished_task_changes_nothing(client, session_id: str) -> None:
    async def _noop() -> None:
        return None

    finished = asyncio.create_task(_noop())
    await finished
    task = _add_task("bg_routes_done", session_id=session_id, handle=finished)
    task.status = "completed"
    task.completed_at = 1_700_000_000_000

    response = await client.post("/api/background-task/bg_routes_done/cancel")

    assert response.status_code == 409
    assert (task.status, task.completed_at) == ("completed", 1_700_000_000_000)


def test_shared_reader_sees_the_task_but_cannot_cancel_it(sessions) -> None:
    """v2: a session shared read-only shows its task, but only the owner stops it."""
    sessions["ses_shared"] = _session("ses_shared", "owner-id").model_copy(
        update={"metadata": {"shared_local": True}}
    )
    task = _add_task("bg_shared", session_id="ses_shared")
    reader = _client(_user("reader-id"))

    assert reader.get("/api/background-task/bg_shared").status_code == 200
    assert reader.post("/api/background-task/bg_shared/cancel").status_code == 403
    assert task.status == "running"
