from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, status
import httpx
from types import ModuleType
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


async def _set_bound_console_session() -> None:
    from flocks.storage.storage import Storage

    await Storage.set(
        "console:session",
        {
            "console_login_id": "login_ok",
            "console_session_token": "token_abc",
            "fingerprint": "fp_1",
            "install_id": "inst_1",
            "passport_uid": "pass_1",
            "user_display": "alice",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )


async def test_upgrade_request_lifecycle_local_storage(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()

    create_resp = await client.post(
        "/api/console/upgrade-requests",
        json={
            "product": "Flocks Pro",
            "license_type": "poc",
            "company": "acme",
            "applicant_name": "alice",
            "sales_rep_name": "bob",
            "applicant_email": "alice@example.com",
            "applicant_phone": "13800000000",
            "notes": "need flockspro",
        },
    )
    assert create_resp.status_code == status.HTTP_200_OK
    created = create_resp.json()
    request_id = created["request_id"]
    assert created["status"] == "pending"
    assert created["reason"] == "need flockspro"
    assert created["details"]["company"] == "acme"
    assert created["details"]["applicant_name"] == "alice"
    assert created["details"]["sales_rep_name"] == "bob"
    assert created["details"]["request_kind"] == "new"
    assert created["details"]["console_account_name"] == "alice"

    list_resp = await client.get("/api/console/upgrade-requests")
    assert list_resp.status_code == status.HTTP_200_OK
    assert any(item["request_id"] == request_id for item in list_resp.json())

    get_resp = await client.get(f"/api/console/upgrade-requests/{request_id}")
    assert get_resp.status_code == status.HTTP_200_OK
    assert get_resp.json()["request_id"] == request_id

    refresh_resp = await client.post(f"/api/console/upgrade-requests/{request_id}/refresh")
    assert refresh_resp.status_code == status.HTTP_200_OK
    assert refresh_resp.json()["status"] == "pending"

    cancel_resp = await client.post(f"/api/console/upgrade-requests/{request_id}/cancel")
    assert cancel_resp.status_code == status.HTTP_200_OK
    assert cancel_resp.json()["status"] == "cancelled"


async def test_upgrade_request_missing_returns_404(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())

    get_resp = await client.get("/api/console/upgrade-requests/not_found")
    assert get_resp.status_code == status.HTTP_404_NOT_FOUND

    refresh_resp = await client.post("/api/console/upgrade-requests/not_found/refresh")
    assert refresh_resp.status_code == status.HTTP_404_NOT_FOUND

    cancel_resp = await client.post("/api/console/upgrade-requests/not_found/cancel")
    assert cancel_resp.status_code == status.HTTP_404_NOT_FOUND


async def test_create_upgrade_request_requires_console_login(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await Storage.delete("console:session")

    resp = await client.post(
        "/api/console/upgrade-requests",
        json={
            "product": "Flocks Pro",
            "license_type": "poc",
            "company": "acme",
            "applicant_name": "alice",
            "applicant_email": "alice@example.com",
            "applicant_phone": "+1 415 555 0100",
        },
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "云账号未登录" in resp.text


async def test_create_upgrade_request_rejects_unsupported_license_type(client: AsyncClient):
    resp = await client.post(
        "/api/console/upgrade-requests",
        json={
            "product": "Flocks Pro",
            "license_type": "unsupported",
            "company": "acme",
            "applicant_name": "alice",
        },
    )

    assert resp.status_code == 422


async def test_fallback_license_state_does_not_mark_license_activated(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(console_routes, "_machine_fingerprint", lambda install_id: f"fp_{install_id}", raising=False)

    record = {
        "request_id": "req_fallback",
        "license_id": "lic_fallback",
        "activate_key": "signed.token.value",
        "details": {"activation_receipt": "signed.receipt.value"},
    }

    console_routes._fallback_write_pro_license_state(record, "signed.token.value", "missing license public key")

    state = json.loads((tmp_path / "flockspro" / "license.json").read_text(encoding="utf-8"))
    assert state["key"] == "signed.token.value"
    assert state["payload"] == {}
    assert state["activation_receipt"] == "signed.receipt.value"
    assert "license_activated_at" not in record["details"]
    assert record["details"]["license_activate_fallback_saved_at"]


async def test_pro_upgrade_activation_can_disable_fallback_license_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    class _Checker:
        def activate(self, *_args, **_kwargs):
            raise RuntimeError("activation service unavailable")

    runtime_module = ModuleType("flockspro.license.runtime")
    flockspro_module = ModuleType("flockspro")
    license_module = ModuleType("flockspro.license")
    runtime_module.get_license_checker = lambda: _Checker()  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "flockspro", flockspro_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license", license_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license.runtime", runtime_module)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)

    record = {
        "request_id": "req_no_fallback",
        "license_id": "lic_no_fallback",
        "activate_key": "signed.token.value",
        "details": {},
    }

    await console_routes._maybe_activate_pro_license(record, allow_fallback=False)

    assert record["details"]["license_activate_error"] == "activation service unavailable"
    assert not (tmp_path / "flockspro" / "license.json").exists()
    assert "license_activate_fallback_saved_at" not in record["details"]


async def test_refresh_pro_license_updates_record_timestamp(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import console_upgrade as console_routes

    class _Checker:
        async def refresh(self):
            return {"active": True}

    runtime_module = ModuleType("flockspro.license.runtime")
    flockspro_module = ModuleType("flockspro")
    license_module = ModuleType("flockspro.license")
    runtime_module.get_license_checker = lambda: _Checker()  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "flockspro", flockspro_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license", license_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license.runtime", runtime_module)

    record = {
        "request_id": "req_refresh",
        "details": {},
        "updated_at": "2026-05-15T10:00:00+00:00",
    }

    await console_routes._maybe_refresh_pro_license(record)

    assert record["details"]["license_refreshed_at"]
    assert record["updated_at"] == record["details"]["license_refreshed_at"]
    assert record["updated_at"] != "2026-05-15T10:00:00+00:00"


async def test_pro_package_status_reports_installed_marker(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {
            "bundle_version": "pro-v2026-05-13-3",
            "flockspro_component_version": "1.2.3",
            "build_id": "build_1",
            "installed_at": "2026-05-15T12:00:00+00:00",
        },
    )

    resp = await client.get("/api/console/pro-package-status")

    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["installed"] is True
    assert payload["runtime_importable"] is True
    assert payload["install_marker_present"] is True
    assert payload["flockspro_component_version"] == "1.2.3"


async def test_pro_package_status_treats_install_marker_as_installed(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: False)
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {
            "bundle_version": "pro-v2026.6.23",
            "flockspro_component_version": "2026.6.23",
            "installed_at": "2026-06-29T04:00:00+00:00",
        },
    )

    resp = await client.get("/api/console/pro-package-status")

    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["installed"] is True
    assert payload["runtime_importable"] is False
    assert payload["install_marker_present"] is True
    assert payload["inactive_reason"] == "flockspro_not_installed"


async def test_downgrade_pro_package_reports_console_and_preserves_request(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage
    from flocks.updater.models import UpdateProgress

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()
    request_id = "req_downgrade_001"
    await Storage.set("console:upgrade_request_ids", [request_id], "json")
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "activated",
            "previous_request_id": None,
            "reason": None,
            "suggestion": None,
            "activate_key": "key_downgrade",
            "license_id": "lic_downgrade",
            "license_status": "poc",
            "manifest_url": "https://manifest.example.com/v1/manifest/latest",
            "details": {
                "license_id": "lic_downgrade",
                "console_account_name": "alice",
                "passport_uid": "pass_1",
                "auto_install_pro_version": "v2026.6.24",
            },
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    posted_payloads: list[dict] = []
    local_downgrade_started = False

    class _Response:
        def raise_for_status(self):
            return None

        def json(self) -> dict:
            return {"id": "instrec_downgrade", "ok": True}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            assert url == "https://console.example.com/v1/pro-bundles/installations"
            assert headers == {"Authorization": "Bearer token_abc"}
            posted_payloads.append(json)
            return _Response()

    async def _fake_downgrade(*, restart: bool, reason: str | None = None, after_uninstall=None):
        nonlocal local_downgrade_started
        assert restart is True
        assert reason == "user_requested"
        assert posted_payloads == []
        assert after_uninstall is not None
        yield UpdateProgress(stage="downgrading", message="Removing Flocks Pro component...", success=None)
        local_downgrade_started = True
        yield UpdateProgress(stage="reporting", message="Reporting OSS downgrade to Console...", success=None)
        await after_uninstall()
        assert posted_payloads, "Console must be synced after local downgrade"
        yield UpdateProgress(stage="done", message="Downgraded to OSS edition.", success=True)

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _Client())
    monkeypatch.setattr(console_routes, "perform_pro_bundle_downgrade", _fake_downgrade)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {
            "release_id": "rel_downgrade",
            "bundle_release_id": "rel_downgrade",
            "installed_version": "v2026.6.24",
            "core_version": "v2026.6.21",
            "flockspro_component_version": "v2026.6.24",
            "build_id": "job_downgrade",
        },
    )
    monkeypatch.setattr(
        console_routes,
        "_get_pro_capability_status",
        lambda: {"pro_enabled": True, "active": True, "license_id": "lic_downgrade"},
    )

    resp = await client.post("/api/console/pro-package/downgrade", json={"reason": "user_requested"})

    assert resp.status_code == status.HTTP_200_OK
    assert "Reporting OSS downgrade to Console" in resp.text
    assert local_downgrade_started is True
    assert posted_payloads[0]["install_result"] == "downgraded"
    assert posted_payloads[0]["runtime_edition"] == "oss"
    assert posted_payloads[0]["request_id"] == request_id
    assert posted_payloads[0]["license_id"] == "lic_downgrade"
    assert posted_payloads[0]["bundle_version"] == "v2026.6.24"
    assert "installed_version" not in posted_payloads[0]
    assert "oss_version" not in posted_payloads[0]
    stored = await Storage.get(f"console:upgrade_request:{request_id}")
    assert stored["status"] == "activated"
    assert stored["details"]["local_downgrade_reported_at"]
    assert stored["details"]["local_downgrade_result"] == "done"
    assert stored["details"]["local_downgrade_installation_id"] == "instrec_downgrade"


async def test_report_pro_bundle_downgrade_uses_request_target_when_marker_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    await _set_bound_console_session()
    posted_payloads: list[dict] = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self) -> dict:
            return {"id": "instrec_fallback", "ok": True}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            posted_payloads.append(json)
            return _Response()

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _Client())
    monkeypatch.setattr(console_routes, "_read_pro_bundle_install_marker", lambda: {})
    monkeypatch.setattr(console_routes, "_get_pro_capability_status", lambda: {"license_id": "lic_fallback"})

    record = {
        "request_id": "req_fallback",
        "license_id": "lic_fallback",
        "approved_bundle_release_id": "rel_fallback",
        "details": {
            "bundle_release_id": "rel_fallback",
            "bundle_version_update_to": "v2026.7.8",
            "core_version_update_to": "v2026.7.1",
            "flockspro_component_version_update_to": "v2026.7.8-pro",
            "target_build_id": "build_fallback",
        },
    }

    await console_routes._report_pro_bundle_downgrade(record, reason="retry_after_marker_missing")

    assert posted_payloads[0]["request_id"] == "req_fallback"
    assert posted_payloads[0]["release_id"] == "rel_fallback"
    assert posted_payloads[0]["bundle_release_id"] == "rel_fallback"
    assert posted_payloads[0]["license_id"] == "lic_fallback"
    assert posted_payloads[0]["bundle_version"] == "v2026.7.8"
    assert posted_payloads[0]["core_version"] == "v2026.7.1"
    assert posted_payloads[0]["flockspro_component_version"] == "v2026.7.8-pro"
    assert posted_payloads[0]["build_id"] == "build_fallback"


async def test_downgrade_pro_package_does_not_report_when_local_downgrade_fails(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.updater.models import UpdateProgress

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()
    posted_payloads: list[dict] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            posted_payloads.append(json)
            raise AssertionError("Console downgrade receipt must not be sent before local downgrade succeeds")

    async def _fake_downgrade(*, restart: bool, reason: str | None = None, after_uninstall=None):
        assert after_uninstall is not None
        yield UpdateProgress(stage="downgrading", message="Removing Flocks Pro component...", success=None)
        yield UpdateProgress(stage="error", message="local uninstall failed", success=False)

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _Client())
    monkeypatch.setattr(console_routes, "perform_pro_bundle_downgrade", _fake_downgrade)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(console_routes, "_read_pro_bundle_install_marker", lambda: {"bundle_version": "v2026.6.24"})

    resp = await client.post("/api/console/pro-package/downgrade", json={"reason": "user_requested"})

    assert resp.status_code == status.HTTP_200_OK
    assert "local uninstall failed" in resp.text
    assert posted_payloads == []


async def test_downgrade_pro_package_reports_console_failure_after_local_downgrade(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage
    from flocks.updater.models import UpdateProgress

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()
    request_id = "req_downgrade_report_failed"
    await Storage.set("console:upgrade_request_ids", [request_id], "json")
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "activated",
            "previous_request_id": None,
            "reason": None,
            "suggestion": None,
            "activate_key": "key_report_failed",
            "license_id": "lic_report_failed",
            "license_status": "poc",
            "manifest_url": "https://manifest.example.com/v1/manifest/latest",
            "details": {
                "license_id": "lic_report_failed",
                "console_account_name": "alice",
                "passport_uid": "pass_1",
                "auto_install_pro_version": "v2026.6.24",
            },
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            request = httpx.Request("POST", url)
            response = httpx.Response(status.HTTP_503_SERVICE_UNAVAILABLE, request=request, json={"message": "console down"})
            raise httpx.HTTPStatusError("console down", request=request, response=response)

    called = False

    async def _fake_downgrade(*, restart: bool, reason: str | None = None, after_uninstall=None):
        nonlocal called
        called = True
        assert after_uninstall is not None
        yield UpdateProgress(stage="downgrading", message="Removing Flocks Pro component...", success=None)
        yield UpdateProgress(stage="reporting", message="Reporting OSS downgrade to Console...", success=None)
        await after_uninstall()
        yield UpdateProgress(stage="done", message="should not run", success=True)

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _Client())
    monkeypatch.setattr(console_routes, "perform_pro_bundle_downgrade", _fake_downgrade)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(console_routes, "_read_pro_bundle_install_marker", lambda: {"installed_version": "v2026.6.24"})

    resp = await client.post("/api/console/pro-package/downgrade", json={"reason": "user_requested"})

    assert resp.status_code == status.HTTP_200_OK
    assert "console down" in resp.text
    assert called is True
    stored = await Storage.get(f"console:upgrade_request:{request_id}")
    assert stored["details"]["local_downgrade_result"] == "failed"
    assert stored["details"]["local_downgrade_error"] == "console down"
    assert stored["details"]["local_downgrade_report_error"] == "console down"


async def test_flockspro_license_status_fallback_reports_uninstalled(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import flockspro_license as license_routes

    app = FastAPI()
    app.include_router(license_routes.router, prefix="/api/flockspro/license")
    monkeypatch.setattr(license_routes, "_is_pro_component_installed", lambda: False)
    monkeypatch.setattr(license_routes, "require_user", lambda _req: _mock_admin())

    transport = httpx.ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as local_client:
        resp = await local_client.get("/api/flockspro/license/status")

    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["active"] is False
    assert payload["pro_enabled"] is False
    assert payload["license_status"] == "uninstalled"
    assert payload["inactive_reason"] == "flockspro_not_installed"


async def test_flockspro_license_status_delegates_to_pro_runtime(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import flockspro_license as license_routes

    app = FastAPI()
    app.include_router(license_routes.router, prefix="/api/flockspro/license")
    monkeypatch.setattr(license_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(
        license_routes,
        "_get_pro_capability_status",
        lambda: {"active": True, "pro_enabled": True, "license_status": "poc", "license_id": "lic_1"},
    )
    monkeypatch.setattr(license_routes, "require_user", lambda _req: _mock_admin())

    transport = httpx.ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as local_client:
        resp = await local_client.get("/api/flockspro/license/status")

    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["active"] is True
    assert payload["pro_enabled"] is True
    assert payload["activated"] is True
    assert payload["license_id"] == "lic_1"


async def test_flockspro_license_refresh_sends_heartbeat_from_core(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import flockspro_license as license_routes

    app = FastAPI()
    app.include_router(license_routes.router, prefix="/api/flockspro/license")
    monkeypatch.setattr(license_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(
        license_routes,
        "_get_pro_capability_status",
        lambda: {"active": True, "pro_enabled": True, "license_status": "poc", "license_id": "lic_1"},
    )
    monkeypatch.setattr(license_routes, "require_user", lambda _req: _mock_admin())

    heartbeat_calls: list[str] = []
    refresh_calls: list[str] = []

    async def _send_heartbeat():
        heartbeat_calls.append("sent")
        return {"ok": True}

    class _Checker:
        async def refresh(self):
            refresh_calls.append("refreshed")
            return {"active": True}

    runtime_module = ModuleType("flockspro.license.runtime")
    runtime_module.get_license_checker = lambda: _Checker()
    license_module = ModuleType("flockspro.license")
    flockspro_module = ModuleType("flockspro")
    monkeypatch.setitem(__import__("sys").modules, "flockspro", flockspro_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license", license_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license.runtime", runtime_module)
    monkeypatch.setattr(license_routes.ConsoleLoginService, "send_heartbeat", _send_heartbeat)

    transport = httpx.ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as local_client:
        resp = await local_client.post("/api/flockspro/license/refresh")

    assert resp.status_code == status.HTTP_200_OK
    assert heartbeat_calls == ["sent"]
    assert refresh_calls == ["refreshed"]
    assert resp.json()["license_id"] == "lic_1"


async def test_create_upgrade_request_does_not_link_previous_request_when_omitted(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "http://console.local")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()

    class _FakeResponse:
        status_code = status.HTTP_200_OK

        def json(self) -> dict:
            return {
                "request_id": "req_new_001",
                "status": "pending",
                "reason": None,
                "suggestion": None,
                "activate_key": None,
                "manifest_url": None,
                "form_data": {
                    "product": "Flocks Pro",
                    "license_type": "poc",
                    "company": "acme",
                    "applicant_name": "alice",
                },
            }

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            assert url == "http://console.local/v1/upgrade-requests"
            assert "previous_request_id" not in json
            assert json["console_login_id"] == "login_ok"
            assert json["fingerprint"] == "fp_1"
            assert json["install_id"] == "inst_1"
            assert json["passport_uid"] == "pass_1"
            assert json["form_data"]["request_kind"] == "license_change"
            assert json["form_data"]["console_account_name"] == "alice"
            assert json["form_data"]["sales_rep_name"] == "bob"
            assert headers == {"Authorization": "Bearer token_abc"}
            return _FakeResponse()

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _FakeClient())

    resp = await client.post(
        "/api/console/upgrade-requests",
        json={
            "product": "Flocks Pro",
            "license_type": "poc",
            "request_kind": "license_change",
            "company": "acme",
            "applicant_name": "alice",
            "sales_rep_name": "bob",
            "applicant_email": "alice@example.com",
            "applicant_phone": "+1 415 555 0100",
        },
    )

    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["request_id"] == "req_new_001"
    assert payload["previous_request_id"] is None


async def test_create_upgrade_request_maps_console_failure_to_502(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "http://console.local")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()

    class _FakeResponse:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        text = '{"message":"console unavailable"}'

        def json(self) -> dict:
            return {"message": "console unavailable"}

        def raise_for_status(self) -> None:
            request = httpx.Request("POST", "http://console.local/v1/upgrade-requests")
            response = httpx.Response(self.status_code, request=request, json=self.json())
            raise httpx.HTTPStatusError("console call failed", request=request, response=response)

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            assert url == "http://console.local/v1/upgrade-requests"
            return _FakeResponse()

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _FakeClient())

    resp = await client.post(
        "/api/console/upgrade-requests",
        json={
            "product": "Flocks Pro",
            "license_type": "poc",
            "company": "acme",
            "applicant_name": "alice",
            "applicant_email": "alice@example.com",
            "applicant_phone": "+1 415 555 0100",
        },
    )

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert "console unavailable" in resp.text


async def test_create_upgrade_request_sanitizes_html_console_failure(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "http://console.local")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()

    class _FakeResponse:
        status_code = status.HTTP_502_BAD_GATEWAY

        def raise_for_status(self) -> None:
            request = httpx.Request("POST", "http://console.local/v1/upgrade-requests")
            response = httpx.Response(
                self.status_code,
                request=request,
                text="<html><head><title>502 Bad Gateway</title></head><body>bad gateway</body></html>",
                headers={"content-type": "text/html"},
            )
            raise httpx.HTTPStatusError("console call failed", request=request, response=response)

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            assert url == "http://console.local/v1/upgrade-requests"
            return _FakeResponse()

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _FakeClient())

    resp = await client.post(
        "/api/console/upgrade-requests",
        json={
            "product": "Flocks Pro",
            "license_type": "poc",
            "company": "acme",
            "applicant_name": "alice",
            "applicant_email": "alice@example.com",
            "applicant_phone": "+1 415 555 0100",
        },
    )

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert "console 升级服务暂不可用" in resp.text
    assert "<html>" not in resp.text


async def test_cancel_approved_request_falls_back_to_local_cancel_when_console_rejects(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "http://console.local")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()

    request_id = "req_approved_001"
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "approved",
            "previous_request_id": None,
            "reason": None,
            "suggestion": "ready to upgrade",
            "activate_key": None,
            "manifest_url": None,
            "details": {"company": "acme"},
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = httpx.Request("GET", "http://console.local/v1/upgrade-requests")
                response = httpx.Response(self.status_code, request=request, json=self._payload)
                raise httpx.HTTPStatusError("console call failed", request=request, response=response)

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None):
            assert url == f"http://console.local/v1/upgrade-requests/{request_id}/withdraw"
            assert headers == {"Authorization": "Bearer token_abc"}
            return _FakeResponse(status.HTTP_400_BAD_REQUEST, {"message": "cannot withdraw approved"})

        async def get(self, url, headers=None):
            assert url == f"http://console.local/v1/upgrade-requests/{request_id}"
            assert headers == {"Authorization": "Bearer token_abc"}
            return _FakeResponse(
                status.HTTP_200_OK,
                {
                    "request_id": request_id,
                    "status": "approved",
                    "suggestion": "ready to upgrade",
                    "form_data": {"company": "acme"},
                },
            )

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _FakeClient())

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/cancel")
    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["status"] == "cancelled"


async def test_refresh_approved_request_does_not_auto_activate_install(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    request_id = "req_auto_001"
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "approved",
            "previous_request_id": None,
            "reason": None,
            "suggestion": None,
            "activate_key": "key_auto",
            "manifest_url": "https://manifest.example.com/v1/manifest/latest",
            "details": {"company": "acme"},
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/refresh")
    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["status"] == "approved"
    assert "auto_install_task_scheduled_at" not in payload["details"]


async def test_refresh_request_remote_form_data_overwrites_stale_local_details(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "http://console.local")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()
    request_id = "req_refresh_remote_details"
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "approved",
            "previous_request_id": None,
            "reason": None,
            "suggestion": None,
            "activate_key": "old_key",
            "manifest_url": "https://manifest.example.com/v1/manifest/latest",
            "license_id": "lic_old",
            "license_status": "poc",
            "max_admins": 1,
            "max_members": 2,
            "expires_at": 100,
            "details": {
                "company": "acme",
                "license_id": "lic_old",
                "license_effective_expires_at": 100,
                "local_only": "keep",
            },
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "request_id": request_id,
                "status": "activated",
                "activate_key": "new_key",
                "license_id": "lic_new",
                "license_status": "poc",
                "max_admins": 3,
                "max_members": 20,
                "expires_at": 1782532851,
                "form_data": {
                    "company": "acme",
                    "license_id": "lic_new",
                    "license_effective_expires_at": 1782532851,
                },
            }

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            assert url == f"http://console.local/v1/upgrade-requests/{request_id}"
            assert headers == {"Authorization": "Bearer token_abc"}
            return _FakeResponse()

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _FakeClient())

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/refresh")

    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["status"] == "activated"
    assert payload["license_id"] == "lic_new"
    assert payload["max_admins"] == 3
    assert payload["max_members"] == 20
    assert payload["details"]["license_id"] == "lic_new"
    assert payload["details"]["license_effective_expires_at"] == 1782532851
    assert payload["details"]["license_refreshed_at"]
    assert payload["details"]["license_refreshed_at"] == payload["updated_at"]
    assert payload["details"]["license_refreshed_at"] != "2026-05-08T08:00:00+00:00"
    assert payload["details"]["local_only"] == "keep"


async def test_start_approved_request_streams_restart_without_marking_activated(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage
    from flocks.updater.models import UpdateProgress

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()
    request_id = "req_start_001"
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "approved",
            "previous_request_id": None,
            "reason": None,
            "suggestion": None,
            "activate_key": "key_start",
            "manifest_url": "https://manifest.example.com/v1/manifest/latest",
            "details": {"company": "acme"},
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    async def _fake_perform_pro_bundle_install(*args, **kwargs):
        assert args == ()
        assert kwargs["restart"] is True
        assert kwargs["console_session_token"] == "token_abc"
        yield UpdateProgress(stage="fetching", message="Downloading Flocks Pro bundle...", success=None)
        yield UpdateProgress(stage="restarting", message="Restarting service...", success=None)

    async def _noop(_record: dict, **_kwargs):
        return None

    reported: list[tuple[str, str | None]] = []

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        reported.append((install_result, error_message))

    monkeypatch.setattr(console_routes, "perform_pro_bundle_install", _fake_perform_pro_bundle_install)
    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_mark_console_upgrade_activated", _noop)
    monkeypatch.setattr(console_routes, "_get_pro_capability_status", lambda: {"pro_enabled": True, "active": True})
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {
            "bundle_version": "v2026.6.5",
            "flockspro_component_version": "v2026.6.5",
        },
    )

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/start")
    assert resp.status_code == status.HTTP_200_OK
    assert "Downloading Flocks Pro bundle" in resp.text
    assert "Restarting service" in resp.text

    stored = await Storage.get(f"console:upgrade_request:{request_id}")
    assert stored["status"] == "approved"
    assert stored["details"]["auto_install_result"] == "restarting"
    assert "auto_install_version" not in stored["details"]
    assert reported == []


async def test_restarting_request_reports_receipt_after_service_restart(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()
    request_id = "req_restart_complete"
    await Storage.set("console:upgrade_request_ids", [request_id], "json")
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "approved",
            "previous_request_id": None,
            "reason": None,
            "suggestion": None,
            "activate_key": "key_start",
            "manifest_url": "https://manifest.example.com/v1/manifest/latest",
            "details": {
                "auto_install_result": "restarting",
                "approved_bundle_release_id": "rel_restart",
                "latest_pro_bundle": {
                    "release_id": "rel_restart",
                    "bundle_version": "v2026.6.24",
                    "core_version": "v2026.6.21",
                    "flockspro_component_version": "v2026.6.24",
                    "build_id": "job_restart",
                },
            },
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    async def _noop(_record: dict, **_kwargs):
        return None

    reported: list[tuple[str, str | None]] = []

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        reported.append((install_result, error_message))
        record.setdefault("details", {})["install_receipt_reported_at"] = "2026-06-24T08:11:09+00:00"

    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_mark_console_upgrade_activated", _noop)
    monkeypatch.setattr(console_routes, "_get_pro_capability_status", lambda: {"pro_enabled": True, "active": True})
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {
            "release_id": "rel_restart",
            "bundle_release_id": "rel_restart",
            "bundle_version": "v2026.6.24",
            "core_version": "v2026.6.21",
            "flockspro_component_version": "v2026.6.24",
            "build_id": "job_restart",
        },
    )

    resp = await client.get(f"/api/console/upgrade-requests/{request_id}")

    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["status"] == "activated"
    assert payload["details"]["auto_install_result"] == "done"
    assert payload["details"]["auto_install_bundle_version"] == "v2026.6.24"
    assert reported == [("success", None)]


async def test_start_approved_request_reports_error_after_restart_stage(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage
    from flocks.updater.models import UpdateProgress

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()
    request_id = "req_start_restart_then_error"
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "approved",
            "previous_request_id": None,
            "reason": None,
            "suggestion": None,
            "activate_key": "key_start",
            "manifest_url": "https://manifest.example.com/v1/manifest/latest",
            "details": {"company": "acme"},
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    async def _fake_perform_pro_bundle_install(*args, **kwargs):
        yield UpdateProgress(stage="fetching", message="Downloading Flocks Pro bundle...", success=None)
        yield UpdateProgress(stage="restarting", message="Restarting service...", success=None)
        yield UpdateProgress(stage="error", message="Failed to build restart command: missing python", success=False)

    async def _noop(_record: dict, **_kwargs):
        return None

    reported: list[tuple[str, str | None]] = []

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        reported.append((install_result, error_message))

    monkeypatch.setattr(console_routes, "perform_pro_bundle_install", _fake_perform_pro_bundle_install)
    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_mark_console_upgrade_activated", _noop)
    monkeypatch.setattr(console_routes, "_get_pro_capability_status", lambda: {"pro_enabled": True, "active": True})

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/start")

    assert resp.status_code == status.HTTP_200_OK
    assert "Restarting service" in resp.text
    assert "Failed to build restart command" in resp.text
    stored = await Storage.get(f"console:upgrade_request:{request_id}")
    assert stored["status"] == "approved"
    assert stored["details"]["auto_install_result"] == "failed"
    assert stored["details"]["auto_install_error"] == "Failed to build restart command: missing python"
    assert reported == [("failed", "Failed to build restart command: missing python")]


async def test_start_activated_request_reinstalls_when_pro_package_missing(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage
    from flocks.updater.models import UpdateProgress

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    request_id = "req_start_activated_missing_pro"
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "activated",
            "previous_request_id": None,
            "reason": None,
            "suggestion": None,
            "activate_key": "key_start",
            "license_id": "lic_start",
            "manifest_url": "https://manifest.example.com/v1/manifest/latest",
            "details": {"company": "acme", "license_id": "lic_start"},
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    installed = False

    async def _fake_perform_pro_bundle_install(*args, **kwargs):
        nonlocal installed
        assert args == ()
        assert kwargs["restart"] is True
        yield UpdateProgress(stage="fetching", message="Downloading Flocks Pro bundle...", success=None)
        installed = True
        yield UpdateProgress(stage="done", message="Flocks Pro component installed.", success=True)

    async def _noop(_record: dict, **_kwargs):
        return None

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        return None

    monkeypatch.setattr(console_routes, "perform_pro_bundle_install", _fake_perform_pro_bundle_install)
    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_mark_console_upgrade_activated", _noop)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: installed)
    monkeypatch.setattr(console_routes, "_get_pro_capability_status", lambda: {"pro_enabled": True, "active": True})
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {"bundle_version": "v2026.5.9"} if installed else {},
    )

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/start")

    assert resp.status_code == status.HTTP_200_OK
    assert "Downloading Flocks Pro bundle" in resp.text
    stored = await Storage.get(f"console:upgrade_request:{request_id}")
    assert stored["status"] == "activated"
    assert stored["details"]["auto_install_result"] == "done"
    assert stored["details"]["auto_install_bundle_version"] == "v2026.5.9"


async def test_start_revoked_request_does_not_reinstall(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: False)
    request_id = "req_start_revoked"
    await Storage.set(
        f"console:upgrade_request:{request_id}",
        {
            "request_id": request_id,
            "status": "activated",
            "license_id": "lic_revoked",
            "license_status": "revoked",
            "activate_key": "key_revoked",
            "details": {"license_id": "lic_revoked", "license_status": "revoked"},
            "created_at": "2026-05-08T08:00:00+00:00",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/start")

    assert resp.status_code == status.HTTP_400_BAD_REQUEST


async def test_auto_activate_reports_already_latest_install(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    reported: list[tuple[str, str | None]] = []

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        reported.append((install_result, error_message))

    async def _noop(_record: dict, **_kwargs):
        return None

    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(console_routes, "_get_pro_capability_status", lambda: {"pro_enabled": True, "active": True})
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {"bundle_version": "v2026.5.9"},
    )

    record = {
        "request_id": "req_auto_002",
        "status": "approved",
        "activate_key": "key_auto",
        "details": {},
        "created_at": "2026-05-08T08:00:00+00:00",
        "updated_at": "2026-05-08T08:00:00+00:00",
    }

    payload = await console_routes._maybe_auto_activate_upgrade(record)
    assert payload["status"] == "activated"
    assert payload["details"]["auto_install_result"] == "already_latest"
    assert payload["details"]["auto_install_bundle_version"] == "v2026.5.9"
    assert reported == [("success", None)]


async def test_auto_activate_reinstalls_when_existing_pro_marker_is_not_target_bundle(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.updater.models import UpdateProgress

    marker_state = {
        "payload": {
            "release_id": "rel_20260601",
            "bundle_release_id": "rel_20260601",
            "bundle_version": "v2026.6.1",
            "flockspro_component_version": "v2026.6.1",
            "build_id": "job_20260601",
        }
    }
    reported: list[tuple[str, str | None]] = []

    async def _fake_perform_pro_bundle_install(*args, **kwargs):
        assert args == ()
        assert kwargs["restart"] is False
        marker_state["payload"] = {
            "release_id": "rel_20260605",
            "bundle_release_id": "rel_20260605",
            "bundle_version": "v2026.6.5",
            "flockspro_component_version": "v2026.6.5",
            "build_id": "job_20260605",
        }
        yield UpdateProgress(stage="done", message="Flocks Pro component installed.", success=True)

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        reported.append((install_result, error_message))

    async def _noop(_record: dict, **_kwargs):
        return None

    monkeypatch.setattr(console_routes, "perform_pro_bundle_install", _fake_perform_pro_bundle_install)
    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(console_routes, "_get_pro_capability_status", lambda: {"pro_enabled": True, "active": True})
    monkeypatch.setattr(console_routes, "_read_pro_bundle_install_marker", lambda: marker_state["payload"])

    record = {
        "request_id": "req_auto_reinstall_target_bundle",
        "status": "approved",
        "activate_key": "key_auto",
        "details": {
            "auto_install_result": "already_latest",
            "approved_bundle_release_id": "rel_20260605",
            "latest_pro_bundle": {
                "release_id": "rel_20260605",
                "bundle_version": "v2026.6.5",
                "flockspro_component_version": "v2026.6.5",
                "build_id": "job_20260605",
            },
        },
        "created_at": "2026-06-05T08:00:00+00:00",
        "updated_at": "2026-06-05T08:00:00+00:00",
    }

    payload = await console_routes._maybe_auto_activate_upgrade(record)

    assert payload["status"] == "activated"
    assert payload["details"]["auto_install_result"] == "done"
    assert payload["details"]["auto_install_release_id"] == "rel_20260605"
    assert payload["details"]["auto_install_bundle_version"] == "v2026.6.5"
    assert reported == [("success", None)]


async def test_auto_activate_does_not_mark_activated_when_license_inactive(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    reported: list[tuple[str, str | None]] = []

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        reported.append((install_result, error_message))

    async def _noop(_record: dict, **_kwargs):
        return None

    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(
        console_routes,
        "_get_pro_capability_status",
        lambda: {"pro_enabled": False, "active": False, "license_status": "expired", "inactive_reason": "expired"},
    )
    monkeypatch.setattr(console_routes, "_read_pro_bundle_install_marker", lambda: {"bundle_version": "v2026.5.9"})

    record = {
        "request_id": "req_auto_inactive",
        "status": "approved",
        "activate_key": "key_auto",
        "details": {},
        "created_at": "2026-05-08T08:00:00+00:00",
        "updated_at": "2026-05-08T08:00:00+00:00",
    }

    payload = await console_routes._maybe_auto_activate_upgrade(record)
    assert payload["status"] == "approved"
    assert payload["details"]["auto_install_result"] == "failed"
    assert "license activation is inactive" in payload["details"]["auto_install_error"]
    assert payload["details"]["runtime_license_inactive_reason"] == "expired"
    assert reported
    assert reported[-1][0] == "failed"
    assert "license activation is inactive" in (reported[-1][1] or "")


async def test_auto_activate_installs_pro_bundle_when_core_version_is_latest(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.updater.models import UpdateProgress

    installed = False

    async def _fake_perform_pro_bundle_install(*args, **kwargs):
        nonlocal installed
        assert args == ()
        assert kwargs["restart"] is False
        yield UpdateProgress(stage="syncing", message="Installing Flocks Pro component...", success=None)
        installed = True
        yield UpdateProgress(stage="done", message="Flocks Pro component installed from v2026.5.9", success=True)

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        return None

    async def _noop(_record: dict, **_kwargs):
        return None

    monkeypatch.setattr(console_routes, "perform_pro_bundle_install", _fake_perform_pro_bundle_install)
    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: installed)
    monkeypatch.setattr(console_routes, "_get_pro_capability_status", lambda: {"pro_enabled": True, "active": True})
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {"bundle_version": "v2026.5.9"} if installed else {},
    )

    record = {
        "request_id": "req_auto_003",
        "status": "approved",
        "activate_key": "key_auto",
        "details": {},
        "created_at": "2026-05-08T08:00:00+00:00",
        "updated_at": "2026-05-08T08:00:00+00:00",
    }

    payload = await console_routes._maybe_auto_activate_upgrade(record)
    assert payload["status"] == "activated"
    assert payload["details"]["auto_install_result"] == "done"
    assert payload["details"]["auto_install_bundle_version"] == "v2026.5.9"


async def test_report_pro_bundle_installation_uses_license_id(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    posted_payloads: list[dict] = []

    class _Response:
        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            posted_payloads.append(json)
            assert url == "https://console.example.com/v1/pro-bundles/installations"
            assert headers == {"Authorization": "Bearer token_abc"}
            return _Response()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    await _set_bound_console_session()
    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _Client())
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {"bundle_version": "v2026.5.9"},
    )

    record = {
        "request_id": "req_receipt",
        "status": "approved",
        "activate_key": "activation_token",
        "license_id": "lic_receipt",
        "details": {
            "license_id": "lic_receipt",
            "approved_bundle_release_id": "rel_receipt",
            "latest_pro_bundle": {
                "release_id": "rel_receipt",
                "bundle_version": "v2026.6.5",
                "core_version": "v2026.6.1",
                "flockspro_component_version": "v2026.6.5",
                "build_id": "job_receipt",
            },
        },
    }

    await console_routes._report_pro_bundle_installation(record, install_result="success")

    assert posted_payloads[0]["license_id"] == "lic_receipt"
    assert posted_payloads[0]["request_id"] == "req_receipt"
    assert posted_payloads[0]["release_id"] == "rel_receipt"
    assert posted_payloads[0]["bundle_release_id"] == "rel_receipt"
    assert posted_payloads[0]["core_version"] == "v2026.6.1"
    assert "oss_version" not in posted_payloads[0]
    assert posted_payloads[0]["build_id"] == "job_receipt"


async def test_report_failed_installation_uses_target_bundle_when_marker_is_stale(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import console_upgrade as console_routes

    posted_payloads: list[dict] = []

    class _Response:
        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            posted_payloads.append(json)
            return _Response()

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    await _set_bound_console_session()
    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _Client())
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {
            "release_id": "rel_old",
            "bundle_release_id": "rel_old",
            "bundle_version": "v2026.6.1",
            "flockspro_component_version": "v2026.6.1",
            "build_id": "job_old",
        },
    )

    record = {
        "request_id": "req_failed_receipt",
        "status": "approved",
        "activate_key": "activation_token",
        "license_id": "lic_receipt",
        "details": {
            "license_id": "lic_receipt",
            "approved_bundle_release_id": "rel_new",
            "latest_pro_bundle": {
                "release_id": "rel_new",
                "bundle_version": "v2026.6.5",
                "core_version": "v2026.6.5",
                "flockspro_component_version": "v2026.6.5",
                "build_id": "job_new",
            },
        },
    }

    await console_routes._report_pro_bundle_installation(
        record,
        install_result="failed",
        error_message="install failed",
    )

    assert posted_payloads[0]["release_id"] == "rel_new"
    assert posted_payloads[0]["bundle_release_id"] == "rel_new"
    assert posted_payloads[0]["bundle_version"] == "v2026.6.5"
    assert posted_payloads[0]["build_id"] == "job_new"
    assert posted_payloads[0]["install_result"] == "failed"
