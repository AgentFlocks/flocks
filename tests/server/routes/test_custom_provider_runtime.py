import os
from types import SimpleNamespace

import pytest

from flocks.provider.provider import ModelCapabilities, ModelInfo, Provider
from flocks.provider.sdk.azure import AzureProvider
from flocks.server.routes import custom_provider
from flocks.server.routes.custom_provider import (
    CreateModelReq,
    _add_model_to_runtime,
    _resolve_model_limits,
)


def test_model_info_pricing_accepts_currency_string():
    model = ModelInfo(
        id="demo-model",
        name="Demo Model",
        provider_id="custom-demo",
        capabilities=ModelCapabilities(),
        pricing={"input": 0.1, "output": 0.2, "currency": "USD"},
    )

    assert model.pricing == {"input": 0.1, "output": 0.2, "currency": "USD"}


def test_add_model_to_runtime_preserves_reasoning_and_currency(monkeypatch):
    class DummyProvider:
        _custom_models = []
        _config_models = []

    provider = DummyProvider()
    body = CreateModelReq(
        model_id="minimax:MiniMax-M2.7",
        name="minimax:MiniMax-M2.7",
        context_window=200000,
        max_output_tokens=200000,
        supports_vision=False,
        supports_tools=True,
        supports_streaming=True,
        supports_reasoning=True,
        input_price=0.0,
        output_price=0.0,
        currency="USD",
    )

    original_models = Provider._models
    Provider._models = {}
    monkeypatch.setattr(Provider, "get", classmethod(lambda cls, provider_id: provider))

    try:
        _add_model_to_runtime("custom-demo", body)
        saved = Provider._models[body.model_id]

        assert saved.capabilities.supports_reasoning is True
        assert saved.pricing == {"input": 0.0, "output": 0.0, "currency": "USD"}
        assert provider._custom_models[0].pricing["currency"] == "USD"
        assert provider._config_models[0].capabilities.supports_reasoning is True
    finally:
        Provider._models = original_models


def test_add_azure_deployment_to_runtime_config_models(monkeypatch):
    provider = AzureProvider()
    provider.id = "azure-openai"
    provider._config_models = []
    body = CreateModelReq(
        model_id="customer-prod-deployment",
        name="Customer Production Deployment",
        context_window=128000,
        max_output_tokens=4096,
        supports_vision=False,
        supports_tools=True,
        supports_streaming=True,
        supports_reasoning=False,
        input_price=0.0,
        output_price=0.0,
        currency="USD",
    )

    original_models = Provider._models
    Provider._models = {}
    monkeypatch.setattr(Provider, "get", classmethod(lambda cls, provider_id: provider))

    try:
        _add_model_to_runtime("azure-openai", body)

        assert Provider._models[body.model_id].provider_id == "azure-openai"
        assert provider._config_models[0].id == "customer-prod-deployment"
        assert provider._config_models[0].name == "Customer Production Deployment"
    finally:
        Provider._models = original_models


@pytest.mark.asyncio
async def test_resolve_model_limits_uses_explicit_values():
    body = CreateModelReq(
        model_id="gpt-explicit",
        name="GPT Explicit",
        context_window=64000,
        max_output_tokens=16000,
    )

    resolved = await _resolve_model_limits("custom-openai", body, {"models": {}})

    assert resolved.context_window == 64000
    assert resolved.max_output_tokens == 16000
    assert resolved.source == "explicit"


@pytest.mark.asyncio
async def test_resolve_model_limits_uses_existing_provider_config():
    body = CreateModelReq(model_id="known-model", name="Known Model")

    resolved = await _resolve_model_limits(
        "custom-openai",
        body,
        {
            "models": {
                "known-model": {
                    "context_window": 200000,
                    "max_output_tokens": 32000,
                },
            },
        },
    )

    assert resolved.context_window == 200000
    assert resolved.max_output_tokens == 32000
    assert resolved.source == "catalog"


@pytest.mark.asyncio
async def test_resolve_model_limits_uses_flocks_catalog(monkeypatch):
    body = CreateModelReq(model_id="catalog-model", name="Catalog Model")
    model_def = SimpleNamespace(
        id="catalog-model",
        limits=SimpleNamespace(
            context_window=131072,
            max_output_tokens=8192,
        ),
        capabilities=None,
    )

    monkeypatch.setattr(
        "flocks.provider.model_catalog.get_provider_model_definitions",
        lambda provider_id: [model_def],
    )

    resolved = await _resolve_model_limits("openai", body, {"models": {}})

    assert resolved.context_window == 131072
    assert resolved.max_output_tokens == 8192
    assert resolved.source == "catalog"


@pytest.mark.asyncio
async def test_resolve_model_limits_uses_models_dev(monkeypatch):
    body = CreateModelReq(model_id="gpt-models-dev", name="GPT Models Dev")

    async def fake_fetch_models_dev():
        return {
            "openai": {
                "models": {
                    "gpt-models-dev": {
                        "limit": {
                            "context": 128000,
                            "output": 16384,
                        },
                    },
                },
            },
        }

    monkeypatch.setattr(custom_provider, "_fetch_models_dev", fake_fetch_models_dev)

    resolved = await _resolve_model_limits("custom-openai", body, {"models": {}})

    assert resolved.context_window == 128000
    assert resolved.max_output_tokens == 16384
    assert resolved.source == "models_dev"


@pytest.mark.asyncio
async def test_resolve_model_limits_uses_models_dev_model_prefix(monkeypatch):
    body = CreateModelReq(
        model_id="deepseek:deepseek-v4-flash",
        name="DeepSeek V4 Flash",
    )

    async def fake_fetch_models_dev():
        return {
            "deepseek": {
                "models": {
                    "deepseek-v4-flash": {
                        "limit": {
                            "context": 1000000,
                            "output": 384000,
                        },
                    },
                },
            },
        }

    monkeypatch.setattr(custom_provider, "_fetch_models_dev", fake_fetch_models_dev)

    resolved = await _resolve_model_limits("custom-tb-local", body, {"models": {}})

    assert resolved.context_window == 1000000
    assert resolved.max_output_tokens == 384000
    assert resolved.source == "models_dev"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_id", "model_id", "expected_context", "expected_output", "expected_source"),
    [
        ("custom-deepseek", "deepseek-v4", 128000, 8192, "fallback"),
        ("custom-deepseek", "deepseek-v4-flash", 1000000, 384000, "models_dev"),
        ("custom-anything", "deepseek:deepseek-v4-flash", 1000000, 384000, "models_dev"),
        ("custom-bailian", "bailian:deepseek-v4-flash", 128000, 8192, "fallback"),
        ("openrouter", "deepseek/deepseek-v4-flash", 1048576, 131072, "models_dev"),
    ],
)
async def test_resolve_model_limits_auto_detects_common_model_inputs(
    monkeypatch,
    provider_id,
    model_id,
    expected_context,
    expected_output,
    expected_source,
):
    body = CreateModelReq(model_id=model_id, name=model_id)

    async def fake_fetch_models_dev():
        return {
            "deepseek": {
                "models": {
                    "deepseek-v4-flash": {
                        "limit": {
                            "context": 1000000,
                            "output": 384000,
                        },
                    },
                    "deepseek-v4-pro": {
                        "limit": {
                            "context": 1000000,
                            "output": 384000,
                        },
                    },
                },
            },
            "alibaba": {"models": {}},
            "openrouter": {
                "models": {
                    "deepseek/deepseek-v4-flash": {
                        "limit": {
                            "context": 1048576,
                            "output": 131072,
                        },
                    },
                },
            },
        }

    monkeypatch.setattr(custom_provider, "_fetch_models_dev", fake_fetch_models_dev)

    resolved = await _resolve_model_limits(provider_id, body, {"models": {}})

    assert resolved.context_window == expected_context
    assert resolved.max_output_tokens == expected_output
    assert resolved.source == expected_source


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("FLOCKS_LIVE_TEST") != "1",
    reason="requires live models.dev network access",
)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_id", "model_id", "expected_context", "expected_output", "expected_source"),
    [
        ("custom-deepseek", "deepseek-v4", 128000, 8192, "fallback"),
        ("custom-deepseek", "deepseek-v4-flash", 1000000, 384000, "models_dev"),
        ("custom-anything", "deepseek:deepseek-v4-flash", 1000000, 384000, "models_dev"),
        ("custom-bailian", "bailian:deepseek-v4-flash", 128000, 8192, "fallback"),
        ("openrouter", "deepseek/deepseek-v4-flash", 1048576, 131072, "models_dev"),
    ],
)
async def test_resolve_model_limits_live_models_dev_auto_detects_current_inputs(
    provider_id,
    model_id,
    expected_context,
    expected_output,
    expected_source,
):
    body = CreateModelReq(model_id=model_id, name=model_id)

    resolved = await _resolve_model_limits(provider_id, body, {"models": {}})

    assert resolved.context_window == expected_context
    assert resolved.max_output_tokens == expected_output
    assert resolved.source == expected_source


@pytest.mark.asyncio
async def test_resolve_model_limits_falls_back_when_models_dev_unavailable(monkeypatch):
    body = CreateModelReq(model_id="unknown-model", name="Unknown Model")

    async def fake_fetch_models_dev():
        return None

    monkeypatch.setattr(custom_provider, "_fetch_models_dev", fake_fetch_models_dev)

    resolved = await _resolve_model_limits("custom-openai", body, {"models": {}})

    assert resolved.context_window == custom_provider.FALLBACK_CONTEXT_WINDOW
    assert resolved.max_output_tokens == custom_provider.FALLBACK_MAX_OUTPUT_TOKENS
    assert resolved.source == "fallback"
