"""Tests for ordered default-model fallback configuration routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from flocks.provider.provider import Provider
from flocks.provider.types import ModelType
from flocks.server.routes import default_model as default_model_routes


class _ModelManagerStub:
    """Small model-manager stub for fallback route validation."""

    def __init__(self, models, disabled=None):
        self._models = models
        self._disabled = set(disabled or [])

    def get_model(self, provider_id: str, model_id: str):
        return self._models.get((provider_id, model_id))

    def get_setting(self, provider_id: str, model_id: str):
        if (provider_id, model_id) in self._disabled:
            return SimpleNamespace(enabled=False)
        return None


def _definition(model_type: ModelType = ModelType.LLM):
    return SimpleNamespace(model_type=model_type)


@pytest.fixture
def fallback_route_stubs(monkeypatch: pytest.MonkeyPatch):
    """Prevent fallback route tests from touching real config and providers."""
    writer = MagicMock()
    runtime_config = SimpleNamespace(
        provider={},
        disabled_providers=[],
        enabled_providers=None,
        fallback_providers=[],
    )
    apply_config = AsyncMock()
    configured_providers = {
        "openai": SimpleNamespace(is_configured=lambda: True),
        "openrouter": SimpleNamespace(is_configured=lambda: True),
        "anthropic": SimpleNamespace(is_configured=lambda: True),
    }

    monkeypatch.setattr(default_model_routes, "ConfigWriter", writer)
    monkeypatch.setattr(
        default_model_routes.Config,
        "get",
        AsyncMock(return_value=runtime_config),
    )
    monkeypatch.setattr(
        default_model_routes.Config,
        "resolve_default_llm",
        AsyncMock(return_value={
            "provider_id": "anthropic",
            "model_id": "claude-primary",
        }),
    )
    monkeypatch.setattr(Provider, "apply_config", apply_config)
    monkeypatch.setattr(
        Provider,
        "get",
        lambda provider_id: configured_providers.get(provider_id),
    )
    writer.get_fallback_override_source.return_value = None
    writer.runtime_config = runtime_config
    writer.apply_config = apply_config
    writer.configured_providers = configured_providers
    return writer


@pytest.mark.asyncio
async def test_get_fallbacks_uses_effective_config_and_keeps_stale_entries(
    client: AsyncClient,
    fallback_route_stubs: MagicMock,
):
    fallback_route_stubs.runtime_config.fallback_providers = [
        {
            "provider_id": "removed-provider",
            "model_id": "vendor/removed-model",
        },
    ]

    response = await client.get("/api/default-model/fallbacks")

    assert response.status_code == 200
    assert response.json() == {
        "fallback_providers": [
            {
                "provider_id": "removed-provider",
                "model_id": "vendor/removed-model",
            },
        ],
    }


@pytest.mark.asyncio
async def test_get_fallbacks_does_not_read_only_writable_layer(
    client: AsyncClient,
    fallback_route_stubs: MagicMock,
):
    fallback_route_stubs.get_fallback_providers.return_value = [
        {"provider_id": "global", "model_id": "global-model"},
    ]
    fallback_route_stubs.runtime_config.fallback_providers = [
        {"provider_id": "inline", "model_id": "effective-model"},
    ]

    response = await client.get("/api/default-model/fallbacks")

    assert response.status_code == 200
    assert response.json() == {
        "fallback_providers": [
            {"provider_id": "inline", "model_id": "effective-model"},
        ],
    }
    fallback_route_stubs.get_fallback_providers.assert_not_called()


@pytest.mark.asyncio
async def test_put_fallbacks_rejects_higher_priority_override(
    client: AsyncClient,
    fallback_route_stubs: MagicMock,
):
    fallback_route_stubs.get_fallback_override_source.return_value = (
        "FLOCKS_CONFIG_CONTENT"
    )

    response = await client.put(
        "/api/default-model/fallbacks",
        json={"fallback_providers": []},
    )

    assert response.status_code == 409
    assert "FLOCKS_CONFIG_CONTENT" in str(response.json())
    fallback_route_stubs.set_fallback_providers.assert_not_called()


@pytest.mark.asyncio
async def test_put_fallbacks_normalizes_and_preserves_order(
    client: AsyncClient,
    fallback_route_stubs: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    models = {
        ("openai", "gpt-4o"): _definition(),
        ("openrouter", "vendor/model-v2"): _definition(),
    }
    monkeypatch.setattr(
        default_model_routes,
        "get_model_manager",
        lambda: _ModelManagerStub(models),
    )

    response = await client.put(
        "/api/default-model/fallbacks",
        json={
            "fallback_providers": [
                {"provider_id": " openai ", "model_id": " gpt-4o "},
                {
                    "provider_id": "openrouter",
                    "model_id": "vendor/model-v2",
                },
            ],
        },
    )

    expected = [
        {"provider_id": "openai", "model_id": "gpt-4o"},
        {"provider_id": "openrouter", "model_id": "vendor/model-v2"},
    ]
    assert response.status_code == 200
    assert response.json() == {"fallback_providers": expected}
    fallback_route_stubs.set_fallback_providers.assert_called_once_with(expected)


@pytest.mark.asyncio
async def test_put_fallbacks_loads_config_models_before_validation(
    client: AsyncClient,
    fallback_route_stubs: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    identity = ("openai", "configured-model")
    models = {}
    monkeypatch.setattr(
        default_model_routes,
        "get_model_manager",
        lambda: _ModelManagerStub(models),
    )

    async def load_config_models(config):
        assert config is fallback_route_stubs.runtime_config
        models[identity] = _definition()

    fallback_route_stubs.apply_config.side_effect = load_config_models

    response = await client.put(
        "/api/default-model/fallbacks",
        json={
            "fallback_providers": [
                {"provider_id": identity[0], "model_id": identity[1]},
            ],
        },
    )

    assert response.status_code == 200
    fallback_route_stubs.apply_config.assert_awaited_once_with(
        fallback_route_stubs.runtime_config
    )


@pytest.mark.asyncio
async def test_put_empty_fallbacks_clears_configuration(
    client: AsyncClient,
    fallback_route_stubs: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        default_model_routes,
        "get_model_manager",
        lambda: _ModelManagerStub({}),
    )

    response = await client.put(
        "/api/default-model/fallbacks",
        json={"fallback_providers": []},
    )

    assert response.status_code == 200
    assert response.json() == {"fallback_providers": []}
    fallback_route_stubs.set_fallback_providers.assert_called_once_with([])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "models", "disabled", "configured", "detail"),
    [
        (
            [{"provider_id": " ", "model_id": "gpt-4o"}],
            {},
            set(),
            True,
            "must include provider_id and model_id",
        ),
        (
            [
                {"provider_id": "openai", "model_id": "gpt-4o"},
                {"provider_id": " openai ", "model_id": " gpt-4o "},
            ],
            {("openai", "gpt-4o"): _definition()},
            set(),
            True,
            "Duplicate fallback model",
        ),
        (
            [{"provider_id": "anthropic", "model_id": "claude-primary"}],
            {("anthropic", "claude-primary"): _definition()},
            set(),
            True,
            "is the current default LLM",
        ),
        (
            [{"provider_id": "openai", "model_id": "missing-model"}],
            {},
            set(),
            True,
            "Unknown fallback model",
        ),
        (
            [{"provider_id": "openai", "model_id": "embedding-model"}],
            {
                ("openai", "embedding-model"): _definition(
                    ModelType.TEXT_EMBEDDING
                ),
            },
            set(),
            True,
            "is not an LLM",
        ),
        (
            [{"provider_id": "openai", "model_id": "gpt-disabled"}],
            {("openai", "gpt-disabled"): _definition()},
            {("openai", "gpt-disabled")},
            True,
            "is disabled",
        ),
        (
            [{"provider_id": "openai", "model_id": "gpt-4o"}],
            {("openai", "gpt-4o"): _definition()},
            set(),
            False,
            "is not configured",
        ),
    ],
)
async def test_put_fallbacks_rejects_invalid_entries(
    client: AsyncClient,
    fallback_route_stubs: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    payload,
    models,
    disabled,
    configured,
    detail,
):
    monkeypatch.setattr(
        default_model_routes,
        "get_model_manager",
        lambda: _ModelManagerStub(models, disabled),
    )
    if not configured:
        fallback_route_stubs.configured_providers.pop("openai", None)

    response = await client.put(
        "/api/default-model/fallbacks",
        json={"fallback_providers": payload},
    )

    assert response.status_code == 400
    assert detail in str(response.json())
    fallback_route_stubs.set_fallback_providers.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("disabled_providers", "enabled_providers"),
    [
        (["openai"], None),
        ([], ["anthropic"]),
    ],
)
async def test_put_fallbacks_honors_provider_filters(
    client: AsyncClient,
    fallback_route_stubs: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    disabled_providers,
    enabled_providers,
):
    fallback_route_stubs.runtime_config.disabled_providers = disabled_providers
    fallback_route_stubs.runtime_config.enabled_providers = enabled_providers
    monkeypatch.setattr(
        default_model_routes,
        "get_model_manager",
        lambda: _ModelManagerStub({("openai", "gpt-4o"): _definition()}),
    )

    response = await client.put(
        "/api/default-model/fallbacks",
        json={
            "fallback_providers": [
                {"provider_id": "openai", "model_id": "gpt-4o"},
            ],
        },
    )

    assert response.status_code == 400
    assert "provider 'openai' is disabled" in str(response.json())
    fallback_route_stubs.set_fallback_providers.assert_not_called()
