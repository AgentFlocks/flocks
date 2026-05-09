from flocks.provider.provider import ModelCapabilities, ModelInfo
from flocks.provider.sdk.azure import AzureProvider


def test_azure_provider_returns_configured_deployment_models():
    provider = AzureProvider()
    provider._config_models = [
        ModelInfo(
            id="customer-prod-deployment",
            name="Customer Production Deployment",
            provider_id="azure",
            capabilities=ModelCapabilities(
                supports_tools=True,
                supports_streaming=True,
                context_window=128000,
                max_tokens=4096,
            ),
        )
    ]

    models = provider.get_models()

    assert [m.id for m in models] == ["customer-prod-deployment"]
    assert models[0].name == "Customer Production Deployment"


def test_azure_provider_returns_fallback_models_without_config():
    provider = AzureProvider()

    models = provider.get_models()

    assert {m.id for m in models} == {"gpt-5.4", "gpt-5-mini"}
    assert all(m.provider_id == "azure" for m in models)
