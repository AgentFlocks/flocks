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


async def test_cloud_login_init_returns_binding_payload(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())
    monkeypatch.setenv("FLOCKS_ACT_BASE_URL", "")
    monkeypatch.setenv("FLOCKS_PORTAL_BASE_URL", "http://127.0.0.1:3000")

    resp = await client.get("/api/auth/cloud/login")
    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["binding_id"]
    parsed = urlparse(payload["portal_login_url"])
    params = parse_qs(parsed.query)
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:3000"
    assert params.get("binding_id", [None])[0] == payload["binding_id"]
    assert params.get("return_to", [None])[0] == "/auth/cloud/return"
    assert params.get("return-to", [None])[0] == "/auth/cloud/return"


async def test_cloud_login_return_maps_value_error_to_400(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    async def _exchange_binding(*, binding_id: str, passport_uid: str | None = None):
        raise ValueError(f"invalid binding id: {binding_id}")

    monkeypatch.setattr(auth_routes.CloudBindingService, "exchange_binding", _exchange_binding)

    resp = await client.get("/api/auth/cloud/return", params={"binding_id": "bad_id"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "invalid binding id" in resp.text


async def test_cloud_login_return_success(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    async def _exchange_binding(*, binding_id: str, passport_uid: str | None = None):
        assert binding_id == "bind_ok"
        assert passport_uid is None
        return {
            "binding_id": binding_id,
            "cloud_session_token": "token_abc",
            "fingerprint": "fp_1",
            "install_id": "inst_1",
        }

    monkeypatch.setattr(auth_routes.CloudBindingService, "exchange_binding", _exchange_binding)

    resp = await client.get("/api/auth/cloud/return", params={"binding_id": "bind_ok"})
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["cloud_session_token"] == "token_abc"


async def test_local_cloud_login_return_without_passport_uid_still_binds(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import auth as auth_routes
    from flocks.storage.storage import Storage

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())
    monkeypatch.setenv("FLOCKS_ACT_BASE_URL", "")
    await Storage.delete("cloud:session")

    init_resp = await client.get("/api/auth/cloud/login")
    assert init_resp.status_code == status.HTTP_200_OK
    binding_id = init_resp.json()["binding_id"]

    return_resp = await client.get("/api/auth/cloud/return", params={"binding_id": binding_id})
    assert return_resp.status_code == status.HTTP_200_OK

    session_resp = await client.get("/api/auth/cloud/session")
    assert session_resp.status_code == status.HTTP_200_OK
    payload = session_resp.json()
    assert payload["bound"] is True
    assert payload["account_name"] == "local-cloud-user"


async def test_cloud_session_status(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    async def _get_bound_session():
        return {
            "binding_id": "bind_ok",
            "user_display": "test_user",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(auth_routes.CloudBindingService, "get_bound_session", _get_bound_session)

    resp = await client.get("/api/auth/cloud/session")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["bound"] is True
    assert resp.json()["binding_id"] == "bind_ok"
    assert resp.json()["account_name"] == "test_user"


async def test_cloud_session_unbind(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    called = {"ok": False}

    async def _clear_bound_session():
        called["ok"] = True

    monkeypatch.setattr(auth_routes.CloudBindingService, "clear_bound_session", _clear_bound_session)

    resp = await client.post("/api/auth/cloud/unbind")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["success"] is True
    assert called["ok"] is True


async def test_cloud_session_status_without_account_name_treated_unbound(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    async def _get_bound_session():
        return {
            "binding_id": "bind_no_name",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(auth_routes.CloudBindingService, "get_bound_session", _get_bound_session)

    resp = await client.get("/api/auth/cloud/session")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["bound"] is False


async def test_cloud_sync_now(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    called = {"heartbeat": False, "sync": False}

    async def _send_heartbeat():
        called["heartbeat"] = True
        return {
            "ok": True,
            "node": {"received_at": "2026-01-01T00:00:00+00:00"},
        }

    async def _sync_node_profile(*, force: bool = False, source: str = "scheduled"):
        assert force is True
        assert source == "manual"
        called["sync"] = True
        return {
            "ok": True,
            "node": {"received_at": "2026-01-01T00:00:00+00:00"},
        }

    monkeypatch.setattr(auth_routes.CloudBindingService, "send_heartbeat", _send_heartbeat)
    monkeypatch.setattr(auth_routes.CloudBindingService, "sync_node_profile", _sync_node_profile)

    resp = await client.post("/api/auth/cloud/sync-now")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["success"] is True
    assert resp.json()["synced_at"] == "2026-01-01T00:00:00+00:00"
    assert called["heartbeat"] is True
    assert called["sync"] is True


async def test_cloud_sync_now_maps_value_error_to_400(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "require_admin", lambda _req: _mock_admin())

    async def _send_heartbeat():
        raise ValueError("云账号未绑定")

    async def _sync_node_profile(*, force: bool = False, source: str = "scheduled"):
        raise ValueError("云账号未绑定")

    monkeypatch.setattr(auth_routes.CloudBindingService, "send_heartbeat", _send_heartbeat)
    monkeypatch.setattr(auth_routes.CloudBindingService, "sync_node_profile", _sync_node_profile)

    resp = await client.post("/api/auth/cloud/sync-now")
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "云账号未绑定" in resp.text

