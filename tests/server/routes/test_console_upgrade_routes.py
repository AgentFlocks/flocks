from __future__ import annotations

import pytest
from fastapi import status
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
            "license_type": "trial_30d",
            "company": "acme",
            "applicant_name": "alice",
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
            "license_type": "trial_30d",
            "company": "acme",
            "applicant_name": "alice",
        },
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "云账号未登录" in resp.text


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
            "installed_version": "pro-v2026-05-13-3",
            "flockspro_component_version": "1.2.3",
            "build_id": "build_1",
            "installed_at": "2026-05-15T12:00:00+00:00",
        },
    )

    resp = await client.get("/api/console/pro-package-status")

    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["installed"] is True
    assert payload["flockspro_component_version"] == "1.2.3"


async def test_sync_console_license_revocations_imports_into_checker(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "http://console.local")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()

    class _FakeResponse:
        def json(self) -> dict:
            return {"revoked_license_ids": ["lic_revoked"]}

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            assert url == "http://console.local/v1/licenses/revocations"
            assert headers == {"Authorization": "Bearer token_abc"}
            return _FakeResponse()

    imported: list[str] = []

    class _Checker:
        def import_revocation(self, revoked_license_ids):
            imported.extend(revoked_license_ids)

    runtime_module = ModuleType("flockspro.license.runtime")
    flockspro_module = ModuleType("flockspro")
    license_module = ModuleType("flockspro.license")
    runtime_module.get_license_checker = lambda: _Checker()  # type: ignore[attr-defined]
    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _FakeClient())
    monkeypatch.setitem(__import__("sys").modules, "flockspro", flockspro_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license", license_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license.runtime", runtime_module)

    resp = await client.post("/api/console/licenses/sync-revocations")

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["imported"] is True
    assert imported == ["lic_revoked"]


async def test_sync_console_license_revocations_switches_from_revoked_runtime_license(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "http://console.local")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
    await _set_bound_console_session()
    await Storage.set("console:upgrade_request_ids", ["req_old", "req_new"], "json")
    await Storage.set(
        "console:upgrade_request:req_old",
        {
            "request_id": "req_old",
            "status": "activated",
            "activate_key": "old_token",
            "license_id": "lic_old",
            "license_status": "trial",
            "details": {"license_id": "lic_old"},
            "created_at": "2026-05-15T10:00:00+00:00",
            "updated_at": "2026-05-15T10:00:00+00:00",
        },
        "json",
    )
    await Storage.set(
        "console:upgrade_request:req_new",
        {
            "request_id": "req_new",
            "status": "approved",
            "activate_key": "new_token",
            "license_id": "lic_new",
            "license_status": "trial",
            "details": {"license_id": "lic_new"},
            "created_at": "2026-05-15T11:00:00+00:00",
            "updated_at": "2026-05-15T11:00:00+00:00",
        },
        "json",
    )

    class _FakeResponse:
        def __init__(self, payload: dict, status_code: int = status.HTTP_200_OK) -> None:
            self._payload = payload
            self.status_code = status_code

        def json(self) -> dict:
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            if url == "http://console.local/v1/licenses/revocations":
                return _FakeResponse({"revoked_license_ids": ["lic_old"]})
            if url == "http://console.local/v1/licenses/lic_old":
                return _FakeResponse(
                    {
                        "license_id": "lic_old",
                        "revoked": True,
                        "license_status": "revoked",
                        "effective_status": "revoked",
                        "effective_expires_at": 1778825933,
                    }
                )
            if url == "http://console.local/v1/licenses/lic_new":
                return _FakeResponse(
                    {
                        "license_id": "lic_new",
                        "license_status": "trial",
                        "effective_status": "trial",
                        "effective_expires_at": 1781417933,
                        "effective_max_admins": 2,
                        "effective_max_members": 6,
                    }
                )
            raise AssertionError(url)

    class _Checker:
        def __init__(self) -> None:
            self.license_id = "lic_old"
            self.active = False
            self.activated_tokens: list[str] = []
            self.refreshed = False

        def import_revocation(self, revoked_license_ids):
            assert revoked_license_ids == ["lic_old"]

        def status(self):
            return {
                "license_id": self.license_id,
                "license_status": "revoked" if not self.active else "trial",
                "active": self.active,
            }

        def activate(self, token: str):
            self.activated_tokens.append(token)
            self.license_id = "lic_new"
            self.active = True
            return self.status()

        async def refresh(self):
            self.refreshed = True
            return self.status()

    checker = _Checker()
    runtime_module = ModuleType("flockspro.license.runtime")
    flockspro_module = ModuleType("flockspro")
    license_module = ModuleType("flockspro.license")
    runtime_module.get_license_checker = lambda: checker  # type: ignore[attr-defined]
    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _FakeClient())
    monkeypatch.setitem(__import__("sys").modules, "flockspro", flockspro_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license", license_module)
    monkeypatch.setitem(__import__("sys").modules, "flockspro.license.runtime", runtime_module)

    resp = await client.post("/api/console/licenses/sync-revocations")

    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["activated_license_id"] == "lic_new"
    assert payload["refreshed_license_id"] == "lic_new"
    assert checker.activated_tokens == ["new_token"]
    assert checker.refreshed is True


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
                    "license_type": "trial_30d",
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
            assert json["form_data"]["request_kind"] == "trial_extension"
            assert json["form_data"]["console_account_name"] == "alice"
            assert headers == {"Authorization": "Bearer token_abc"}
            return _FakeResponse()

    monkeypatch.setattr(console_routes.httpx, "AsyncClient", lambda timeout=10: _FakeClient())

    resp = await client.post(
        "/api/console/upgrade-requests",
        json={
            "product": "Flocks Pro",
            "license_type": "trial_30d",
            "request_kind": "trial_extension",
            "company": "acme",
            "applicant_name": "alice",
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
            "license_type": "trial_30d",
            "company": "acme",
            "applicant_name": "alice",
        },
    )

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert "console unavailable" in resp.text


async def test_cancel_approved_request_falls_back_to_local_cancel_when_console_rejects(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "http://console.local")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())

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

        async def post(self, url):
            assert url == f"http://console.local/v1/upgrade-requests/{request_id}/withdraw"
            return _FakeResponse(status.HTTP_400_BAD_REQUEST, {"message": "cannot withdraw approved"})

        async def get(self, url):
            assert url == f"http://console.local/v1/upgrade-requests/{request_id}"
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


async def test_start_approved_request_streams_upgrade_and_marks_activated(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes
    from flocks.storage.storage import Storage
    from flocks.updater.models import UpdateProgress

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")
    monkeypatch.setattr(console_routes, "require_admin", lambda _req: _mock_admin())
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
        yield UpdateProgress(stage="fetching", message="Downloading Flocks Pro bundle...", success=None)
        yield UpdateProgress(stage="restarting", message="Restarting service...", success=None)

    async def _noop(_record: dict):
        return None

    reported: list[tuple[str, str | None]] = []

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        reported.append((install_result, error_message))

    monkeypatch.setattr(console_routes, "perform_pro_bundle_install", _fake_perform_pro_bundle_install)
    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_mark_console_upgrade_activated", _noop)
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {"installed_version": "v2026.5.9"},
    )

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/start")
    assert resp.status_code == status.HTTP_200_OK
    assert "Downloading Flocks Pro bundle" in resp.text
    assert "Restarting service" in resp.text

    stored = await Storage.get(f"console:upgrade_request:{request_id}")
    assert stored["status"] == "activated"
    assert stored["details"]["auto_install_result"] == "restarting"
    assert stored["details"]["auto_install_version"] == "v2026.5.9"
    assert reported == [("success", None)]


async def test_auto_activate_reports_already_latest_install(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    reported: list[tuple[str, str | None]] = []

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        reported.append((install_result, error_message))

    async def _noop(_record: dict):
        return None

    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: True)
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {"installed_version": "v2026.5.9"},
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
    assert payload["details"]["auto_install_version"] == "v2026.5.9"
    assert reported == [("success", None)]


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

    async def _noop(_record: dict):
        return None

    monkeypatch.setattr(console_routes, "perform_pro_bundle_install", _fake_perform_pro_bundle_install)
    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)
    monkeypatch.setattr(console_routes, "_is_pro_component_installed", lambda: installed)
    monkeypatch.setattr(
        console_routes,
        "_read_pro_bundle_install_marker",
        lambda: {"installed_version": "v2026.5.9"} if installed else {},
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
    assert payload["details"]["auto_install_version"] == "v2026.5.9"

