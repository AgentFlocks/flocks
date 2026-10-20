import asyncio
import gc
import re
import time
import warnings

import pytest

import flocks.extensions as extensions
import flocks.server.auth as auth
from flocks.extensions import (
    register_public_path,
    register_shutdown_hook,
    register_startup_hook,
    run_shutdown_hooks,
    run_startup_hooks,
)
from flocks.server.auth import auth_middleware_exempt, register_public_path_pattern

WEBHOOK_PATTERN = r"^/api/ext/v1/webhooks/[A-Za-z0-9_-]{1,64}/?$"


@pytest.fixture
def isolated_registries(monkeypatch):
    monkeypatch.setattr(auth, "_EXTENSION_PUBLIC_PATH_REGEXES", [])
    monkeypatch.setattr(extensions, "_shutdown_hooks", [])
    monkeypatch.setattr(extensions, "_startup_hooks", [])


def test_registered_public_path_exempts_only_full_matches(isolated_registries):
    path = "/api/ext/v1/webhooks/repo-1"
    assert not auth_middleware_exempt(path)

    register_public_path(WEBHOOK_PATTERN)
    register_public_path(re.compile(WEBHOOK_PATTERN))

    assert len(auth._EXTENSION_PUBLIC_PATH_REGEXES) == 1
    assert auth_middleware_exempt(path)
    assert auth_middleware_exempt(path + "/")
    assert not auth_middleware_exempt(path + "/retry")
    assert not auth_middleware_exempt(path + "\n")
    assert not auth_middleware_exempt("/api/ext/v1/tasks")


@pytest.mark.parametrize(
    "pattern",
    [
        # Each alternative would prefix-match on its own under re.match.
        r"^/api/ext/v1/webhooks/repo|/api/ext/v1/webhooks/other$",
        r"/api/ext/v1/webhooks/repo",
    ],
)
def test_public_path_alternations_and_missing_anchors_cannot_open_a_prefix(isolated_registries, pattern):
    register_public_path(pattern)

    assert auth_middleware_exempt("/api/ext/v1/webhooks/repo")
    assert not auth_middleware_exempt("/api/ext/v1/webhooks/repo/retry")
    assert not auth_middleware_exempt("/api/ext/v1/webhooks/repository")


def test_the_same_pattern_with_other_flags_is_registered_separately(isolated_registries):
    first = register_public_path_pattern(WEBHOOK_PATTERN)
    assert register_public_path_pattern(re.compile(WEBHOOK_PATTERN)) is first
    insensitive = register_public_path_pattern(re.compile(WEBHOOK_PATTERN, re.IGNORECASE))
    assert insensitive is not first and len(auth._EXTENSION_PUBLIC_PATH_REGEXES) == 2
    assert auth_middleware_exempt("/API/EXT/v1/webhooks/repo")


@pytest.mark.parametrize("pattern", [None, 123, b"^/api/ext/v1/hooks$", re.compile(rb"^/api/ext/v1/hooks$")])
def test_public_path_registration_rejects_non_text_patterns(isolated_registries, pattern):
    with pytest.raises(TypeError):
        register_public_path(pattern)

    assert auth._EXTENSION_PUBLIC_PATH_REGEXES == []
    assert not auth_middleware_exempt("/api/session")


@pytest.mark.asyncio
async def test_shutdown_hooks_run_in_order_and_never_block_shutdown(isolated_registries):
    calls: list[str] = []

    def failing() -> None:
        calls.append("failing")
        raise RuntimeError("boom")

    async def slow() -> None:
        calls.append("slow")
        await asyncio.sleep(10)

    async def finished() -> None:
        calls.append("async")

    async def returned() -> None:
        calls.append("returned coroutine")

    register_shutdown_hook("failing", failing)
    register_shutdown_hook("slow", slow, timeout_seconds=0.05)
    register_shutdown_hook("async", finished)
    register_shutdown_hook("lambda", lambda: returned())
    register_shutdown_hook("sync", lambda: calls.append("replaced"))
    register_shutdown_hook("sync", lambda: calls.append("sync"))

    await asyncio.wait_for(run_shutdown_hooks(), timeout=2)

    assert calls == ["failing", "slow", "async", "returned coroutine", "sync"]


@pytest.mark.asyncio
async def test_blocking_sync_hook_keeps_the_loop_free_and_times_out(isolated_registries):
    calls: list[str] = []
    register_shutdown_hook("blocking", lambda: time.sleep(1.0), timeout_seconds=0.05)
    register_shutdown_hook("after", lambda: calls.append("after"))

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    ticking = asyncio.create_task(ticker())
    started = time.monotonic()
    await asyncio.wait_for(run_shutdown_hooks(), timeout=0.5)
    elapsed = time.monotonic() - started
    ticking.cancel()

    assert elapsed < 0.5
    assert ticks > 3  # the loop kept running while the hook blocked its thread
    assert calls == ["after"]


@pytest.mark.asyncio
async def test_hook_that_lets_its_own_cancellation_escape_does_not_stop_later_hooks(isolated_registries):
    calls: list[str] = []

    async def cancels_own_child() -> None:
        child = asyncio.create_task(asyncio.sleep(10))
        await asyncio.sleep(0)
        child.cancel()
        await child

    register_shutdown_hook("cancelling", cancels_own_child)
    register_shutdown_hook("after", lambda: calls.append("after"))

    await run_shutdown_hooks()

    assert calls == ["after"]


@pytest.mark.asyncio
async def test_cancelling_the_shutdown_itself_still_propagates(isolated_registries):
    calls: list[str] = []
    started = asyncio.Event()

    async def waiting() -> None:
        started.set()
        await asyncio.sleep(10)

    register_shutdown_hook("waiting", waiting)
    register_shutdown_hook("after", lambda: calls.append("after"))

    shutdown = asyncio.create_task(run_shutdown_hooks())
    await started.wait()
    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown

    assert calls == []


@pytest.mark.asyncio
async def test_hook_that_ignores_cancellation_is_left_behind_at_its_timeout(isolated_registries):
    calls: list[str] = []
    release = asyncio.Event()

    async def stubborn() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await release.wait()

    register_shutdown_hook("stubborn", stubborn, timeout_seconds=0.05)
    register_shutdown_hook("after", lambda: calls.append("after"))

    started = time.monotonic()
    await asyncio.wait_for(run_shutdown_hooks(), timeout=1)

    assert time.monotonic() - started < 0.5
    assert calls == ["after"]
    release.set()
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_coroutine_returned_after_the_timeout_is_closed(isolated_registries):
    async def cleanup() -> None:
        raise AssertionError("must not run after its timeout")

    def slow_factory():
        time.sleep(0.2)
        return cleanup()

    register_shutdown_hook("late", slow_factory, timeout_seconds=0.05)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await run_shutdown_hooks()
        await asyncio.sleep(0.4)
        gc.collect()

    assert not [warning for warning in caught if "never awaited" in str(warning.message)]


@pytest.mark.asyncio
async def test_cancelled_shutdown_still_reads_a_hook_failure_raised_while_cleaning_up(isolated_registries):
    started = asyncio.Event()
    reported: list[dict] = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))

    async def failing_cleanup() -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            raise RuntimeError("cleanup failed")

    register_shutdown_hook("failing-cleanup", failing_cleanup)
    shutdown = asyncio.create_task(run_shutdown_hooks())
    await started.wait()
    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    await asyncio.sleep(0.01)
    gc.collect()
    await asyncio.sleep(0.01)
    loop.set_exception_handler(None)

    assert not [context for context in reported if "never retrieved" in str(context.get("message"))]


@pytest.mark.asyncio
async def test_startup_hooks_run_apart_from_shutdown_hooks_and_never_stop_each_other(isolated_registries):
    calls: list[str] = []

    def failing() -> None:
        calls.append("failing")
        raise RuntimeError("boom")

    async def slow() -> None:
        calls.append("slow")
        await asyncio.sleep(10)

    async def service() -> None:
        calls.append("service")

    register_startup_hook("failing", failing)
    register_startup_hook("slow", slow, timeout_seconds=0.05)
    register_startup_hook("service", lambda: calls.append("replaced"))
    register_startup_hook("service", service)
    register_shutdown_hook("service", lambda: calls.append("stopped"))

    await asyncio.wait_for(run_startup_hooks(), timeout=2)
    assert calls == ["failing", "slow", "service"]

    await asyncio.wait_for(run_shutdown_hooks(), timeout=2)
    assert calls == ["failing", "slow", "service", "stopped"]


@pytest.mark.asyncio
async def test_a_cancelled_startup_ends_once_the_running_hook_has_cleaned_up(isolated_registries):
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def starting() -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            await asyncio.sleep(0.05)  # stopping what it had started
            cleaned.set()

    register_startup_hook("starting", starting)
    startup = asyncio.create_task(run_startup_hooks())
    await started.wait()
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup

    # The lifespan runs the shutdown hooks only after this, so they never overlap the cleanup.
    assert cleaned.is_set()
