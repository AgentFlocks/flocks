import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import yaml

from fastapi import HTTPException
from flocks.tool.registry import Tool, ToolCategory, ToolInfo, ToolRegistry


class TestAPIServiceManagement:
    @pytest.mark.asyncio
    async def test_list_api_services_returns_enabled_state_and_bilingual_descriptions(self):
        from flocks.server.routes.provider import list_api_services

        metadata_by_id = {
            "threatbook_api": {
                "name": "ThreatBook",
                "description": "Threat intelligence lookups",
                "description_cn": "威胁情报查询服务",
            },
            "fofa": {
                "name": "FOFA",
                "description": "Internet asset search",
                "description_cn": "互联网资产检索服务",
            },
        }

        with (
            patch("flocks.tool.registry.ToolRegistry") as mock_tool_registry,
            patch("flocks.config.config_writer.ConfigWriter.list_api_services_raw", return_value={
                "threatbook_api": {"enabled": False},
            }),
            patch("flocks.config.config_writer.ConfigWriter.get_api_service_raw", side_effect=lambda service_id: {
                "threatbook_api": {"enabled": False},
            }.get(service_id, {})),
            patch(
                "flocks.server.routes.provider._load_api_service_metadata_data",
                side_effect=lambda service_id: metadata_by_id[service_id],
            ),
            patch(
                "flocks.server.routes.provider._read_api_service_status_cache",
                new=AsyncMock(return_value={
                    "fofa": {
                        "status": "connected",
                        "latency_ms": 120,
                        "checked_at": 123,
                    }
                }),
            ),
            patch(
                "flocks.server.routes.provider._get_api_service_tool_infos",
                side_effect=lambda service_id: [object(), object()] if service_id == "threatbook_api" else [object()],
            ),
        ):
            mock_tool_registry.init = MagicMock()
            mock_tool_registry.get_api_service_ids.return_value = {"fofa"}

            result = await list_api_services()

        assert [item.id for item in result] == ["fofa", "threatbook_api"]
        assert result[0].enabled is False
        assert result[0].status == "disabled"
        assert result[0].description == "Internet asset search"
        assert result[0].description_cn == "互联网资产检索服务"
        assert result[1].enabled is False
        assert result[1].status == "disabled"
        assert result[1].tool_count == 2

    @pytest.mark.asyncio
    async def test_update_api_service_persists_enabled_flag_and_updates_status_cache(self):
        from flocks.server.routes.provider import (
            APIServiceSummary,
            APIServiceUpdateRequest,
            update_api_service,
        )

        existing_config = {"apiKey": "{secret:threatbook_api_key}"}
        expected_summary = APIServiceSummary(
            id="threatbook_api",
            name="ThreatBook",
            enabled=False,
            status="disabled",
            tool_count=2,
        )

        with (
            patch("flocks.config.config_writer.ConfigWriter.get_api_service_raw", return_value=existing_config.copy()),
            patch("flocks.config.config_writer.ConfigWriter.set_api_service") as mock_set_service,
            patch("flocks.server.routes.provider._set_api_service_tools_enabled", return_value=2) as mock_toggle_tools,
            patch(
                "flocks.server.routes.provider._read_api_service_status_cache",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "flocks.server.routes.provider._write_api_service_status_cache",
                new=AsyncMock(),
            ) as mock_write_status,
            patch(
                "flocks.server.routes.provider._build_api_service_summary",
                return_value=expected_summary,
            ),
        ):
            result = await update_api_service(
                "threatbook_api",
                APIServiceUpdateRequest(enabled=False),
            )

        mock_set_service.assert_called_once_with(
            "threatbook_api",
            {"apiKey": "{secret:threatbook_api_key}", "enabled": False},
        )
        mock_toggle_tools.assert_called_once_with("threatbook_api", False)
        written_statuses = mock_write_status.await_args.args[0]
        assert written_statuses["threatbook_api"]["status"] == "disabled"
        assert result == expected_summary


class TestToolRouteAPIServiceSync:
    def test_effective_api_tool_state_requires_service_credentials(self):
        from flocks.server.routes.tool import _get_effective_tool_enabled

        tool_info = ToolInfo(
            name="threatbook_ip_query",
            description="Query threat intelligence",
            category=ToolCategory.CUSTOM,
            enabled=True,
            source="api",
            provider="threatbook_api",
        )

        with patch("flocks.server.routes.provider._get_api_service_enabled", return_value=False):
            assert _get_effective_tool_enabled(tool_info) is False

    def test_effective_api_tool_state_keeps_tool_level_disable(self):
        from flocks.server.routes.tool import _get_effective_tool_enabled, _build_tool_response

        tool_info = ToolInfo(
            name="threatbook_ip_query",
            description="Query threat intelligence",
            category=ToolCategory.CUSTOM,
            enabled=False,
            source="api",
            provider="threatbook_api",
        )

        with patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"enabled": True},
        ):
            assert _get_effective_tool_enabled(tool_info) is False
            response = _build_tool_response(tool_info)

        assert tool_info.enabled is False
        assert response.enabled is False

    @pytest.mark.asyncio
    async def test_update_tool_allows_api_tool_toggle(self):
        from flocks.server.routes.tool import ToolUpdateRequest, update_tool

        tool = MagicMock()
        tool.info = ToolInfo(
            name="threatbook_ip_query",
            description="Query threat intelligence",
            category=ToolCategory.CUSTOM,
            enabled=True,
            source="api",
            provider="threatbook_api",
        )

        with (
            patch("flocks.server.routes.tool.ToolRegistry.init"),
            patch("flocks.server.routes.tool.ToolRegistry.get", return_value=tool),
            patch("flocks.tool.tool_loader.find_yaml_tool", return_value=None),
            patch("flocks.server.routes.tool._build_tool_response") as mock_build,
        ):
            mock_build.return_value = MagicMock()
            await update_tool("threatbook_ip_query", ToolUpdateRequest(enabled=False))
            assert tool.info.enabled is False

    @pytest.mark.asyncio
    async def test_create_tool_auto_enables_provider_backed_api_service(self):
        from flocks.server.routes.tool import (
            CreateToolRequest,
            ToolInfoResponse,
            ToolRegistry,
            create_tool,
        )

        tool = MagicMock()
        tool.info = ToolInfo(
            name="threatbook_ip_query",
            description="Query threat intelligence",
            category=ToolCategory.CUSTOM,
            enabled=True,
            source="api",
            provider="threatbook_api",
        )
        response = ToolInfoResponse(
            name="threatbook_ip_query",
            description="Query threat intelligence",
            category="custom",
            source="api",
            source_name="threatbook_api",
            enabled=True,
            parameters=[],
            requires_confirmation=False,
        )

        with (
            patch("flocks.server.routes.tool.ToolRegistry.init"),
            patch("flocks.tool.tool_loader.create_yaml_tool", return_value="dummy.yaml"),
            patch("flocks.tool.tool_loader.yaml_to_tool", return_value=tool),
            patch("flocks.server.routes.tool.ToolRegistry.register"),
            patch.object(ToolRegistry, "_plugin_tool_names", []),
            patch("flocks.server.routes.tool._build_tool_response", return_value=response),
            patch(
                "flocks.server.routes.provider.update_api_service",
                new=AsyncMock(),
            ) as mock_update_api_service,
        ):
            result = await create_tool(
                CreateToolRequest(
                    name="threatbook_ip_query",
                    description="Query threat intelligence",
                    category="custom",
                    provider="threatbook_api",
                    enabled=True,
                    handler={
                        "type": "http",
                        "method": "GET",
                        "url": "https://example.com",
                    },
                )
            )

        assert result == response
        assert mock_update_api_service.await_count == 1
        args = mock_update_api_service.await_args.args
        assert args[0] == "threatbook_api"
        assert args[1].enabled is True

    @pytest.mark.asyncio
    async def test_create_tool_does_not_enable_service_when_explicitly_disabled(self):
        from flocks.server.routes.tool import CreateToolRequest, ToolInfoResponse, ToolRegistry, create_tool

        tool = MagicMock()
        tool.info = ToolInfo(
            name="threatbook_ip_query",
            description="Query threat intelligence",
            category=ToolCategory.CUSTOM,
            enabled=False,
            source="api",
            provider="threatbook_api",
        )
        response = ToolInfoResponse(
            name="threatbook_ip_query",
            description="Query threat intelligence",
            category="custom",
            source="api",
            source_name="threatbook_api",
            enabled=False,
            parameters=[],
            requires_confirmation=False,
        )

        with (
            patch("flocks.server.routes.tool.ToolRegistry.init"),
            patch("flocks.tool.tool_loader.create_yaml_tool", return_value="dummy.yaml"),
            patch("flocks.tool.tool_loader.yaml_to_tool", return_value=tool),
            patch("flocks.server.routes.tool.ToolRegistry.register"),
            patch.object(ToolRegistry, "_plugin_tool_names", []),
            patch("flocks.server.routes.tool._build_tool_response", return_value=response),
            patch(
                "flocks.server.routes.provider.update_api_service",
                new=AsyncMock(),
            ) as mock_update_api_service,
        ):
            result = await create_tool(
                CreateToolRequest(
                    name="threatbook_ip_query",
                    description="Query threat intelligence",
                    category="custom",
                    provider="threatbook_api",
                    enabled=False,
                    handler={
                        "type": "http",
                        "method": "GET",
                        "url": "https://example.com",
                    },
                )
            )

        assert result == response
        assert mock_update_api_service.await_count == 0


class TestProviderBackedApiServiceStatus:
    @pytest.mark.asyncio
    async def test_provider_test_updates_api_service_status_cache_when_configured(self):
        from flocks.server.routes.provider import test_provider_credentials

        provider = MagicMock()
        provider._config = None
        provider._base_url = None
        provider.configure = MagicMock()
        provider.chat = AsyncMock(return_value=MagicMock(content="Paris"))

        model = MagicMock()
        model.id = "test-model"

        mock_secrets = MagicMock()
        mock_secrets.get.side_effect = lambda key: "api-key"

        mock_config = MagicMock()

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch("flocks.config.config.Config.get", new_callable=AsyncMock, return_value=mock_config),
            patch("flocks.config.config_writer.ConfigWriter.get_api_service_raw", return_value={"apiKey": "{secret:threatbook_api_key}"}),
            patch("flocks.server.routes.provider.Provider") as mock_provider_cls,
            patch("flocks.server.routes.provider._save_api_service_status", new=AsyncMock()) as mock_save_status,
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = provider
            mock_provider_cls.list_models.return_value = [model]

            result = await test_provider_credentials("threatbook_api")

        assert result["success"] is True
        assert mock_save_status.await_count == 1
        saved_args = mock_save_status.await_args.args
        assert saved_args[0] == "threatbook_api"
        assert saved_args[1]["success"] is True

    @pytest.mark.asyncio
    async def test_skip_status_cache_update_for_disabled_api_service(self):
        from flocks.server.routes.provider import _save_api_service_status_if_configured

        with (
            patch("flocks.config.config_writer.ConfigWriter.get_api_service_raw", return_value={"enabled": False}),
            patch("flocks.server.routes.provider._save_api_service_status", new=AsyncMock()) as mock_save_status,
        ):
            await _save_api_service_status_if_configured("threatbook_api", {"success": True})

        assert mock_save_status.await_count == 0


class TestAPIServiceStatusCache:
    @pytest.mark.asyncio
    async def test_read_api_service_status_cache_ignores_expired_entries(self):
        from flocks.server.routes.provider import _read_api_service_status_cache

        with (
            patch("flocks.server.routes.provider.Storage.init", new=AsyncMock()),
            patch(
                "flocks.server.routes.provider.Storage.read",
                new=AsyncMock(return_value={
                    "checked_at": 1,
                    "statuses": {"threatbook_api": {"status": "connected"}},
                }),
            ),
        ):
            result = await _read_api_service_status_cache(max_age_seconds=60)

        assert result == {}


class TestProviderYamlMetadata:
    def test_load_api_service_metadata_uses_config_writer_raw_data(self):
        from flocks.server.routes.provider import _load_api_service_metadata_data

        with (
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={"enabled": False, "base_url": "https://api.example.test"},
            ),
            patch(
                "flocks.server.routes.provider._load_provider_yaml_metadata",
                return_value={
                    "name": "Example Service",
                    "description": "Example description",
                },
            ),
        ):
            metadata = _load_api_service_metadata_data("example_service")

        assert metadata is not None
        assert metadata["name"] == "Example Service"
        assert metadata["base_url"] == "https://api.example.test"
        assert metadata["enabled"] is False

    def test_load_provider_yaml_metadata_from_project_plugins(self, tmp_path, monkeypatch):
        from flocks.server.routes.provider import _load_provider_yaml_metadata

        provider_dir = tmp_path / ".flocks" / "plugins" / "tools" / "api" / "threatbook"
        provider_dir.mkdir(parents=True)
        (provider_dir / "_provider.yaml").write_text(yaml.safe_dump({
            "name": "ThreatBook",
            "service_id": "threatbook_api",
            "description": "Threat intelligence",
            "description_cn": "威胁情报",
        }), encoding="utf-8")
        (provider_dir / "threatbook_ip_query.yaml").write_text(yaml.safe_dump({
            "name": "threatbook_ip_query",
            "provider": "threatbook_api",
            "description": "Query IP threat intelligence",
            "handler": {"type": "script", "script_file": "threatbook.handler.py", "function": "ip_query"},
        }), encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        metadata = _load_provider_yaml_metadata("threatbook_api")

        assert metadata is not None
        assert metadata["name"] == "ThreatBook"
        assert metadata["description_cn"] == "威胁情报"
        assert metadata["apis"][0]["name"] == "threatbook_ip_query"

    @pytest.mark.asyncio
    async def test_get_api_service_metadata_returns_credential_schema(self):
        from flocks.server.routes.provider import get_api_service_metadata

        with patch(
            "flocks.server.routes.provider._load_api_service_metadata_data",
            return_value={
                "name": "Qingteng",
                "description": "Qingteng API service",
                "credential_fields": [
                    {"key": "base_url", "storage": "config", "config_key": "base_url", "input_type": "url"},
                    {"key": "username", "storage": "config", "config_key": "username"},
                    {"key": "password", "storage": "secret", "config_key": "password", "secret_id": "qingteng_password"},
                ],
            },
        ):
            result = await get_api_service_metadata("qingteng")

        assert result.name == "Qingteng"
        assert result.credential_schema is not None
        assert [field["key"] for field in result.credential_schema] == ["base_url", "username", "password"]
        assert result.credential_schema[2]["secret_id"] == "qingteng_password"


def _make_api_tool(name: str, provider: str, *, native: bool = False, enabled: bool = True) -> Tool:
    """Helper to create a minimal API tool for bootstrap tests."""
    async def _noop(ctx, **kw):
        pass
    return Tool(
        info=ToolInfo(
            name=name,
            description="test tool",
            category=ToolCategory.CUSTOM,
            source="api",
            provider=provider,
            native=native,
            enabled=enabled,
        ),
        handler=_noop,
    )


class TestBootstrapUserApiServices:
    """Tests for ToolRegistry._bootstrap_user_api_services()."""

    def _run_bootstrap(self, tools: dict, get_raw_side_effect):
        """Patch ToolRegistry._tools, run bootstrap, return mock_set."""
        with (
            patch.object(ToolRegistry, "_tools", tools),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                side_effect=get_raw_side_effect,
            ),
            patch(
                "flocks.config.config_writer.ConfigWriter.set_api_service",
            ) as mock_set,
        ):
            ToolRegistry._bootstrap_user_api_services()
        return mock_set

    def test_bootstrap_creates_enabled_entry_for_new_provider(self):
        """When api_services has no entry for the provider, bootstrap writes {enabled: True}."""
        tools = {"my_api_tool": _make_api_tool("my_api_tool", "my_service")}
        mock_set = self._run_bootstrap(tools, get_raw_side_effect=lambda _: None)
        mock_set.assert_called_once_with("my_service", {"enabled": True})

    def test_bootstrap_adds_enabled_true_when_key_missing(self):
        """When api_services.<provider> exists but lacks 'enabled', bootstrap adds enabled: True."""
        existing = {"apiKey": "{secret:key}", "base_url": "https://api.example.com"}
        tools = {"my_api_tool": _make_api_tool("my_api_tool", "my_service")}
        mock_set = self._run_bootstrap(
            tools,
            get_raw_side_effect=lambda _: existing.copy(),
        )
        mock_set.assert_called_once()
        written = mock_set.call_args.args[1]
        assert written["enabled"] is True
        assert written["apiKey"] == "{secret:key}"
        assert written["base_url"] == "https://api.example.com"

    def test_bootstrap_does_not_overwrite_explicit_disabled(self):
        """When api_services.<provider>.enabled is False, bootstrap leaves it untouched."""
        existing = {"enabled": False, "apiKey": "{secret:key}"}
        tools = {"my_api_tool": _make_api_tool("my_api_tool", "my_service")}
        mock_set = self._run_bootstrap(
            tools,
            get_raw_side_effect=lambda _: existing.copy(),
        )
        mock_set.assert_not_called()

    def test_bootstrap_skips_native_tools(self):
        """Native (project-level) tools should not trigger bootstrap."""
        tools = {"native_tool": _make_api_tool("native_tool", "native_svc", native=True)}
        mock_set = self._run_bootstrap(tools, get_raw_side_effect=lambda _: None)
        mock_set.assert_not_called()

    def test_bootstrap_then_sync_keeps_disabled_tool_off(self):
        """End-to-end: bootstrap respects explicit disabled, sync keeps tool disabled."""
        tool = _make_api_tool("my_api_tool", "my_service")
        tools = {"my_api_tool": tool}

        with (
            patch.object(ToolRegistry, "_tools", tools),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={"enabled": False},
            ),
            patch("flocks.config.config_writer.ConfigWriter.set_api_service") as mock_set,
            patch(
                "flocks.config.config_writer.ConfigWriter.list_api_services_raw",
                return_value={"my_service": {"enabled": False}},
            ),
        ):
            ToolRegistry._bootstrap_user_api_services()
            ToolRegistry._sync_api_service_states()

        mock_set.assert_not_called()
        assert tool.info.enabled is False
