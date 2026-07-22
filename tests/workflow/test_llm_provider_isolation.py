"""Regression tests for ``flocks.workflow.llm.LLMClient._prepare_provider``.

The workflow LLM path must no longer mutate the process-wide Provider
singleton that the session / agent runner uses. Instead it should build an
isolated provider instance seeded from the shared provider's current config.

These tests pin down that contract:

* ``_prepare_provider()`` returns a distinct provider instance and leaves the
  shared provider's ``_config`` / ``_client`` untouched.
* workflow-specific overrides (api_key / base_url / trust_env) are applied
  only to the isolated provider instance.
* Providers that are configured purely via runtime/env state (``_api_key``
  with no ``_config``) remain usable when workflow overrides are applied.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from flocks.provider.provider import ProviderConfig
from flocks.workflow import llm as workflow_llm


class _FakeProvider:
    """Minimal stand-in for a registered provider instance."""

    def __init__(
        self,
        provider_id: str = "fake-provider",
        *,
        api_key: Optional[str] = "session-key",
        base_url: Optional[str] = "https://session.example.com",
        custom_settings: Optional[Dict[str, Any]] = None,
        configured: bool = True,
    ) -> None:
        self.id = provider_id
        self.name = f"Provider {provider_id}"
        self._api_key = api_key
        self._base_url = base_url
        self._config: Optional[ProviderConfig] = (
            ProviderConfig(
                provider_id=provider_id,
                api_key=api_key,
                base_url=base_url,
                custom_settings=dict(custom_settings or {}),
            )
            if configured
            else None
        )
        self._config_models: List[str] = ["model-a"]
        self._client: Any = object()
        self.configure_calls: List[ProviderConfig] = []

    def configure(self, config: ProviderConfig) -> None:
        self.configure_calls.append(config)
        self._config = config

    def is_configured(self) -> bool:
        api_key = self._config.api_key if self._config else self._api_key
        return api_key is not None


@pytest.fixture
def fake_provider() -> _FakeProvider:
    return _FakeProvider(
        custom_settings={"trust_env": True, "verify_ssl": False},
    )


@pytest.fixture
def patched_runtime(fake_provider: _FakeProvider):
    """Patch out the IO-heavy bits of ``LLMClient`` for unit testing.

    * ``Provider._ensure_initialized`` becomes a no-op.
    * ``Provider.get`` returns our fake.
    * ``_run_coro_sync`` short-circuits so ``Provider.apply_config`` /
      ``Config.get`` never reach a real event loop.
    """

    def _fake_run_coro_sync(coro):
        # Drain the coroutine to avoid "coroutine was never awaited" warnings.
        try:
            coro.close()
        except Exception:
            pass
        return {}

    with patch.object(
        workflow_llm.Provider, "_ensure_initialized", lambda: None
    ), patch.object(
        workflow_llm.Provider, "get", lambda provider_id: fake_provider
    ), patch.object(
        workflow_llm, "_run_coro_sync", side_effect=_fake_run_coro_sync
    ):
        yield


def _build_client(
    *,
    workflow_trust_env_set: bool = False,
    workflow_trust_env: bool = False,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> workflow_llm.LLMClient:
    """Build an ``LLMClient`` while controlling whether the workflow config
    actually contains a ``trust_env`` key.
    """
    workflow_cfg: Dict[str, Any] = {}
    if workflow_trust_env_set:
        workflow_cfg["trust_env"] = workflow_trust_env

    with patch.object(
        workflow_llm.LLMClient,
        "_load_workflow_llm_config",
        return_value=workflow_cfg,
    ):
        return workflow_llm.LLMClient(
            api_key=api_key,
            base_url=base_url,
            provider_id="fake-provider",
        )


def test_prepare_provider_returns_isolated_instance_when_config_unchanged(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """Workflow should clone the provider instead of mutating the shared singleton."""
    client = _build_client()

    original_client = fake_provider._client
    assert original_client is not None

    prepared = client._prepare_provider("fake-provider")

    assert prepared is not fake_provider
    assert prepared._client is None, (
        "workflow provider should always start with an isolated client cache"
    )
    assert fake_provider._client is original_client, (
        "shared provider client must not be reset by workflow preparation"
    )
    assert fake_provider.configure_calls == []
    assert prepared._config is not None
    assert prepared._config == fake_provider._config
    assert prepared._config_models == fake_provider._config_models
    assert prepared._config_models is not fake_provider._config_models


def test_prepare_provider_does_not_override_session_trust_env(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """If workflow.llm.trust_env is not set, shared custom_settings stay untouched."""
    client = _build_client(
        workflow_trust_env_set=False, workflow_trust_env=False
    )

    prepared = client._prepare_provider("fake-provider")

    assert fake_provider._config is not None
    assert fake_provider._config.custom_settings == {
        "trust_env": True,
        "verify_ssl": False,
    }
    assert fake_provider.configure_calls == []
    assert prepared._config is not None
    assert prepared._config.custom_settings == {
        "trust_env": True,
        "verify_ssl": False,
    }


def test_prepare_provider_overrides_when_workflow_trust_env_explicit(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """If workflow.llm.trust_env IS set, only the isolated provider is overridden."""
    client = _build_client(
        workflow_trust_env_set=True, workflow_trust_env=False
    )

    prepared = client._prepare_provider("fake-provider")

    assert fake_provider.configure_calls == []
    assert fake_provider._config is not None
    assert fake_provider._config.custom_settings == {
        "trust_env": True,
        "verify_ssl": False,
    }
    assert prepared._config is not None
    assert prepared._config.custom_settings == {
        "trust_env": False,
        "verify_ssl": False,
    }
    assert prepared._client is None


def test_prepare_provider_reconfigures_when_api_key_changes(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """A workflow api_key override must only affect the isolated provider."""
    client = _build_client(api_key="workflow-supplied-key")

    prepared = client._prepare_provider("fake-provider")

    assert fake_provider.configure_calls == []
    assert fake_provider._config is not None
    assert fake_provider._config.api_key == "session-key"
    assert prepared._config is not None
    assert prepared._config.api_key == "workflow-supplied-key"
    assert prepared._client is None


def test_prepare_provider_reconfigures_when_base_url_changes(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """A workflow base_url override must only affect the isolated provider."""
    client = _build_client(base_url="https://workflow.example.com")

    prepared = client._prepare_provider("fake-provider")

    assert fake_provider.configure_calls == []
    assert fake_provider._config is not None
    assert fake_provider._config.base_url == "https://session.example.com"
    assert prepared._config is not None
    assert prepared._config.base_url == "https://workflow.example.com"
    assert prepared._client is None


def test_prepare_provider_uses_runtime_api_key_when_shared_config_missing(
    patched_runtime,
) -> None:
    """Workflow overrides must not erase runtime/env credentials.

    Providers may be configured only via constructor/runtime state with
    ``_config is None``. When workflow sets ``trust_env``, the isolated
    provider still needs a usable api_key copied from the shared provider.
    """
    shared_provider = _FakeProvider(
        api_key="runtime-key",
        base_url="https://runtime.example.com",
        custom_settings={"verify_ssl": False},
        configured=False,
    )

    def _fake_run_coro_sync(coro):
        try:
            coro.close()
        except Exception:
            pass
        return {}

    with patch.object(
        workflow_llm.Provider, "_ensure_initialized", lambda: None
    ), patch.object(
        workflow_llm.Provider, "get", lambda provider_id: shared_provider
    ), patch.object(
        workflow_llm, "_run_coro_sync", side_effect=_fake_run_coro_sync
    ):
        client = _build_client(
            workflow_trust_env_set=True,
            workflow_trust_env=False,
        )
        prepared = client._prepare_provider("fake-provider")

    assert shared_provider._config is None
    assert prepared._config is not None
    assert prepared._config.api_key == "runtime-key"
    assert prepared._config.base_url == "https://runtime.example.com"
    assert prepared._config.custom_settings == {"trust_env": False}
