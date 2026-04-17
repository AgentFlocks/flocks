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
import threading
from typing import Any, Coroutine

__all__ = ["run_sync", "_get_loop_for_testing"]


_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None


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

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def _get_loop_for_testing() -> tuple[asyncio.AbstractEventLoop | None, threading.Thread | None]:
    """Expose module internals so unit tests can assert persistence."""
    return _loop, _thread
