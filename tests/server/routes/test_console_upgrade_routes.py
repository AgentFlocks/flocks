from __future__ import annotations

import pytest
from fastapi import status
import httpx
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
            assert headers == {"Authorization": "Bearer token_abc"}
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


async def test_refresh_approved_request_schedules_auto_activate_install(
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

    scheduled: list[str] = []

    def _fake_schedule(request_id_arg: str, record: dict):
        scheduled.append(request_id_arg)
        record.setdefault("details", {})["auto_install_task_scheduled_at"] = "2026-05-08T08:01:00+00:00"

    monkeypatch.setattr(console_routes, "_schedule_auto_activate_upgrade", _fake_schedule)

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/refresh")
    assert resp.status_code == status.HTTP_200_OK
    payload = resp.json()
    assert payload["status"] == "approved"
    assert payload["details"]["auto_install_task_scheduled_at"] == "2026-05-08T08:01:00+00:00"
    assert scheduled == [request_id]


async def test_auto_activate_reports_already_latest_install(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import console_upgrade as console_routes

    reported: list[tuple[str, str | None]] = []

    async def _fake_check_update():
        return type(
            "VersionInfoLike",
            (),
            {
                "error": None,
                "has_update": False,
                "current_version": "2026.5.9",
                "latest_version": None,
                "zipball_url": None,
                "tarball_url": None,
                "bundle_sha256": None,
                "bundle_format": None,
            },
        )()

    async def _fake_report(record: dict, *, install_result: str, error_message: str | None = None):
        reported.append((install_result, error_message))

    async def _noop(_record: dict):
        return None

    monkeypatch.setattr(console_routes, "check_update", _fake_check_update)
    monkeypatch.setattr(console_routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(console_routes, "_report_pro_bundle_installation", _fake_report)

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
    assert payload["details"]["auto_install_version"] == "2026.5.9"
    assert reported == [("success", None)]

