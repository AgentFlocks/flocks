from __future__ import annotations

import asyncio
import threading
import time

import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_provider_route_async_state(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import provider as provider_routes

    monkeypatch.setattr(provider_routes, "_provider_initialization_lock", asyncio.Lock())
    monkeypatch.setattr(provider_routes, "_provider_initialization_task", None)
    monkeypatch.setattr(provider_routes, "_dynamic_provider_load_tasks", set())


async def test_initialized_provider_fast_path_does_not_schedule_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import provider as provider_routes

    async def _unexpected_to_thread(*args, **kwargs):
        raise AssertionError("initialized provider should stay on the fast path")

    monkeypatch.setattr(provider_routes.Provider, "_initialized", True)
    monkeypatch.setattr(provider_routes.asyncio, "to_thread", _unexpected_to_thread)

    await provider_routes._ensure_provider_initialized()


async def test_concurrent_provider_initialization_uses_one_worker(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import provider as provider_routes

    calls = 0

    def _slow_initialize() -> None:
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        provider_routes.Provider._initialized = True

    monkeypatch.setattr(provider_routes.Provider, "_initialized", False)
    monkeypatch.setattr(provider_routes.Provider, "_ensure_initialized", _slow_initialize)

    await asyncio.gather(*[
        provider_routes._ensure_provider_initialized()
        for _ in range(10)
    ])

    assert calls == 1


async def test_dynamic_provider_loading_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import provider as provider_routes

    monkeypatch.setattr(
        provider_routes.Provider,
        "_load_dynamic_providers",
        lambda: time.sleep(0.05),
    )

    load_task = asyncio.create_task(provider_routes._load_dynamic_providers())
    heartbeat_ticks = 0
    while not load_task.done():
        await asyncio.sleep(0.005)
        heartbeat_ticks += 1
    await load_task

    assert heartbeat_ticks >= 3


async def test_concurrent_dynamic_load_triggers_are_serialized_but_not_merged(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import provider as provider_routes

    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    calls = 0
    config_version = "provider-a"
    scanned_versions: list[str] = []
    active_workers = 0
    max_active_workers = 0

    def _blocking_load() -> None:
        nonlocal calls, active_workers, max_active_workers
        calls += 1
        scanned_versions.append(config_version)
        active_workers += 1
        max_active_workers = max(max_active_workers, active_workers)
        try:
            if calls == 1:
                first_started.set()
                release_first.wait(timeout=1)
            else:
                second_started.set()
        finally:
            active_workers -= 1

    monkeypatch.setattr(
        provider_routes.Provider,
        "_load_dynamic_providers",
        _blocking_load,
    )

    first = asyncio.create_task(provider_routes._load_dynamic_providers())
    while not first_started.is_set():
        await asyncio.sleep(0.001)

    config_version = "provider-b"
    second = asyncio.create_task(provider_routes._load_dynamic_providers())
    try:
        await asyncio.sleep(0.01)
        assert calls == 1
    finally:
        release_first.set()

    await asyncio.wait_for(asyncio.gather(first, second), timeout=0.2)
    assert second_started.is_set()
    assert calls == 2
    assert scanned_versions == ["provider-a", "provider-b"]
    assert max_active_workers == 1


async def test_cancelled_dynamic_load_waiter_keeps_its_scan_and_serial_order(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import provider as provider_routes

    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    calls = 0
    active_workers = 0
    max_active_workers = 0

    def _blocking_load() -> None:
        nonlocal calls, active_workers, max_active_workers
        calls += 1
        active_workers += 1
        max_active_workers = max(max_active_workers, active_workers)
        try:
            if calls == 1:
                first_started.set()
                release_first.wait(timeout=1)
            else:
                second_started.set()
        finally:
            active_workers -= 1

    monkeypatch.setattr(
        provider_routes.Provider,
        "_load_dynamic_providers",
        _blocking_load,
    )

    cancelled_waiter = asyncio.create_task(provider_routes._load_dynamic_providers())
    while not first_started.is_set():
        await asyncio.sleep(0.001)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    second = asyncio.create_task(provider_routes._load_dynamic_providers())
    try:
        await asyncio.sleep(0.01)
        assert calls == 1
    finally:
        release_first.set()

    await asyncio.wait_for(second, timeout=0.2)
    assert second_started.is_set()
    assert calls == 2
    assert max_active_workers == 1


async def test_list_providers_initialization_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import provider as provider_routes

    def _slow_initialize() -> None:
        time.sleep(0.1)

    async def _config():
        raise RuntimeError("no config in focused route test")

    monkeypatch.setattr(provider_routes.Provider, "_initialized", False)
    monkeypatch.setattr(provider_routes.Provider, "_ensure_initialized", _slow_initialize)
    monkeypatch.setattr(provider_routes.Config, "get", _config)
    monkeypatch.setattr(provider_routes.ConfigWriter, "list_provider_ids", lambda: [])

    route_task = asyncio.create_task(provider_routes.list_providers())
    heartbeat_ticks = 0
    while not route_task.done():
        await asyncio.sleep(0.005)
        heartbeat_ticks += 1

    response = await route_task

    assert heartbeat_ticks >= 3
    assert response.all == []
