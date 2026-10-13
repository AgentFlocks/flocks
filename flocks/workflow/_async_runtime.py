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

from flocks.provider.llm_budget import get_loop_budget, set_loop_budget

__all__ = [
    "run_sync",
    "run_sync_cancellable",
    "configure_llm_concurrency",
    "llm_concurrency_limit",
    "is_workflow_loop",
    "DEFAULT_LLM_MAX_CONCURRENCY",
    "_get_loop_for_testing",
]

# Cap on LLM calls in flight on the workflow loop (``llm.ask`` plus every
# subagent session started by ``delegate_task`` inside a workflow). Interactive
# sessions run on the server loop and are not budgeted, so this is the share of
# the provider's quota that workflows can take away from them. It is a
# process-wide cap: there is one workflow loop, so all concurrent runs (syslog /
# kafka / scheduled / manual) draw from these slots together, not one budget per
# run. Overridden by ``workflow.llm.max_concurrency`` in flocks.json; 0 lifts
# the cap. A wait longer than a few seconds is logged as
# ``llm_budget.slot.slow_acquire`` — the wait counts towards the node timeout,
# so without that line a saturated budget just looks like a node timing out.
DEFAULT_LLM_MAX_CONCURRENCY = 8


_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_llm_limit: int = DEFAULT_LLM_MAX_CONCURRENCY


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

        set_loop_budget(loop, _llm_limit)
        _loop = loop
        _thread = thread

    return _loop


def configure_llm_concurrency(limit: int | None) -> None:
    """Set the workflow loop's LLM budget (``None`` / ``0`` = unlimited).

    Safe to call from any thread and before the loop exists; the value is
    applied when the loop starts. Callers holding a slot keep it.
    """
    global _llm_limit
    try:
        resolved = int(limit) if limit is not None else 0
    except (TypeError, ValueError):
        resolved = 0
    _llm_limit = max(0, resolved)
    with _lock:
        loop = _loop
    if loop is not None and loop.is_running():
        set_loop_budget(loop, _llm_limit)


def llm_concurrency_limit() -> int:
    """Current cap for LLM calls on the workflow loop; ``0`` means unlimited."""
    with _lock:
        loop = _loop
    if loop is not None and loop.is_running():
        return get_loop_budget(loop)
    return _llm_limit


def is_workflow_loop() -> bool:
    """True when called from a coroutine running on the workflow loop."""
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return current is _loop


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
    loop = _ensure_loop()
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
    loop = _ensure_loop()
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


def _get_loop_for_testing() -> tuple[asyncio.AbstractEventLoop | None, threading.Thread | None]:
    """Expose module internals so unit tests can assert persistence."""
    return _loop, _thread
