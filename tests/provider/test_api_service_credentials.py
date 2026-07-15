from unittest.mock import MagicMock, patch

import pytest


class TestAPIServiceCredentials:
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
        assert result.api_key == "skyeye-login-key"
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
        assert result.api_key == "tdp-api-key"
        assert result.secret == "tdp-secret"
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
        assert result.api_key == "onesec-api-key"
        assert result.secret == "onesec-secret"
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
            "password": "qt-secret",
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
