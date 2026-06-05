from __future__ import annotations

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request


pytestmark = pytest.mark.asyncio


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/update/check", "headers": []})


async def test_check_version_requires_admin_for_flockspro(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes

    called = False

    def _deny_admin(_request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    async def _fake_check_update(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("Pro update checks must not reach updater before admin auth")

    monkeypatch.setattr(update_routes, "require_admin", _deny_admin)
    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    with pytest.raises(HTTPException) as exc:
        await update_routes.check_version(_request(), locale=None, edition="flockspro")

    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert called is False


async def test_check_version_keeps_flocks_channel_public(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    def _deny_admin(_request):
        raise AssertionError("Flocks channel check should not require admin at route level")

    async def _fake_check_update(**kwargs):
        assert kwargs == {"locale": "zh-CN", "force_console_manifest": False}
        return VersionInfo(current_version="v2026.5.9")

    monkeypatch.setattr(update_routes, "require_admin", _deny_admin)
    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    info = await update_routes.check_version(_request(), locale="zh-CN", edition="flocks")

    assert info.current_version == "v2026.5.9"
