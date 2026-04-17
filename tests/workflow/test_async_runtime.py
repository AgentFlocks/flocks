"""Tests for the persistent background event loop used by workflow llm calls."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from flocks.workflow import _async_runtime


async def _echo(value):
    await asyncio.sleep(0)
    return value


def test_run_sync_reuses_the_same_background_loop_and_thread():
    """Multiple calls must share one persistent loop / thread."""
    _async_runtime.run_sync(_echo(1))
    loop_a, thread_a = _async_runtime._get_loop_for_testing()

    assert loop_a is not None
    assert thread_a is not None
    assert loop_a.is_running()
    assert thread_a.is_alive()

    for i in range(5):
        assert _async_runtime.run_sync(_echo(i)) == i

    loop_b, thread_b = _async_runtime._get_loop_for_testing()
    assert loop_b is loop_a
    assert thread_b is thread_a
    assert loop_b.is_running()
    assert thread_b.is_alive()


def test_run_sync_handles_concurrent_submissions_without_crosstalk():
    """Concurrent run_sync from many threads must all get their own result back."""
    count = 64

    def _worker(i: int) -> int:
        return _async_runtime.run_sync(_echo(i))

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="test-wf-llm") as pool:
        results = list(pool.map(_worker, range(count)))

    assert sorted(results) == list(range(count))

    loop, thread = _async_runtime._get_loop_for_testing()
    assert loop is not None and loop.is_running()
    assert thread is not None and thread.is_alive()


def test_run_sync_from_inside_the_loop_thread_raises_instead_of_deadlocking():
    """Invoking run_sync from the dedicated loop's own thread would self-deadlock.

    The guard must surface that as RuntimeError instead of hanging the caller.
    """
    loop = _async_runtime._ensure_loop()

    async def _trigger_from_loop():
        _async_runtime.run_sync(_echo("never"))

    future = asyncio.run_coroutine_threadsafe(_trigger_from_loop(), loop)
    with pytest.raises(RuntimeError, match="self-deadlock"):
        future.result(timeout=2.0)
