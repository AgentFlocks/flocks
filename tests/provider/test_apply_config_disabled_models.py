"""``disabled: true`` in flocks.json soft-deletes a model: apply_config must keep it out of the runtime."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flocks.provider.provider import Provider


@pytest.mark.asyncio
async def test_apply_config_skips_disabled_models():
    Provider._ensure_initialized()
    provider = Provider.get("openai-compatible")
    assert provider is not None
    original_config = provider._config
    original_models = list(getattr(provider, "_config_models", []) or [])
    fake_cfg = SimpleNamespace(
        provider={
            "openai-compatible": SimpleNamespace(
                name="Disabled Model Provider",
                options={"apiKey": "not-needed", "baseURL": "http://127.0.0.1:1/v1"},
                models={
                    "alive-model": {"name": "Alive"},
                    "dead-model": {"name": "Dead", "disabled": True},
                },
            )
        }
    )
    try:
        await Provider.apply_config(fake_cfg, provider_id="openai-compatible")
        ids = [m.id for m in provider._config_models]
        assert "alive-model" in ids
        assert "dead-model" not in ids
        assert "dead-model" not in [m.id for m in Provider.list_models("openai-compatible")]
    finally:
        provider._config = original_config
        provider._config_models = original_models
