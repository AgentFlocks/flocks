"""Persistent background event loop for workflow-driven async calls.

Workflow code runs synchronously (engine + parallel node executor), but the
provider SDKs it invokes (OpenAI / httpx / anyio) expose async APIs and hold
loop-bound resources such as ``httpx.AsyncClient`` connection pools.

Previously every ``_run_coro_sync`` call spun up an ephemeral event loop via
``asyncio.run``. When the upstream server cut the stream short (e.g. 504 TTFT)
the OpenAI stream object retained a reference to the httpx client; once the
ephemeral loop was closed, later garbage-collection attempts to ``aclose()``
the hanging stream hit ``RuntimeError: Event loop is closed`` and dumped a
noisy (but functionally harmless) traceback on stderr.

This module provides a single, long-lived ``asyncio`` loop running on a
dedicated daemon thread. ``run_sync`` submits coroutines to it via
``run_coroutine_threadsafe`` and blocks on the returned ``Future``. Because
the loop never closes during the process lifetime, any late async cleanup
(stream ``aclose``, GC, etc.) always runs on a live loop.

Loop affinity with the server:
    Hook pipelines run inside these coroutines (``llm.before`` redaction,
    ``tool.execute.before`` gates) and may hold resources bound to the loop
    that created them -- the commercial extension keeps one ``grpc.aio``
    channel on the server's main loop. Awaiting such a resource from a
    different loop fails with "attached to a different loop", so when the
    server has registered its loop (:func:`register_primary_loop`) workflow
    coroutines are submitted there instead of to the private loop. The
    private loop remains the fallback for CLI / test processes without a
    running server loop, and for callers already executing on the primary
    loop's thread (submitting there would self-deadlock).

Design notes:
    * Lazy initialization guarded by a module-level ``threading.Lock`` so
      concurrent first-callers cannot start multiple loops.
    * The backing thread is a daemon -> no explicit shutdown hook needed;
      it exits automatically when the interpreter tears down.
    * Calling ``run_sync`` from inside the dedicated loop's own thread would
      deadlock (``Future.result()`` would wait for a coroutine that can only
      run after the caller returns). We detect that case and raise instead
      of hanging, to surface mis-wired callers loudly.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Callable, Coroutine

__all__ = [
    "run_sync",
    "run_sync_cancellable",
    "register_primary_loop",
    "primary_loop",
    "_get_loop_for_testing",
]


_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_primary_loop: asyncio.AbstractEventLoop | None = None


def register_primary_loop(loop: asyncio.AbstractEventLoop | None = None) -> asyncio.AbstractEventLoop:
    """Record the server's main event loop as the preferred target for workflow coroutines.

    Called from async server entry points (startup sync, run/trigger/poller
    dispatch). Idempotent; the latest registered loop wins so a restarted
    in-process server replaces a stale, closed loop.
    """
    global _primary_loop
    if loop is None:
        loop = asyncio.get_running_loop()
    _primary_loop = loop
    return loop


def primary_loop() -> asyncio.AbstractEventLoop | None:
    """Return the registered server loop when it is still usable, else ``None``."""
    loop = _primary_loop
    if loop is None or loop.is_closed() or not loop.is_running():
        return None
    return loop


def _current_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _select_loop() -> asyncio.AbstractEventLoop:
    """Prefer the server loop; fall back to the private loop when unusable.

    The private loop is also used when the caller is *on* the server loop's
    thread: blocking there on a future that needs the same loop would hang.
    """
    primary = primary_loop()
    if primary is not None and _current_loop() is not primary:
        return primary
    return _ensure_loop()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Return a running background loop, starting it on first use."""
    global _loop, _thread

    if _loop is not None and _loop.is_running():
        return _loop

    with _lock:
        if _loop is not None and _loop.is_running():
            return _loop

        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _runner() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            try:
                loop.run_forever()
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        thread = threading.Thread(
            target=_runner,
            name="flocks-workflow-llm-loop",
            daemon=True,
        )
        thread.start()
        ready.wait()

        _loop = loop
        _thread = thread

    return _loop


def _assert_not_workflow_loop(coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop) -> None:
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if current is loop:
        # Prevent "coroutine was never awaited" warnings on the misuse path;
        # run_coroutine_threadsafe never got a chance to take ownership.
        try:
            coro.close()
        except Exception:
            pass
        raise RuntimeError(
            "run_sync cannot be invoked from the dedicated workflow loop "
            "(would self-deadlock); await the coroutine directly instead."
        )


def run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run *coro* on the shared background loop and return its result.

    Blocks the caller's thread until the coroutine completes, mirroring the
    semantics of the previous ``asyncio.run``-based implementation.

    Raises:
        RuntimeError: invoked from the dedicated loop's own thread (would
            self-deadlock). Call sites from inside the loop should simply
            ``await`` the coroutine directly.
    """
    loop = _select_loop()
    _assert_not_workflow_loop(coro, loop)

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result()
    except concurrent.futures.CancelledError as exc:
        # Preserve the cancellation semantics of the previous asyncio.run()
        # path: callers (e.g. LLMClient.ask's `except Exception` retry loop,
        # or an outer asyncio runtime tearing down the request) rely on
        # cancellation propagating past `except Exception`. In Python 3.12
        # asyncio.CancelledError inherits from BaseException, but
        # concurrent.futures.CancelledError still inherits from Exception
        # and would otherwise be silently swallowed and retried / rewrapped
        # as ValueError by workflow fallback logic.
        raise asyncio.CancelledError() from exc


def run_sync_cancellable(
    coro: Coroutine[Any, Any, Any],
    cancel_checker: Callable[[], bool],
    *,
    poll_interval_s: float = 0.1,
) -> Any:
    """Run *coro* on the shared loop while polling a sync cancellation hook."""
    loop = _select_loop()
    _assert_not_workflow_loop(coro, loop)

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        while True:
            if cancel_checker():
                future.cancel()
                raise asyncio.CancelledError()
            try:
                return future.result(timeout=poll_interval_s)
            except concurrent.futures.TimeoutError:
                # A completed coroutine can itself raise TimeoutError (also
                # used by asyncio.wait_for). It is indistinguishable by type
                # from an unfinished Future's polling timeout. Retrying a
                # failed Future spins without waiting and keeps extending the
                # same exception's traceback, consuming unbounded memory.
                if future.done():
                    # Resolve outside the polling try/except. This also handles
                    # success or cancellation racing the timed-out wait.
                    return future.result()
    except concurrent.futures.CancelledError as exc:
        raise asyncio.CancelledError() from exc


def _clear_primary_loop_for_testing() -> None:
    global _primary_loop
    _primary_loop = None


def _get_loop_for_testing() -> tuple[asyncio.AbstractEventLoop | None, threading.Thread | None]:
    """Expose module internals so unit tests can assert persistence."""
    return _loop, _thread
