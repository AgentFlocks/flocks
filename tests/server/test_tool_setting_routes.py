"""End-to-end tests for the tool overlay HTTP routes.

Covers:
- ``PATCH /api/tools/{name}`` writes/clears the user overlay correctly,
  including the "request matches default → drop overlay" shortcut.
- ``POST /api/tools/{name}/reset`` clears the overlay and restores the
  registration default.
- The service-gate semantics: overlay enabled=true must NOT open a tool
  whose API service is currently disabled, both in-memory and on disk.
- ToolInfoResponse exposes ``enabled_default`` / ``enabled_customized``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.tool.registry import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolRegistry,
    ToolResult,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _stub_api_tool(name: str, *, enabled: bool, provider: str = "onesec_api") -> Tool:
    async def handler(ctx: ToolContext, value: str = "ok") -> ToolResult:
        return ToolResult(success=True, output=value)

    return Tool(
        info=ToolInfo(
            name=name,
            description=f"stub api tool {name}",
            category=ToolCategory.CUSTOM,
            enabled=enabled,
            provider=provider,
        ),
        handler=handler,
    )


@pytest.fixture()
def tool_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a TestClient with the tool router and an isolated config dir.

    Seeds the registry with two stub API tools so tests can exercise the
    overlay/service-gate combinations without touching real plugin YAML.
    """
    config_dir = tmp_path / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))

    from flocks.config.config import Config
    Config._global_config = None
    Config._cached_config = None
    (config_dir / "flocks.json").write_text(json.dumps({}))

    saved_tools = dict(ToolRegistry._tools)
    saved_defaults = dict(ToolRegistry._enabled_defaults)
    saved_initialized = ToolRegistry._initialized

    enabled_tool = _stub_api_tool("onesec_dns_test", enabled=True)
    disabled_tool = _stub_api_tool("onesec_threat_test", enabled=False)
    ToolRegistry._tools = {
        enabled_tool.info.name: enabled_tool,
        disabled_tool.info.name: disabled_tool,
    }
    ToolRegistry._enabled_defaults = {
        enabled_tool.info.name: True,
        disabled_tool.info.name: False,
    }
    # Skip plugin discovery — our stub registry is enough.
    ToolRegistry._initialized = True

    from flocks.server.routes.tool import router

    app = FastAPI()
    app.include_router(router, prefix="/api/tools")
    client = TestClient(app, raise_server_exceptions=True)

    yield client, enabled_tool, disabled_tool

    ToolRegistry._tools = saved_tools
    ToolRegistry._enabled_defaults = saved_defaults
    ToolRegistry._initialized = saved_initialized


def _set_service(*, enabled: bool, sid: str = "onesec_api") -> None:
    from flocks.config.config_writer import ConfigWriter
    ConfigWriter.set_api_service(sid, {
        "apiKey": "{secret:test_key}",
        "enabled": enabled,
    })


def _read_settings() -> dict:
    from flocks.config.config_writer import ConfigWriter
    return ConfigWriter.list_tool_settings()


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestToolInfoResponse:
    def test_lists_factory_default_and_no_setting_initially(self, tool_client):
        client, _, disabled_tool = tool_client
        _set_service(enabled=True)

        res = client.get(f"/api/tools/{disabled_tool.info.name}")
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is False  # YAML default
        assert body["enabled_default"] is False
        assert body["enabled_customized"] is False

    def test_reload_path_refreshes_enabled_default(self, tool_client):
        """Regression for review P2: when the YAML on disk changes its
        ``enabled:`` default and the tool gets re-registered (as
        ``POST /api/tools/{name}/reload`` and ``PUT /api/tools/{name}``
        both do internally), the ``enabled_default`` exposed over HTTP
        must pick up the new value instead of echoing the one observed
        on first load.
        """
        client, enabled_tool, _ = tool_client
        _set_service(enabled=True)

        # Sanity: initial default is True (seeded by the fixture).
        body = client.get(f"/api/tools/{enabled_tool.info.name}").json()
        assert body["enabled_default"] is True

        # Simulate the YAML being edited to ship with enabled: false
        # and the same reload path the PUT/reload routes take.
        v2 = _stub_api_tool(enabled_tool.info.name, enabled=False)
        ToolRegistry.register(v2)

        body = client.get(f"/api/tools/{enabled_tool.info.name}").json()
        assert body["enabled_default"] is False, (
            "register() must refresh _enabled_defaults; otherwise PUT/reload "
            "leave the HTTP API exposing the old factory default and "
            "`reset` restores the wrong value"
        )


class TestUpdateTool:
    def test_overlay_persisted_when_differs_from_default(self, tool_client):
        client, _, disabled_tool = tool_client
        _set_service(enabled=True)

        res = client.patch(
            f"/api/tools/{disabled_tool.info.name}",
            json={"enabled": True},
        )
        body = res.json()
        assert body["enabled"] is True
        assert body["enabled_default"] is False
        assert body["enabled_customized"] is True
        assert _read_settings() == {disabled_tool.info.name: {"enabled": True}}
        assert disabled_tool.info.enabled is True

    def test_request_equal_to_default_drops_overlay(self, tool_client):
        client, enabled_tool, _ = tool_client
        _set_service(enabled=True)

        # First disable to plant an overlay…
        client.patch(f"/api/tools/{enabled_tool.info.name}", json={"enabled": False})
        assert enabled_tool.info.name in _read_settings()

        # …then re-enable, which equals the default and must REMOVE the overlay.
        res = client.patch(f"/api/tools/{enabled_tool.info.name}", json={"enabled": True})
        body = res.json()
        assert body["enabled"] is True
        assert body["enabled_customized"] is False
        assert _read_settings() == {}

    def test_overlay_enable_blocked_by_disabled_service(self, tool_client):
        """The intent is persisted but the in-memory state stays disabled."""
        client, _, disabled_tool = tool_client
        _set_service(enabled=False)

        res = client.patch(
            f"/api/tools/{disabled_tool.info.name}",
            json={"enabled": True},
        )
        body = res.json()
        # In-memory result honours the gate.
        assert disabled_tool.info.enabled is False
        # Effective enabled (HTTP view) is also False because service is off.
        assert body["enabled"] is False
        # But the overlay IS persisted so re-enabling the service later restores intent.
        assert _read_settings() == {disabled_tool.info.name: {"enabled": True}}
        assert body["enabled_customized"] is True

    def test_overlay_disable_works_regardless_of_service(self, tool_client):
        client, enabled_tool, _ = tool_client
        _set_service(enabled=True)

        res = client.patch(
            f"/api/tools/{enabled_tool.info.name}",
            json={"enabled": False},
        )
        body = res.json()
        assert body["enabled"] is False
        assert enabled_tool.info.enabled is False
        assert _read_settings() == {enabled_tool.info.name: {"enabled": False}}


class TestResetToolSetting:
    def test_reset_restores_default_and_removes_overlay(self, tool_client):
        client, _, disabled_tool = tool_client
        _set_service(enabled=True)

        client.patch(f"/api/tools/{disabled_tool.info.name}", json={"enabled": True})
        assert disabled_tool.info.enabled is True
        assert _read_settings()  # has entry

        res = client.post(f"/api/tools/{disabled_tool.info.name}/reset")
        body = res.json()
        assert body["enabled"] is False
        assert body["enabled_default"] is False
        assert body["enabled_customized"] is False
        assert disabled_tool.info.enabled is False
        assert _read_settings() == {}

    def test_reset_for_default_enabled_with_disabled_service_yields_false(self, tool_client):
        client, enabled_tool, _ = tool_client
        _set_service(enabled=False)

        # Plant a contrarian overlay first.
        client.patch(f"/api/tools/{enabled_tool.info.name}", json={"enabled": False})

        res = client.post(f"/api/tools/{enabled_tool.info.name}/reset")
        body = res.json()
        # Default is True, but service is off → in-memory enabled must be False.
        assert body["enabled_default"] is True
        assert body["enabled"] is False
        assert enabled_tool.info.enabled is False

    def test_reset_unknown_tool_returns_404(self, tool_client):
        client, _, _ = tool_client
        res = client.post("/api/tools/no_such_tool/reset")
        assert res.status_code == 404
