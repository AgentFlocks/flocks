from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response


pytestmark = pytest.mark.asyncio


async def test_login_emits_audit_event(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    async def _login(username: str, _password: str):
        return (
            SimpleNamespace(
                id="usr_1",
                username=username,
                role="admin",
                status="active",
                must_reset_password=False,
                created_at=None,
                updated_at=None,
                last_login_at=None,
            ),
            "sess_1",
        )

    emitted: list[tuple[str, dict]] = []

    async def _emit(event_type: str, payload: dict):
        emitted.append((event_type, payload))

    monkeypatch.setattr(auth_routes.AuthService, "login", _login)
    monkeypatch.setattr(auth_routes, "_emit_auth_audit", _emit)
    monkeypatch.setattr(auth_routes, "should_use_secure_cookie", lambda _request: False)
    monkeypatch.setattr(auth_routes, "set_session_cookie", lambda _response, _session_id, secure=False: None)

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    response = Response()
    payload = auth_routes.LoginRequest(username="chenjie", password="Password123!")
    await auth_routes.login(payload, response, request)

    assert emitted
    assert emitted[0][0] == "account.login"
    assert emitted[0][1]["user_id"] == "usr_1"
    assert emitted[0][1]["actor_id"] == "chenjie"
    assert emitted[0][1]["actor_name"] == "chenjie"


async def test_login_failed_emits_audit_event(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    async def _login(_username: str, _password: str):
        raise ValueError("用户名或密码错误")

    emitted: list[tuple[str, dict]] = []

    async def _emit(event_type: str, payload: dict):
        emitted.append((event_type, payload))

    monkeypatch.setattr(auth_routes.AuthService, "login", _login)
    monkeypatch.setattr(auth_routes, "_emit_auth_audit", _emit)

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    response = Response()
    payload = auth_routes.LoginRequest(username="chenjie", password="bad")
    with pytest.raises(HTTPException):
        await auth_routes.login(payload, response, request)

    assert emitted
    assert emitted[0][0] == "account.login_failed"
    assert emitted[0][1]["username"] == "chenjie"


async def test_logout_emits_audit_event(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    revoked = {"called": False}
    emitted: list[tuple[str, dict]] = []

    async def _revoke(session_id: str):
        assert session_id == "sess_1"
        revoked["called"] = True

    async def _emit(event_type: str, payload: dict):
        emitted.append((event_type, payload))

    monkeypatch.setattr(auth_routes, "require_user", lambda _request: SimpleNamespace(id="usr_1", username="chenjie", role="admin"))
    monkeypatch.setattr(auth_routes.AuthService, "revoke_session", _revoke)
    monkeypatch.setattr(auth_routes, "_emit_auth_audit", _emit)
    monkeypatch.setattr(auth_routes, "clear_session_cookie", lambda _response: None)

    request = SimpleNamespace(
        cookies={"flocks_session": "sess_1"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    response = Response()
    result = await auth_routes.logout(response, request)

    assert result["success"] is True
    assert revoked["called"] is True
    assert emitted
    assert emitted[0][0] == "account.logout"
    assert emitted[0][1]["user_id"] == "usr_1"


async def test_emit_auth_audit_fallback_writes_when_null_sink(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    class _NullSink:
        pass

    monkeypatch.setattr("flocks.audit.NullAuditSink", _NullSink, raising=False)
    monkeypatch.setattr("flocks.audit.get_sink", lambda: _NullSink)

    written = {"event_type": None, "payload": None}

    class _AuditEvent:
        def __init__(self, **kwargs):
            written["event_type"] = kwargs.get("event_type")
            written["payload"] = kwargs.get("payload")

    class _SqliteSink:
        async def write(self, event):
            _ = event
            return None

    monkeypatch.setattr("flockspro.audit.service.AuditEvent", _AuditEvent, raising=False)
    monkeypatch.setattr("flockspro.audit.sinks.SqliteAuditSink", _SqliteSink, raising=False)

    await auth_routes._emit_auth_audit_fallback(
        "account.login",
        {"user_id": "usr_1", "username": "chenjie", "session_id": "sess_1"},
    )

    assert written["event_type"] == "account.login"
    assert written["payload"]["user_id"] == "usr_1"


async def test_emit_auth_audit_fallback_skips_when_non_null_sink(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    class _CustomSink:
        pass

    monkeypatch.setattr("flocks.audit.get_sink", lambda: _CustomSink)
    called = {"write": False}

    class _SqliteSink:
        async def write(self, event):
            _ = event
            called["write"] = True

    monkeypatch.setattr("flockspro.audit.sinks.SqliteAuditSink", _SqliteSink, raising=False)

    await auth_routes._emit_auth_audit_fallback(
        "account.logout",
        {"user_id": "usr_1", "username": "chenjie", "session_id": "sess_1"},
    )
    assert called["write"] is False
