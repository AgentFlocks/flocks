from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from flocks.mcp.types import McpStatus, McpStatusInfo
from flocks.server.routes import mcp as mcp_routes
from flocks.tool import tool_loader


class TestNativeMcpGroup:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored_group,expected", [
        (["legacy"], None), ("x" * 33, None), ("a\x00b", None),
        ("a\nb", None), ("a\x7fb", None), (None, None), ("", ""), (" Valid ", "Valid"),
    ])
    async def test_bad_stored_group_cannot_break_mixed_status_and_info_reads(
        self, tmp_path, monkeypatch, stored_group, expected,
    ):
        import json
        from types import SimpleNamespace
        from flocks.config.config_writer import ConfigWriter
        from flocks.mcp.types import normalize_mcp_group

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        configs = {
            "legacy": {
                "type": "remote", "url": "https://example.invalid/mcp", "enabled": False,
                "headers": {"Authorization": "{secret:unchanged}"}, "group": stored_group,
            },
            "healthy": {"type": "remote", "url": "https://example.invalid/good", "group": " Operations "},
            "inherited": {"type": "remote", "url": "https://example.invalid/default"},
            "explicit-null": {"type": "remote", "url": "https://example.invalid/clear", "group": None},
        }
        path = ConfigWriter._get_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bypass current validation only to reproduce legacy stored data.
        path.write_text(json.dumps({"mcp": configs}), encoding="utf-8")
        before = path.read_bytes()
        entry = SimpleNamespace(group="Canonical", group_readonly=True)
        monkeypatch.setattr(mcp_routes.McpCatalog, "get", lambda: SimpleNamespace(get_entry=lambda name: entry))
        monkeypatch.setattr(mcp_routes.MCP, "status", AsyncMock(return_value={
            "healthy": McpStatusInfo(status=McpStatus.CONNECTED, tools_count=3),
        }))
        monkeypatch.setattr(mcp_routes.MCP, "get_server_info", AsyncMock(return_value=None))

        statuses = await mcp_routes.get_mcp_status()
        assert set(statuses) == set(configs)
        assert statuses["legacy"]["group"] == expected
        assert statuses["legacy"]["status"] == McpStatus.DISABLED
        assert statuses["legacy"]["group_readonly"] is False
        assert statuses["healthy"]["group"] == "Operations"
        assert statuses["healthy"]["tools_count"] == 3
        assert statuses["inherited"]["group"] == "Canonical"
        assert statuses["explicit-null"]["group"] is None
        info = await mcp_routes.get_mcp_server_info("legacy")
        assert info["group"] == info["config"]["group"] == info["status"]["group"] == expected
        assert info["config"]["url"] == configs["legacy"]["url"]
        assert mcp_routes._to_frontend_mcp_config(configs["legacy"])["group"] == expected
        assert entry.group == "Canonical" and entry.group_readonly is True
        assert path.read_bytes() == before
        assert ConfigWriter.get_mcp_server("legacy")["group"] == stored_group
        if stored_group is not None and expected is None:
            with pytest.raises(ValueError):
                normalize_mcp_group(stored_group)
            with pytest.raises(ValueError):
                mcp_routes.McpUpdateRequest(config={"group": stored_group})

    @pytest.mark.parametrize("group", ["Instance", "", None])
    def test_native_writers_preserve_group_yml_metadata_and_replace_connection(self, tmp_path, monkeypatch, group):
        import yaml
        from flocks.config.config_writer import ConfigWriter

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setattr(tool_loader, "_MCP_SUBDIR", tmp_path / "mcp")
        yaml_path = tmp_path / "mcp/native-server.yml"
        yaml_path.parent.mkdir()
        yaml_path.write_text(yaml.safe_dump({
            "name": "native-server", "type": "local", "command": ["old"], "cwd": "/old",
            "environment": {"KEY": "{secret:old}"}, "args": ["old"], "group": "Old YAML",
            "description": "Native template metadata", "future_metadata": {"keep": True},
        }))
        ConfigWriter.add_mcp_server("native-server", {"type": "local", "command": ["old"], "group": group})
        replacement = {"type": "remote", "url": "https://example.invalid/new", "enabled": False}
        # Exercise the native writer boundary without the route wrapper.
        ConfigWriter.add_mcp_server("native-server", replacement)
        result = tool_loader.save_mcp_config("native-server", replacement)
        assert result == yaml_path
        assert "group" not in replacement
        assert ConfigWriter.get_mcp_server("native-server") == {**replacement, "group": group or ""}
        assert yaml.safe_load(yaml_path.read_text()) == {
            "name": "native-server", **replacement, "group": group or "",
            "description": "Native template metadata", "future_metadata": {"keep": True},
        }
        assert list(yaml_path.parent.iterdir()) == [yaml_path]
        for clear in (None, ""):
            saved = tool_loader.save_mcp_config("native-server", {**replacement, "group": clear})
            assert yaml.safe_load(saved.read_text())["group"] == ""

    def test_yaml_only_group_is_preserved_without_json_record(self, tmp_path, monkeypatch):
        import yaml
        from flocks.config.config_writer import ConfigWriter

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setattr(tool_loader, "_MCP_SUBDIR", tmp_path / "mcp")
        first = tool_loader.save_mcp_config("yaml-only", {"type": "remote", "url": "https://example.invalid", "group": "YAML"})
        saved = tool_loader.save_mcp_config("yaml-only", {"type": "local", "command": ["new"]})
        assert saved == first
        assert yaml.safe_load(saved.read_text()) == {"name": "yaml-only", "type": "local", "command": ["new"], "group": "YAML"}
        assert ConfigWriter.get_mcp_server("yaml-only") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("group", ["  Operations  ", "", None])
    async def test_group_only_persists_raw_and_yaml_without_connection_changes(self, tmp_path, monkeypatch, group):
        import yaml
        from flocks.config.config_writer import ConfigWriter

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setattr(tool_loader, "_MCP_SUBDIR", tmp_path / "mcp")
        original = {
            "type": "sse", "url": "https://example.invalid/mcp", "enabled": False,
            "transport": "http", "oauth": {"clientId": "keep"},
            "auth": {"type": "apikey", "value": "{secret:keep}"},
            "headers": {"Authorization": "{secret:header}"},
            "env": {"OTHER": "value"}, "unknown": {"keep": True}, "group": "Package",
        }
        ConfigWriter.add_mcp_server("native-group", original)
        forbidden = AsyncMock(side_effect=AssertionError("metadata cannot change runtime state"))
        for method in ("connect", "disconnect", "remove", "status"):
            monkeypatch.setattr(mcp_routes.MCP, method, forbidden)
        result = await mcp_routes.update_mcp_server(
            "native-group", mcp_routes.McpUpdateRequest(config={"group": group}),
        )
        expected = {**original, "group": (group or "").strip()}
        assert ConfigWriter.get_mcp_server("native-group") == expected
        saved = yaml.safe_load((tmp_path / "mcp" / "native_group.yaml").read_text())
        assert saved == {"name": "native-group", **expected}
        assert result["config"]["group"] == expected["group"]
        assert result["reconnected"] is False
        forbidden.assert_not_called()
        # An omitted group remains unchanged and also cannot trigger a reconnect.
        await mcp_routes.update_mcp_server("native-group", mcp_routes.McpUpdateRequest(config={}))
        assert ConfigWriter.get_mcp_server("native-group") == expected

    @pytest.mark.asyncio
    async def test_group_only_preserves_yaml_fields_and_original_filename(self, tmp_path, monkeypatch):
        import yaml
        from flocks.config.config_writer import ConfigWriter

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setattr(tool_loader, "_MCP_SUBDIR", tmp_path / "mcp")
        original = {"type": "remote", "url": "https://example.invalid/runtime", "group": "Before"}
        ConfigWriter.add_mcp_server("native-group", original)
        yaml_path = tmp_path / "mcp" / "native-group.yml"
        yaml_path.parent.mkdir()
        source = {"name": "native-group", "type": "remote", "url": "https://example.invalid/source", "group": "Before", "future_metadata": {"kept": True}}
        yaml_path.write_text(yaml.safe_dump(source))
        await mcp_routes.update_mcp_server("native-group", mcp_routes.McpUpdateRequest(config={"group": None}))
        assert yaml.safe_load(yaml_path.read_text()) == {**source, "group": ""}
        assert ConfigWriter.get_mcp_server("native-group") == {**original, "group": ""}
        assert sorted(path.name for path in yaml_path.parent.iterdir()) == ["native-group.yml"]

    @pytest.mark.parametrize("group", ["Operations", "", None])
    def test_native_config_replacement_preserves_omitted_group(self, tmp_path, monkeypatch, group):
        import yaml
        from flocks.config.config_writer import ConfigWriter

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setattr(tool_loader, "_MCP_SUBDIR", tmp_path / "mcp")
        ConfigWriter.add_mcp_server("native", {"type": "remote", "url": "https://example.invalid/old", "group": group})
        replacement = {"type": "remote", "url": "https://example.invalid/new", "enabled": False}
        mcp_routes._persist_mcp_server_config("native", replacement)
        expected = {**replacement, "group": group or ""}
        assert ConfigWriter.get_mcp_server("native") == expected
        assert yaml.safe_load((tmp_path / "mcp" / "native.yaml").read_text()) == {"name": "native", **expected}
        assert "group" not in replacement

    @pytest.mark.asyncio
    @pytest.mark.parametrize("saved_group", ["Operations", "", None])
    async def test_catalog_reinstall_preserves_native_group(self, tmp_path, monkeypatch, saved_group):
        from types import SimpleNamespace
        from flocks.config.config_writer import ConfigWriter

        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setattr(tool_loader, "_MCP_SUBDIR", tmp_path / "mcp")
        ConfigWriter.add_mcp_server("native", {"type": "local", "command": ["old"], "group": saved_group})
        entry = SimpleNamespace(
            name="Native", group="Package", required_env_vars={},
            to_mcp_config=lambda *args, **kwargs: {"type": "local", "command": ["new"]},
        )
        monkeypatch.setattr(mcp_routes.McpCatalog, "get", lambda: SimpleNamespace(get_entry=lambda name: entry))
        result = await mcp_routes.install_from_catalog(mcp_routes.CatalogInstallRequest(server_id="native", skip_package_install=True))
        assert result["config"]["group"] == (saved_group or "")
        assert ConfigWriter.get_mcp_server("native")["group"] == (saved_group or "")

    @pytest.mark.asyncio
    async def test_unconfigured_catalog_group_write_rejected(self, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(mcp_routes.ConfigWriter, "get_mcp_server", lambda name: None)
        monkeypatch.setattr(mcp_routes, "_persist_mcp_server_config", lambda *a: pytest.fail("must not install"))
        with pytest.raises(HTTPException) as exc:
            await mcp_routes.update_mcp_server("catalog-only", mcp_routes.McpUpdateRequest(config={"group": "G"}))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_status_and_info_config_clear_outranks_catalog_default(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(mcp_routes.McpCatalog, "get", lambda: SimpleNamespace(get_entry=lambda name: SimpleNamespace(group="Package")))
        monkeypatch.setattr(mcp_routes.MCP, "status", AsyncMock(return_value={
            "cleared": McpStatusInfo(status=McpStatus.CONNECTED),
        }))
        configs = {
            "cleared": {"type": "remote", "url": "https://example.invalid", "group": ""},
            "inherited": {"type": "remote", "url": "https://example.invalid"},
        }
        monkeypatch.setattr(mcp_routes.ConfigWriter, "list_mcp_servers", lambda: configs)
        monkeypatch.setattr(mcp_routes.ConfigWriter, "get_mcp_server", configs.get)
        monkeypatch.setattr(mcp_routes.MCP, "get_server_info", AsyncMock(return_value=None))
        statuses = await mcp_routes.get_mcp_status()
        assert statuses["cleared"]["group"] == ""
        assert statuses["inherited"]["group"] == "Package"
        info = await mcp_routes.get_mcp_server_info("cleared")
        assert info["group"] == info["config"]["group"] == info["status"]["group"] == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path,payload", [
        ("PUT", "/api/mcp/validation-only", {"config": {"group": "a\x00b"}}),
        ("PATCH", "/api/tools/validation-only", {"group": 42}),
        ("PATCH", "/api/provider/api-services/validation-only", {"group": "x" * 33}),
    ])
    async def test_native_group_http_validation_errors_are_serializable(self, client, method, path, payload):
        response = await client.request(method, path, json=payload)
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("group", [42, [], "x" * 33, "a\x00b"])
    def test_group_validation_rejects_invalid_config(self, group):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            mcp_routes.McpUpdateRequest(config={"group": group})


class TestMcpRoutes:

    @pytest.mark.asyncio
    async def test_add_mcp_server_allows_missing_credentials(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        attempted_connects: list[str] = []

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_connects.append(name)
            return False

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {
                "qianxin-mcp": McpStatusInfo(
                    status=McpStatus.FAILED,
                    error="Secret not found: qianxin_mcp_key",
                )
            }

        async def fake_remove(name: str) -> bool:
            return True

        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "list_mcp_servers",
            lambda: stored_configs.copy(),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.post(
            "/api/mcp",
            json={
                "name": "demo-mcp",
                "config": {
                    "type": "remote",
                    "url": "https://example.com/mcp",
                    "auth": {
                        "type": "apikey",
                        "location": "query",
                        "param_name": "apikey",
                        "value": "",
                    },
                },
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["demo-mcp"]["status"] == "disconnected"
        assert stored_configs["demo-mcp"]["auth"]["value"] == ""
        assert attempted_connects == []

    @pytest.mark.asyncio
    async def test_add_mcp_server_rejects_non_auth_failures(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        persisted_names: list[str] = []

        async def fake_connect(name: str, config: dict) -> bool:
            return False

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {
                "broken-mcp": McpStatusInfo(
                    status=McpStatus.FAILED,
                    error="Connection refused",
                )
            }

        async def fake_remove(name: str) -> bool:
            raise AssertionError("remove should not be called for non-auth failures")

        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: persisted_names.append(name),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.post(
            "/api/mcp",
            json={
                "name": "broken-mcp",
                "config": {
                    "type": "remote",
                    "url": "https://example.com/mcp",
                    "headers": {
                        "Authorization": "Bearer token123",
                    },
                },
            },
        )

        assert resp.status_code == 400, resp.text
        assert "Connection refused" in resp.text
        assert persisted_names == []

    @pytest.mark.asyncio
    async def test_add_mcp_server_allows_missing_header_credentials(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        attempted_connects: list[str] = []
        removed_servers: list[str] = []

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_connects.append(name)
            return False

        async def fake_status() -> dict[str, McpStatusInfo]:
            if "qianxin-mcp" in removed_servers:
                return {}
            return {
                "qianxin-mcp": McpStatusInfo(
                    status=McpStatus.FAILED,
                    error="Secret not found: qianxin_mcp_key",
                )
            }

        async def fake_remove(name: str) -> bool:
            removed_servers.append(name)
            return True

        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "list_mcp_servers",
            lambda: stored_configs.copy(),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.post(
            "/api/mcp",
            json={
                "name": "qianxin-mcp",
                "config": {
                    "type": "remote",
                    "url": "https://example.com/mcp",
                    "headers": {
                        "Api-Key": "{secret:qianxin_mcp_key}",
                    },
                },
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["qianxin-mcp"]["status"] == "disconnected"
        assert stored_configs["qianxin-mcp"]["headers"]["Api-Key"] == "{secret:qianxin_mcp_key}"
        assert attempted_connects == ["qianxin-mcp"]
        assert removed_servers == ["qianxin-mcp"]

    @pytest.mark.asyncio
    async def test_add_mcp_server_without_key_attempts_anonymous_connect(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        attempted_connects: list[str] = []

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_connects.append(name)
            return True

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {"qianxin-mcp": McpStatusInfo(status=McpStatus.CONNECTED)}

        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "list_mcp_servers",
            lambda: stored_configs.copy(),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.post(
            "/api/mcp",
            json={
                "name": "qianxin-mcp",
                "config": {
                    "type": "remote",
                    "url": "https://example.com/mcp",
                },
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["qianxin-mcp"]["status"] == "connected"
        assert stored_configs["qianxin-mcp"]["url"] == "https://example.com/mcp"
        assert attempted_connects == ["qianxin-mcp"]

    @pytest.mark.asyncio
    async def test_get_mcp_server_info_returns_config_for_disconnected_remote_alias(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_get_server_info(name: str):
            return None

        async def fake_config_get(cls):
            return type("ConfigStub", (), {"mcp": {}})()

        monkeypatch.setattr(mcp_routes.MCP, "get_server_info", fake_get_server_info)
        monkeypatch.setattr(
            mcp_routes.Config,
            "get",
            classmethod(fake_config_get),
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "sse",
                "url": "https://example.com/mcp",
                "enabled": True,
            },
        )

        resp = await client.get("/api/mcp/demo-remote")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"]["status"] == "disconnected"
        assert data["config"]["type"] == "sse"
        assert data["config"]["url"] == "https://example.com/mcp"

    @pytest.mark.asyncio
    async def test_get_mcp_server_info_masks_plaintext_sensitive_values(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_get_server_info(name: str):
            return None

        async def fake_config_get(cls):
            return type("ConfigStub", (), {"mcp": {}})()

        monkeypatch.setattr(mcp_routes.MCP, "get_server_info", fake_get_server_info)
        monkeypatch.setattr(
            mcp_routes.Config,
            "get",
            classmethod(fake_config_get),
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "remote",
                "url": "https://example.com/mcp?apikey=url-token&mode=full",
                "auth": {
                    "type": "apikey",
                    "location": "header",
                    "param_name": "Authorization",
                    "value": "Bearer token123",
                },
                "headers": {
                    "Authorization": "Bearer token123",
                    "X-Client": "flocks",
                },
            },
        )

        resp = await client.get("/api/mcp/demo-remote")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["config"]["url"] == "https://example.com/mcp?apikey=***&mode=full"
        assert "url-token" not in resp.text
        assert data["config"]["auth"]["value"] == "***"
        assert data["config"]["headers"]["Authorization"] == "***"
        assert data["config"]["headers"]["X-Client"] == "flocks"

    @pytest.mark.asyncio
    async def test_test_mcp_connection_normalizes_sse_alias_to_remote(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        seen: dict[str, str] = {}
        removed_servers: list[str] = []

        async def fake_connect(name: str, config: dict) -> bool:
            seen["name"] = name
            seen["type"] = config["type"]
            return False

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {
                "demo-sse__test__": McpStatusInfo(
                    status=McpStatus.FAILED,
                    error="auth missing",
                )
            }

        async def fake_remove(name: str) -> bool:
            removed_servers.append(name)
            return True

        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)

        resp = await client.post(
            "/api/mcp/test",
            json={
                "name": "demo-sse",
                "config": {
                    "type": "sse",
                    "url": "https://example.com/mcp",
                },
            },
        )

        assert resp.status_code == 200, resp.text
        assert seen["name"] == "demo-sse__test__"
        assert seen["type"] == "remote"
        assert removed_servers == ["demo-sse__test__"]

    @pytest.mark.asyncio
    async def test_update_mcp_server_merges_partial_config_and_clears_runtime_state(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        removed_servers: list[str] = []
        attempted_reconnects: list[tuple[str, dict]] = []

        async def fake_config_get(cls):
            return type(
                "ConfigStub",
                (),
                {
                    "mcp": {
                        "qianxin-mcp": {
                            "type": "remote",
                            "url": "https://old.example.com/mcp",
                            "headers": {"Api-Key": "{secret:qianxin_mcp_key}"},
                        }
                    }
                },
            )()

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {"qianxin-mcp": McpStatusInfo(status=McpStatus.CONNECTED)}

        async def fake_remove(name: str) -> bool:
            removed_servers.append(name)
            return True

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_reconnects.append((name, dict(config)))
            return True

        monkeypatch.setattr(
            mcp_routes.Config,
            "get",
            classmethod(fake_config_get),
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "remote",
                "url": "https://old.example.com/mcp",
                "headers": {"Api-Key": "{secret:qianxin_mcp_key}"},
            },
        )
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.put(
            "/api/mcp/qianxin-mcp",
            json={"config": {"type": "sse", "url": "https://new.example.com/mcp"}},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["config"]["type"] == "sse"
        assert data["config"]["url"] == "https://new.example.com/mcp"
        assert data["reconnected"] is True
        assert data["reconnect_error"] is None
        assert stored_configs["qianxin-mcp"]["type"] == "remote"
        assert stored_configs["qianxin-mcp"]["url"] == "https://new.example.com/mcp"
        assert stored_configs["qianxin-mcp"]["headers"]["Api-Key"] == "{secret:qianxin_mcp_key}"
        assert removed_servers == ["qianxin-mcp"]
        assert attempted_reconnects == [
            (
                "qianxin-mcp",
                {
                    "type": "remote",
                    "url": "https://new.example.com/mcp",
                    "transport": "sse",
                    "headers": {"Api-Key": "{secret:qianxin_mcp_key}"},
                },
            )
        ]

    @pytest.mark.asyncio
    async def test_update_mcp_server_can_disable_and_persist_state(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        removed_servers: list[str] = []

        async def fake_config_get(cls):
            return type(
                "ConfigStub",
                (),
                {
                    "mcp": {
                        "panther": {
                            "type": "local",
                            "command": ["python", "-m", "mcp_panther"],
                            "enabled": True,
                        }
                    }
                },
            )()

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {"panther": McpStatusInfo(status=McpStatus.CONNECTED)}

        async def fake_remove(name: str) -> bool:
            removed_servers.append(name)
            return True

        monkeypatch.setattr(
            mcp_routes.Config,
            "get",
            classmethod(fake_config_get),
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "local",
                "command": ["python", "-m", "mcp_panther"],
                "enabled": True,
            },
        )
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.put(
            "/api/mcp/panther",
            json={"config": {"enabled": False}},
        )

        assert resp.status_code == 200, resp.text
        assert stored_configs["panther"]["enabled"] is False
        assert removed_servers == ["panther"]

    @pytest.mark.asyncio
    async def test_update_mcp_server_returns_warning_when_reconnect_fails(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        removed_servers: list[str] = []
        attempted_reconnects: list[str] = []

        async def fake_config_get(cls):
            return type(
                "ConfigStub",
                (),
                {
                    "mcp": {
                        "GaodeMap": {
                            "type": "remote",
                            "url": "https://old.example.com/mcp",
                            "auth": {
                                "type": "apikey",
                                "location": "header",
                                "param_name": "Authorization",
                                "value": "{secret:GaodeMap_mcp_key}",
                            },
                        }
                    }
                },
            )()

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {"GaodeMap": McpStatusInfo(status=McpStatus.CONNECTED)}

        async def fake_remove(name: str) -> bool:
            removed_servers.append(name)
            return True

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_reconnects.append(name)
            return False

        monkeypatch.setattr(
            mcp_routes.Config,
            "get",
            classmethod(fake_config_get),
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "remote",
                "url": "https://old.example.com/mcp",
                "auth": {
                    "type": "apikey",
                    "location": "header",
                    "param_name": "Authorization",
                    "value": "{secret:GaodeMap_mcp_key}",
                },
            },
        )
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.put(
            "/api/mcp/GaodeMap",
            json={"config": {"url": "https://new.example.com/mcp"}},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["reconnected"] is False
        assert data["reconnect_error"] == "Failed to reconnect MCP server: GaodeMap"
        assert "reconnect failed" in data["message"]
        assert stored_configs["GaodeMap"]["url"] == "https://new.example.com/mcp"
        assert removed_servers == ["GaodeMap"]
        assert attempted_reconnects == ["GaodeMap"]

    @pytest.mark.asyncio
    async def test_update_mcp_server_extracts_url_api_key_before_persisting(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        saved_secrets: dict[str, str] = {}

        async def fake_config_get(cls):
            return type(
                "ConfigStub",
                (),
                {
                    "mcp": {
                        "demo-mcp": {
                            "type": "remote",
                            "url": "https://old.example.com/mcp",
                        }
                    }
                },
            )()

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {}

        monkeypatch.setattr(
            mcp_routes.Config,
            "get",
            classmethod(fake_config_get),
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "remote",
                "url": "https://old.example.com/mcp",
            },
        )
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        class SecretManagerStub:
            def set(self, key: str, value: str) -> None:
                saved_secrets[key] = value

        monkeypatch.setattr(
            "flocks.security.get_secret_manager",
            lambda: SecretManagerStub(),
        )

        resp = await client.put(
            "/api/mcp/demo-mcp",
            json={"config": {"url": "https://example.com/mcp?apikey=token123"}},
        )

        assert resp.status_code == 200, resp.text
        assert saved_secrets == {"demo-mcp_mcp_key": "token123"}
        assert (
            stored_configs["demo-mcp"]["url"]
            == "https://example.com/mcp?apikey={secret:demo-mcp_mcp_key}"
        )

    @pytest.mark.asyncio
    async def test_update_mcp_server_restores_masked_sensitive_values(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        saved_secrets: dict[str, str] = {}

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {}

        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "remote",
                "url": "https://old.example.com/mcp",
                "auth": {
                    "type": "apikey",
                    "location": "header",
                    "param_name": "Authorization",
                    "value": "Bearer token123",
                },
                "headers": {
                    "Authorization": "Bearer token123",
                    "X-Client": "flocks",
                },
            },
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        class SecretManagerStub:
            def set(self, key: str, value: str) -> None:
                saved_secrets[key] = value

        monkeypatch.setattr(
            "flocks.security.get_secret_manager",
            lambda: SecretManagerStub(),
        )

        resp = await client.put(
            "/api/mcp/demo-mcp",
            json={
                "config": {
                    "url": "https://new.example.com/mcp",
                    "auth": {
                        "type": "apikey",
                        "location": "header",
                        "param_name": "Authorization",
                        "value": "***",
                    },
                    "headers": {
                        "Authorization": "***",
                        "X-Client": "flocks-web",
                    },
                }
            },
        )

        assert resp.status_code == 200, resp.text
        assert saved_secrets == {
            "demo-mcp_mcp_key": "token123",
            "demo-mcp_authorization_header": "Bearer token123",
        }
        assert stored_configs["demo-mcp"]["url"] == "https://new.example.com/mcp"
        assert stored_configs["demo-mcp"]["auth"]["value"] == "{secret:demo-mcp_mcp_key}"
        assert (
            stored_configs["demo-mcp"]["headers"]["Authorization"]
            == "{secret:demo-mcp_authorization_header}"
        )
        assert stored_configs["demo-mcp"]["headers"]["X-Client"] == "flocks-web"

    @pytest.mark.asyncio
    async def test_update_mcp_server_restores_masked_url_query_secret(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        saved_secrets: dict[str, str] = {}

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {}

        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "remote",
                "url": "https://old.example.com/mcp?apikey=url-token&mode=full",
                "enabled": False,
            },
        )
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        class SecretManagerStub:
            def set(self, key: str, value: str) -> None:
                saved_secrets[key] = value

        monkeypatch.setattr(
            "flocks.security.get_secret_manager",
            lambda: SecretManagerStub(),
        )

        resp = await client.put(
            "/api/mcp/demo-mcp",
            json={
                "config": {
                    "url": "https://new.example.com/mcp?apikey=***&mode=compact",
                    "enabled": False,
                }
            },
        )

        assert resp.status_code == 200, resp.text
        assert saved_secrets == {"demo-mcp_mcp_key": "url-token"}
        assert (
            stored_configs["demo-mcp"]["url"]
            == "https://new.example.com/mcp?apikey={secret:demo-mcp_mcp_key}&mode=compact"
        )

    # ---------------------------------------------------------------------
    # should_reconnect: the contract for ``PUT /api/mcp/{name}`` is that
    # any save where the new config asks the server to be enabled AND the
    # config is complete enough to dial must trigger a fresh connect — not
    # just the "was already connected" case.  These tests cover the three
    # flows the production fix needs to keep working:
    #   * first enable (no runtime status entry at all);
    #   * fixing credentials after a previous FAILED connect;
    #   * blocked configs (pending {secret:...}) must NOT auto-connect.
    # ---------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_update_mcp_server_connects_on_first_enable_without_prior_status(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        attempted_connects: list[str] = []

        async def fake_status() -> dict[str, McpStatusInfo]:
            # No runtime status — server has never been touched in this
            # process (the catalog-install + later-enable flow).
            return {}

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_connects.append(name)
            return True

        async def fake_remove(name: str) -> bool:
            raise AssertionError("remove must not run when there is no runtime state")

        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "local",
                "command": ["python", "-m", "mcp_panther"],
                "enabled": False,
            },
        )
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.put(
            "/api/mcp/panther",
            json={"config": {"enabled": True}},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["reconnected"] is True
        assert data["reconnect_error"] is None
        assert attempted_connects == ["panther"], (
            "first enable must trigger a connect — otherwise the user has "
            "to restart the process for a freshly-enabled server's tools "
            "to register"
        )
        assert stored_configs["panther"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_update_mcp_server_reconnects_after_previous_failure(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """User fixed credentials, saves the config — the route must dial
        again even though the prior status was FAILED rather than CONNECTED."""
        stored_configs: dict[str, dict] = {}
        attempted_connects: list[str] = []
        removed: list[str] = []

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {
                "panther": McpStatusInfo(
                    status=McpStatus.FAILED,
                    error="Authentication failed",
                )
            }

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_connects.append(name)
            return True

        async def fake_remove(name: str) -> bool:
            removed.append(name)
            return True

        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "local",
                "command": ["python", "-m", "mcp_panther"],
                "enabled": True,
            },
        )
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.put(
            "/api/mcp/panther",
            json={"config": {"command": ["python", "-m", "mcp_panther", "--fixed"]}},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["reconnected"] is True
        assert attempted_connects == ["panther"], (
            "saving a config when the server was FAILED must re-dial — "
            "otherwise the user has to manually click Connect after fixing "
            "credentials"
        )
        assert removed == ["panther"]

    @pytest.mark.asyncio
    async def test_update_mcp_server_skips_connect_when_credentials_blank(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """When the saved config leaves an apikey auth ``value`` blank —
        which is how the UI represents "I have not entered credentials
        yet" — ``get_connect_block_reason`` flags the config as pending.
        The route must NOT auto-connect in that case; those connects
        would always fail and waste cycles.
        """
        stored_configs: dict[str, dict] = {}
        attempted_connects: list[str] = []

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {}

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_connects.append(name)
            return True

        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "type": "remote",
                "url": "https://example.com/mcp",
                "auth": {
                    "type": "apikey",
                    "location": "header",
                    "param_name": "Authorization",
                    # Blank value — UI representation for "no credentials
                    # entered yet"; flagged as pending by
                    # ``config_has_pending_credentials``.
                    "value": "",
                },
                "enabled": False,
            },
        )
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.put(
            "/api/mcp/blocked-mcp",
            json={"config": {"enabled": True}},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert attempted_connects == [], (
            "configs with blank credential slots must not be auto-connected "
            "— that would just generate failed login attempts"
        )
        assert data["reconnected"] is False

    @pytest.mark.asyncio
    async def test_catalog_install_defaults_to_disabled_without_connecting(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}
        attempted_connects: list[str] = []

        class CatalogEntryStub:
            name = "Panther SIEM"
            required_env_vars = {}

            def to_mcp_config(self, env_overrides=None, args=None):
                return {
                    "type": "local",
                    "command": ["python", "-m", "mcp_panther"],
                }

        class CatalogStub:
            def get_entry(self, server_id: str):
                if server_id == "panther":
                    return CatalogEntryStub()
                return None

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_connects.append(name)
            return True

        async def fake_preflight_install(entry) -> None:
            return None

        monkeypatch.setattr(mcp_routes.McpCatalog, "get", lambda: CatalogStub())
        monkeypatch.setattr(mcp_routes, "preflight_install", fake_preflight_install)
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)

        resp = await client.post(
            "/api/mcp/catalog/install",
            json={"server_id": "panther"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["config"]["enabled"] is False
        assert stored_configs["panther"]["enabled"] is False
        assert attempted_connects == []

    @pytest.mark.asyncio
    async def test_catalog_auto_setup_defaults_to_disabled(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        stored_configs: dict[str, dict] = {}

        class CatalogEntryStub:
            id = "panther"
            requires_auth = False

            def to_mcp_config(self):
                return {
                    "type": "local",
                    "command": ["python", "-m", "mcp_panther"],
                }

        class CatalogStub:
            entries = [CatalogEntryStub()]

        monkeypatch.setattr(mcp_routes.McpCatalog, "get", lambda: CatalogStub())
        monkeypatch.setattr(mcp_routes.ConfigWriter, "list_mcp_servers", lambda: stored_configs.copy())
        monkeypatch.setattr(
            mcp_routes.ConfigWriter,
            "add_mcp_server",
            lambda name, config: stored_configs.__setitem__(name, config),
        )

        resp = await client.post("/api/mcp/catalog/auto-setup")

        assert resp.status_code == 200, resp.text
        assert stored_configs["panther"]["enabled"] is False
        assert resp.json()["newly_configured"] == ["panther"]

    @pytest.mark.asyncio
    async def test_existing_mcp_test_merges_saved_config_with_url_override(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        seen: dict[str, dict] = {}
        removed_servers: list[str] = []

        async def fake_config_get(cls):
            return type(
                "ConfigStub",
                (),
                {
                    "mcp": {
                        "qianxin-mcp": {
                            "type": "remote",
                            "url": "https://old.example.com/mcp",
                            "headers": {"Api-Key": "{secret:qianxin_mcp_key}"},
                        }
                    }
                },
            )()

        async def fake_connect(name: str, config: dict) -> bool:
            seen["name"] = name
            seen["config"] = dict(config)
            return False

        async def fake_status() -> dict[str, McpStatusInfo]:
            return {
                "qianxin-mcp__test__": McpStatusInfo(
                    status=McpStatus.FAILED,
                    error="Secret not found: qianxin_mcp_key",
                )
            }

        async def fake_remove(name: str) -> bool:
            removed_servers.append(name)
            return True

        monkeypatch.setattr(
            mcp_routes.Config,
            "get",
            classmethod(fake_config_get),
        )
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)

        resp = await client.post(
            "/api/mcp/qianxin-mcp/test",
            json={"config": {"type": "sse", "url": "https://new.example.com/mcp"}},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is False
        assert seen["name"] == "qianxin-mcp__test__"
        assert seen["config"]["type"] == "remote"
        assert seen["config"]["url"] == "https://new.example.com/mcp"
        assert seen["config"]["headers"]["Api-Key"] == "{secret:qianxin_mcp_key}"
        assert removed_servers == ["qianxin-mcp__test__"]

    @pytest.mark.asyncio
    async def test_connect_mcp_server_without_credentials_attempts_connect(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        attempted_connects: list[str] = []

        async def fake_config_get(cls):
            return type(
                "ConfigStub",
                (),
                {
                    "mcp": {
                        "qianxin-mcp": {
                            "type": "remote",
                            "url": "https://example.com/mcp",
                        }
                    }
                },
            )()

        async def fake_connect(name: str, config: dict) -> bool:
            attempted_connects.append(name)
            return True

        monkeypatch.setattr(
            mcp_routes.Config,
            "get",
            classmethod(fake_config_get),
        )
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)

        resp = await client.post("/api/mcp/qianxin-mcp/connect")

        assert resp.status_code == 200, resp.text
        assert resp.json() is True
        assert attempted_connects == ["qianxin-mcp"]

    @pytest.mark.asyncio
    async def test_connect_mcp_server_times_out_with_explicit_error(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_config_get(cls):
            return type(
                "ConfigStub",
                (),
                {
                    "mcp": {
                        "qianxin-mcp": {
                            "type": "remote",
                            "url": "https://example.com/mcp",
                            "headers": {"Authorization": "Bearer token123"},
                            "timeout": 1,
                        }
                    }
                },
            )()

        async def fake_connect(name: str, config: dict) -> bool:
            await asyncio.sleep(10)
            return True

        monkeypatch.setattr(
            mcp_routes.Config,
            "get",
            classmethod(fake_config_get),
        )
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)

        resp = await client.post("/api/mcp/qianxin-mcp/connect")

        assert resp.status_code == 504, resp.text
        assert "timed out" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_configure_threatbook_mcp_rejects_global_region_without_mutation(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        raw_state: dict = {"mcp": {}}
        secret_state: dict[str, str] = {}
        validated_configs: list[dict] = []
        connected_configs: list[dict] = []

        class FakeSecrets:
            def _load(self):
                return dict(secret_state)

            def _save(self, data):
                secret_state.clear()
                secret_state.update(data)

            def set(self, secret_id: str, value: str):
                secret_state[secret_id] = value

        async def fake_test(request):
            validated_configs.append(request.config)
            return {"success": True, "message": "ok", "tools_count": 4}

        async def fake_status():
            return {}

        async def fake_load(name: str):
            return raw_state["mcp"].get(name)

        async def fake_connect(name: str, config: dict):
            connected_configs.append(config)
            return True

        async def fake_refresh(name: str):
            return 4

        def fake_persist(name: str, config: dict):
            raw_state["mcp"][name] = dict(config)

        monkeypatch.setattr(mcp_routes, "get_secret_manager", lambda: FakeSecrets())
        monkeypatch.setattr(mcp_routes, "test_mcp_connection", fake_test)
        monkeypatch.setattr(mcp_routes, "_load_mcp_server_config", fake_load)
        monkeypatch.setattr(mcp_routes, "_persist_mcp_server_config", fake_persist)
        monkeypatch.setattr(mcp_routes.ConfigWriter, "_read_raw", lambda: raw_state.copy())
        monkeypatch.setattr(mcp_routes, "_load_raw_mcp_server_config", lambda name: raw_state["mcp"].get(name))
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(mcp_routes.MCP, "refresh_tools", fake_refresh)

        resp = await client.post(
            "/api/mcp/threatbook_mcp/threatbook-configure",
            json={"region": "global", "api_key": "global key"},
        )

        assert resp.status_code == 400, resp.text
        assert "China region only" in resp.json()["message"]
        assert validated_configs == []
        assert raw_state == {"mcp": {}}
        assert secret_state == {}
        assert connected_configs == []

    @pytest.mark.asyncio
    async def test_get_threatbook_mcp_credentials_uses_non_duplicated_secret_id(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        requested_ids: list[str] = []

        class FakeSecrets:
            def get(self, secret_id: str):
                requested_ids.append(secret_id)
                return "312abcdef321" if secret_id == "threatbook_mcp_key" else None

        monkeypatch.setattr(mcp_routes, "get_secret_manager", lambda: FakeSecrets())

        resp = await client.get("/api/mcp/threatbook_mcp/credentials")

        assert resp.status_code == 200, resp.text
        assert resp.json()["has_credential"] is True
        assert resp.json()["secret_id"] == "threatbook_mcp_key"
        assert resp.json()["api_key_masked"] == "************"
        assert "312abcdef321" not in resp.text
        assert requested_ids == ["threatbook_mcp_key"]

    @pytest.mark.asyncio
    async def test_reveal_threatbook_mcp_credentials_returns_full_key_on_demand(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        class FakeSecrets:
            def get(self, secret_id: str):
                return "312abcdef321" if secret_id == "threatbook_mcp_key" else None

        monkeypatch.setattr(mcp_routes, "get_secret_manager", lambda: FakeSecrets())
        audit = AsyncMock()
        monkeypatch.setattr(mcp_routes, "emit_audit_event", audit)

        resp = await client.post("/api/mcp/threatbook_mcp/credentials/reveal")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"api_key": "312abcdef321"}
        assert resp.headers["cache-control"] == "no-store"
        audit.assert_awaited_once()
        event_type, payload = audit.await_args.args
        assert event_type == "mcp.credentials_reveal"
        assert payload["mcp_name"] == "threatbook_mcp"
        assert "312abcdef321" not in repr(payload)

    @pytest.mark.asyncio
    async def test_mcp_credentials_support_historical_inline_url_key(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        class FakeSecrets:
            def get(self, secret_id: str):
                return None

        monkeypatch.setattr(mcp_routes, "get_secret_manager", lambda: FakeSecrets())
        monkeypatch.setattr(
            mcp_routes,
            "_load_raw_mcp_server_config",
            lambda _name: {
                "type": "remote",
                "url": "https://mcp.threatbook.cn/mcp?apikey=historical-key",
            },
        )
        monkeypatch.setattr(mcp_routes, "emit_audit_event", AsyncMock())

        masked = await client.get("/api/mcp/threatbook_mcp/credentials")
        revealed = await client.post("/api/mcp/threatbook_mcp/credentials/reveal")

        assert masked.status_code == 200, masked.text
        assert masked.json()["has_credential"] is True
        assert masked.json()["api_key_masked"] == "************"
        assert "historical-key" not in masked.text
        assert revealed.status_code == 200, revealed.text
        assert revealed.json() == {"api_key": "historical-key"}

    @pytest.mark.asyncio
    async def test_reveal_mcp_credentials_returns_not_found_when_missing(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        class FakeSecrets:
            def get(self, secret_id: str):
                return None

        monkeypatch.setattr(mcp_routes, "get_secret_manager", lambda: FakeSecrets())

        resp = await client.post("/api/mcp/threatbook_mcp/credentials/reveal")

        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_configure_threatbook_mcp_does_not_persist_failed_validation(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        persisted: list[dict] = []

        async def fake_test(request):
            return {"success": False, "message": "invalid regional key"}

        monkeypatch.setattr(mcp_routes, "test_mcp_connection", fake_test)
        monkeypatch.setattr(
            mcp_routes,
            "_persist_mcp_server_config",
            lambda name, config: persisted.append(config),
        )

        resp = await client.post(
            "/api/mcp/threatbook_mcp/threatbook-configure",
            json={"region": "cn", "api_key": "bad-key"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is False
        assert resp.json()["message"] == "invalid regional key"
        assert persisted == []

    @pytest.mark.asyncio
    async def test_configure_threatbook_mcp_restores_previous_state_when_apply_fails(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        old_config = {
            "type": "remote",
            "url": "https://mcp.threatbook.cn/mcp?apikey={secret:threatbook_mcp_key}",
            "enabled": True,
        }
        raw_state = {"mcp": {"threatbook_mcp": dict(old_config)}}
        secret_state = {"threatbook_mcp_key": "old-key"}
        connect_attempts: list[str] = []

        class FakeSecrets:
            def _load(self):
                return dict(secret_state)

            def _save(self, data):
                secret_state.clear()
                secret_state.update(data)

            def set(self, secret_id: str, value: str):
                secret_state[secret_id] = value

        async def fake_test(request):
            return {"success": True, "message": "ok"}

        async def fake_status():
            return {
                "threatbook_mcp": McpStatusInfo(status=McpStatus.CONNECTED),
            }

        async def fake_remove(name: str):
            return True

        async def fake_load(name: str):
            return raw_state["mcp"].get(name)

        async def fake_connect(name: str, config: dict):
            connect_attempts.append(config["url"])
            return len(connect_attempts) > 1

        def fake_write_raw(data: dict):
            raw_state.clear()
            raw_state.update(data)

        def fake_persist(name: str, config: dict):
            raw_state["mcp"][name] = dict(config)

        monkeypatch.setattr(mcp_routes, "get_secret_manager", lambda: FakeSecrets())
        monkeypatch.setattr(mcp_routes, "test_mcp_connection", fake_test)
        monkeypatch.setattr(mcp_routes, "_load_mcp_server_config", fake_load)
        monkeypatch.setattr(mcp_routes, "_load_raw_mcp_server_config", lambda name: raw_state["mcp"].get(name))
        monkeypatch.setattr(mcp_routes, "_persist_mcp_server_config", fake_persist)
        monkeypatch.setattr(mcp_routes.ConfigWriter, "_read_raw", lambda: {"mcp": {"threatbook_mcp": dict(raw_state["mcp"]["threatbook_mcp"])}})
        monkeypatch.setattr(mcp_routes.ConfigWriter, "_write_raw", fake_write_raw)
        monkeypatch.setattr(mcp_routes.MCP, "status", fake_status)
        monkeypatch.setattr(mcp_routes.MCP, "remove", fake_remove)
        monkeypatch.setattr(mcp_routes.MCP, "connect", fake_connect)
        monkeypatch.setattr(tool_loader, "save_mcp_config", lambda name, config: None)
        monkeypatch.setattr(tool_loader, "delete_mcp_config", lambda name: True)

        resp = await client.post(
            "/api/mcp/threatbook_mcp/threatbook-configure",
            json={"region": "cn", "api_key": "new-key"},
        )

        assert resp.status_code == 500, resp.text
        assert raw_state["mcp"]["threatbook_mcp"] == old_config
        assert secret_state["threatbook_mcp_key"] == "old-key"
        assert connect_attempts[-1] == old_config["url"]
