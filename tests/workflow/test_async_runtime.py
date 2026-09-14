"""Tests for the persistent background event loop used by workflow llm calls."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
import threading

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


def test_run_sync_re_raises_cancellation_as_asyncio_cancelled_error():
    """Cancellation must propagate as asyncio.CancelledError, not as the
    concurrent.futures.CancelledError that run_coroutine_threadsafe yields.

    In Python 3.12 asyncio.CancelledError inherits from BaseException (so it
    flows past ``except Exception``), while concurrent.futures.CancelledError
    inherits from Exception and would be swallowed by workflow retry/fallback
    logic. ``run_sync`` must preserve the stricter asyncio semantics.
    """
    async def _self_cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        _async_runtime.run_sync(_self_cancel())

    async def _never_returns():
        await asyncio.sleep(5)

    loop = _async_runtime._ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(_never_returns(), loop)
    fut.cancel()
    import concurrent.futures as _cf
    with pytest.raises(_cf.CancelledError):
        fut.result(timeout=2.0)


def test_run_sync_cancellation_is_not_swallowed_by_except_exception():
    """Guards the specific regression reviewed: a try/except Exception wrapper
    (mirroring LLMClient.ask()'s retry loop) must NOT absorb the cancellation."""
    async def _self_cancel():
        raise asyncio.CancelledError()

    swallowed = False
    try:
        try:
            _async_runtime.run_sync(_self_cancel())
        except Exception:
            swallowed = True
    except asyncio.CancelledError:
        pass

    assert swallowed is False, (
        "run_sync leaked concurrent.futures.CancelledError (Exception subclass); "
        "LLMClient.ask() retry/fallback would silently eat a real cancellation."
    )


@pytest.mark.parametrize("cancellable", [False, True])
def test_run_sync_from_inside_the_loop_thread_raises_instead_of_deadlocking(cancellable):
    """Invoking run_sync from the dedicated loop's own thread would self-deadlock.

    The guard must surface that as RuntimeError instead of hanging the caller.
    """
    loop = _async_runtime._ensure_loop()

    async def _trigger_from_loop():
        if cancellable:
            _async_runtime.run_sync_cancellable(_echo("never"), lambda: False)
        else:
            _async_runtime.run_sync(_echo("never"))

    future = asyncio.run_coroutine_threadsafe(_trigger_from_loop(), loop)
    with pytest.raises(RuntimeError, match="self-deadlock"):
        future.result(timeout=2.0)


@pytest.mark.parametrize("use_wait_for", [False, True])
def test_cancellable_propagates_coroutine_timeout_without_traceback_growth(monkeypatch, use_wait_for):
    """A failed Future must not be polled forever as if it were unfinished."""
    submitted = []
    submit = asyncio.run_coroutine_threadsafe

    def capture_submission(coro, loop):
        future = submit(coro, loop)
        submitted.append(future)
        return future

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", capture_submission)
    polls = 0

    def never_cancel():
        nonlocal polls
        polls += 1
        # Bound failures on old code: do not let a regression OOM the test host.
        assert polls < 1000, "completed timeout Future was polled repeatedly"
        return False

    async def timed_out():
        if use_wait_for:
            await asyncio.wait_for(asyncio.sleep(60), timeout=0.005)
        else:
            raise TimeoutError("upstream timed out")

    with pytest.raises(TimeoutError):
        _async_runtime.run_sync_cancellable(timed_out(), never_cancel, poll_interval_s=0.001)

    traceback = submitted[0].exception().__traceback__
    depth = 0
    while traceback is not None:
        depth += 1
        traceback = traceback.tb_next
    assert depth < 30
    assert _async_runtime.run_sync(_echo("loop still usable")) == "loop still usable"


def test_cancellable_keeps_polling_an_unfinished_future():
    polls = 0
    release = threading.Event()

    def never_cancel():
        nonlocal polls
        polls += 1
        assert polls < 1000
        if polls >= 2:
            release.set()
        return False

    async def delayed_result():
        while not release.is_set():
            await asyncio.sleep(0.001)
        return "done"

    assert _async_runtime.run_sync_cancellable(
        delayed_result(), never_cancel, poll_interval_s=0.001,
    ) == "done"
    assert polls > 1


@pytest.mark.parametrize("outcome", ["success", "cancelled", "error"])
def test_cancellable_resolves_completion_racing_a_poll_timeout(monkeypatch, outcome):
    class RacingFuture(Future):
        def result(self, timeout=None):
            if not self.done():
                if outcome == "success":
                    self.set_result("done")
                elif outcome == "cancelled":
                    self.cancel()
                else:
                    self.set_exception(ValueError("upstream failure"))
                raise TimeoutError("poll expired immediately before completion")
            return super().result(timeout=timeout)

    future = RacingFuture()

    def submit(coro, loop):
        coro.close()
        return future

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            _async_runtime.run_sync_cancellable(_echo(None), lambda: False)
    elif outcome == "error":
        with pytest.raises(ValueError, match="upstream failure"):
            _async_runtime.run_sync_cancellable(_echo(None), lambda: False)
    else:
        assert _async_runtime.run_sync_cancellable(_echo(None), lambda: False) == "done"


def test_cancellable_propagates_coroutine_cancellation():
    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        _async_runtime.run_sync_cancellable(cancelled(), lambda: False)


def test_cancellable_operator_cancel_releases_the_waiting_coroutine():
    started = threading.Event()
    stopped = threading.Event()

    async def waiting():
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()

    with pytest.raises(asyncio.CancelledError):
        _async_runtime.run_sync_cancellable(waiting(), started.is_set, poll_interval_s=0.001)
    assert stopped.wait(timeout=2), "cancelled coroutine did not run its cleanup"


def test_cancellable_parallel_timeouts_do_not_poison_other_calls():
    def worker(index):
        polls = 0

        def never_cancel():
            nonlocal polls
            polls += 1
            assert polls < 1000
            return False

        async def call():
            await asyncio.sleep(0)
            if index % 2:
                raise TimeoutError(f"timeout-{index}")
            return index

        if index % 2:
            with pytest.raises(TimeoutError, match=f"^timeout-{index}$"):
                _async_runtime.run_sync_cancellable(call(), never_cancel, poll_interval_s=0.001)
            return index
        return _async_runtime.run_sync_cancellable(call(), never_cancel, poll_interval_s=0.001)

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(worker, range(200))) == list(range(200))


@pytest.mark.parametrize("method", ["run", "run_safe"])
def test_cancellable_tool_adapter_propagates_tool_timeout(monkeypatch, method):
    from flocks.tool import ToolRegistry
    from flocks.workflow.tools_adapter import FlocksToolAdapter

    async def execute(*args, **kwargs):
        await asyncio.wait_for(asyncio.sleep(60), timeout=0.005)

    monkeypatch.setattr(ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(ToolRegistry, "get", lambda name: object())
    monkeypatch.setattr(ToolRegistry, "execute", execute)
    polls = 0

    def never_cancel():
        nonlocal polls
        polls += 1
        assert polls < 1000, "tool timeout was swallowed by cancellation polling"
        return False

    adapter = FlocksToolAdapter().with_cancel_checker(never_cancel)
    with pytest.raises(TimeoutError):
        getattr(adapter, method)("synthetic-timeout")
