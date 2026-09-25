from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from flocks.voice.settings import KEY_ENV, update_settings
from flocks.server.routes import voice as voice_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    from flocks.voice import settings
    monkeypatch.setattr(settings, "_legacy_path", None)
    monkeypatch.setattr(settings, "_legacy_key", None)
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.setattr(voice_routes, "clear_auth_context", lambda _token: None)

    async def auth(connection):
        role = connection.headers.get("x-test-role", "admin")
        if role == "anonymous":
            raise HTTPException(status_code=401)
        user = SimpleNamespace(id="test-user", role=role)
        connection.state.auth_user = user
        return None, None, user

    monkeypatch.setattr(voice_routes, "apply_auth_for_request", auth)
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request, call_next):
        if request.headers.get("x-test-role") != "anonymous":
            request.state.auth_user = SimpleNamespace(id="test-user", role=request.headers.get("x-test-role", "admin"))
        return await call_next(request)

    app.include_router(voice_routes.router, prefix="/api/voice")
    with TestClient(app) as test_client:
        yield test_client


def test_settings_complete_user_flow(client):
    base = "/api/voice"
    assert client.get(base + "/status").json() == {"enabled": False, "configured": False, "available": False, "capture_mode": "voice"}
    saved = client.put(base + "/config", json={"api_key": "test-only-credential"})
    assert saved.status_code == 200
    assert not saved.json()["enabled"] and saved.json()["configured"]
    enabled = client.put(base + "/config", json={"enabled": True})
    assert enabled.json()["available"]
    assert "test-only-credential" not in client.get(base + "/config").text
    assert client.put(base + "/config", json={"api_key": ""}).json()["available"] is False


@pytest.mark.parametrize("payload,code", [
    ({"enabled": True}, 400), ({"enabled": "true"}, 422),
    ({"enabled": 1}, 422), ({"enabled": None}, 400),
    ({"api_key": None}, 400), ({"api_key": "test\ncredential"}, 400),
    ({"api_key": "test-only-credential", "unknown": "test-only-credential"}, 422),
    ({"api_key": "x" * 70000}, 413),
])
def test_invalid_settings_are_safe(client, payload, code, caplog):
    response = client.put("/api/voice/config", json=payload)
    assert response.status_code == code
    assert "test-only-credential" not in response.text
    assert "test-only-credential" not in caplog.text


def test_invalid_json_safe(client):
    response = client.put("/api/voice/config", content='{"api_key":"test-only-credential"')
    assert response.status_code == 422
    assert "test-only-credential" not in response.text


@pytest.mark.parametrize("role,code", [("member", 403), ("anonymous", 401)])
def test_settings_require_admin(client, role, code):
    headers = {"x-test-role": role}
    assert client.get("/api/voice/config", headers=headers).status_code == code
    assert client.put("/api/voice/config", headers=headers, json={"api_key": "test"}).status_code == code
    assert client.get("/api/voice/status", headers=headers).status_code == (401 if role == "anonymous" else 200)



@pytest.mark.parametrize("configured,enabled,role,expected", [
    (False, False, "admin", 4403), (True, False, "admin", 4403),
    (True, True, "anonymous", 4401),
])
def test_socket_gates(client, configured, enabled, role, expected):
    if configured:
        update_settings({"api_key": "test", "enabled": enabled})
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/voice/stream", headers={"origin": "http://testserver", "x-test-role": role}):
            pass
    assert error.value.code == expected


def test_socket_authenticated_and_uses_current_settings(client, monkeypatch):
    received = []

    async def serve(websocket, api_key, authorize=None, **options):
        assert await authorize()
        received.append(api_key)
        await websocket.send_json({"type": "ready"})
        await websocket.close()

    monkeypatch.setattr(voice_routes, "serve", serve)
    update_settings({"api_key": "test", "enabled": True})
    with client.websocket_connect("/api/voice/stream", headers={"origin": "http://testserver", "x-test-role": "member"}) as socket:
        assert socket.receive_json() == {"type": "ready"}
    assert received == ["test"]
    update_settings({"enabled": False})
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/voice/stream", headers={"origin": "http://testserver"}):
            pass
    assert received == ["test"]


@pytest.mark.asyncio
async def test_origin_policy_uses_explicit_core_origins(monkeypatch):
    from flocks.server import app
    from starlette.websockets import WebSocket

    monkeypatch.setattr(app, "_read_cors_config", lambda: (["http://localhost:5173"], None))

    def socket(origin, cookie=False):
        headers = [(b"host", b"testserver")]
        if origin:
            headers.append((b"origin", origin.encode()))
        if cookie:
            headers.append((b"cookie", b"flocks_session=test"))
        return WebSocket({"type": "websocket", "scheme": "ws", "path": "/", "query_string": b"", "headers": headers}, None, None)

    assert await voice_routes._origin_allowed(socket("http://testserver"))
    assert await voice_routes._origin_allowed(socket("http://localhost:5173"))
    assert not await voice_routes._origin_allowed(socket("https://attacker.example"))
    assert not await voice_routes._origin_allowed(socket("null"))
    assert not await voice_routes._origin_allowed(socket("http://["))
    assert not await voice_routes._origin_allowed(socket(None, cookie=True))
    assert await voice_routes._origin_allowed(socket(None))


def test_socket_real_cookie_auth_calls_ingress_adapter(client, monkeypatch):
    from flocks.server import auth
    from flocks.auth.context import AuthUser

    called = []
    user = AuthUser(id="cookie-user", username="member", role="member", status="active", must_reset_password=False)

    async def has_users():
        return True

    async def session_user(session_id):
        assert session_id == "voice-test-session"
        return SimpleNamespace(to_auth_user=lambda: user)

    async def adapter(connection, resolved_user):
        called.append((connection.url.path, resolved_user.id))
        return {"voice-test-ingress": True}

    async def serve(websocket, api_key, authorize=None, **options):
        assert websocket.state.extension_context["voice-test-ingress"]
        assert websocket.state.auth_user.id == "cookie-user"
        await websocket.send_json({"type": "ready"})
        await websocket.close()

    monkeypatch.setattr(auth.AuthService, "has_users", has_users)
    monkeypatch.setattr(auth.AuthService, "get_user_by_session_id", session_user)
    monkeypatch.setattr(auth, "_auth_context_adapters", {"voice-test": adapter})
    monkeypatch.setattr(voice_routes, "apply_auth_for_request", auth.apply_auth_for_request)
    monkeypatch.setattr(voice_routes, "clear_auth_context", auth.clear_auth_context)
    monkeypatch.setattr(voice_routes, "serve", serve)
    update_settings({"api_key": "test", "enabled": True})
    with client.websocket_connect("/api/voice/stream", headers={
        "origin": "http://testserver", "cookie": "flocks_session=voice-test-session",
    }) as socket:
        assert socket.receive_json() == {"type": "ready"}
    assert called == [("/api/voice/stream", "cookie-user")]


def test_open_runtime_settings_and_socket_need_no_enterprise_license(client, monkeypatch):
    options = []

    async def serve(socket, key, authorize=None, **kwargs):
        assert await authorize()
        options.append(kwargs)
        await socket.send_json({"type": "ready"})
        await socket.close()

    monkeypatch.setattr(voice_routes, "serve", serve)
    saved = client.put("/api/voice/config", json={
        "api_key": "test", "enabled": True, "hotwords": ["Flocks", " 海淀 "], "capture_mode": "raw",
    })
    assert saved.status_code == 200
    assert saved.json()["hotwords"] == ["Flocks", "海淀"]
    assert client.get("/api/voice/status").json()["capture_mode"] == "raw"
    with client.websocket_connect("/api/voice/stream", headers={"origin": "http://testserver"}) as socket:
        assert socket.receive_json() == {"type": "ready"}
    assert options == [{"hotwords": ("Flocks", "海淀")}]


@pytest.mark.parametrize("payload", [
    {"hotwords": ["a"] * 101}, {"hotwords": ["a" * 65]}, {"hotwords": [""]},
    {"capture_mode": "invalid"},
])
def test_bad_recognition_settings_do_not_mutate_config(client, payload):
    assert client.put("/api/voice/config", json={"api_key": "test", "enabled": True}).status_code == 200
    assert client.put("/api/voice/config", json=payload).status_code == 400
    assert client.get("/api/voice/status").json()["available"]
