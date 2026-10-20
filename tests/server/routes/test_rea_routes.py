"""REA routes: unified status (with the install conversation) and the uninstall with its confirmation."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.rea import runtime, service, uninstaller
from flocks.rea.installer import ReaInstaller, ReaInstallJob
from flocks.rea.uninstaller import ReaUninstaller
from flocks.server.routes import rea as rea_routes
from flocks.session.session import Session


def _app(role: str, user_id: str | None = None) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def user_auth(request, call_next):
        user = AuthUser(id=user_id or f"{role}-1", username=user_id or role, role=role)
        request.state.auth_user = user
        context = set_current_auth_user(user)
        try:
            return await call_next(request)
        finally:
            reset_current_auth_user(context)

    app.include_router(rea_routes.router, prefix="/api")
    return app


def _client(role: str, user_id: str | None = None) -> TestClient:
    return TestClient(_app(role, user_id), raise_server_exceptions=True)


def _async_client(role: str, user_id: str | None = None) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app(role, user_id)), base_url="http://test")


async def _session(tmp_path, owner: str):
    return await Session.create(
        project_id="rea_routes", directory=str(tmp_path), title="REA 逆向分析",
        owner_user_id=owner, owner_username=owner,
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from flocks.mcp import MCP

    runtime.reset_environment_cache()
    ReaInstaller.reset_for_testing()
    ReaUninstaller.reset_for_testing()
    monkeypatch.setattr("flocks.server.routes.event.publish_event", AsyncMock())
    monkeypatch.setenv("FLOCKS_REA_JAVA_HOME", str(tmp_path / "no-jdk"))
    monkeypatch.setattr(runtime, "java_home_candidates", lambda: [str(tmp_path / "no-jdk")])
    monkeypatch.setattr(MCP, "status_snapshot", lambda: ({}, True))

    async def fake_environment(*, refresh=False):
        return [runtime.EnvironmentCheck(key="node", ok=True, detail="v26.1.0"),
                runtime.EnvironmentCheck(key="npx", ok=True, detail="/opt/node/bin/npx"),
                runtime.EnvironmentCheck(key="storage", ok=True, detail="/srv/flocks/data/rea")]

    monkeypatch.setattr(runtime, "check_environment", fake_environment)
    yield
    ReaInstaller.reset_for_testing()
    ReaUninstaller.reset_for_testing()


def test_status_reflects_role_badge_and_hides_paths():
    member = _client("member").get("/api/rea/status")
    assert member.status_code == 200
    body = member.json()
    assert body["package"] == "rea-agents" and body["version"] == "6.4.0"
    assert body["state"] == "not_installed" and body["installed"] is False
    assert body["canInstall"] is False and body["badge"] is True
    checks = {check["key"]: check for check in body["environment"]["checks"]}
    assert checks["node"]["detail"] == "v26.1.0"
    assert checks["npx"]["detail"] is None and checks["storage"]["detail"] is None

    admin = _client("admin").get("/api/rea/status").json()
    assert admin["canInstall"] is True and admin["badge"] is True and admin["firstInstallCompleted"] is False


def test_the_badge_disappears_after_the_first_install_and_stays_away():
    service.record_first_install("admin-1")
    client = _client("admin")
    assert client.get("/api/rea/status").json()["badge"] is False
    assert _client("admin", "admin-2").get("/api/rea/status").json()["badge"] is True


def test_failed_job_detail_is_only_shown_to_admins():
    ReaInstaller._job = ReaInstallJob(
        id="reainst_1", status="failed", stage="components", progress=10, startedAt=1, finishedAt=2,
        errorCode="blocked", errorDetail="jdk: needs root",
        blockers=[{"code": "jdk_needs_root", "command": "dnf install -y java-21-openjdk-devel"}],
    )
    admin = _client("admin").get("/api/rea/status").json()
    assert admin["state"] == "install_failed"
    assert admin["job"]["errorDetail"] == "jdk: needs root" and admin["job"]["blockers"]
    member = _client("member").get("/api/rea/status").json()
    assert member["state"] == "install_failed"
    assert member["job"]["errorCode"] == "blocked"
    assert member["job"]["errorDetail"] is None and member["job"]["blockers"] == []


@pytest.mark.asyncio
async def test_the_install_session_is_the_one_rexs_install_recorded(tmp_path):
    own = await _session(tmp_path, "admin-1")
    foreign = await _session(tmp_path, "someone-else")
    # The entry only fills the guide in; the install tool records the conversation it runs in.
    async with _async_client("admin") as admin:
        assert (await admin.post("/api/rea/install-session/reserve")).status_code in (404, 405)
        assert (await admin.post("/api/rea/install-session", json={"sessionId": own.id})).status_code in (404, 405)
        assert (await admin.get("/api/rea/status")).json()["installSessionId"] is None
        service.record_install_session(foreign.id, AuthUser(id="admin-1", username="admin", role="admin"))
        assert (await admin.get("/api/rea/status")).json()["installSessionId"] is None  # not writable for them
        service.record_install_session(own.id, AuthUser(id="admin-1", username="admin", role="admin"))
        assert (await admin.get("/api/rea/status")).json()["installSessionId"] == own.id

    # Members never get it; another administrator has their own.
    async with _async_client("member") as member:
        assert (await member.get("/api/rea/status")).json()["installSessionId"] is None
    async with _async_client("admin", "admin-2") as other:
        assert (await other.get("/api/rea/status")).json()["installSessionId"] is None


@pytest.mark.asyncio
async def test_an_expired_or_archived_install_session_is_not_reopened(tmp_path, monkeypatch):
    session = await _session(tmp_path, "admin-1")
    service.record_install_session(session.id, AuthUser(id="admin-1", username="admin", role="admin"))
    async with _async_client("admin") as admin:
        later = int(time.time() * 1000) + service.INSTALL_SESSION_REUSE_MS + 1
        monkeypatch.setattr(service, "_now_ms", lambda: later)
        assert (await admin.get("/api/rea/status")).json()["installSessionId"] is None
        monkeypatch.setattr(ReaInstaller, "is_running", classmethod(lambda cls: True))
        assert (await admin.get("/api/rea/status")).json()["installSessionId"] == session.id
        monkeypatch.setattr(ReaInstaller, "is_running", classmethod(lambda cls: False))
        monkeypatch.setattr(service, "_now_ms", lambda: int(time.time() * 1000))
        assert await Session.archive(session.project_id, session.id) is True
        assert (await admin.get("/api/rea/status")).json()["installSessionId"] is None


def test_uninstall_preview_is_admin_only():
    assert _client("member").get("/api/rea/uninstall/preview").status_code == 403
    preview = _client("admin").get("/api/rea/uninstall/preview").json()
    assert preview["state"] == "not_installed"
    assert preview["tasks"] == []
    assert any("MCP 服务 rea" in item for item in preview["removes"])
    assert preview["keeps"] == uninstaller.kept_resources()
    assert any(item.endswith("ghidra_*") for item in preview["keeps"])


def test_uninstall_needs_confirmation_while_related_tasks_run(monkeypatch):
    tasks = [{"kind": "session", "id": "ses_a", "title": "分析样本"}]

    async def related():
        return tasks

    monkeypatch.setattr(uninstaller, "related_tasks", related)
    monkeypatch.setattr(rea_routes, "related_tasks", related)
    assert _client("member").post("/api/rea/uninstall", json={}).status_code == 403

    refused = _client("admin").post("/api/rea/uninstall", json={})
    assert refused.status_code == 409
    payload = refused.json()
    detail = payload.get("message") or payload["detail"]  # the Flocks app wraps HTTPException details
    assert detail["code"] == "tasks_running" and detail["tasks"] == tasks
    assert ReaUninstaller.current_job() is None
    assert _client("admin").get("/api/rea/uninstall/preview").json()["tasks"] == tasks


def test_confirmed_uninstall_starts_and_reports_progress(monkeypatch):
    seen = {}

    async def start(cls, *, stop_tasks, user_id=None, previous_state=None):
        seen.update(stop_tasks=stop_tasks, user_id=user_id, previous_state=previous_state)
        cls._job = uninstaller.ReaUninstallJob(id="u1", status="running", startedAt=1, previousState=previous_state)
        return cls._job

    monkeypatch.setattr(ReaUninstaller, "start", classmethod(start))
    monkeypatch.setattr(
        ReaUninstaller, "is_running", classmethod(lambda cls: cls._job is not None and cls._job.status == "running"),
    )
    response = _client("admin").post("/api/rea/uninstall", json={"stopTasks": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert seen == {"stop_tasks": True, "user_id": "admin-1", "previous_state": "not_installed"}
    assert body["state"] == "uninstalling" and body["uninstallJob"]["id"] == "u1"


def test_uninstall_result_detail_is_only_for_admins(monkeypatch):
    ReaUninstaller._job = uninstaller.ReaUninstallJob(
        id="u1", status="failed", startedAt=1, finishedAt=2, residues=["技能 reverse-engineer-anything"],
        errorDetail="Permission denied", removed=["MCP 服务 rea 的配置"],
    )
    monkeypatch.setattr(uninstaller, "has_resources", lambda: True)
    monkeypatch.setattr(service, "has_resources", lambda: True)
    admin = _client("admin").get("/api/rea/status").json()
    assert admin["state"] == "uninstall_failed"
    assert admin["uninstallJob"]["residues"] == ["技能 reverse-engineer-anything"]
    member = _client("member").get("/api/rea/status").json()
    assert member["state"] == "uninstall_failed"
    assert member["uninstallJob"]["residues"] == [] and member["uninstallJob"]["errorDetail"] is None
    assert member["uninstallJob"]["removed"] == ["MCP 服务 rea 的配置"]


def test_status_requires_a_user():
    app = FastAPI()
    app.include_router(rea_routes.router, prefix="/api")
    response = TestClient(app, raise_server_exceptions=False).get("/api/rea/status")
    assert response.status_code in (401, 403)
