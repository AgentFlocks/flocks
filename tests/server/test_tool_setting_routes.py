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
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.server.auth import require_admin
from flocks.tool.code.lsp_tool import lsp_tool
from flocks.tool.registry import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolRegistry,
    ToolResult,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

_TEST_SERVICE_ID = "test_api_service"


def _stub_api_tool(name: str, *, enabled: bool, provider: str = _TEST_SERVICE_ID) -> Tool:
    async def handler(ctx: ToolContext, value: str = "ok") -> ToolResult:
        return ToolResult(success=True, output=value)

    return Tool(
        info=ToolInfo(
            name=name,
            description=f"stub api tool {name}",
            category=ToolCategory.CUSTOM,
            enabled=enabled,
            provider=provider,
            source="api",
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
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))

    from flocks.config.config import Config
    Config._global_config = None
    Config._cached_config = None
    (config_dir / "flocks.json").write_text(json.dumps({}))

    saved_tools = dict(ToolRegistry._tools)
    saved_defaults = dict(ToolRegistry._enabled_defaults)
    saved_failure_state = dict(ToolRegistry._failure_state)
    saved_revision = ToolRegistry._revision
    saved_config_state_token = ToolRegistry._config_state_token
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
    ToolRegistry._failure_state = {}
    # Skip plugin discovery — our stub registry is enough.
    ToolRegistry._initialized = True

    from flocks.server.routes.tool import router

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: AuthUser(
        id="admin-test",
        username="admin-test",
        role="admin",
        status="active",
        must_reset_password=False,
    )
    app.include_router(router, prefix="/api/tools")
    client = TestClient(app, raise_server_exceptions=True)

    yield client, enabled_tool, disabled_tool

    ToolRegistry._tools = saved_tools
    ToolRegistry._enabled_defaults = saved_defaults
    ToolRegistry._failure_state = saved_failure_state
    ToolRegistry._revision = saved_revision
    ToolRegistry._config_state_token = saved_config_state_token
    ToolRegistry._initialized = saved_initialized


def _set_service(*, enabled: bool, sid: str = _TEST_SERVICE_ID) -> None:
    from flocks.config.config_writer import ConfigWriter
    ConfigWriter.set_api_service(sid, {
        "apiKey": "{secret:test_key}",
        "enabled": enabled,
    })


def _read_settings() -> dict:
    from flocks.config.config_writer import ConfigWriter
    return ConfigWriter.list_tool_settings()


def _viewer_client() -> TestClient:
    from flocks.server.routes.tool import router

    app = FastAPI()

    @app.middleware("http")
    async def _viewer_auth(request, call_next):
        request.state.auth_user = AuthUser(
            id="viewer-test",
            username="viewer-test",
            role="viewer",
            status="active",
            must_reset_password=False,
        )
        return await call_next(request)

    app.include_router(router, prefix="/api/tools")
    return TestClient(app, raise_server_exceptions=True)


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_tool_mutation_routes_require_admin():
    client = _viewer_client()

    responses = [
        client.patch("/api/tools/anything", json={"enabled": True}),
        client.post("/api/tools/anything/reset"),
        client.post("/api/tools/refresh"),
        client.post("/api/tools/anything/reload"),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]


class TestNativeToolGroup:
    def test_editor_and_reload_cannot_shadow_selected_core_tool(self, tool_client, tmp_path, monkeypatch):
        import yaml
        from flocks.tool import tool_loader

        client, original, _ = tool_client
        core = Tool(ToolInfo(name=original.info.name, description="Core", native=False, group="Canonical"), lsp_tool)
        ToolRegistry.register(core)
        shadow = tmp_path / "shadow.yaml"
        shadow.write_text(yaml.safe_dump({
            "name": core.info.name, "description": "Shadow", "group": "User",
            "handler": {"type": "http", "url": "https://example.invalid"},
        }))
        before = shadow.read_bytes()
        monkeypatch.setattr(tool_loader, "_find_yaml_file", lambda name: shadow)
        response = client.put(f"/api/tools/{core.info.name}", json={"description": "changed"})
        assert response.status_code == 400, response.text
        response = client.post(f"/api/tools/{core.info.name}/reload")
        assert response.status_code == 400, response.text
        assert ToolRegistry._tools[core.info.name] is core
        assert shadow.read_bytes() == before

    def test_removing_group_override_restores_registration_default(self, tool_client):
        from flocks.config.config_writer import ConfigWriter
        from flocks.server.routes.tool import _invalidate_tool_summary_cache

        client, tool, _ = tool_client
        tool.info.group = "Package"
        response = client.patch(f"/api/tools/{tool.info.name}", json={"group": "User"})
        assert response.json()["group"] == "User"
        assert tool.info.group == "Package"
        ConfigWriter.delete_tool_setting(tool.info.name, field="group")
        ToolRegistry._apply_tool_settings()
        _invalidate_tool_summary_cache()
        assert client.get(f"/api/tools/{tool.info.name}").json()["group"] == "Package"
        rows = client.get("/api/tools/page").json()["items"]
        assert next(row for row in rows if row["name"] == tool.info.name)["group"] == "Package"

    @pytest.mark.parametrize("group", [None, "", "Override"])
    def test_shipped_group_rejection_precedes_enabled_and_yaml_changes(self, tool_client, tmp_path, monkeypatch, group):
        import yaml
        from flocks.config.config_writer import ConfigWriter
        from flocks.tool import registry, tool_loader

        client, original, _ = tool_client
        installation = tmp_path / "installation"
        monkeypatch.setattr(registry, "__file__", str(installation / "flocks/tool/registry.py"))
        path = installation / ".flocks/plugins/tools/api/source.yaml"
        path.parent.mkdir(parents=True)
        raw = {"name": original.info.name, "description": "before", "group": "Canonical", "enabled": True,
               "handler": {"type": "http", "url": "https://example.invalid"}}
        path.write_text(yaml.safe_dump(raw))
        tool = tool_loader.yaml_to_tool(raw, path)
        ToolRegistry.register(tool)
        config = ConfigWriter._read_raw()
        config["tool_settings"] = {tool.info.name: {"group": "Stale"}}
        ConfigWriter._write_raw(config)
        before_config = ConfigWriter._get_config_path().read_bytes()
        before_yaml = path.read_bytes()
        response = client.patch(f"/api/tools/{tool.info.name}", json={"group": group, "enabled": False})
        assert response.status_code == 400, response.text
        assert ConfigWriter._get_config_path().read_bytes() == before_config
        assert tool.info.enabled is True
        response = client.put(f"/api/tools/{tool.info.name}", json={"group": group, "description": "changed"})
        assert response.status_code == 400, response.text
        assert path.read_bytes() == before_yaml
        assert ConfigWriter._get_config_path().read_bytes() == before_config
        response = client.get(f"/api/tools/{tool.info.name}")
        assert response.json()["group"] == "Canonical"
        assert response.json()["group_readonly"] is True
        rows = client.get("/api/tools/page").json()["items"]
        row = next(row for row in rows if row["name"] == tool.info.name)
        assert row["group"] == "Canonical" and row["group_readonly"] is True
        # Same fixed value is accepted without changing the stale saved overlay.
        response = client.patch(f"/api/tools/{tool.info.name}", json={"group": "Canonical"})
        assert response.status_code == 200, response.text
        assert ConfigWriter._get_config_path().read_bytes() == before_config

    def test_group_only_clear_and_enabled_reset_preserve_metadata(self, tool_client, monkeypatch):
        from flocks.config.config_writer import ConfigWriter

        client, tool, _ = tool_client
        _set_service(enabled=True)
        monkeypatch.setattr(ToolRegistry, "refresh_plugin_tools", lambda *a, **k: pytest.fail("metadata must not rescan"))
        name = tool.info.name
        tool.info.group = "Pack default"
        ConfigWriter.set_tool_setting(name, {"future_metadata": {"kept": True}})
        response = client.patch(f"/api/tools/{name}", json={"group": "  Operations  "})
        assert response.status_code == 200, response.text
        assert response.json()["group"] == "Operations"
        assert response.json()["enabled"] is True
        assert response.json()["enabled_customized"] is False
        assert tool.info.group == "Pack default"

        client.patch(f"/api/tools/{name}", json={"enabled": False})
        assert _read_settings()[name]["group"] == "Operations"
        reset = client.post(f"/api/tools/{name}/reset")
        assert reset.json()["enabled_customized"] is False
        assert _read_settings()[name] == {"group": "Operations", "future_metadata": {"kept": True}}
        client.patch(f"/api/tools/{name}", json={"enabled": False})
        client.patch(f"/api/tools/{name}", json={"enabled": True})
        assert _read_settings()[name]["group"] == "Operations"
        assert "enabled" not in _read_settings()[name]

        for clear in (None, "", "   "):
            response = client.patch(f"/api/tools/{name}", json={"group": clear})
            assert response.status_code == 200, response.text
            assert response.json()["group"] == ""
            assert _read_settings()[name]["group"] == ""
            assert _read_settings()[name]["future_metadata"] == {"kept": True}

    def test_yaml_editor_preserves_effective_group_and_only_changes_metadata(self, tool_client, tmp_path, monkeypatch):
        import yaml
        from flocks.tool import tool_loader

        client, tool, _ = tool_client
        _set_service(enabled=True)
        path = tmp_path / "native.yaml"
        data = {
            "name": tool.info.name, "description": "before", "group": "Package",
            "handler": {"type": "http", "url": "https://example.invalid", "method": "GET"},
            "future_metadata": {"kept": True},
        }
        path.write_text(yaml.safe_dump(data))
        monkeypatch.setattr(tool_loader, "_find_yaml_file", lambda name: path)
        client.patch(f"/api/tools/{tool.info.name}", json={"group": "User", "enabled": False})
        response = client.put(f"/api/tools/{tool.info.name}", json={"description": "after"})
        assert response.status_code == 200, response.text
        assert response.json()["group"] == "User"
        assert yaml.safe_load(path.read_text())["group"] == "Package"
        live = ToolRegistry.get(tool.info.name)
        monkeypatch.setattr(tool_loader, "yaml_to_tool", lambda *args: pytest.fail("metadata-only must not reload handler"))
        cleared = client.put(f"/api/tools/{tool.info.name}", json={"group": None})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["group"] == ""
        assert cleared.json()["enabled"] is False
        assert ToolRegistry.get(tool.info.name) is live
        saved = yaml.safe_load(path.read_text())
        assert saved["group"] == ""
        assert saved["future_metadata"] == {"kept": True}
        assert saved["handler"] == data["handler"]
        assert _read_settings()[tool.info.name]["group"] == ""

    def test_auto_disable_keeps_group(self, tool_client):
        client, tool, _ = tool_client
        _set_service(enabled=True)
        client.patch(f"/api/tools/{tool.info.name}", json={"group": "Operations"})
        for _ in range(ToolRegistry._failure_disable_threshold):
            ToolRegistry._record_failure(tool, {}, "upstream failure")
        assert _read_settings()[tool.info.name] == {"group": "Operations", "enabled": False}

    def test_filter_before_paging_and_full_query_group_facets(self, tool_client):
        from flocks.config.config_writer import ConfigWriter

        client, first, second = tool_client
        _set_service(enabled=True)
        # Prime the existing index, then update metadata without reloading tools.
        client.get("/api/tools/page")
        client.patch(f"/api/tools/{first.info.name}", json={"group": "A, B"})
        for idx in range(3):
            tool = _stub_api_tool(f"matching_{idx}", enabled=True)
            tool.info.group = "A, B" if idx < 2 else "Elsewhere"
            ToolRegistry.register(tool)
        response = client.get("/api/tools/page", params={"group": " A, B ", "limit": 1, "offset": 1, "sort_by": "name"})
        body = response.json()
        assert response.status_code == 200, body
        assert body["total"] == 3
        assert len(body["items"]) == 1
        assert body["items"][0]["group"] == "A, B"
        assert body["facets"]["group"] == {"A, B": 3, "Elsewhere": 1, "": 1}
        assert body["facets"]["source_groups"] == {"api": 1}
        assert body["facets"]["category"] == {"custom": 3}
        assert body["facets"]["source"] == {"api": 3}
        assert body["facets"]["source_name"] == {_TEST_SERVICE_ID: 3}
        assert body["facets"]["enabled"] == {"true": 3}
        ungrouped = client.get("/api/tools/page", params={"group": ""}).json()
        assert [row["name"] for row in ungrouped["items"]] == [second.info.name]
        searched = client.get("/api/tools/page", params={"q": "matching", "group": "A, B", "limit": 1}).json()
        assert searched["total"] == 2
        assert searched["facets"]["group"] == {"A, B": 2, "Elsewhere": 1}
        assert len(client.get("/api/tools").json()) == 5
        assert client.get(f"/api/tools/{first.info.name}").json()["group"] == "A, B"
        # A write from a different worker must invalidate the original summary cache too.
        ConfigWriter.set_tool_setting(first.info.name, {"group": "Elsewhere"})
        assert client.get("/api/tools/page", params={"group": "A, B"}).json()["total"] == 2

    @pytest.mark.parametrize("group", [42, [], {}, "x" * 33, "a\x00b", "a\x7fb"])
    def test_invalid_group_rejected_without_mutation(self, tool_client, group):
        client, tool, _ = tool_client
        response = client.patch(f"/api/tools/{tool.info.name}", json={"group": group})
        assert response.status_code == 422
        assert _read_settings() == {}

    def test_group_uses_original_admin_guard_and_not_device_override(self, tool_client):
        client, tool, _ = tool_client
        assert _viewer_client().patch(f"/api/tools/{tool.info.name}", json={"group": "G"}).status_code == 403
        assert client.patch(f"/api/tools/{tool.info.name}?device_id=example", json={"group": "G"}).status_code == 400
        assert _read_settings() == {}


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
    def test_manual_reenable_clears_repeated_failure_count(self, tool_client):
        client, enabled_tool, _ = tool_client
        _set_service(enabled=True)
        revision_before = ToolRegistry.revision()
        enabled_tool.info.enabled = False
        ToolRegistry._failure_state[enabled_tool.info.name] = {
            "key": "same-failure",
            "count": ToolRegistry._failure_disable_threshold,
        }
        ToolRegistry._failure_state[enabled_tool.info.name] = {
            "key": "same-failure",
            "count": ToolRegistry._failure_disable_threshold,
        }

        res = client.patch(
            f"/api/tools/{enabled_tool.info.name}",
            json={"enabled": True},
        )

        assert res.status_code == 200
        assert res.json()["enabled"] is True
        assert enabled_tool.info.name not in ToolRegistry._failure_state
        assert ToolRegistry.revision() == revision_before + 1

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

    def test_device_enable_clears_override_and_enables_global_tool(
        self, tool_client, monkeypatch: pytest.MonkeyPatch
    ):
        client, _, disabled_tool = tool_client
        _set_service(enabled=True)
        delete_override = AsyncMock(return_value=True)
        set_override = AsyncMock()
        monkeypatch.setattr(
            "flocks.tool.device.store.delete_device_tool_setting",
            delete_override,
        )
        monkeypatch.setattr(
            "flocks.tool.device.store.set_device_tool_enabled",
            set_override,
        )

        res = client.patch(
            f"/api/tools/{disabled_tool.info.name}?device_id=dev-a",
            json={"enabled": True},
        )

        body = res.json()
        assert res.status_code == 200
        assert body["enabled"] is True
        assert disabled_tool.info.enabled is True
        assert _read_settings() == {disabled_tool.info.name: {"enabled": True}}
        delete_override.assert_awaited_once_with("dev-a", disabled_tool.info.name)
        set_override.assert_not_awaited()

    def test_device_disable_writes_false_override_only(
        self, tool_client, monkeypatch: pytest.MonkeyPatch
    ):
        client, enabled_tool, _ = tool_client
        _set_service(enabled=True)
        delete_override = AsyncMock()
        set_override = AsyncMock()
        monkeypatch.setattr(
            "flocks.tool.device.store.delete_device_tool_setting",
            delete_override,
        )
        monkeypatch.setattr(
            "flocks.tool.device.store.set_device_tool_enabled",
            set_override,
        )

        res = client.patch(
            f"/api/tools/{enabled_tool.info.name}?device_id=dev-a",
            json={"enabled": False},
        )

        body = res.json()
        assert res.status_code == 200
        assert body["enabled"] is True
        assert enabled_tool.info.enabled is True
        assert _read_settings() == {}
        set_override.assert_awaited_once_with("dev-a", enabled_tool.info.name, False)
        delete_override.assert_not_awaited()


class TestApiServiceToolSync:
    def test_service_enable_does_not_resurrect_disabled_tool_overlay(
        self,
        tool_client,
        monkeypatch: pytest.MonkeyPatch,
    ):
        client, enabled_tool, _ = tool_client
        _set_service(enabled=True)
        client.patch(f"/api/tools/{enabled_tool.info.name}", json={"enabled": False})
        assert enabled_tool.info.enabled is False

        from flocks.server.routes import provider as provider_routes

        monkeypatch.setattr(
            provider_routes,
            "_get_api_service_tool_infos",
            lambda _provider_id: [enabled_tool.info, tool_client[2].info],
        )

        matched = provider_routes._set_api_service_tools_enabled(_TEST_SERVICE_ID, True)

        assert matched == 2
        assert enabled_tool.info.enabled is False
        assert _read_settings() == {enabled_tool.info.name: {"enabled": False}}

    def test_service_state_change_bumps_tool_revision(
        self,
        tool_client,
        monkeypatch: pytest.MonkeyPatch,
    ):
        _, enabled_tool, disabled_tool = tool_client
        _set_service(enabled=True)
        revision_before = ToolRegistry.revision()
        enabled_tool.info.enabled = False

        from flocks.server.routes import provider as provider_routes

        monkeypatch.setattr(
            provider_routes,
            "_get_api_service_tool_infos",
            lambda _provider_id: [enabled_tool.info, disabled_tool.info],
        )

        provider_routes._set_api_service_tools_enabled(_TEST_SERVICE_ID, True)

        assert enabled_tool.info.enabled is True
        assert disabled_tool.info.enabled is False
        assert enabled_tool.info.name not in ToolRegistry._failure_state
        assert ToolRegistry.revision() == revision_before + 1


class TestResetToolSetting:
    def test_reset_reenable_clears_failure_count_and_bumps_revision(self, tool_client):
        client, enabled_tool, _ = tool_client
        _set_service(enabled=True)
        client.patch(f"/api/tools/{enabled_tool.info.name}", json={"enabled": False})
        ToolRegistry._failure_state[enabled_tool.info.name] = {
            "key": "same-failure",
            "count": ToolRegistry._failure_disable_threshold,
        }
        revision_before = ToolRegistry.revision()

        res = client.post(f"/api/tools/{enabled_tool.info.name}/reset")

        assert res.status_code == 200
        assert res.json()["enabled"] is True
        assert enabled_tool.info.name not in ToolRegistry._failure_state
        assert ToolRegistry.revision() == revision_before + 1

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
