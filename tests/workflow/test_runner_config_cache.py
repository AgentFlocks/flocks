"""Regression tests for ``flocks.workflow.runner._load_config_data``.

The TTL cache was introduced to stop ``asyncio.run(Config.get())`` from
spawning a fresh ``_UnixSelectorEventLoop`` (and its self-pipe ``socketpair``
FDs) on every workflow execution, which under high-frequency syslog loads
exhausted the process FD limit.  These tests pin the externally observable
behaviour of the cache so that future refactors do not silently regress to
the old per-call-fetch behaviour.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from flocks.workflow import runner as runner_module


class _FakeConfig:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self, **_kwargs: Any) -> Dict[str, Any]:
        return dict(self._payload)


@pytest.fixture(autouse=True)
def _reset_config_cache() -> None:
    """Each test starts from a clean cache and restores it afterwards."""
    runner_module._config_cache = {}
    runner_module._config_cache_ts = 0.0
    yield
    runner_module._config_cache = {}
    runner_module._config_cache_ts = 0.0


def _install_fake_config_get(
    monkeypatch: pytest.MonkeyPatch,
    payload: Dict[str, Any],
    *,
    calls: Dict[str, int] | None = None,
    raises_after: int | None = None,
) -> Dict[str, int]:
    """Replace ``Config.get`` with an awaitable that returns ``payload``.

    ``Config.get`` is a coroutine; ``runner._load_config_data`` calls
    ``_run_coro_sync(Config.get())`` so the stub must itself be awaitable
    (i.e. an ``async def``).  ``_run_coro_sync`` is also patched so the test
    avoids spawning real event loops.
    """
    state: Dict[str, int] = calls if calls is not None else {"n": 0}

    async def _fake_get() -> _FakeConfig:
        state["n"] += 1
        if raises_after is not None and state["n"] > raises_after:
            raise RuntimeError("transient")
        return _FakeConfig(payload)

    monkeypatch.setattr(runner_module.Config, "get", staticmethod(_fake_get))

    # Drive the coroutine synchronously without standing up a real event
    # loop (that is exactly what the cache is meant to avoid).
    def _runner_run_coro_sync(coro):
        try:
            coro.send(None)
        except StopIteration as stop:
            return stop.value
        raise AssertionError("fake Config.get must not suspend")

    monkeypatch.setattr(runner_module, "_run_coro_sync", _runner_run_coro_sync)
    return state


def test_load_config_data_caches_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Within the TTL window ``Config.get`` must be invoked exactly once."""
    fake_payload = {"sandbox": {"mode": "off"}}
    calls = _install_fake_config_get(monkeypatch, fake_payload)

    result_a = runner_module._load_config_data()
    result_b = runner_module._load_config_data()
    result_c = runner_module._load_config_data()

    assert result_a == fake_payload
    assert result_b == fake_payload
    assert result_c == fake_payload
    # The whole point of the cache is to amortise the underlying call.
    assert calls["n"] == 1


def test_load_config_data_reloads_after_ttl_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the TTL elapses ``Config.get`` must be invoked again."""
    fake_payload = {"sandbox": {"mode": "off"}}
    calls = _install_fake_config_get(monkeypatch, fake_payload)

    runner_module._load_config_data()
    assert calls["n"] == 1

    # Simulate the TTL elapsing by rewinding the cache timestamp.
    runner_module._config_cache_ts = (
        runner_module._config_cache_ts - runner_module._config_cache_ttl - 1.0
    )
    runner_module._load_config_data()
    assert calls["n"] == 2


def test_load_config_data_returns_a_shallow_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Top-level key reassignment in the caller's snapshot must not leak.

    The contract is a *shallow* ``dict(_config_cache)`` copy — nested
    structures are intentionally shared (cheap, and callers are expected
    to treat the result as read-only).  This test pins the shallow-copy
    contract that the cache implementation relies on so a future change
    to ``return _config_cache`` (no copy) is caught.
    """
    fake_payload = {"sandbox": {"mode": "off"}, "other": {"k": "v"}}
    _install_fake_config_get(monkeypatch, fake_payload)

    snapshot = runner_module._load_config_data()
    snapshot["sandbox"] = {"mode": "tampered"}  # replace top-level key
    snapshot["new_top_level"] = "added"

    fresh = runner_module._load_config_data()
    # Top-level mutations on the caller copy must not leak back to the cache.
    assert fresh["sandbox"] == {"mode": "off"}
    assert "new_top_level" not in fresh


def test_load_config_data_falls_back_to_cache_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient ``Config.get`` failure must reuse the last good snapshot."""
    fake_payload = {"sandbox": {"mode": "off"}}
    calls = _install_fake_config_get(monkeypatch, fake_payload, raises_after=1)

    # First call seeds the cache successfully.
    assert runner_module._load_config_data() == fake_payload
    assert calls["n"] == 1

    # Expire the TTL to force a refresh; the underlying call will now raise.
    runner_module._config_cache_ts = (
        runner_module._config_cache_ts - runner_module._config_cache_ttl - 1.0
    )

    # Cached snapshot survives the transient failure.
    assert runner_module._load_config_data() == fake_payload
    assert calls["n"] == 2
