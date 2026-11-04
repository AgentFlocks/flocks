"""Regression: revealing device credentials (plaintext secrets) is admin-only.

A non-admin member must not be able to read stored device secrets via
``POST /api/devices/{id}/credentials``. Other device-management endpoints stay
member-accessible by design; only credential reveal is gated with
``require_admin`` (see flocks/server/routes/device.py).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from flocks.auth.context import AuthUser
from flocks.server.routes import device as device_routes


def _request_with_user(user: AuthUser | None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/devices/dev-x/credentials",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "state": {},
    }
    request = Request(scope)
    if user is not None:
        request.state.auth_user = user
    return request


@pytest.mark.asyncio
async def test_member_cannot_reveal_device_credentials(monkeypatch: pytest.MonkeyPatch):
    revealed = {"called": False}

    async def fake_fetch_device(device_id: str):
        revealed["called"] = True
        return {"fields": "{}", "storage_key": "k"}

    monkeypatch.setattr(device_routes, "fetch_device", fake_fetch_device)

    member = AuthUser(id="u-member", username="member1", role="member")
    with pytest.raises(HTTPException) as exc:
        await device_routes.route_get_device_credentials("dev-x", _request_with_user(member), None)
    assert exc.value.status_code == 403
    assert exc.value.detail == "仅管理员可执行该操作"
    # The device must never be read for a rejected caller.
    assert revealed["called"] is False


@pytest.mark.asyncio
async def test_unauthenticated_cannot_reveal_device_credentials():
    with pytest.raises(HTTPException) as exc:
        await device_routes.route_get_device_credentials("dev-x", _request_with_user(None), None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_admin_can_reveal_device_credentials(monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch_device(device_id: str):
        return {"fields": '{"api_key": "{secret:device_dev-x_api_key}"}', "storage_key": "k"}

    monkeypatch.setattr(device_routes, "fetch_device", fake_fetch_device)
    monkeypatch.setattr(device_routes, "resolve_for_runtime", lambda f: {"api_key": "sk-real"})

    async def fake_audit(event_type: str, payload: dict):
        pass

    monkeypatch.setattr(device_routes, "_emit_device_audit", fake_audit)

    admin = AuthUser(id="u-admin", username="admin", role="admin")
    resp = await device_routes.route_get_device_credentials("dev-x", _request_with_user(admin), None)
    assert resp.fields == {"api_key": "sk-real"}


@pytest.mark.asyncio
async def test_member_cannot_create_custom_device_template():
    from flocks.tool.device.models import CustomDeviceTemplateCreate

    body = CustomDeviceTemplateCreate(plugin_id="x", name="X", service_id="x", tools=[])
    member = AuthUser(id="u-member", username="member1", role="member")
    with pytest.raises(HTTPException) as exc:
        await device_routes.route_create_custom_device_template(body, _request_with_user(member))
    assert exc.value.status_code == 403
