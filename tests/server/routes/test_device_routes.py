"""Route-level tests for ``POST /api/devices/{id}/test``.

Covers the connectivity-probe behaviour added when host+port providers
(e.g. Sangfor SIP) were brought into scope:

  * empty ``base_url`` but populated ``host``/``port`` → probe targets
    ``https://{host}:{port}``;
  * ``host`` field already carries a scheme → respect it (no double
    ``https://http://...``);
  * neither ``base_url`` nor ``host`` set → user-facing error mentions
    *both* fields, not just ``base_url``.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Dict, Optional
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from flocks.server.routes import device as device_routes
from flocks.tool.device import intake as device_intake
from flocks.tool.device.models import DeviceTestResult
from flocks.tool.registry import ToolCategory, ToolInfo


def _fake_row(*, fields: Dict[str, str], verify_ssl: bool = False) -> dict:
    """Return a dict shaped like the aiosqlite.Row that
    ``fetch_device`` would normally yield.  Route code accesses it with
    ``row["fields"]`` / ``row["verify_ssl"]`` style indexing so a plain
    ``dict`` is enough.
    """
    return {
        "id": "dev-test",
        "storage_key": "onesec_api_v2_8_2",
        "fields": json.dumps(fields),
        "verify_ssl": int(bool(verify_ssl)),
    }


def _install_route_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    row: Optional[dict],
    probe_result: DeviceTestResult,
    captured: dict,
) -> None:
    """Stub out intake dependencies so the test stays isolated from DB / network."""

    async def fake_fetch_device(device_id: str):
        captured["device_id"] = device_id
        return row

    async def fake_probe(base_url: str, *, verify_ssl: bool):
        captured["probed_base_url"] = base_url
        captured["probed_verify_ssl"] = verify_ssl
        return probe_result

    async def fake_record(device_id, *, success, message, latency_ms):
        captured.setdefault("record_calls", []).append(
            {"device_id": device_id, "success": success, "message": message}
        )

    monkeypatch.setattr(device_intake, "fetch_device", fake_fetch_device)
    monkeypatch.setattr(device_intake, "_probe", fake_probe)
    monkeypatch.setattr(device_intake, "record_test_result", fake_record)
    # secrets resolution: return the persisted dict untouched so tests can
    # drive the field values directly.
    monkeypatch.setattr(
        device_intake,
        "resolve_for_runtime",
        lambda db_fields: dict(db_fields),
    )


class TestDeviceTestEndpoint:
    @pytest.mark.asyncio
    async def test_falls_back_to_host_and_port_when_base_url_missing(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Sangfor SIP-style providers (host=192.168.1.100 + port=7443)
        must produce ``https://192.168.1.100:7443`` even though the
        device has no ``base_url`` field at all.
        """
        captured: dict = {}
        _install_route_stubs(
            monkeypatch,
            row=_fake_row(fields={"host": "192.168.1.100", "port": "7443"}),
            probe_result=DeviceTestResult(success=True, message="HTTP 200, 12ms", latency_ms=12),
            captured=captured,
        )

        resp = await client.post("/api/devices/dev-test/test", json={})

        assert resp.status_code == 200, resp.text
        assert captured["probed_base_url"] == "https://192.168.1.100:7443"

    @pytest.mark.asyncio
    async def test_host_only_defaults_to_https_without_port(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Port is optional; when absent the probe should still build a
        well-formed ``https://{host}`` URL rather than dangling a trailing
        colon."""
        captured: dict = {}
        _install_route_stubs(
            monkeypatch,
            row=_fake_row(fields={"host": "console.example.com"}),
            probe_result=DeviceTestResult(success=True, message="ok"),
            captured=captured,
        )

        resp = await client.post("/api/devices/dev-test/test", json={})

        assert resp.status_code == 200, resp.text
        assert captured["probed_base_url"] == "https://console.example.com"

    @pytest.mark.asyncio
    async def test_host_already_with_scheme_is_not_double_prefixed(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Defensive: an operator that typed ``http://10.1.2.3`` into the
        ``host`` field must not produce ``https://http://10.1.2.3``."""
        captured: dict = {}
        _install_route_stubs(
            monkeypatch,
            row=_fake_row(fields={"host": "http://10.1.2.3", "port": "8080"}),
            probe_result=DeviceTestResult(success=True, message="ok"),
            captured=captured,
        )

        resp = await client.post("/api/devices/dev-test/test", json={})

        assert resp.status_code == 200, resp.text
        assert captured["probed_base_url"] == "http://10.1.2.3:8080"

    @pytest.mark.asyncio
    async def test_form_override_base_url_wins_over_persisted_host(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """If the WebUI sends an unsaved ``base_url`` it must take priority
        over the persisted ``host`` field — that's the whole point of the
        override path."""
        captured: dict = {}
        _install_route_stubs(
            monkeypatch,
            row=_fake_row(fields={"host": "192.168.1.100", "port": "7443"}),
            probe_result=DeviceTestResult(success=True, message="ok"),
            captured=captured,
        )

        resp = await client.post(
            "/api/devices/dev-test/test",
            json={"base_url": "https://staging.example.com"},
        )

        assert resp.status_code == 200, resp.text
        assert captured["probed_base_url"] == "https://staging.example.com"

    @pytest.mark.asyncio
    async def test_draft_fields_win_over_persisted_fields_for_probe(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict = {}
        _install_route_stubs(
            monkeypatch,
            row=_fake_row(fields={"base_url": "https://persisted.example.com"}),
            probe_result=DeviceTestResult(success=True, message="ok"),
            captured=captured,
        )

        resp = await client.post(
            "/api/devices/dev-test/test",
            json={"fields": {"base_url": "https://draft.example.com"}},
        )

        assert resp.status_code == 200, resp.text
        assert captured["probed_base_url"] == "https://draft.example.com"
        assert captured["record_calls"] == [
            {"device_id": "dev-test", "success": True, "message": "ok"}
        ]

    @pytest.mark.asyncio
    async def test_masked_draft_secret_keeps_persisted_secret(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict = {}
        _install_route_stubs(
            monkeypatch,
            row=_fake_row(
                fields={
                    "base_url": "https://persisted.example.com",
                    "password": "{secret:device_dev-test_password}",
                }
            ),
            probe_result=DeviceTestResult(success=True, message="ok"),
            captured=captured,
        )
        monkeypatch.setattr(
            device_intake,
            "resolve_for_runtime",
            lambda db_fields: {
                **db_fields,
                "password": "real-password",
            },
        )
        monkeypatch.setattr(
            device_intake,
            "mask_for_display",
            lambda db_fields: (
                {
                    "base_url": "https://persisted.example.com",
                    "password": "r***word",
                },
                {"base_url": True, "password": True},
            ),
        )

        resolved = device_intake._resolve_test_fields(
            {
                "base_url": "https://persisted.example.com",
                "password": "{secret:device_dev-test_password}",
            },
            device_intake.DeviceTestRequest(fields={"password": "r***word"}),
        )
        resp = await client.post(
            "/api/devices/dev-test/test",
            json={"fields": {"password": "r***word"}},
        )

        assert resp.status_code == 200, resp.text
        assert resolved["password"] == "real-password"
        assert captured["probed_base_url"] == "https://persisted.example.com"

    @pytest.mark.asyncio
    async def test_error_message_mentions_both_base_url_and_host(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """When neither ``base_url`` nor ``host`` is configured, the error
        text must tell the operator BOTH field names — otherwise users on
        host+port providers (SIP) get a misleading "base_url" message even
        though that field isn't on their form."""
        captured: dict = {}
        _install_route_stubs(
            monkeypatch,
            row=_fake_row(fields={}),
            # Probe should never run when address is missing — keep a
            # sentinel that would fail loudly if it did.
            probe_result=DeviceTestResult(
                success=True, message="UNREACHABLE: probe ran without an address"
            ),
            captured=captured,
        )

        resp = await client.post("/api/devices/dev-test/test", json={})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is False
        assert "base_url" in data["message"]
        assert "host" in data["message"]
        assert "probed_base_url" not in captured, "probe must not run when no address resolved"

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_device(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_fetch_device(device_id: str):
            return None

        monkeypatch.setattr(device_intake, "fetch_device", fake_fetch_device)

        resp = await client.post("/api/devices/missing-id/test", json={})

        assert resp.status_code == 404


class TestDeviceCredentialEndpoint:
    @pytest.mark.asyncio
    async def test_reveals_only_requested_field_and_emits_audit(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict = {}

        async def fake_fetch_device(device_id: str):
            captured["device_id"] = device_id
            return _fake_row(
                fields={
                    "api_key": "{secret:device_dev-test_api_key}",
                    "base_url": "https://console.onesec.net",
                }
            )

        monkeypatch.setattr(device_routes, "fetch_device", fake_fetch_device)
        monkeypatch.setattr(
            device_routes,
            "resolve_for_runtime",
            lambda db_fields: {
                **db_fields,
                "api_key": "long-real-onesec-api-key-Cd4Y",
            },
        )
        async def fake_emit_audit(event_type: str, payload: dict):
            captured["audit_event_type"] = event_type
            captured["audit_payload"] = payload

        monkeypatch.setattr(device_routes, "_emit_device_audit", fake_emit_audit)

        resp = await client.post(
            "/api/devices/dev-test/credentials",
            json={"field": "api_key"},
        )

        assert resp.status_code == 200, resp.text
        assert captured["device_id"] == "dev-test"
        assert captured["audit_event_type"] == "device.credentials_reveal"
        assert captured["audit_payload"]["device_id"] == "dev-test"
        assert captured["audit_payload"]["field_keys"] == ["api_key"]
        assert resp.json() == {
            "fields": {
                "api_key": "long-real-onesec-api-key-Cd4Y",
            }
        }

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_device(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_fetch_device(device_id: str):
            return None

        monkeypatch.setattr(device_routes, "fetch_device", fake_fetch_device)

        resp = await client.post("/api/devices/missing-id/credentials", json={})

        assert resp.status_code == 404


class TestDeviceToolEndpoint:
    @staticmethod
    def _tool(*, enabled: bool = True):
        return SimpleNamespace(
            info=ToolInfo(
                name="onesig_login",
                description="OneSIG login",
                category=ToolCategory.CUSTOM,
                enabled=enabled,
                source="device",
                provider="onesig_api_v2_5_3_D20260321",
            )
        )

    @pytest.mark.asyncio
    async def test_enable_deletes_per_device_override_without_writing_true(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls: dict[str, object] = {}
        tool = self._tool(enabled=True)

        async def fake_delete(device_id: str, tool_name: str):
            calls["delete"] = (device_id, tool_name)
            return True

        async def fake_set(device_id: str, tool_name: str, enabled: bool):
            calls["set"] = (device_id, tool_name, enabled)

        monkeypatch.setattr(
            device_routes,
            "fetch_device",
            AsyncMock(return_value={"storage_key": "onesig_api_v2_5_3_D20260321"}),
        )
        monkeypatch.setattr("flocks.tool.registry.ToolRegistry.init", lambda: None)
        monkeypatch.setattr("flocks.tool.registry.ToolRegistry.get", lambda _name: tool)
        monkeypatch.setattr(device_routes, "delete_device_tool_setting", fake_delete)
        monkeypatch.setattr(device_routes, "set_device_tool_enabled", fake_set)

        result = await device_routes.route_update_device_tool(
            "dev-a",
            "onesig_login",
            device_routes.DeviceToolUpdateRequest(enabled=True),
        )

        assert calls["delete"] == ("dev-a", "onesig_login")
        assert "set" not in calls
        assert result.enabled_global is True
        assert result.enabled_device is None
        assert result.enabled_effective is True

    @pytest.mark.asyncio
    async def test_enable_global_tool_when_device_tool_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls: dict[str, object] = {}
        tool = self._tool(enabled=False)

        async def fake_delete(device_id: str, tool_name: str):
            calls["delete"] = (device_id, tool_name)
            return False

        def fake_global_enable(target_tool, desired: bool):
            calls["global_enable"] = (target_tool.info.name, desired)
            target_tool.info.enabled = desired
            return desired

        monkeypatch.setattr(
            device_routes,
            "fetch_device",
            AsyncMock(return_value={"storage_key": "onesig_api_v2_5_3_D20260321"}),
        )
        monkeypatch.setattr("flocks.tool.registry.ToolRegistry.init", lambda: None)
        monkeypatch.setattr("flocks.tool.registry.ToolRegistry.get", lambda _name: tool)
        monkeypatch.setattr(device_routes, "delete_device_tool_setting", fake_delete)
        monkeypatch.setattr(
            "flocks.server.routes.tool._set_global_tool_enabled",
            fake_global_enable,
        )

        result = await device_routes.route_update_device_tool(
            "dev-a",
            "onesig_login",
            device_routes.DeviceToolUpdateRequest(enabled=True),
        )

        assert calls["global_enable"] == ("onesig_login", True)
        assert calls["delete"] == ("dev-a", "onesig_login")
        assert result.enabled_global is True
        assert result.enabled_device is None
        assert result.enabled_effective is True


class TestDeviceSyncEndpoint:
    @pytest.mark.asyncio
    async def test_list_devices_does_not_invoke_auto_instance_creation(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        async def fail_ensure_user_device_instances(*, refresh_templates: bool):
            raise AssertionError("GET /api/devices must stay read-only")

        monkeypatch.setattr(
            device_routes,
            "ensure_user_device_instances",
            fail_ensure_user_device_instances,
        )
        async def fake_list_devices(group_id=None):
            return []

        monkeypatch.setattr(device_routes, "list_devices", fake_list_devices)

        resp = await client.get("/api/devices?refresh=true")

        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_sync_invokes_auto_instance_creation_with_refresh_flag(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict = {}

        async def fake_ensure_user_device_instances(*, refresh_templates: bool):
            captured["refresh_templates"] = refresh_templates
            return 3

        monkeypatch.setattr(
            device_routes,
            "ensure_user_device_instances",
            fake_ensure_user_device_instances,
        )

        resp = await client.post("/api/devices/sync?refresh=true")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"created": 3}
        assert captured["refresh_templates"] is True

    @pytest.mark.asyncio
    async def test_sync_allows_non_refresh_sync(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict = {}

        async def fake_ensure_user_device_instances(*, refresh_templates: bool):
            captured["refresh_templates"] = refresh_templates
            return 0

        monkeypatch.setattr(
            device_routes,
            "ensure_user_device_instances",
            fake_ensure_user_device_instances,
        )

        resp = await client.post("/api/devices/sync?refresh=false")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"created": 0}
        assert captured["refresh_templates"] is False
