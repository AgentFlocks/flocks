from unittest.mock import MagicMock

import httpx
import pytest

from flocks.config.config_writer import ConfigWriter
from flocks.provider.model_catalog import (
    get_provider_model_definitions,
    sync_catalog_models_to_config,
)
from flocks.provider.provider import ModelInfo, ProviderConfig
from flocks.provider.sdk import threatbook
from flocks.provider.sdk.threatbook import ThreatBookCnLLMProvider
from flocks.server.routes import provider as provider_routes


class TestThreatBookProviderModelBootstrap:
    @pytest.mark.asyncio
    async def test_router_refresh_discovers_models_and_uses_first_tier_price(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        payload = {
            "code": 0,
            "msg": "ok",
            "data": [
                {
                    "model_name": "MiniMax-M3",
                    "input_price": 99,
                    "output_price": 99,
                    "price_tiers": [
                        {
                            "max_input_tokens": 512000,
                            "input_price": 4.2,
                            "output_price": 16.8,
                        },
                        {
                            "max_input_tokens": None,
                            "input_price": 8.4,
                            "output_price": 33.6,
                        },
                    ],
                },
                {
                    "model_name": "Qwen3.8-Max",
                    "input_price": 12,
                    "output_price": 36,
                },
                {
                    "model_name": "Router-New",
                    "input_price": 3,
                    "output_price": 7,
                },
            ],
        }
        requests = []

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, url, headers=None):
                requests.append((url, headers))
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", url),
                    json=payload,
                )

        monkeypatch.setattr(threatbook.httpx, "AsyncClient", FakeClient)
        provider = ThreatBookCnLLMProvider()
        provider.configure(ProviderConfig(
            provider_id=provider.id,
            api_key="test-key",
            base_url="https://router.example/v1",
            custom_settings={"trust_env": False},
        ))

        assert await provider.refresh_models(force=True) is True
        models = {model.id: model for model in provider.get_models()}
        assert set(models) == {"minimax-m3", "qwen3.8-max", "Router-New"}
        assert models["minimax-m3"].pricing == {
            "input": 4.2,
            "output": 16.8,
            "currency": "CNY",
        }
        assert models["qwen3.8-max"].pricing == {
            "input": 12.0,
            "output": 36.0,
            "currency": "CNY",
        }
        definitions = {model.id: model for model in provider.get_model_definitions()}
        assert definitions["qwen3.8-max"].capabilities.supports_tools is True
        assert definitions["qwen3.8-max"].capabilities.supports_vision is True
        assert definitions["qwen3.8-max"].limits.context_window == 1000000
        assert definitions["Router-New"].pricing.input == 3.0
        assert requests[0][0] == "https://router.example/v1/models"
        assert requests[0][1]["Authorization"] == "Bearer test-key"

    @pytest.mark.asyncio
    async def test_router_refresh_failure_keeps_config_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        class FailingClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, url, headers=None):
                return httpx.Response(
                    404,
                    request=httpx.Request("GET", url),
                    json={"error": {"message": "not found"}},
                )

        monkeypatch.setattr(threatbook.httpx, "AsyncClient", FailingClient)
        provider = ThreatBookCnLLMProvider()
        provider._config_models = [
            ModelInfo(
                id="fallback-model",
                name="fallback-model",
                provider_id=provider.id,
            )
        ]
        provider.configure(ProviderConfig(
            provider_id=provider.id,
            api_key="test-key",
            base_url="https://router.example/v1",
            custom_settings={"trust_env": False},
        ))

        assert await provider.refresh_models(force=True) is False
        models = {model.id: model for model in provider.get_models()}
        assert "fallback-model" not in models
        assert models["minimax-m3"].pricing == {
            "input": 4.2,
            "output": 16.8,
            "currency": "CNY",
        }
        assert models["minimax-m2.5"].pricing["output"] == 8.42
        assert models["qwen3.8-max"].pricing == {
            "input": 12.0,
            "output": 36.0,
            "currency": "CNY",
        }

    @pytest.mark.asyncio
    async def test_catalog_exposes_deepseek_v4_flash_0731_metadata(self):
        result = await provider_routes.get_provider_catalog()
        providers = {provider["id"]: provider for provider in result["providers"]}

        for provider_id in ("threatbook-cn-llm", "threatbook-io-llm"):
            models = {
                model["id"]: model
                for model in providers[provider_id]["models"]
            }
            model = models["deepseek-v4-flash-0731"]
            assert model["limits"] == {
                "context_window": 1000000,
                "max_input_tokens": 1000000,
                "max_output_tokens": 384000,
            }
            assert model["pricing"] == {
                "input": 1.0,
                "output": 2.0,
                "cache_read": (
                    None if provider_id == "threatbook-cn-llm" else 0.2
                ),
                "cache_write": None,
                "currency": "CNY",
            }

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
        assert "kimi-k2.7-code" in raw["models"]
        assert raw["models"]["kimi-k2.7-code"]["name"] == "kimi-k2.7-code"
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
