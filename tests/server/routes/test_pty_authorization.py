"""PTY HTTP and WebSocket entry points share the administrator boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from flocks.auth.context import AuthUser
from flocks.server import auth
from flocks.server.app import _AuthGuardMiddleware
from flocks.server.routes import pty as routes
from flocks.pty.pty import PtyInfo, PtyStatus


@pytest.fixture
def pty_client(monkeypatch):
    app = FastAPI()
    app.add_middleware(_AuthGuardMiddleware)
    app.include_router(routes.router, prefix="/api/pty")
    app.include_router(routes.router, prefix="/pty")
    info = PtyInfo(id="admin-terminal", title="Admin", command="/bin/sh", args=[],
                   cwd="/private/admin", status=PtyStatus.RUNNING, pid=1)
    monkeypatch.setattr(routes.Pty, "list", Mock(return_value=[info]))
    monkeypatch.setattr(routes.Pty, "get", Mock(return_value=info))

    async def connect(_pty_id, websocket, **_kwargs):
        async def echo(data):
            await websocket.send_text(data)
        return {"onMessage": echo, "onClose": lambda: None}

    monkeypatch.setattr(routes.Pty, "connect", AsyncMock(side_effect=connect))
    monkeypatch.setattr(auth.AuthService, "has_users", AsyncMock(return_value=True))

    async def user_for_session(session_id):
        user = AuthUser(id=session_id, username=session_id, role=session_id)
        return SimpleNamespace(to_auth_user=lambda: user)

    monkeypatch.setattr(auth.AuthService, "get_user_by_session_id", user_for_session)
    return TestClient(app)


@pytest.mark.parametrize("prefix", ["/api/pty", "/pty"])
@pytest.mark.parametrize("suffix", ["", "/admin-terminal"])
@pytest.mark.parametrize("role", ["member", "pro"])
def test_member_cannot_discover_admin_terminal(pty_client, prefix, suffix, role):
    pty_client.cookies.set(auth.SESSION_COOKIE_NAME, role)
    assert pty_client.get(prefix + suffix).status_code == 403
    routes.Pty.list.assert_not_called()
    routes.Pty.get.assert_not_called()


@pytest.mark.parametrize("prefix", ["/api/pty", "/pty"])
@pytest.mark.parametrize("role", ["member", "pro"])
def test_member_cannot_connect_or_write_admin_terminal(pty_client, prefix, role):
    pty_client.cookies.set(auth.SESSION_COOKIE_NAME, role)
    with pytest.raises(WebSocketDisconnect) as error:
        with pty_client.websocket_connect(prefix + "/admin-terminal/connect"):
            pytest.fail("A non-admin must be refused before WebSocket acceptance")
    assert error.value.code == 4403
    routes.Pty.get.assert_not_called()
    routes.Pty.connect.assert_not_called()


@pytest.mark.parametrize("prefix", ["/api/pty", "/pty"])
def test_admin_can_list_get_and_use_terminal(pty_client, prefix):
    pty_client.cookies.set(auth.SESSION_COOKIE_NAME, "admin")
    assert pty_client.get(prefix).json()[0]["id"] == "admin-terminal"
    assert pty_client.get(prefix + "/admin-terminal").json()["id"] == "admin-terminal"
    with pty_client.websocket_connect(prefix + "/admin-terminal/connect") as websocket:
        websocket.send_text("harmless test input")
        assert websocket.receive_text() == "harmless test input"
    routes.Pty.connect.assert_awaited_once()


@pytest.mark.parametrize("prefix", ["/api/pty", "/pty"])
def test_anonymous_cannot_discover_or_connect(pty_client, prefix):
    assert pty_client.get(prefix, headers={"Origin": "https://example.test"}).status_code == 401
    with pytest.raises(WebSocketDisconnect) as error:
        with pty_client.websocket_connect(prefix + "/admin-terminal/connect", headers={"Origin": "https://example.test"}):
            pytest.fail("An anonymous client must be refused")
    assert error.value.code == 4401
    routes.Pty.list.assert_not_called()
    routes.Pty.get.assert_not_called()
    routes.Pty.connect.assert_not_called()


@pytest.mark.asyncio
async def test_authentication_failure_closes_without_looking_up_terminal(monkeypatch):
    websocket = SimpleNamespace(close=AsyncMock(), state=SimpleNamespace())
    monkeypatch.setattr(routes, "apply_auth_for_request", AsyncMock(side_effect=RuntimeError("auth unavailable")))
    lookup = Mock()
    monkeypatch.setattr(routes.Pty, "get", lookup)
    await routes.connect_session(websocket, "admin-terminal")
    websocket.close.assert_awaited_once_with(code=1011, reason="Authentication unavailable")
    lookup.assert_not_called()


# -- G09: a managed install's terminal input is policy-visible, or not offered ----------


def test_a_managed_install_offers_no_interactive_terminal_unless_turned_on(pty_client, monkeypatch):
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    monkeypatch.delenv("FLOCKS_PTY_INTERACTIVE", raising=False)
    pty_client.cookies.set(auth.SESSION_COOKIE_NAME, "admin")

    with pytest.raises(WebSocketDisconnect) as error:
        with pty_client.websocket_connect("/api/pty/admin-terminal/connect"):
            pytest.fail("a managed install must not accept the terminal")
    assert error.value.code == 4403
    routes.Pty.connect.assert_not_called()


def test_a_managed_terminal_that_is_turned_on_sends_every_input_through_the_tool_lifecycle(pty_client, monkeypatch):
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    monkeypatch.setenv("FLOCKS_PTY_INTERACTIVE", "1")
    pty_client.cookies.set(auth.SESSION_COOKIE_NAME, "admin")

    with pty_client.websocket_connect("/api/pty/admin-terminal/connect") as websocket:
        websocket.send_text("ls\n")
        assert websocket.receive_text() == "ls\n"
    assert routes.Pty.connect.await_args.kwargs["use_action_lifecycle"] is True


def test_an_unmanaged_install_keeps_the_administrators_terminal_as_it_is(pty_client, monkeypatch):
    monkeypatch.delenv("FLOCKS_DEPLOY_MODE", raising=False)
    monkeypatch.setattr(routes, "declared_managed", lambda: False)
    pty_client.cookies.set(auth.SESSION_COOKIE_NAME, "admin")

    with pty_client.websocket_connect("/api/pty/admin-terminal/connect") as websocket:
        websocket.send_text("x")
        assert websocket.receive_text() == "x"
    assert routes.Pty.connect.await_args.kwargs["use_action_lifecycle"] is False
