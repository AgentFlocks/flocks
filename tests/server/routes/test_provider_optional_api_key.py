"""Tests for optional API key handling on OpenAI-compatible / custom providers.

When ``set_provider_credentials`` is called with an empty/missing api_key:

  * For ``openai-compatible`` and ``custom-*`` provider IDs we accept the
    request and persist a ``not-needed`` placeholder so downstream OpenAI SDK
    clients keep constructing.
  * For all other provider IDs we still reject with HTTP 400.

These tests stub out ``SecretManager`` and ``ConfigWriter`` so they never
touch the user's real ``~/.flocks/config`` directory.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from flocks.server.routes import provider as provider_routes


@pytest.fixture
def patched_runtime(monkeypatch: pytest.MonkeyPatch):
    """Stub out the secret store, config writer, and runtime provider lookup
    so credentials calls remain hermetic and never write to disk.
    """
    fake_secrets = MagicMock()
    runtime_provider = MagicMock()
    runtime_provider._client = None

    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: fake_secrets)
    monkeypatch.setattr(provider_routes.Provider, "_ensure_initialized", MagicMock())
    monkeypatch.setattr(provider_routes.Provider, "get", lambda _pid: runtime_provider)

    # Pretend the provider already exists in flocks.json so set_provider_credentials
    # follows the "update existing" path and never calls add_provider() with real
    # disk writes.
    monkeypatch.setattr(
        provider_routes.ConfigWriter,
        "get_provider_raw",
        lambda pid: {"options": {"baseURL": "http://existing/v1"}},
    )
    monkeypatch.setattr(
        provider_routes.ConfigWriter,
        "update_provider_field",
        MagicMock(),
    )
    monkeypatch.setattr(
        provider_routes.ConfigWriter,
        "add_provider",
        MagicMock(),
    )

    monkeypatch.setattr(
        provider_routes,
        "_get_provider_custom_settings",
        lambda _provider: {},
    )

    return {"secrets": fake_secrets, "provider": runtime_provider}


class TestOptionalApiKey:
    @pytest.mark.asyncio
    async def test_openai_compatible_accepts_empty_api_key(self, patched_runtime):
        """openai-compatible: empty api_key -> success, placeholder persisted."""
        result = await provider_routes.set_provider_credentials(
            "openai-compatible",
            provider_routes.ProviderCredentialRequest(
                api_key="",
                base_url="http://internal-gateway.example.com/v1",
            ),
        )

        assert result["success"] is True
        patched_runtime["secrets"].set.assert_called_once_with(
            "openai-compatible_llm_key", "not-needed"
        )

        configure_call = patched_runtime["provider"].configure.call_args
        assert configure_call is not None
        config_arg = configure_call.args[0]
        assert config_arg.api_key == "not-needed"
        assert config_arg.base_url == "http://internal-gateway.example.com/v1"

    @pytest.mark.asyncio
    async def test_custom_provider_accepts_missing_api_key(self, patched_runtime):
        """custom-*: omitted api_key -> success, placeholder persisted."""
        result = await provider_routes.set_provider_credentials(
            "custom-vllm-internal",
            provider_routes.ProviderCredentialRequest(
                base_url="http://vllm.internal/v1",
            ),
        )

        assert result["success"] is True
        patched_runtime["secrets"].set.assert_called_once_with(
            "custom-vllm-internal_llm_key", "not-needed"
        )

    @pytest.mark.asyncio
    async def test_openai_compatible_whitespace_only_treated_as_empty(
        self, patched_runtime
    ):
        """Whitespace-only api_key on optional providers falls back to placeholder."""
        result = await provider_routes.set_provider_credentials(
            "openai-compatible",
            provider_routes.ProviderCredentialRequest(api_key="   \t\n  "),
        )

        assert result["success"] is True
        patched_runtime["secrets"].set.assert_called_once_with(
            "openai-compatible_llm_key", "not-needed"
        )

    @pytest.mark.asyncio
    async def test_strict_provider_still_rejects_empty_api_key(self, patched_runtime):
        """OpenAI / Anthropic / etc. continue to require an explicit api_key."""
        with pytest.raises(HTTPException) as excinfo:
            await provider_routes.set_provider_credentials(
                "openai",
                provider_routes.ProviderCredentialRequest(api_key=""),
            )
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail == "API key required"
        patched_runtime["secrets"].set.assert_not_called()

    @pytest.mark.asyncio
    async def test_explicit_api_key_for_optional_provider_is_persisted(
        self, patched_runtime
    ):
        """Optional providers still honor a user-supplied key when given."""
        result = await provider_routes.set_provider_credentials(
            "custom-azure-mirror",
            provider_routes.ProviderCredentialRequest(
                api_key="sk-real-secret-XYZ",
                base_url="https://mirror.example.com/v1",
            ),
        )

        assert result["success"] is True
        patched_runtime["secrets"].set.assert_called_once_with(
            "custom-azure-mirror_llm_key", "sk-real-secret-XYZ"
        )

        configure_call = patched_runtime["provider"].configure.call_args
        assert configure_call.args[0].api_key == "sk-real-secret-XYZ"


class TestDynamicOpenAIProviderNoAuth:
    """The dynamic OpenAI-compatible provider must build a client even when
    no API key is configured (covers internal-gateway scenario at runtime)."""

    def test_get_client_uses_placeholder_when_allowed(self, monkeypatch):
        from flocks.provider.sdk.openai_base import OpenAIBaseProvider

        class _NoAuthProvider(OpenAIBaseProvider):
            DEFAULT_BASE_URL = "http://gateway.local/v1"
            ENV_API_KEY = ["NO_AUTH_PROVIDER_API_KEY"]
            ENV_BASE_URL = "NO_AUTH_PROVIDER_BASE_URL"
            CATALOG_ID = ""
            ALLOW_NO_API_KEY = True

            def __init__(self):
                super().__init__(provider_id="no-auth", name="No Auth")

        monkeypatch.delenv("NO_AUTH_PROVIDER_API_KEY", raising=False)

        provider = _NoAuthProvider()
        provider._api_key = None  # simulate "user never provided a key"

        captured: dict = {}

        class _FakeAsyncOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)

        client = provider._get_client()

        assert client is not None
        assert captured["api_key"] == _NoAuthProvider.NO_API_KEY_PLACEHOLDER

    def test_get_client_still_raises_when_not_allowed(self, monkeypatch):
        from flocks.provider.sdk.openai_base import OpenAIBaseProvider

        class _StrictProvider(OpenAIBaseProvider):
            DEFAULT_BASE_URL = "https://api.example.com/v1"
            ENV_API_KEY = ["STRICT_PROVIDER_API_KEY"]
            ENV_BASE_URL = "STRICT_PROVIDER_BASE_URL"
            CATALOG_ID = ""
            ALLOW_NO_API_KEY = False

            def __init__(self):
                super().__init__(provider_id="strict", name="Strict")

        monkeypatch.delenv("STRICT_PROVIDER_API_KEY", raising=False)

        provider = _StrictProvider()
        provider._api_key = None

        with pytest.raises(ValueError, match="API key not configured"):
            provider._get_client()


class TestOpenAICompatibleIsConfigured:
    """``OpenAICompatibleProvider.is_configured()`` should treat a configured
    base URL as sufficient — no API key required for self-hosted endpoints.
    """

    def test_is_configured_with_only_base_url(self):
        from flocks.provider.provider import ProviderConfig
        from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider()
        provider.configure(
            ProviderConfig(
                provider_id="openai-compatible",
                api_key=None,
                base_url="http://localhost:8000/v1",
            )
        )

        assert provider.is_configured() is True

    def test_is_configured_false_when_both_blank(self):
        from flocks.provider.provider import ProviderConfig
        from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider()
        provider._api_key = ""
        provider._base_url = ""
        provider.configure(
            ProviderConfig(
                provider_id="openai-compatible",
                api_key="",
                base_url="",
            )
        )

        assert provider.is_configured() is False

    def test_is_configured_false_before_configure_called(self):
        """Regression: constructor seeds ``_api_key='not-needed'`` and
        ``_base_url='http://localhost:11434/v1'`` from env defaults. We must
        NOT report the provider as configured purely on those defaults — the
        user has to explicitly run through configure() first.
        """
        from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider()
        # _config is None — never configured
        assert provider._config is None
        assert provider._api_key == "not-needed"
        assert provider._base_url  # populated from env default
        assert provider.is_configured() is False

    def test_cherry_inherits_is_configured_override(self):
        """``CherryProvider`` (and the dynamic Cherry variant) should pick up
        the new ``is_configured`` semantics by inheritance — no duplicate
        override required.
        """
        from flocks.provider.sdk.cherry import CherryProvider
        from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider

        assert CherryProvider.is_configured is OpenAICompatibleProvider.is_configured


class TestGetCredentialsHidesPlaceholder:
    """When the saved value is the ``not-needed`` sentinel the GET endpoint
    must mask it as ``None`` so the WebUI doesn't pre-fill the API Key input
    with that internal placeholder string.
    """

    @pytest.mark.asyncio
    async def test_placeholder_is_hidden_but_has_credential_true(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        fake_secrets = MagicMock()
        fake_secrets.get.side_effect = lambda key: {
            "openai-compatible_llm_key": "not-needed",
        }.get(key)

        monkeypatch.setattr(
            "flocks.security.get_secret_manager", lambda: fake_secrets
        )
        monkeypatch.setattr(
            provider_routes.ConfigWriter,
            "get_provider_raw",
            lambda pid: {"options": {"baseURL": "http://internal/v1"}},
        )

        response = await provider_routes.get_provider_credentials("openai-compatible")

        assert response.api_key is None, (
            "Internal sentinel must NOT be exposed to the UI verbatim."
        )
        assert response.api_key_masked is None
        assert response.has_credential is True, (
            "A credential record exists; UI should know that, just not see the literal value."
        )
        assert response.secret_id == "openai-compatible_llm_key"
        assert response.base_url == "http://internal/v1"

    @pytest.mark.asyncio
    async def test_real_api_key_still_returned_and_masked(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        fake_secrets = MagicMock()
        fake_secrets.get.side_effect = lambda key: {
            "openai_llm_key": "sk-real-secret-1234567890",
        }.get(key)

        monkeypatch.setattr(
            "flocks.security.get_secret_manager", lambda: fake_secrets
        )
        monkeypatch.setattr(
            provider_routes.ConfigWriter,
            "get_provider_raw",
            lambda pid: None,
        )

        response = await provider_routes.get_provider_credentials("openai")

        assert response.api_key == "sk-real-secret-1234567890"
        assert response.api_key_masked is not None
        assert response.api_key_masked != "sk-real-secret-1234567890"
        assert response.has_credential is True

    @pytest.mark.asyncio
    async def test_inline_placeholder_in_flocks_json_also_hidden(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Defense-in-depth: even if a hand-edited flocks.json contains an
        inline ``apiKey: 'not-needed'``, the GET endpoint should still mask it.
        """
        fake_secrets = MagicMock()
        fake_secrets.get.return_value = None

        monkeypatch.setattr(
            "flocks.security.get_secret_manager", lambda: fake_secrets
        )
        monkeypatch.setattr(
            provider_routes.ConfigWriter,
            "get_provider_raw",
            lambda pid: {
                "options": {
                    "apiKey": "not-needed",
                    "baseURL": "http://internal/v1",
                }
            },
        )

        response = await provider_routes.get_provider_credentials("custom-internal")

        assert response.api_key is None
        assert response.api_key_masked is None
        assert response.has_credential is True
