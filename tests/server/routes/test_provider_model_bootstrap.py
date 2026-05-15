from unittest.mock import MagicMock

import pytest

from flocks.config.config_writer import ConfigWriter
from flocks.provider.model_catalog import (
    get_provider_model_definitions,
    sync_catalog_models_to_config,
)
from flocks.server.routes import provider as provider_routes


class TestThreatBookProviderModelBootstrap:
    @pytest.mark.asyncio
    async def test_set_provider_credentials_bootstraps_kimi_k26_from_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        fake_secrets = MagicMock()
        runtime_provider = MagicMock()

        monkeypatch.setattr("flocks.security.get_secret_manager", lambda: fake_secrets)
        monkeypatch.setattr(provider_routes.Provider, "_ensure_initialized", MagicMock())
        monkeypatch.setattr(provider_routes.Provider, "get", lambda _provider_id: runtime_provider)

        result = await provider_routes.set_provider_credentials(
            "threatbook-cn-llm",
            provider_routes.ProviderCredentialRequest(api_key="tb-key"),
        )

        assert result["success"] is True

        raw = ConfigWriter.get_provider_raw("threatbook-cn-llm")
        assert raw is not None
        assert "kimi-k2.6" in raw["models"]
        assert raw["models"]["kimi-k2.6"]["name"] == "kimi-k2.6"
        fake_secrets.set.assert_called_once_with("threatbook-cn-llm_llm_key", "tb-key")
        runtime_provider.configure.assert_called_once()

    def test_sync_catalog_models_to_config_backfills_missing_kimi_k26(self):
        existing_models = {
            model.id: {"name": model.name}
            for model in get_provider_model_definitions("threatbook-cn-llm")
            if model.id != "kimi-k2.6"
        }
        assert "kimi-k2.6" not in existing_models

        ConfigWriter.add_provider(
            "threatbook-cn-llm",
            ConfigWriter.build_provider_config(
                "threatbook-cn-llm",
                npm="@ai-sdk/openai-compatible",
                base_url="https://llm.threatbook.cn/v1",
                models=existing_models,
            ),
        )

        added = sync_catalog_models_to_config()
        raw = ConfigWriter.get_provider_raw("threatbook-cn-llm")

        assert added == 1
        assert raw is not None
        assert "kimi-k2.6" in raw["models"]
        assert raw["models"]["kimi-k2.6"]["name"] == "kimi-k2.6"
