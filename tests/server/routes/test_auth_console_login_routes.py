from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import status
from httpx import AsyncClient

from flocks.auth.context import AuthUser


pytestmark = pytest.mark.asyncio


def _mock_admin() -> AuthUser:
    return AuthUser(
        id="usr_admin",
        username="admin",
        role="admin",
        status="active",
        must_reset_password=False,
    )


async def test_console_login_start_returns_payload(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "console_login_id": "remote_login",
                "state": "remote_state",
                "passport_login_url": "https://passport.example/login?service=flocks",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example")
    monkeypatch.setattr("flocks.console.login.httpx.AsyncClient", _Client)

    resp = await client.get("/api/auth/console-login/start")
    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["console_login_id"] == "remote_login"
    parsed = urlparse(payload["passport_login_url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "passport.example"
    assert parse_qs(parsed.query) == {"service": ["flocks"]}


async def test_console_login_start_requires_console_base_url(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")

    resp = await client.get("/api/auth/console-login/start")
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "FLOCKS_CONSOLE_BASE_URL" in resp.text


async def test_remote_console_login_keeps_console_passport_url(monkeypatch: pytest.MonkeyPatch):
    from flocks.console.login import ConsoleLoginService

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "console_login_id": "remote_login",
                "passport_login_url": "https://passport.example/login?service=flocks",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example")
    monkeypatch.setenv("FLOCKS_PORTAL_BASE_URL", "http://127.0.0.1:3000")
    monkeypatch.setattr("flocks.console.login.httpx.AsyncClient", _Client)

    result = await ConsoleLoginService.start_console_login("http://127.0.0.1:5173/flockspro-upgrade/callback")

    parsed = urlparse(result["passport_login_url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "passport.example"
    assert parse_qs(parsed.query) == {"service": ["flocks"]}


async def test_console_login_finish_maps_value_error_to_400(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    async def _finish_console_login(*, console_login_id: str, state: str | None = None, passport_uid: str | None = None):
        raise ValueError(f"invalid console login id: {console_login_id}")

    monkeypatch.setattr(auth_routes.ConsoleLoginService, "finish_console_login", _finish_console_login)

    resp = await client.post("/api/auth/console-login/finish", json={"console_login_id": "bad_id"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "invalid console login id" in resp.text


async def test_console_login_finish_success_does_not_return_token(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    async def _finish_console_login(*, console_login_id: str, state: str | None = None, passport_uid: str | None = None):
        assert console_login_id == "login_ok"
        assert state == "state_ok"
        assert passport_uid is None
        return {
            "console_login_id": console_login_id,
            "console_session_token": "token_abc",
            "fingerprint": "fp_1",
            "install_id": "inst_1",
            "user_display": "test_user",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(auth_routes.ConsoleLoginService, "finish_console_login", _finish_console_login)

    resp = await client.post(
        "/api/auth/console-login/finish",
        json={"console_login_id": "login_ok", "state": "state_ok"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "console_login_id": "login_ok",
        "logged_in": True,
        "account_name": "test_user",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


async def test_console_login_finish_requires_console_base_url(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import auth as auth_routes
    from flocks.storage.storage import Storage

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")
    await Storage.delete("console:session")
    await Storage.set(
        "console:login:login_without_console",
        {"console_login_id": "login_without_console", "state": "state_without_console"},
        "json",
    )

    return_resp = await client.post(
        "/api/auth/console-login/finish",
        json={"console_login_id": "login_without_console", "state": "state_without_console"},
    )
    assert return_resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "FLOCKS_CONSOLE_BASE_URL" in return_resp.text

    session_resp = await client.get("/api/auth/console-login/session")
    assert session_resp.status_code == status.HTTP_200_OK
    assert session_resp.json()["logged_in"] is False


async def test_console_login_session_status(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    async def _get_console_session():
        return {
            "console_login_id": "login_ok",
            "user_display": "test_user",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(auth_routes.ConsoleLoginService, "get_console_session", _get_console_session)

    resp = await client.get("/api/auth/console-login/session")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["logged_in"] is True
    assert resp.json()["console_login_id"] == "login_ok"
    assert resp.json()["account_name"] == "test_user"


async def test_console_login_session_expired_local_token_is_logged_out(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import auth as auth_routes
    from flocks.storage.storage import Storage

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())
    await Storage.set(
        "console:session",
        {
            "console_login_id": "login_expired",
            "console_session_token": "cs_expired",
            "fingerprint": "fp_1",
            "install_id": "inst_1",
            "passport_uid": "passport_1",
            "expires_at": "2000-01-01T00:00:00+00:00",
        },
        "json",
    )

    resp = await client.get("/api/auth/console-login/session")

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["logged_in"] is False
    assert await Storage.get("console:session") is None


async def test_console_login_logout(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    called = {"ok": False}

    async def _logout_console_session():
        called["ok"] = True

    monkeypatch.setattr(auth_routes.ConsoleLoginService, "logout_console_session", _logout_console_session)

    resp = await client.post("/api/auth/console-login/logout")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["success"] is True
    assert called["ok"] is True


async def test_logout_console_session_sends_revoke_body(monkeypatch: pytest.MonkeyPatch):
    from flocks.console.login import ConsoleLoginService
    from flocks.storage.storage import Storage

    captured: dict = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs

            class _Response:
                pass

            return _Response()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example")
    monkeypatch.setattr("flocks.console.login.httpx.AsyncClient", _Client)
    await Storage.set("console:session", {"console_session_token": "cs_test"}, "json")

    await ConsoleLoginService.logout_console_session()

    assert captured["url"] == "https://console.example/v1/console-sessions/revoke"
    assert captured["kwargs"]["headers"] == {"Authorization": "Bearer cs_test"}
    assert captured["kwargs"]["json"] == {"console_session_token": "cs_test"}
    assert await Storage.get("console:session") is None


async def test_refresh_console_session_extends_local_expiry(monkeypatch: pytest.MonkeyPatch):
    from flocks.console.login import ConsoleLoginService
    from flocks.storage.storage import Storage

    captured: dict = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "console_session_token": "cs_test",
                "passport_uid": "passport_1",
                "user_display": "chenjie",
                "user_email": "chenjie@example.com",
                "expires_at": "2026-06-10T00:00:00+00:00",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _Response()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example")
    monkeypatch.setattr("flocks.console.login.httpx.AsyncClient", _Client)
    await Storage.set(
        "console:session",
        {
            "console_session_token": "cs_test",
            "fingerprint": "fp_1",
            "install_id": "inst_1",
            "expires_at": "2099-05-10T00:00:00+00:00",
        },
        "json",
    )

    refreshed = await ConsoleLoginService.refresh_console_session()

    assert captured["url"] == "https://console.example/v1/console-sessions/refresh"
    assert captured["kwargs"]["headers"] == {"Authorization": "Bearer cs_test"}
    assert captured["kwargs"]["json"] == {"console_session_token": "cs_test"}
    assert refreshed["expires_at"] == "2026-06-10T00:00:00+00:00"
    assert refreshed["user_display"] == "chenjie"
    stored = await Storage.get("console:session")
    assert stored["expires_at"] == "2026-06-10T00:00:00+00:00"


async def test_console_login_session_without_account_name_treated_logged_out(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    async def _get_console_session():
        return {
            "console_login_id": "login_no_name",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(auth_routes.ConsoleLoginService, "get_console_session", _get_console_session)

    resp = await client.get("/api/auth/console-login/session")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["logged_in"] is False



