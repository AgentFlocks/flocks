"""Regression tests for ``flocks.workflow.llm.LLMClient._prepare_provider``.

These tests pin down the contract that protects concurrent ``session`` /
``agent`` callers when a workflow runs ``llm.ask()`` against the same
``Provider`` singleton:

* The reconfigure-and-reset sequence is **idempotent**: if the workflow's
  desired config matches what the provider already holds, neither
  ``provider.configure(...)`` nor the ``provider._client = None`` reset is
  performed. This keeps long-running httpx connection pools from being
  thrown away on every workflow LLM call.
* ``trust_env`` is only overridden when the user explicitly set
  ``workflow.llm.trust_env`` in flocks config. Otherwise any value that the
  session previously placed in ``provider._config.custom_settings`` is
  preserved untouched.
* When the config does materially change (e.g. api_key flip), the
  reconfigure path still runs and ``_client`` is reset, as before.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from flocks.provider.provider import ProviderConfig
from flocks.workflow import llm as workflow_llm


class _FakeProvider:
    """Minimal stand-in for a registered ``BaseProvider`` instance.

    Records every ``configure`` invocation so tests can assert idempotency.
    """

    def __init__(
        self,
        provider_id: str = "fake-provider",
        *,
        api_key: Optional[str] = "session-key",
        base_url: Optional[str] = "https://session.example.com",
        custom_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.id = provider_id
        self._config: Optional[ProviderConfig] = ProviderConfig(
            provider_id=provider_id,
            api_key=api_key,
            base_url=base_url,
            custom_settings=dict(custom_settings or {}),
        )
        self._client: Any = object()
        self.configure_calls: List[ProviderConfig] = []

    def configure(self, config: ProviderConfig) -> None:
        self.configure_calls.append(config)
        self._config = config

    def is_configured(self) -> bool:
        return self._config is not None and self._config.api_key is not None


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


def test_prepare_provider_is_idempotent_when_config_unchanged(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """No workflow overrides + no explicit trust_env => no reconfigure, no client reset."""
    client = _build_client()

    original_client = fake_provider._client
    assert original_client is not None

    client._prepare_provider("fake-provider")
    client._prepare_provider("fake-provider")
    client._prepare_provider("fake-provider")

    assert fake_provider.configure_calls == [], (
        "expected zero reconfigure calls when desired config matches existing"
    )
    assert fake_provider._client is original_client, (
        "expected provider._client to be preserved across idempotent calls"
    )


def test_prepare_provider_does_not_override_session_trust_env(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """If workflow.llm.trust_env is not set, session-supplied custom_settings stay intact."""
    client = _build_client(
        workflow_trust_env_set=False, workflow_trust_env=False
    )

    client._prepare_provider("fake-provider")

    assert fake_provider._config is not None
    assert fake_provider._config.custom_settings == {
        "trust_env": True,
        "verify_ssl": False,
    }
    assert fake_provider.configure_calls == [], (
        "trust_env was not explicitly set -> provider.configure must not be called"
    )


def test_prepare_provider_overrides_when_workflow_trust_env_explicit(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """If workflow.llm.trust_env IS set, override custom_settings and reset _client."""
    client = _build_client(
        workflow_trust_env_set=True, workflow_trust_env=False
    )

    client._prepare_provider("fake-provider")

    assert len(fake_provider.configure_calls) == 1
    applied = fake_provider.configure_calls[0]
    assert applied.custom_settings is not None
    assert applied.custom_settings.get("trust_env") is False
    # verify_ssl was carried over from the session-side custom_settings
    assert applied.custom_settings.get("verify_ssl") is False
    assert fake_provider._client is None, (
        "trust_env actually changed -> the SDK client must be reset"
    )


def test_prepare_provider_reconfigures_when_api_key_changes(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """A new api_key is a material change -> reconfigure + client reset."""
    client = _build_client(api_key="workflow-supplied-key")

    client._prepare_provider("fake-provider")

    assert len(fake_provider.configure_calls) == 1
    assert fake_provider.configure_calls[0].api_key == "workflow-supplied-key"
    assert fake_provider._client is None


def test_prepare_provider_reconfigures_when_base_url_changes(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """A new base_url is also a material change."""
    client = _build_client(base_url="https://workflow.example.com")

    client._prepare_provider("fake-provider")

    assert len(fake_provider.configure_calls) == 1
    assert (
        fake_provider.configure_calls[0].base_url
        == "https://workflow.example.com"
    )
    assert fake_provider._client is None


def test_get_provider_lock_is_per_provider(patched_runtime) -> None:
    """Same provider_id returns the same lock instance; different ids get different locks."""
    lock_a1 = workflow_llm._get_provider_lock("fake-provider")
    lock_a2 = workflow_llm._get_provider_lock("fake-provider")
    lock_b = workflow_llm._get_provider_lock("other-provider")

    assert lock_a1 is lock_a2
    assert lock_a1 is not lock_b


def test_prepare_provider_resets_client_when_owning_loop_changes(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """If the existing _client belongs to a different loop, reset it even when config is unchanged.

    This guards against ``httpx.AsyncClient`` cross-loop reuse: a session
    bound the client to uvicorn's main loop, the workflow loop must not
    inherit it without rebuilding on the workflow loop.
    """
    client = _build_client()
    # Simulate the workflow loop having id=999, while ``_client`` was last
    # used on a different loop (e.g. the session's main loop with id=111).
    with patch.object(workflow_llm, "_workflow_loop_id", return_value=999):
        workflow_llm._provider_client_loop_marker[fake_provider] = 111

        original_client = fake_provider._client
        assert original_client is not None

        client._prepare_provider("fake-provider")

        # Even though no config field changed, the client must be reset so
        # the next ``_get_client()`` rebuilds it on the workflow loop.
        assert fake_provider._client is None
        # And the marker is updated to the workflow loop.
        assert workflow_llm._provider_client_loop_marker.get(fake_provider) == 999


def test_prepare_provider_keeps_client_when_owning_loop_matches(
    patched_runtime, fake_provider: _FakeProvider
) -> None:
    """When the existing _client already belongs to the workflow loop, do not reset."""
    client = _build_client()
    with patch.object(workflow_llm, "_workflow_loop_id", return_value=555):
        workflow_llm._provider_client_loop_marker[fake_provider] = 555

        original_client = fake_provider._client
        client._prepare_provider("fake-provider")

        assert fake_provider._client is original_client
        assert fake_provider.configure_calls == []
