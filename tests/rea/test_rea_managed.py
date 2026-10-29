"""A managed install changes REA only with the deployment's release.

Installing or removing REA runs package managers and downloads inside the
service, past the checks a managed deployment puts on every command a tool
runs.  So in a managed install the tool, the installer, the uninstaller and the
uninstall route refuse with ``managed`` before anything starts; elsewhere they
work as before.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.rea import operation
from flocks.rea.installer import ReaInstaller, ReaInstallError
from flocks.rea.uninstaller import ReaUninstaller
from flocks.server.routes import rea as rea_routes
from flocks.tool.rea import rea_analysis_tool
from flocks.tool.registry import ToolContext


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")


@pytest.fixture(autouse=True)
def quiet_events(monkeypatch):
    monkeypatch.setattr("flocks.server.routes.event.publish_event", AsyncMock())


async def test_the_installer_refuses_before_taking_the_hosts_lock(managed):
    with pytest.raises(ReaInstallError) as refused:
        await ReaInstaller.start("light", user_id="admin-1")
    assert refused.value.code == "managed"
    assert ReaInstaller.current_job() is None and operation.holder() is None


async def test_the_uninstaller_refuses_too(managed):
    with pytest.raises(ReaInstallError) as refused:
        await ReaUninstaller.start(stop_tasks=True, user_id="admin-1")
    assert refused.value.code == "managed"
    assert ReaUninstaller.current_job() is None and operation.holder() is None


def _admin_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def admin(request, call_next):
        user = AuthUser(id="admin-1", username="admin", role="admin")
        request.state.auth_user = user
        context = set_current_auth_user(user)
        try:
            return await call_next(request)
        finally:
            reset_current_auth_user(context)

    app.include_router(rea_routes.router, prefix="/api")
    return app


def test_the_uninstall_route_answers_409_managed(managed):
    response = TestClient(_admin_app()).post("/api/rea/uninstall", json={"stopTasks": True})
    assert response.status_code == 409
    payload = response.json()
    detail = payload.get("message") or payload["detail"]
    assert detail["code"] == "managed"
    assert ReaUninstaller.current_job() is None


async def test_the_tool_refuses_before_showing_a_plan(managed, monkeypatch):
    started = []

    async def start(*args, **kwargs):
        started.append(args)

    monkeypatch.setattr(ReaInstaller, "start", start)
    user = AuthUser(id="admin-1", username="admin", role="admin")
    monkeypatch.setattr("flocks.auth.context.get_current_auth_user", lambda: user)

    result = await rea_analysis_tool(ToolContext(session_id="ses_managed", message_id="msg_1", agent="rex"), action="install")

    assert result.success is False and "managed install" in result.error
    assert result.metadata["rea"]["errorCode"] == "managed"
    assert started == []


async def test_without_a_managed_deployment_the_installer_is_not_refused(monkeypatch):
    monkeypatch.delenv("FLOCKS_DEPLOY_MODE", raising=False)
    from flocks.rea.installer import refuse_when_managed

    refuse_when_managed()  # no exception
