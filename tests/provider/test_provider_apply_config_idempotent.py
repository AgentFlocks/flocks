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


def _build_fake_config(provider_id: str) -> Any:
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
