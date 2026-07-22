from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from flocks.mcp.types import McpStatus, McpStatusInfo
from flocks.server.routes import mcp as mcp_routes
from flocks.tool import tool_loader


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
                "url": "https://example.com/mcp",
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
