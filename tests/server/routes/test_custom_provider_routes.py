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
            "currency": "USD",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["provider_id"] == "custom-tb-inner"
    assert data["model_id"] == "minimax:MiniMax-M2.7"
    assert data["currency"] == "USD"

    raw = ConfigWriter.get_provider_raw("custom-tb-inner")
    assert raw is not None
    assert raw["models"]["minimax:MiniMax-M2.7"]["currency"] == "USD"
    assert Provider._models["minimax:MiniMax-M2.7"].pricing["currency"] == "USD"


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
