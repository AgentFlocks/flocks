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
from typing import Dict, Optional

import pytest
from httpx import AsyncClient

from flocks.server.routes import device as device_routes
from flocks.tool.device.models import DeviceTestResult


def _fake_row(*, fields: Dict[str, str], verify_ssl: bool = False) -> dict:
    """Return a dict shaped like the aiosqlite.Row that
    ``fetch_device`` would normally yield.  Route code accesses it with
    ``row["fields"]`` / ``row["verify_ssl"]`` style indexing so a plain
    ``dict`` is enough.
    """
    return {
        "id": "dev-test",
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
    """Stub out fetch_device, _probe and record_test_result on the
    routes module so the test stays isolated from DB / network."""

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

    monkeypatch.setattr(device_routes, "fetch_device", fake_fetch_device)
    monkeypatch.setattr(device_routes, "_probe", fake_probe)
    monkeypatch.setattr(device_routes, "record_test_result", fake_record)
    # secrets resolution: return the persisted dict untouched so tests can
    # drive the field values directly.
    monkeypatch.setattr(
        device_routes,
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

        monkeypatch.setattr(device_routes, "fetch_device", fake_fetch_device)

        resp = await client.post("/api/devices/missing-id/test", json={})

        assert resp.status_code == 404
