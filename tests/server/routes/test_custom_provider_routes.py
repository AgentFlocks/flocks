import json

import pytest
from httpx import ASGITransport, AsyncClient

from flocks.server.app import app


@pytest.fixture
def temp_custom_provider_project(tmp_path, monkeypatch):
    """Create a temporary user config with one custom provider in flocks.json."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    from flocks.config.config import Config
    Config._global_config = None
    Config._cached_config = None
    config_file = config_dir / "flocks.json"
    config_file.write_text(json.dumps({
        "provider": {
            "custom-tb-inner": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "apiKey": "{secret:custom-tb-inner_llm_key}",
                    "baseURL": "https://llm-internal.threatbook-inc.cn/api",
                },
                "models": {},
                "name": "TB-inner",
                "description": "Any OpenAI-compatible API endpoint",
                "created_at": "2026-03-26T11:34:40.470332+00:00",
            }
        }
    }, indent=2))
    return config_dir


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer abc123", "User-Agent": "curl/8.0"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_custom_model_accepts_string_currency(
    client: AsyncClient,
    temp_custom_provider_project,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.config.config_writer import ConfigWriter
    from flocks.provider.provider import Provider

    monkeypatch.setattr(Provider, "_models", Provider._models.copy())

    response = await client.post(
        "/api/custom/models/custom-tb-inner",
        json={
            "model_id": "minimax:MiniMax-M2.7",
            "name": "minimax:MiniMax-M2.7",
            "context_window": 128000,
            "max_output_tokens": 128000,
            "supports_vision": False,
            "supports_tools": True,
            "supports_streaming": True,
            "supports_reasoning": False,
            "input_price": 0.0,
            "output_price": 0.0,
            "cache_read_price": 0.2,
            "currency": "USD",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["provider_id"] == "custom-tb-inner"
    assert data["model_id"] == "minimax:MiniMax-M2.7"
    assert data["cache_read_price"] == 0.2
    assert data["currency"] == "USD"

    raw = ConfigWriter.get_provider_raw("custom-tb-inner")
    assert raw is not None
    assert raw["models"]["minimax:MiniMax-M2.7"]["cache_read_price"] == 0.2
    assert raw["models"]["minimax:MiniMax-M2.7"]["currency"] == "USD"
    assert Provider._models["minimax:MiniMax-M2.7"].pricing["cache_read"] == 0.2
    assert Provider._models["minimax:MiniMax-M2.7"].pricing["currency"] == "USD"


@pytest.mark.asyncio
async def test_update_custom_model_preserves_omitted_cache_price(
    client: AsyncClient,
    temp_custom_provider_project,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.config.config_writer import ConfigWriter
    from flocks.provider.provider import Provider

    monkeypatch.setattr(Provider, "_models", Provider._models.copy())
    payload = {
        "model_id": "cached-model",
        "name": "Cached Model",
        "input_price": 1.0,
        "output_price": 2.0,
        "cache_read_price": 0.2,
        "currency": "CNY",
    }
    created = await client.post(
        "/api/custom/models/custom-tb-inner",
        json=payload,
    )
    assert created.status_code == 201, created.text

    payload.pop("cache_read_price")
    updated = await client.post(
        "/api/custom/models/custom-tb-inner",
        json=payload,
    )

    assert updated.status_code == 201, updated.text
    assert updated.json()["cache_read_price"] == 0.2
    raw = ConfigWriter.get_provider_raw("custom-tb-inner")
    assert raw is not None
    assert raw["models"]["cached-model"]["cache_read_price"] == 0.2
    assert Provider._models["cached-model"].pricing["cache_read"] == 0.2

    cleared = await client.post(
        "/api/custom/models/custom-tb-inner",
        json={**payload, "cache_read_price": None},
    )
    assert cleared.status_code == 201, cleared.text
    assert cleared.json()["cache_read_price"] is None
    raw = ConfigWriter.get_provider_raw("custom-tb-inner")
    assert raw is not None
    assert raw["models"]["cached-model"]["cache_read_price"] is None
    assert "cache_read" not in Provider._models["cached-model"].pricing


@pytest.mark.asyncio
async def test_create_custom_model_defaults_reasoning_on(
    client: AsyncClient,
    temp_custom_provider_project,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.config.config_writer import ConfigWriter
    from flocks.provider.provider import Provider

    monkeypatch.setattr(Provider, "_models", Provider._models.copy())

    response = await client.post(
        "/api/custom/models/custom-tb-inner",
        json={
            "model_id": "custom-reasoning-default",
            "name": "Custom Reasoning Default",
        },
    )

    assert response.status_code == 201, response.text
    raw = ConfigWriter.get_provider_raw("custom-tb-inner")
    assert raw is not None
    assert raw["models"]["custom-reasoning-default"]["supports_reasoning"] is True
    assert Provider._models["custom-reasoning-default"].capabilities.supports_reasoning is True


# ==================== auto-promote first model to default LLM ====================
# Regression: a user adding a model from the model-management page (not the
# onboarding wizard) ends up with a configured provider but no default LLM.
# /api/onboarding/status then reports completed=False and the WebUI re-opens
# the setup wizard on every login. create_model now promotes the FIRST model
# to default when no default exists and the provider already has credentials.


class _StubSecretManager:
    def __init__(self, values):
        self._values = values

    def get(self, key):
        return self._values.get(key)

    def has(self, key):
        return key in self._values


@pytest.fixture
def _credential_stub(monkeypatch):
    """Stub the SecretManager exactly where credential.get_api_key resolves it.

    credential.get_api_key does `from flocks.security import get_secret_manager`
    at call time, so the name lives in the flocks.security __init__ namespace
    (not the flocks.security.secrets submodule).
    """
    import flocks.security as security_module

    return lambda values: monkeypatch.setattr(
        security_module, "get_secret_manager", lambda: _StubSecretManager(values)
    )


async def _create_model(client, model_id="first-model"):
    response = await client.post(
        "/api/custom/models/custom-tb-inner",
        json={
            "model_id": model_id,
            "name": model_id,
            "context_window": 32768,
            "max_output_tokens": 4096,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_first_model_auto_becomes_default_when_credential_exists(
    client: AsyncClient, temp_custom_provider_project, _credential_stub
):
    from flocks.config.config_writer import ConfigWriter

    _credential_stub({"custom-tb-inner_llm_key": "sk-test-first"})

    await _create_model(client)

    default = ConfigWriter.get_default_model("llm")
    assert default == {"provider_id": "custom-tb-inner", "model_id": "first-model"}


@pytest.mark.asyncio
async def test_first_model_with_inline_api_key_auto_becomes_default(
    client: AsyncClient, temp_custom_provider_project, _credential_stub
):
    from flocks.config.config_writer import ConfigWriter

    # No secret, but flocks.json holds a plain-text inline apiKey.
    _credential_stub({})
    ConfigWriter.update_provider_field("custom-tb-inner", "options.apiKey", "sk-inline-test")

    await _create_model(client)

    default = ConfigWriter.get_default_model("llm")
    assert default == {"provider_id": "custom-tb-inner", "model_id": "first-model"}


@pytest.mark.asyncio
async def test_first_model_without_credential_does_not_become_default(
    client: AsyncClient, temp_custom_provider_project, _credential_stub
):
    from flocks.config.config_writer import ConfigWriter

    # Secret reference in config, but the secret value is absent -> no credential.
    _credential_stub({})

    await _create_model(client)

    assert ConfigWriter.get_default_model("llm") is None


@pytest.mark.asyncio
async def test_second_model_does_not_override_existing_default(
    client: AsyncClient, temp_custom_provider_project, _credential_stub
):
    from flocks.config.config_writer import ConfigWriter

    _credential_stub({"custom-tb-inner_llm_key": "sk-test-second"})

    await _create_model(client, model_id="first-model")
    await _create_model(client, model_id="second-model")

    default = ConfigWriter.get_default_model("llm")
    assert default == {"provider_id": "custom-tb-inner", "model_id": "first-model"}


@pytest.mark.asyncio
async def test_create_model_does_not_override_an_existing_default_from_another_provider(
    client: AsyncClient, temp_custom_provider_project, _credential_stub
):
    from flocks.config.config_writer import ConfigWriter

    _credential_stub({"custom-tb-inner_llm_key": "sk-test-override"})
    # A default from a different provider (e.g. configured via onboarding) must be kept.
    ConfigWriter.set_default_model("llm", "threatbook-cn-llm", "qwen3-max")

    await _create_model(client)

    default = ConfigWriter.get_default_model("llm")
    assert default == {"provider_id": "threatbook-cn-llm", "model_id": "qwen3-max"}
