from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.security.secrets import SecretManager


class TestAPIServiceCredentials:
    @pytest.mark.asyncio
    async def test_isolated_provider_probe_does_not_mutate_registered_instance(self):
        from flocks.provider.provider import ProviderConfig
        from flocks.server.routes import provider as provider_routes

        class FakeProvider:
            def __init__(self):
                self._config = ProviderConfig(
                    provider_id="openai",
                    api_key="saved-key",
                    base_url="https://saved.example/v1",
                )
                self._base_url = "https://default.example/v1"
                self._client = object()

            def configure(self, config):
                self._config = config
                self._client = None

            async def chat(self, *_args, **_kwargs):
                return SimpleNamespace(content="Paris")

        shared_provider = FakeProvider()
        original_client = shared_provider._client

        with (
            patch.object(provider_routes, "_ensure_provider_initialized", AsyncMock()),
            patch.object(provider_routes, "_load_dynamic_providers", AsyncMock()),
            patch.object(provider_routes.Provider, "get", return_value=shared_provider),
            patch.object(
                provider_routes.Provider,
                "list_models",
                return_value=[SimpleNamespace(id="gpt-test")],
            ),
            patch.object(provider_routes.Config, "get", side_effect=AssertionError("must not load config")),
        ):
            result = await provider_routes._test_provider_credentials_impl(
                "openai",
                provider_routes.TestCredentialRequest(model_id="gpt-test"),
                api_key_override="candidate-key",
                isolated_provider=True,
                base_url_override="https://candidate.example/v1",
            )

        assert result["success"] is True
        assert shared_provider._config.api_key == "saved-key"
        assert shared_provider._config.base_url == "https://saved.example/v1"
        assert shared_provider._client is original_client

    @pytest.mark.asyncio
    async def test_temporary_api_probe_does_not_enable_tools_or_persist_status(self):
        from flocks.server.routes import provider as provider_routes
        from flocks.tool.credential_context import activate_credential_overrides
        from flocks.tool.registry import ToolCategory, ToolInfo, ToolResult

        tool_info = ToolInfo(
            name="threatbook_cn_probe",
            description="Read-only connectivity probe",
            category=ToolCategory.CUSTOM,
            parameters=[],
            enabled=False,
        )

        with (
            patch.object(provider_routes, "_ensure_provider_initialized", AsyncMock()),
            patch.object(provider_routes, "_load_dynamic_providers", AsyncMock()),
            patch.object(provider_routes.Provider, "get", return_value=None),
            patch("flocks.tool.registry.ToolRegistry.init_async", AsyncMock()),
            patch("flocks.tool.registry.ToolRegistry.list_tools", return_value=[tool_info]),
            patch(
                "flocks.tool.registry.ToolRegistry.get_dynamic_tools_by_module",
                return_value={},
            ),
            patch(
                "flocks.tool.registry.ToolRegistry.execute",
                AsyncMock(return_value=ToolResult(success=False, error="invalid key")),
            ) as execute,
            patch(
                "flocks.server.routes.tool._get_tool_source",
                return_value=("api", "threatbook-cn"),
            ),
            patch(
                "flocks.tool.probe_loader.get_connectivity_spec",
                return_value=None,
            ),
            patch.object(provider_routes, "_set_api_service_tools_enabled") as set_enabled,
            patch.object(provider_routes.Storage, "write", AsyncMock()) as write_status,
        ):
            async with activate_credential_overrides(
                secret_values={"threatbook_cn_api_key": "candidate-key"},
                service_id="threatbook-cn",
                config_values={"enabled": True},
            ):
                result = await provider_routes._test_provider_credentials_impl(
                    "threatbook-cn",
                    api_key_override="candidate-key",
                )

        assert result["success"] is False
        execute.assert_awaited_once()
        set_enabled.assert_not_called()
        write_status.assert_not_awaited()
        assert tool_info.enabled is False

    @pytest.mark.asyncio
    async def test_base_url_alone_is_not_a_configured_credential(self):
        from flocks.server.routes.provider import get_service_credentials

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = None

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={"base_url": "https://api.threatbook.cn"},
            ),
        ):
            result = await get_service_credentials("threatbook-cn")

        assert result.base_url == "https://api.threatbook.cn"
        assert result.api_key_masked is None
        assert result.has_credential is False

    @pytest.mark.asyncio
    async def test_get_service_credentials_returns_base_url_and_username(self):
        from flocks.server.routes.provider import get_service_credentials

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "skyeye-login-key"

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={
                    "apiKey": "{secret:skyeye_api_key}",
                    "base_url": "https://skyeye-domain/skyeye",
                    "username": "skyeye",
                },
            ),
        ):
            result = await get_service_credentials("skyeye_api")

        assert result.secret_id == "skyeye_api_key"
        assert result.api_key is None
        assert result.api_key_masked
        assert result.api_key_masked != "skyeye-login-key"
        assert result.base_url == "https://skyeye-domain/skyeye"
        assert result.username == "skyeye"
        assert result.has_credential is True

    @pytest.mark.asyncio
    async def test_set_service_credentials_persists_api_key_base_url_and_username(self):
        from flocks.server.routes.provider import ProviderCredentialRequest, set_service_credentials

        mock_secrets = MagicMock()

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={"enabled": True},
            ),
            patch("flocks.config.config_writer.ConfigWriter.set_api_service") as mock_set_api_service,
        ):
            result = await set_service_credentials(
                "skyeye_api",
                ProviderCredentialRequest(
                    api_key="wGxEg13pd27KbfsW",
                    base_url="https://skyeye-domain/skyeye",
                    username="skyeye",
                ),
            )

        mock_secrets.set.assert_called_once_with("skyeye_api_key", "wGxEg13pd27KbfsW")
        mock_set_api_service.assert_called_once_with(
            "skyeye_api",
            {
                "enabled": True,
                "apiKey": "{secret:skyeye_api_key}",
                "base_url": "https://skyeye-domain/skyeye",
                "username": "skyeye",
            },
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_configure_service_credentials_does_not_save_failed_key(self):
        from flocks.server.routes import provider as provider_routes

        validation = {"success": False, "message": "invalid key"}
        test_credentials = AsyncMock(return_value=validation)
        save_credentials = AsyncMock()

        with (
            patch.object(provider_routes, "_test_provider_credentials_impl", test_credentials),
            patch.object(provider_routes, "set_service_credentials", save_credentials),
            patch.object(
                provider_routes.ConfigWriter,
                "get_api_service_raw",
                return_value={"enabled": True},
            ),
        ):
            result = await provider_routes.configure_service_credentials(
                "threatbook-cn",
                provider_routes.ProviderCredentialRequest(api_key="bad-key"),
            )

        assert result == validation
        save_credentials.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_configure_service_credentials_saves_key_after_validation(self):
        from flocks.server.routes import provider as provider_routes

        validation = {"success": True, "message": "connected", "latency_ms": 12}
        test_credentials = AsyncMock(return_value=validation)
        save_credentials = AsyncMock(return_value={"success": True})
        request = provider_routes.ProviderCredentialRequest(api_key="valid-key")

        with (
            patch.object(provider_routes, "_test_provider_credentials_impl", test_credentials),
            patch.object(provider_routes, "set_service_credentials", save_credentials),
            patch.object(
                provider_routes.ConfigWriter,
                "get_api_service_raw",
                return_value={"enabled": True},
            ),
        ):
            result = await provider_routes.configure_service_credentials(
                "threatbook-cn",
                request,
            )

        assert result == validation
        test_credentials.assert_awaited_once()
        save_credentials.assert_awaited_once_with("threatbook-cn", request)

    @pytest.mark.asyncio
    async def test_set_service_credentials_uses_metadata_secret_for_hyphenated_service(self):
        from flocks.server.routes.provider import ProviderCredentialRequest, set_service_credentials

        mock_secrets = MagicMock()

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.server.routes.provider._load_api_service_metadata_data",
                return_value={"auth": {"secret": "threatbook_cn_api_key"}},
            ),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={"enabled": True},
            ),
            patch("flocks.config.config_writer.ConfigWriter.set_api_service") as mock_set_api_service,
        ):
            result = await set_service_credentials(
                "threatbook-cn",
                ProviderCredentialRequest(api_key="tb-key"),
            )

        mock_secrets.set.assert_called_once_with("threatbook_cn_api_key", "tb-key")
        mock_secrets.delete.assert_called_once_with("threatbook-cn_api_key")
        mock_set_api_service.assert_called_once_with(
            "threatbook-cn",
            {
                "enabled": True,
                "apiKey": "{secret:threatbook_cn_api_key}",
            },
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_set_service_credentials_can_update_base_url_and_username_only(self):
        from flocks.server.routes.provider import ProviderCredentialRequest, set_service_credentials

        mock_secrets = MagicMock()

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={
                    "apiKey": "{secret:skyeye_api_key}",
                    "base_url": "https://old.example.com/skyeye",
                    "username": "old-user",
                },
            ),
            patch("flocks.config.config_writer.ConfigWriter.set_api_service") as mock_set_api_service,
        ):
            result = await set_service_credentials(
                "skyeye_api",
                ProviderCredentialRequest(
                    base_url="https://skyeye-domain/skyeye",
                    username="skyeye",
                ),
            )

        mock_secrets.set.assert_not_called()
        mock_set_api_service.assert_called_once_with(
            "skyeye_api",
            {
                "apiKey": "{secret:skyeye_api_key}",
                "base_url": "https://skyeye-domain/skyeye",
                "username": "skyeye",
            },
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_get_service_credentials_returns_tdp_secret(self):
        from flocks.server.routes.provider import get_service_credentials

        mock_secrets = MagicMock()
        mock_secrets.get.side_effect = lambda key: {
            "tdp_api_key": "tdp-api-key",
            "tdp_secret": "tdp-secret",
        }.get(key)

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={
                    "apiKey": "{secret:tdp_api_key}",
                    "secret": "{secret:tdp_secret}",
                    "base_url": "https://tdp.example.com",
                },
            ),
        ):
            result = await get_service_credentials("tdp_api")

        assert result.secret_id == "tdp_api_key"
        assert result.api_key is None
        assert result.api_key_masked != "tdp-api-key"
        assert result.secret is None
        assert result.secret_masked != "tdp-secret"
        assert result.base_url == "https://tdp.example.com"
        assert result.has_credential is True

    @pytest.mark.asyncio
    async def test_set_service_credentials_persists_tdp_secret_separately(self):
        from flocks.server.routes.provider import ProviderCredentialRequest, set_service_credentials

        mock_secrets = MagicMock()

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={"enabled": True},
            ),
            patch(
                "flocks.server.routes.provider._load_api_service_metadata_data",
                return_value={"auth": {"secret": "tdp_api_key", "secret_secret": "tdp_secret"}},
            ),
            patch("flocks.config.config_writer.ConfigWriter.set_api_service") as mock_set_api_service,
        ):
            result = await set_service_credentials(
                "tdp_api",
                ProviderCredentialRequest(
                    api_key="tdp-api-key",
                    secret="tdp-secret",
                    base_url="https://tdp.example.com",
                ),
            )

        mock_secrets.set.assert_any_call("tdp_api_key", "tdp-api-key")
        mock_secrets.set.assert_any_call("tdp_secret", "tdp-secret")
        mock_set_api_service.assert_called_once_with(
            "tdp_api",
            {
                "enabled": True,
                "apiKey": "{secret:tdp_api_key}",
                "secret": "{secret:tdp_secret}",
                "base_url": "https://tdp.example.com",
            },
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_get_service_credentials_splits_legacy_onesec_combined_secret(self):
        from flocks.server.routes.provider import get_service_credentials

        mock_secrets = MagicMock()
        mock_secrets.get.side_effect = lambda key: {
            "onesec_credentials": "onesec-api-key|onesec-secret",
        }.get(key)

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={
                    "apiKey": "{secret:onesec_credentials}",
                    "base_url": "https://console.onesec.net",
                },
            ),
            patch(
                "flocks.server.routes.provider._load_api_service_metadata_data",
                return_value={"auth": {"secret": "onesec_api_key", "secret_secret": "onesec_secret"}},
            ),
        ):
            result = await get_service_credentials("onesec_api")

        assert result.secret_id == "onesec_credentials"
        assert result.api_key is None
        assert result.api_key_masked != "onesec-api-key"
        assert result.secret is None
        assert result.secret_masked != "onesec-secret"
        assert result.base_url == "https://console.onesec.net"
        assert result.has_credential is True

    @pytest.mark.asyncio
    async def test_set_service_credentials_persists_onesec_secret_separately(self):
        from flocks.server.routes.provider import ProviderCredentialRequest, set_service_credentials

        mock_secrets = MagicMock()
        mock_secrets.get.side_effect = lambda key: {
            "onesec_credentials": "legacy-api-key|legacy-secret",
        }.get(key)

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.server.routes.provider._load_api_service_metadata_data",
                return_value={"auth": {"secret": "onesec_api_key", "secret_secret": "onesec_secret"}},
            ),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={
                    "enabled": True,
                    "apiKey": "{secret:onesec_credentials}",
                },
            ),
            patch("flocks.config.config_writer.ConfigWriter.set_api_service") as mock_set_api_service,
        ):
            result = await set_service_credentials(
                "onesec_api",
                ProviderCredentialRequest(
                    api_key="onesec-api-key",
                    secret="onesec-secret",
                    base_url="https://console.onesec.net",
                ),
            )

        mock_secrets.set.assert_any_call("onesec_api_key", "onesec-api-key")
        mock_secrets.set.assert_any_call("onesec_secret", "onesec-secret")
        mock_secrets.delete.assert_any_call("onesec_api_secret")
        mock_secrets.delete.assert_any_call("onesec_credentials")
        mock_set_api_service.assert_called_once_with(
            "onesec_api",
            {
                "enabled": True,
                "apiKey": "{secret:onesec_api_key}",
                "secret": "{secret:onesec_secret}",
                "base_url": "https://console.onesec.net",
            },
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_get_service_credentials_returns_dynamic_qingteng_fields(self):
        from flocks.server.routes.provider import get_service_credentials

        mock_secrets = MagicMock()
        mock_secrets.get.side_effect = lambda key: {
            "qingteng_password": "qt-secret",
        }.get(key)

        metadata = {
            "credential_fields": [
                {"key": "base_url", "storage": "config", "config_key": "base_url"},
                {"key": "username", "storage": "config", "config_key": "username"},
                {"key": "password", "storage": "secret", "config_key": "password", "secret_id": "qingteng_password"},
            ]
        }

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={
                    "base_url": "https://qt.example.com:8443/openapi",
                    "username": "alice",
                    "password": "{secret:qingteng_password}",
                },
            ),
            patch("flocks.server.routes.provider._load_api_service_metadata_data", return_value=metadata),
        ):
            result = await get_service_credentials("qingteng")

        assert result.base_url == "https://qt.example.com:8443/openapi"
        assert result.username == "alice"
        assert result.fields == {
            "base_url": "https://qt.example.com:8443/openapi",
            "username": "alice",
            "password": SecretManager.mask("qt-secret"),
        }
        assert result.secret_ids == {"password": "qingteng_password"}
        assert result.has_credential is True

    @pytest.mark.asyncio
    async def test_set_service_credentials_persists_qingteng_password_reference(self):
        from flocks.server.routes.provider import ProviderCredentialRequest, set_service_credentials

        mock_secrets = MagicMock()
        metadata = {
            "credential_fields": [
                {"key": "base_url", "storage": "config", "config_key": "base_url"},
                {"key": "username", "storage": "config", "config_key": "username"},
                {"key": "password", "storage": "secret", "config_key": "password", "secret_id": "qingteng_password"},
            ]
        }

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={"enabled": True},
            ),
            patch("flocks.server.routes.provider._load_api_service_metadata_data", return_value=metadata),
            patch("flocks.config.config_writer.ConfigWriter.set_api_service") as mock_set_api_service,
        ):
            result = await set_service_credentials(
                "qingteng",
                ProviderCredentialRequest(
                    fields={
                        "base_url": "https://qt.example.com:8443/openapi",
                        "username": "alice",
                        "password": "qt-secret",
                    }
                ),
            )

        mock_secrets.set.assert_called_once_with("qingteng_password", "qt-secret")
        mock_set_api_service.assert_called_once_with(
            "qingteng",
            {
                "enabled": True,
                "base_url": "https://qt.example.com:8443/openapi",
                "username": "alice",
                "password": "{secret:qingteng_password}",
            },
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_set_service_credentials_keeps_fofa_compound_secret_canonical(self):
        from flocks.server.routes.provider import ProviderCredentialRequest, set_service_credentials

        mock_secrets = MagicMock()
        metadata = {
            "auth": {
                "secret": "fofa_key",
                "secret_secret": "fofa_email",
            },
            "compound_secret": {
                "canonical_secret": "fofa_key",
                "derived_secrets": {
                    "email": "fofa_email",
                    "api_key": "fofa_api_key",
                },
                "persist_secondary_secret": False,
            },
        }

        with (
            patch("flocks.security.get_secret_manager", return_value=mock_secrets),
            patch(
                "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
                return_value={"enabled": True},
            ),
            patch("flocks.server.routes.provider._load_api_service_metadata_data", return_value=metadata),
            patch("flocks.config.config_writer.ConfigWriter.set_api_service") as mock_set_api_service,
        ):
            result = await set_service_credentials(
                "fofa",
                ProviderCredentialRequest(api_key="analyst@example.com:fofa-api-key"),
            )

        mock_secrets.set.assert_called_once_with("fofa_key", "analyst@example.com:fofa-api-key")
        mock_set_api_service.assert_called_once_with(
            "fofa",
            {
                "enabled": True,
                "apiKey": "{secret:fofa_key}",
            },
        )
        assert result["success"] is True
