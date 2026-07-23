"""Idempotency tests for ``Provider.apply_config``.

The session calls ``Provider.apply_config(provider_id=...)`` on every
step, and the workflow does the same per ``llm.ask``. Before the fix
both call sites unconditionally rewrote ``provider._config`` and
rebuilt ``provider._config_models`` from scratch — which (a) caused
race-prone reads on the mutable model list across event loops, and
(b) created noisy ``provider.config_models.loaded`` log entries on
every hot-path call.

These tests pin down that consecutive ``apply_config`` calls with the
same backing flocks.json are now a no-op at the mutation level.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from flocks.provider.provider import (
    ModelCapabilities,
    ModelInfo,
    Provider,
    ProviderConfig,
)


class _Recorder:
    """Wraps a provider so we can count ``configure`` calls and
    ``_config_models`` rebuilds during ``apply_config``.
    """

    def __init__(self, real_provider: Any) -> None:
        self.real_provider = real_provider
        self.configure_calls = 0
        self.models_assignments = 0
        # Capture the ``_config_models`` setter via property descriptor.
        original_setattr = type(real_provider).__setattr__

        recorder = self

        def patched_setattr(self_obj, key, value):
            if key == "_config_models":
                recorder.models_assignments += 1
            original_setattr(self_obj, key, value)

        self._original_setattr = original_setattr
        self._patched_setattr = patched_setattr

    def __enter__(self):
        type(self.real_provider).__setattr__ = self._patched_setattr
        original_configure = self.real_provider.configure

        def patched_configure(cfg):
            self.configure_calls += 1
            return original_configure(cfg)

        self._original_configure = original_configure
        self.real_provider.configure = patched_configure
        return self

    def __exit__(self, *_args, **_kwargs):
        type(self.real_provider).__setattr__ = self._original_setattr
        self.real_provider.configure = self._original_configure


def _build_fake_config(
    provider_id: str,
    *,
    model_first_chunk_timeout_s: int = 480,
) -> Any:
    """Return a SimpleNamespace mimicking ``ConfigInfo`` shape needed by
    ``apply_config``: ``.provider`` dict -> ``.options`` (with model_dump)
    and ``.models``.
    """
    options = SimpleNamespace(
        model_dump=lambda exclude_none, by_alias: {
            "api_key": "static-key",
            "base_url": "https://static.example.com",
            "trust_env": False,
        }
    )
    model_options = {
        "static-model": SimpleNamespace(
            model_dump=lambda: {
                "name": "Static Model",
                "supports_streaming": True,
                "supports_tools": True,
                "supports_vision": False,
                "supports_reasoning": False,
                "max_tokens": 4096,
                "stream_first_chunk_timeout_s": model_first_chunk_timeout_s,
            }
        )
    }
    return SimpleNamespace(
        provider={
            provider_id: SimpleNamespace(options=options, models=model_options),
        }
    )


@pytest.mark.asyncio
async def test_apply_config_is_idempotent_for_unchanged_input() -> None:
    """Two consecutive ``apply_config`` calls with the same config must
    skip both ``provider.configure(...)`` and ``_config_models`` rebuild
    on the second call.
    """
    Provider._ensure_initialized()
    provider = Provider.get("openai-compatible")
    assert provider is not None

    fake_cfg = _build_fake_config("openai-compatible")

    with patch(
        "flocks.provider.provider.Config.get", return_value=fake_cfg
    ):
        # First call primes the provider with the desired config.
        await Provider.apply_config(provider_id="openai-compatible")

        with _Recorder(provider) as rec:
            # Second call should observe no material change and skip.
            await Provider.apply_config(provider_id="openai-compatible")

        assert rec.configure_calls == 0, (
            "apply_config must not re-run provider.configure when the "
            "desired ProviderConfig matches the existing one"
        )
        assert rec.models_assignments == 0, (
            "apply_config must not rebuild _config_models when the desired "
            "model list matches the existing one"
        )
        assert provider.get_models()[0].custom_settings == {
            "stream_first_chunk_timeout_s": 480,
        }


@pytest.mark.asyncio
async def test_apply_config_still_mutates_when_input_changes() -> None:
    """A genuine config change (different api_key) must still trigger
    ``provider.configure`` exactly once.
    """
    Provider._ensure_initialized()
    provider = Provider.get("openai-compatible")
    assert provider is not None

    # First, prime with one config.
    first_cfg = _build_fake_config("openai-compatible")
    with patch(
        "flocks.provider.provider.Config.get", return_value=first_cfg
    ):
        await Provider.apply_config(provider_id="openai-compatible")

    # Second, swap api_key.
    second_options = SimpleNamespace(
        model_dump=lambda exclude_none, by_alias: {
            "api_key": "rotated-key",
            "base_url": "https://static.example.com",
            "trust_env": False,
        }
    )
    second_cfg = SimpleNamespace(
        provider={
            "openai-compatible": SimpleNamespace(
                options=second_options,
                models=first_cfg.provider["openai-compatible"].models,
            )
        }
    )

    with patch(
        "flocks.provider.provider.Config.get", return_value=second_cfg
    ), _Recorder(provider) as rec:
        await Provider.apply_config(provider_id="openai-compatible")

    assert rec.configure_calls == 1, (
        f"expected exactly 1 configure call on api_key change, got {rec.configure_calls}"
    )


@pytest.mark.asyncio
async def test_apply_config_rebuilds_models_when_stream_timeout_changes() -> None:
    """A model timeout change must invalidate the model-list signature."""
@pytest.mark.parametrize(
    "options",
    [
        None,
        SimpleNamespace(
            model_dump=lambda exclude_none, by_alias: {
                "api_key": " ",
                "base_url": "",
            }
        ),
    ],
    ids=["missing-options", "empty-credentials"],
)
async def test_apply_config_loads_name_and_models_without_credentials(
    options: Any,
) -> None:
    """Provider metadata must not depend on resolved credentials."""
    Provider._ensure_initialized()
    provider = Provider.get("openai-compatible")
    assert provider is not None

    first_cfg = _build_fake_config(
        "openai-compatible",
        model_first_chunk_timeout_s=480,
    )
    with patch("flocks.provider.provider.Config.get", return_value=first_cfg):
        await Provider.apply_config(provider_id="openai-compatible")

    second_cfg = _build_fake_config(
        "openai-compatible",
        model_first_chunk_timeout_s=600,
    )
    with patch(
        "flocks.provider.provider.Config.get",
        return_value=second_cfg,
    ), _Recorder(provider) as rec:
        await Provider.apply_config(provider_id="openai-compatible")

    assert rec.models_assignments == 1
    assert provider.get_models()[0].custom_settings == {
        "stream_first_chunk_timeout_s": 600,
    }
    original_config = provider._config
    original_models = list(getattr(provider, "_config_models", []) or [])
    original_name = provider.name
    model_id = f"credentialless-model-{id(options)}"
    fake_cfg = SimpleNamespace(
        provider={
            "openai-compatible": SimpleNamespace(
                name="Credentialless Provider",
                options=options,
                models={
                    model_id: {
                        "name": "Credentialless Model",
                        "supports_tools": True,
                    }
                },
            )
        }
    )

    try:
        with _Recorder(provider) as first:
            await Provider.apply_config(fake_cfg, provider_id="openai-compatible")

        assert first.configure_calls == 0
        assert first.models_assignments == 1
        assert provider.name == "Credentialless Provider"
        assert [model.id for model in provider._config_models] == [model_id]

        with _Recorder(provider) as second:
            await Provider.apply_config(fake_cfg, provider_id="openai-compatible")

        assert second.configure_calls == 0
        assert second.models_assignments == 0
    finally:
        provider._config = original_config
        provider._config_models = original_models
        provider.name = original_name
