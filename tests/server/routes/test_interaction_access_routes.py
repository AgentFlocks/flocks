"""Session ownership checks for question and permission interactions."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.server.auth import require_user
from flocks.session.session import SessionInfo


def _user(user_id: str, username: str, *, role: str = "member") -> AuthUser:
    return AuthUser(
        id=user_id,
        username=username,
        role=role,
        status="active",
        must_reset_password=False,
    )


def _client(router, *, prefix: str, user: AuthUser) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_user] = lambda: user
    app.include_router(router, prefix=prefix)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def owned_session() -> SessionInfo:
    return SessionInfo(
        id="ses_owner",
        project_id="project",
        directory="/tmp",
        title="Owned session",
        owner_user_id="owner-id",
        owner_username="owner",
    )


def test_question_routes_reject_non_owner(monkeypatch: pytest.MonkeyPatch, owned_session: SessionInfo):
    from flocks.server.routes import question as question_routes

    request_id = "question-owner-only"
    question_routes.store_question_request(request_id, {
        "id": request_id,
        "sessionID": owned_session.id,
        "questions": [{"question": "Continue?"}],
    })
    monkeypatch.setattr(question_routes.Session, "get_by_id", AsyncMock(return_value=owned_session))
    client = _client(question_routes.router, prefix="/question", user=_user("other-id", "other"))

    try:
        assert client.get(f"/question/session/{owned_session.id}/pending").status_code == 403
        assert client.post(
            f"/question/{request_id}/reply",
            json={"answers": [["yes"]]},
        ).status_code == 403
        assert client.post(f"/question/{request_id}/reject").status_code == 403
        assert question_routes.get_question_request(request_id) is not None
    finally:
        question_routes.clear_request_state(request_id)


def test_question_owner_can_list_and_reply(monkeypatch: pytest.MonkeyPatch, owned_session: SessionInfo):
    from flocks.server.routes import question as question_routes

    request_id = "question-owner-reply"
    question_routes.store_question_request(request_id, {
        "id": request_id,
        "sessionID": owned_session.id,
        "questions": [{"question": "Continue?"}],
    })
    monkeypatch.setattr(question_routes.Session, "get_by_id", AsyncMock(return_value=owned_session))
    monkeypatch.setattr("flocks.server.routes.event.publish_event", AsyncMock())
    client = _client(question_routes.router, prefix="/question", user=_user("owner-id", "owner"))

    try:
        pending = client.get(f"/question/session/{owned_session.id}/pending")
        assert pending.status_code == 200
        assert [item["id"] for item in pending.json()] == [request_id]
        reply = client.post(
            f"/question/{request_id}/reply",
            json={"answers": [["yes"]]},
        )
        assert reply.status_code == 200
        assert question_routes.get_request_answer(request_id) == [["yes"]]
    finally:
        question_routes.clear_request_state(request_id)


def test_permission_routes_filter_and_reject_non_owner(
    monkeypatch: pytest.MonkeyPatch,
    owned_session: SessionInfo,
):
    from flocks.permission.next import PermissionRequestInfo
    from flocks.server.routes import permission as permission_routes

    info = PermissionRequestInfo(
        id="perm-owner-only",
        sessionID=owned_session.id,
        permission="bash",
        patterns=["*"],
        metadata={"messageID": "msg-1"},
        always=[],
        tool={"name": "bash"},
        time={"created": 1},
    )
    monkeypatch.setattr(permission_routes.Session, "get_by_id", AsyncMock(return_value=owned_session))
    monkeypatch.setattr(permission_routes.PermissionNext, "list_pending_infos", AsyncMock(return_value=[info]))
    monkeypatch.setattr(permission_routes.PermissionNext, "get_pending_info", AsyncMock(return_value=info))
    reply_mock = AsyncMock()
    monkeypatch.setattr(permission_routes.PermissionNext, "reply", reply_mock)
    client = _client(permission_routes.router, prefix="/permission", user=_user("other-id", "other"))

    assert client.get("/permission").json() == []
    assert client.get(f"/permission/{info.id}").status_code == 403
    assert client.post(
        f"/permission/{info.id}/reply",
        json={"allow": True, "always": False},
    ).status_code == 403
    reply_mock.assert_not_awaited()


def test_permission_owner_can_reply(monkeypatch: pytest.MonkeyPatch, owned_session: SessionInfo):
    from flocks.permission.next import PermissionRequestInfo
    from flocks.server.routes import permission as permission_routes

    info = PermissionRequestInfo(
        id="perm-owner-reply",
        sessionID=owned_session.id,
        permission="bash",
        patterns=["*"],
        metadata={"messageID": "msg-1"},
        always=[],
        tool={"name": "bash"},
        time={"created": 1},
    )
    monkeypatch.setattr(permission_routes.Session, "get_by_id", AsyncMock(return_value=owned_session))
    monkeypatch.setattr(permission_routes.PermissionNext, "get_pending_info", AsyncMock(return_value=info))
    reply_mock = AsyncMock()
    monkeypatch.setattr(permission_routes.PermissionNext, "reply", reply_mock)
    client = _client(permission_routes.router, prefix="/permission", user=_user("owner-id", "owner"))

    response = client.post(
        f"/permission/{info.id}/reply",
        json={"allow": True, "always": False},
    )

    assert response.status_code == 200
    reply_mock.assert_awaited_once_with(info.id, "allow", session_id=owned_session.id)


@pytest.mark.parametrize(
    ("allow", "expected_reply"),
    [(True, "allow_session"), (False, "deny_session")],
)
def test_permission_member_remember_is_session_scoped(
    monkeypatch: pytest.MonkeyPatch,
    owned_session: SessionInfo,
    allow: bool,
    expected_reply: str,
):
    from flocks.permission.next import PermissionRequestInfo
    from flocks.server.routes import permission as permission_routes

    info = PermissionRequestInfo(
        id=f"perm-member-remember-{allow}",
        sessionID=owned_session.id,
        permission="bash",
        patterns=["*"],
    )
    monkeypatch.setattr(permission_routes.Session, "get_by_id", AsyncMock(return_value=owned_session))
    monkeypatch.setattr(permission_routes.PermissionNext, "get_pending_info", AsyncMock(return_value=info))
    reply_mock = AsyncMock()
    monkeypatch.setattr(permission_routes.PermissionNext, "reply", reply_mock)
    client = _client(permission_routes.router, prefix="/permission", user=_user("owner-id", "owner"))

    response = client.post(
        f"/permission/{info.id}/reply",
        json={"allow": allow, "always": True},
    )

    assert response.status_code == 200
    reply_mock.assert_awaited_once_with(info.id, expected_reply, session_id=owned_session.id)


@pytest.mark.parametrize(
    ("allow", "expected_reply"),
    [(True, "always"), (False, "never")],
)
def test_permission_admin_remember_can_be_global(
    monkeypatch: pytest.MonkeyPatch,
    owned_session: SessionInfo,
    allow: bool,
    expected_reply: str,
):
    from flocks.permission.next import PermissionRequestInfo
    from flocks.server.routes import permission as permission_routes

    admin = _user("admin-id", "admin", role="admin")
    admin_session = owned_session.model_copy(update={
        "owner_user_id": admin.id,
        "owner_username": admin.username,
    })
    info = PermissionRequestInfo(
        id=f"perm-admin-remember-{allow}",
        sessionID=admin_session.id,
        permission="bash",
        patterns=["*"],
    )
    monkeypatch.setattr(permission_routes.Session, "get_by_id", AsyncMock(return_value=admin_session))
    monkeypatch.setattr(permission_routes.PermissionNext, "get_pending_info", AsyncMock(return_value=info))
    reply_mock = AsyncMock()
    monkeypatch.setattr(permission_routes.PermissionNext, "reply", reply_mock)
    client = _client(permission_routes.router, prefix="/permission", user=admin)

    response = client.post(
        f"/permission/{info.id}/reply",
        json={"allow": allow, "always": True},
    )

    assert response.status_code == 200
    reply_mock.assert_awaited_once_with(info.id, expected_reply, session_id=admin_session.id)
