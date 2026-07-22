"""Tests for ordered default-model fallback configuration routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from flocks.provider.provider import Provider
from flocks.provider.types import ModelType
from flocks.server.routes import default_model as default_model_routes


class _ModelManagerStub:
    """Small ModelManager stub used to isolate fallback route validation."""

    def __init__(self, models, disabled=None):
        self._models = models
        self._disabled = set(disabled or [])

    def get_model(self, provider_id: str, model_id: str):
        return self._models.get((provider_id, model_id))

    def get_setting(self, provider_id: str, model_id: str):
        identity = (provider_id, model_id)
        if identity in self._disabled:
            return SimpleNamespace(enabled=False)
        return None


def _definition(model_type: ModelType = ModelType.LLM):
    return SimpleNamespace(model_type=model_type)


@pytest.fixture
def fallback_route_stubs(monkeypatch: pytest.MonkeyPatch):
    """Prevent fallback route tests from reading or writing real model state."""
    writer = MagicMock()
    writer.get_fallback_providers.return_value = []
    runtime_config = SimpleNamespace(
        provider={},
        disabled_providers=[],
        enabled_providers=None,
        fallback_providers=[],
    )
    apply_config = AsyncMock()
    monkeypatch.setattr(default_model_routes, "ConfigWriter", writer)
    monkeypatch.setattr(
        default_model_routes.Config,
        "get",
        AsyncMock(return_value=runtime_config),
    )
    monkeypatch.setattr(
        default_model_routes.Config,
        "resolve_default_llm",
        AsyncMock(
            return_value={
                "provider_id": "anthropic",
                "model_id": "claude-primary",
            }
        ),
    )
    monkeypatch.setattr(Provider, "apply_config", apply_config)
    writer.get_fallback_override_source.return_value = None
    writer.runtime_config = runtime_config
    writer.apply_config = apply_config
    return writer


@pytest.mark.asyncio
async def test_get_fallbacks_uses_static_route_and_retains_stale_entries(
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
            }
        ]
    }


@pytest.mark.asyncio
async def test_get_fallbacks_uses_effective_merged_config(
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
        ]
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
    assert "FLOCKS_CONFIG_CONTENT" in response.json()["message"]
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
            ]
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
    """Cold-start validation sees config models even without credentials."""
    identity = ("openai-compatible", "configured-model")
    models = {}
    manager = _ModelManagerStub(models)
    monkeypatch.setattr(
        default_model_routes,
        "get_model_manager",
        lambda: manager,
    )
    fallback_route_stubs.runtime_config.provider = {
        "openai-compatible": SimpleNamespace(
            options=SimpleNamespace(api_key=None, base_url=None),
            models={identity[1]: SimpleNamespace(name="Configured Model")},
        )
    }

    async def load_config_models(config):
        assert config is fallback_route_stubs.runtime_config
        models[identity] = _definition()

    fallback_route_stubs.apply_config.side_effect = load_config_models

    response = await client.put(
        "/api/default-model/fallbacks",
        json={
            "fallback_providers": [
                {"provider_id": identity[0], "model_id": identity[1]}
            ]
        },
    )

    assert response.status_code == 200
    fallback_route_stubs.apply_config.assert_awaited_once_with(
        fallback_route_stubs.runtime_config
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("disabled_providers", "enabled_providers", "expected_status"),
    [
        (["openai"], None, 400),
        ([], ["anthropic"], 400),
        ([], [], 200),
    ],
    ids=["explicitly-disabled", "excluded-by-enabled", "empty-enabled-allows"],
)
async def test_put_fallbacks_honors_provider_filters(
    client: AsyncClient,
    fallback_route_stubs: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    disabled_providers,
    enabled_providers,
    expected_status,
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
                {"provider_id": "openai", "model_id": "gpt-4o"}
            ]
        },
    )

    assert response.status_code == expected_status
    if expected_status == 400:
        assert "provider 'openai' is disabled" in response.json()["message"]
        fallback_route_stubs.set_fallback_providers.assert_not_called()


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
    ("payload", "models", "disabled", "detail"),
    [
        (
            [{"provider_id": " ", "model_id": "gpt-4o"}],
            {},
            set(),
            "must include provider_id and model_id",
        ),
        (
            [
                {"provider_id": "openai", "model_id": "gpt-4o"},
                {"provider_id": " openai ", "model_id": " gpt-4o "},
            ],
            {("openai", "gpt-4o"): _definition()},
            set(),
            "Duplicate fallback model",
        ),
        (
            [{"provider_id": "anthropic", "model_id": "claude-primary"}],
            {("anthropic", "claude-primary"): _definition()},
            set(),
            "is the current default LLM",
        ),
        (
            [{"provider_id": "missing", "model_id": "missing-model"}],
            {},
            set(),
            "Unknown fallback model",
        ),
        (
            [{"provider_id": "openai", "model_id": "embedding-model"}],
            {
                ("openai", "embedding-model"): _definition(
                    ModelType.TEXT_EMBEDDING
                )
            },
            set(),
            "is not an LLM",
        ),
        (
            [{"provider_id": "openai", "model_id": "gpt-disabled"}],
            {("openai", "gpt-disabled"): _definition()},
            {("openai", "gpt-disabled")},
            "is disabled",
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
    detail,
):
    monkeypatch.setattr(
        default_model_routes,
        "get_model_manager",
        lambda: _ModelManagerStub(models, disabled),
    )

    response = await client.put(
        "/api/default-model/fallbacks",
        json={"fallback_providers": payload},
    )

    assert response.status_code == 400
    assert detail in response.json()["message"]
    fallback_route_stubs.set_fallback_providers.assert_not_called()
