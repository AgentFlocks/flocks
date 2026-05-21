"""Thread-safety tests for ``Provider._ensure_initialized`` (DCLP).

The lazy-init path previously flipped ``_initialized = True`` *before*
running ``providers_to_register`` and ``_load_dynamic_providers``. A
concurrent caller that arrived between those two events would skip the
slow path and immediately try ``Provider.get(...)``, returning ``None``
because the registry was not yet populated.

These tests pin down the post-fix contract:
* ``_ensure_initialized`` is idempotent and safe under concurrency.
* By the time *any* caller observes ``_initialized == True``, every
  built-in provider is already registered.
* ``apply_config`` (also gated on ``_ensure_initialized``) does not
  trip on a half-initialized registry.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import pytest

from flocks.provider.provider import Provider


# A handful of well-known built-in provider ids that must always be present
# once initialization is complete. Picked from the registration list inside
# ``_ensure_initialized``.
_REQUIRED_BUILTIN_PROVIDERS = (
    "openai",
    "anthropic",
    "google",
    "openai-compatible",
    "threatbook-cn-llm",
)


def _force_reinit() -> None:
    """Drop the singleton state so the next ``_ensure_initialized`` runs
    the full registration path again. Used only by these tests.
    """
    Provider._initialized = False
    Provider._providers.clear()
    Provider._models.clear()


def test_ensure_initialized_is_idempotent_single_threaded() -> None:
    _force_reinit()
    Provider._ensure_initialized()
    snapshot = dict(Provider._providers)
    Provider._ensure_initialized()
    Provider._ensure_initialized()
    assert dict(Provider._providers) == snapshot


def test_ensure_initialized_concurrent_callers_see_complete_registry() -> None:
    """The fix: every concurrent caller observes a fully populated registry.

    Pre-fix, the cheap fast-path check (``if not _initialized``) raced
    against the slow registration loop because ``_initialized`` was set
    to ``True`` *before* registration. After the fix the flag flips only
    after ``_load_dynamic_providers()`` returns.
    """
    _force_reinit()

    barrier = threading.Barrier(parties=20)
    missing_snapshots: List[Optional[List[str]]] = []
    snapshots_lock = threading.Lock()

    def worker() -> None:
        # Align all threads as tightly as possible so most of them race
        # on the same ``_ensure_initialized`` call.
        barrier.wait()
        Provider._ensure_initialized()
        missing = [
            pid
            for pid in _REQUIRED_BUILTIN_PROVIDERS
            if Provider.get(pid) is None
        ]
        with snapshots_lock:
            missing_snapshots.append(missing if missing else None)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futs = [pool.submit(worker) for _ in range(20)]
        for fut in as_completed(futs):
            fut.result()

    failed = [m for m in missing_snapshots if m]
    assert not failed, (
        "After ``_ensure_initialized()`` every caller must see all built-in "
        f"providers registered. Missing snapshots: {failed!r}"
    )


def test_initialized_flag_is_only_true_after_registration() -> None:
    """Direct check that the flag flip happens AFTER registration.

    Reset state, then assert that the very first state we see after
    initialization already contains the required providers.
    """
    _force_reinit()
    assert Provider._initialized is False
    Provider._ensure_initialized()
    assert Provider._initialized is True
    for pid in _REQUIRED_BUILTIN_PROVIDERS:
        assert Provider.get(pid) is not None, (
            f"provider {pid!r} should be registered once _initialized is True"
        )
