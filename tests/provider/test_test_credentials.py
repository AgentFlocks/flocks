"""
Tests for the test-credentials endpoint logic.

Verifies that:
1. Invalid API keys are NOT reported as "success".
2. Missing tools path returns failure, not success.
3. Valid API keys with successful tool execution return success.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flocks.tool.registry import ToolResult, ToolInfo, ToolCategory, ToolParameter, ParameterType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_info(name: str):
    """Create a minimal ToolInfo for testing."""
    return ToolInfo(
        name=name,
        description=f"Test tool {name}",
        category=ToolCategory.CUSTOM,
        parameters=[
            ToolParameter(
                name="ip",
                type=ParameterType.STRING,
                description="IP address",
                required=True,
            )
        ],
    )


# Patch targets: these imports happen inside the test_provider_credentials
# function body, so we patch at the source module level.
_PATCH_SECRET_MGR = "flocks.security.get_secret_manager"
_PATCH_PROVIDER = "flocks.server.routes.provider.Provider"
_PATCH_TOOL_REGISTRY = "flocks.tool.registry.ToolRegistry"
_PATCH_CONFIG_GET = "flocks.config.config.Config.get"
_PATCH_CONFIG_RAW = "flocks.config.config_writer.ConfigWriter.get_provider_raw"
_PATCH_TOOL_SOURCE = "flocks.server.routes.tool._get_tool_source"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTestCredentialsNoToolsPath:
    """When no service tools are found, the endpoint should return failure."""

    @pytest.mark.asyncio
    async def test_no_tools_returns_failure(self):
        """If no enabled tools match the service, success must be False."""
        from flocks.server.routes.provider import test_provider_credentials

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "fake-api-key"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "threatbook_api")),
        ):
            # Setup: no registered provider (API service path)
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            # Setup: no tools match
            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = []
            mock_tr._dynamic_tools_by_module = {}

            result = await test_provider_credentials("threatbook_api")

            assert result["success"] is False, (
                f"Expected failure when no service tools found, got: {result}"
            )


class TestTestCredentialsToolExecution:
    """When tools are found, the endpoint should execute them and check results."""

    @pytest.mark.asyncio
    async def test_tool_failure_returns_failure(self):
        """If the tool returns success=False, the test should report failure."""
        from flocks.server.routes.provider import test_provider_credentials

        tool_info = _make_tool_info("threatbook_ip_query")

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "bad-api-key"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "threatbook_api")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [tool_info]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.threatbook": ["threatbook_ip_query"],
            }
            mock_tr.execute = AsyncMock(return_value=ToolResult(
                success=False,
                error="ThreatBook API error: invalid apikey",
            ))

            result = await test_provider_credentials("threatbook_api")

            assert result["success"] is False, (
                f"Expected failure for invalid API key, got: {result}"
            )

    @pytest.mark.asyncio
    async def test_tool_success_returns_success(self):
        """If the tool returns success=True, the test should report success."""
        from flocks.server.routes.provider import test_provider_credentials

        tool_info = _make_tool_info("threatbook_ip_query")

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "valid-api-key"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "threatbook_api")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [tool_info]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.threatbook": ["threatbook_ip_query"],
            }
            mock_tr.execute = AsyncMock(return_value=ToolResult(
                success=True,
                output={"ip": "8.8.8.8", "severity": "info"},
            ))

            result = await test_provider_credentials("threatbook_api")

            assert result["success"] is True, (
                f"Expected success for valid API key, got: {result}"
            )

    @pytest.mark.asyncio
    async def test_service_prefers_lightweight_query_tool_over_file_upload(self):
        """Connectivity checks should avoid file/upload tools when a query tool exists."""
        from flocks.server.routes.provider import test_provider_credentials

        url_tool = ToolInfo(
            name="threatbook_cn_url_scan",
            description="URL scan",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="url",
                    type=ParameterType.STRING,
                    description="URL",
                    required=True,
                )
            ],
        )
        upload_tool = ToolInfo(
            name="threatbook_cn_file_upload",
            description="File upload",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="file_path",
                    type=ParameterType.STRING,
                    description="File path",
                    required=True,
                )
            ],
        )

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "valid-api-key"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "threatbook-cn")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            # Put upload first on purpose to prove sorting is stable.
            mock_tr.list_tools.return_value = [upload_tool, url_tool]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.threatbook_cn": [
                    "threatbook_cn_file_upload",
                    "threatbook_cn_url_scan",
                ],
            }
            mock_tr.execute = AsyncMock(return_value=ToolResult(
                success=True,
                output={"ok": True},
            ))

            result = await test_provider_credentials("threatbook-cn")

            assert result["success"] is True, result
            assert result["tool_tested"] == "threatbook_cn_url_scan"
            mock_tr.execute.assert_awaited_once_with(
                tool_name="threatbook_cn_url_scan",
                url="https://example.com",
            )

    @pytest.mark.asyncio
    async def test_service_uses_enum_action_instead_of_placeholder_string(self):
        """Connectivity checks should use enum-backed action values, not the generic 'test' placeholder."""
        from flocks.server.routes.provider import test_provider_credentials

        onesec_dns_tool = ToolInfo(
            name="onesec_dns",
            description="OneSEC DNS grouped tool",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="action",
                    type=ParameterType.STRING,
                    description="DNS action",
                    required=True,
                    enum=[
                        "dns_search_blocked_queries",
                        "dns_get_public_ip_list",
                        "dns_get_all_destination_list",
                    ],
                )
            ],
        )

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "valid-api-key"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "onesec_api")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [onesec_dns_tool]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.onesec": ["onesec_dns"],
            }
            mock_tr.execute = AsyncMock(return_value=ToolResult(
                success=True,
                output={"items": []},
            ))

            result = await test_provider_credentials("onesec_api")

            assert result["success"] is True, result
            assert result["tool_tested"] == "onesec_dns"
            mock_tr.execute.assert_awaited_once_with(
                tool_name="onesec_dns",
                action="dns_get_public_ip_list",
            )

    @pytest.mark.asyncio
    async def test_service_prefers_login_probe_over_action_dispatch_tool(self):
        """When a parameter-free login tool exists (e.g. qingteng_login), it
        should be tried before action-dispatch tools whose handler-side validation
        requires extra fields beyond the JSON schema (e.g. qingteng_assets which
        needs `resource` + `os_type` for `assets.refresh`).
        """
        from flocks.server.routes.provider import test_provider_credentials

        login_tool = ToolInfo(
            name="qingteng_login",
            description="Qingteng login probe",
            category=ToolCategory.CUSTOM,
            parameters=[],
            requires_confirmation=False,
        )
        assets_tool = ToolInfo(
            name="qingteng_assets",
            description="Qingteng assets dispatch",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="action",
                    type=ParameterType.STRING,
                    description="Asset action",
                    required=True,
                    enum=["list", "refresh", "refresh_status", "delete_host"],
                )
            ],
            requires_confirmation=True,
        )

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "valid-creds"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "qingteng")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [assets_tool, login_tool]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.qingteng": ["qingteng_assets", "qingteng_login"],
            }
            mock_tr.execute = AsyncMock(return_value=ToolResult(
                success=True,
                output={"jwt": "fake", "signKey": "fake", "comId": "1"},
            ))

            result = await test_provider_credentials("qingteng")

            assert result["success"] is True, result
            assert result["tool_tested"] == "qingteng_login"
            mock_tr.execute.assert_awaited_once_with(tool_name="qingteng_login")

    @pytest.mark.asyncio
    async def test_login_probe_does_not_overmatch_business_tools(self):
        """`_is_login_probe` must only match dedicated probes — not arbitrary
        business tools whose names happen to contain ``login`` (e.g. TDP's
        ``tdp_login_api_list`` / ``tdp_login_weakpwd_list``). Otherwise we
        would prioritise an expensive query endpoint over the cheaper
        connectivity-style tool that the sort key normally prefers.
        """
        from flocks.server.routes.provider import test_provider_credentials

        login_business_tool = ToolInfo(
            name="tdp_login_api_list",
            description="TDP login entry list (business query, NOT a probe)",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="action",
                    type=ParameterType.STRING,
                    description="sub-action",
                    required=False,
                    enum=["summary", "category", "list"],
                )
            ],
            requires_confirmation=False,
        )
        ip_query_tool = ToolInfo(
            name="tdp_ip_query",
            description="TDP IP query (preferred lightweight check)",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="ip",
                    type=ParameterType.STRING,
                    description="IP address",
                    required=False,
                )
            ],
            requires_confirmation=False,
        )

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "valid-creds"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "tdp_api")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [login_business_tool, ip_query_tool]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.tdp_api": [
                    "tdp_login_api_list",
                    "tdp_ip_query",
                ],
            }
            mock_tr.execute = AsyncMock(return_value=ToolResult(
                success=True,
                output={"items": []},
            ))

            result = await test_provider_credentials("tdp_api")

            assert result["success"] is True, result
            assert result["tool_tested"] == "tdp_ip_query", (
                "Expected the lightweight ip_query tool to be picked, but "
                "got %r. This usually means _is_login_probe is doing a loose "
                "substring match and mis-classifying tdp_login_api_list as a "
                "probe." % result.get("tool_tested")
            )
            executed_tool = mock_tr.execute.await_args.kwargs["tool_name"]
            assert executed_tool == "tdp_ip_query", (
                "Only the lightweight ip_query tool should have been awaited; "
                "got %r." % executed_tool
            )

    @pytest.mark.asyncio
    async def test_single_attempt_only_to_avoid_account_lockout(self):
        """Connectivity test must probe the service **at most once**.

        Some API services (OneSIG / OneSec / Qingteng / ...) lock the
        account after a small number of consecutive failed logins. The
        old behaviour iterated up to ``service_tools[:5] × _build_param_sets``
        which could fire ~30 login attempts on a single bad-credential test
        and trip the server-side lockout. The current contract is exactly
        one tool, exactly one parameter set per click.
        """
        from flocks.server.routes.provider import test_provider_credentials

        assets_tool = ToolInfo(
            name="qingteng_assets",
            description="Qingteng assets dispatch",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="action",
                    type=ParameterType.STRING,
                    description="Asset action",
                    required=True,
                    enum=["list", "refresh"],
                )
            ],
            requires_confirmation=True,
        )

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "valid-creds"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "qingteng")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [assets_tool]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.qingteng": ["qingteng_assets"],
            }

            mock_tr.execute = AsyncMock(return_value=ToolResult(
                success=False,
                error="Missing required parameters for assets.list: resource, os_type",
            ))

            result = await test_provider_credentials("qingteng")

            assert result["success"] is False, result
            assert mock_tr.execute.await_count == 1, (
                "Expected exactly one execute() call to avoid account lockout, "
                f"got {mock_tr.execute.await_count}"
            )
            assert "attempts" in result, result
            assert len(result["attempts"]) == 1, result["attempts"]
            assert result["tool_tested"] == "qingteng_assets"
            assert "为避免连续失败导致账号锁定" in result["message"], result["message"]

    @pytest.mark.asyncio
    async def test_action_dispatch_login_tool_uses_test_action(self):
        """For services whose login tool is action-dispatch (e.g.
        ``onesig_login``, ``onesec_login``, ``onesig_v2_5_older_login``)
        — i.e. the name ends in ``_login`` and ``action`` is a required
        enum parameter — the connectivity probe must be invoked with
        ``action="test"``.

        Why: handlers like ``flocks/.flocks/plugins/tools/api/onesig/onesig.handler.py``
        special-case ``action="test"`` in ``_dispatch_group`` and route to
        ``_CONNECTIVITY_TEST_ACTIONS[group]`` — a single read-only call
        (e.g. ``get_account``). Picking any other enum value (``login``,
        ``logout``, ``change_password``, ``get_pubkey``, ...) would either
        force a fresh login round-trip or invoke a sensitive write path,
        both of which are unsafe for a connectivity test.
        """
        from flocks.server.routes.provider import test_provider_credentials

        login_tool = ToolInfo(
            name="onesig_login",
            description="OneSIG action-dispatch login tool",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="action",
                    type=ParameterType.STRING,
                    description="Login subaction",
                    required=True,
                    enum=[
                        "login",
                        "logout",
                        "change_password",
                        "get_pubkey",
                        "get_account",
                        "get_captcha",
                    ],
                )
            ],
            requires_confirmation=False,
        )
        assets_tool = ToolInfo(
            name="onesig_assets",
            description="OneSIG assets dispatch",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="action",
                    type=ParameterType.STRING,
                    description="Asset action",
                    required=True,
                    enum=["asset_list", "asset_create", "asset_delete"],
                )
            ],
            requires_confirmation=True,
        )

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "valid-creds"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "onesig_api")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            # Put the assets tool first to prove the sort ranking promotes
            # the `_login` action-dispatch tool above other groups.
            mock_tr.list_tools.return_value = [assets_tool, login_tool]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.onesig": ["onesig_assets", "onesig_login"],
            }
            mock_tr.execute = AsyncMock(return_value=ToolResult(
                success=True,
                output={"username": "admin"},
            ))

            result = await test_provider_credentials("onesig_api")

            assert result["success"] is True, result
            assert result["tool_tested"] == "onesig_login"
            assert mock_tr.execute.await_count == 1
            mock_tr.execute.assert_awaited_once_with(
                tool_name="onesig_login",
                action="test",
            )

    @pytest.mark.asyncio
    async def test_service_metadata_secret_is_used_for_hyphenated_service(self):
        """API services should prefer metadata-defined secret ids over provider_id defaults."""
        from flocks.server.routes.provider import test_provider_credentials

        tool_info = _make_tool_info("threatbook_ip_query")

        mock_secrets = MagicMock()
        mock_secrets.get.side_effect = lambda key: {
            "threatbook_cn_api_key": "valid-api-key",
            "threatbook-cn_api_key": None,
        }.get(key)

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch("flocks.config.config_writer.ConfigWriter.get_api_service_raw", return_value={}),
            patch(
                "flocks.server.routes.provider._load_api_service_metadata_data",
                return_value={"auth": {"secret": "threatbook_cn_api_key"}},
            ),
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "threatbook-cn")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [tool_info]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.threatbook_cn": ["threatbook_ip_query"],
            }
            mock_tr.execute = AsyncMock(return_value=ToolResult(
                success=True,
                output={"ip": "8.8.8.8", "severity": "info"},
            ))

            result = await test_provider_credentials("threatbook-cn")

            assert result["success"] is True, (
                f"Expected success when metadata secret exists, got: {result}"
            )

    @pytest.mark.asyncio
    async def test_tool_exception_returns_failure(self):
        """If the tool execution raises, the test should report failure."""
        from flocks.server.routes.provider import test_provider_credentials

        tool_info = _make_tool_info("threatbook_ip_query")

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "bad-api-key"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "threatbook_api")),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None

            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [tool_info]
            mock_tr._dynamic_tools_by_module = {
                "flocks.tool.generated.threatbook": ["threatbook_ip_query"],
            }
            mock_tr.execute = AsyncMock(side_effect=Exception("Connection refused"))

            result = await test_provider_credentials("threatbook_api")

            assert result["success"] is False, (
                f"Expected failure when tool raises exception, got: {result}"
            )


class TestTestCredentialsNoCredentials:
    """When no credentials are stored, should return failure."""

    @pytest.mark.asyncio
    async def test_no_credentials_returns_failure(self):
        from flocks.server.routes.provider import test_provider_credentials

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = None

        with patch(_PATCH_SECRET_MGR, return_value=mock_secrets):
            result = await test_provider_credentials("threatbook_api")

            assert result["success"] is False, (
                f"Expected failure when no credentials, got: {result}"
            )


class TestTestCredentialsInlineConfigFallback:
    """Inline flocks.json apiKey values should work when no secret exists."""

    @pytest.mark.asyncio
    async def test_inline_config_api_key_is_used_for_provider_test(self):
        from flocks.server.routes.provider import test_provider_credentials

        provider = MagicMock()
        provider.is_configured.return_value = False
        provider.chat = AsyncMock(return_value=MagicMock(content="Paris"))

        model = MagicMock()
        model.id = "qianfan-code-latest"

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = None

        mock_config = MagicMock()

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_CONFIG_RAW, return_value={
                "options": {"apiKey": "inline-qianfan-key"},
            }),
            patch(_PATCH_CONFIG_GET, new_callable=AsyncMock, return_value=mock_config),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = provider
            mock_provider_cls.list_models.return_value = [model]

            result = await test_provider_credentials("qianfan")

            assert result["success"] is True, result
            provider.configure.assert_called_once()
            configured = provider.configure.call_args.args[0]
            assert configured.api_key == "inline-qianfan-key"

    @pytest.mark.asyncio
    async def test_existing_custom_settings_are_preserved_during_provider_test(self):
        from flocks.server.routes.provider import test_provider_credentials

        provider = MagicMock()
        provider._config = MagicMock(
            custom_settings={"verify_ssl": False},
            base_url="https://gateway.internal/v1",
        )
        provider.chat = AsyncMock(return_value=MagicMock(content="Paris"))

        model = MagicMock()
        model.id = "gateway-model"

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "gateway-api-key"

        mock_config = MagicMock()

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_CONFIG_GET, new_callable=AsyncMock, return_value=mock_config),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls._load_dynamic_providers = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = provider
            mock_provider_cls.list_models.return_value = [model]

            result = await test_provider_credentials("internal-openai")

            assert result["success"] is True, result
            provider.configure.assert_called_once()
            configured = provider.configure.call_args.args[0]
            assert configured.api_key == "gateway-api-key"
            assert configured.base_url == "https://gateway.internal/v1"
            assert configured.custom_settings["verify_ssl"] is False
