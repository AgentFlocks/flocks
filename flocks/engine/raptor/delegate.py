"""
Raptor in-process sub-agent orchestration utilities.

Architecture
------------
The raptor engine achieves in-process parallel sub-agent execution through
``RaptorStreamProcessor.execute_deferred_parallel()``: when the LLM emits
multiple ``delegate_task`` calls in a single response, all of them are
executed concurrently via ``asyncio.gather`` inside the same event loop.

Raptor uses asyncio tasks so sibling tool calls run in the same Python process
and event loop.

Key behaviours this module adds on top of the baseline parallel execution:

1. **Abort propagation** — When the parent session is aborted while child
   sessions are running, ``abort_child_sessions()`` signals each known child
   to stop.  The tracker is populated by ``RaptorStreamProcessor`` as delegate
   tools complete and expose their child ``sessionId`` in the result metadata.

2. **Abort-aware gather** — ``abort_aware_gather()`` wraps ``asyncio.gather``
   with a watcher coroutine that cancels all in-flight tasks as soon as the
   parent's abort event fires.

None of the utilities in this module modify ``SessionLoop._run_loop()`` or
any other core Flocks component.
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, List, Optional

from flocks.utils.log import Log

log = Log.create(service="engine.raptor.delegate")


# ---------------------------------------------------------------------------
# Child session abort propagation
# ---------------------------------------------------------------------------

async def abort_child_sessions(session_ids: List[str]) -> None:
    """Signal all known child sessions to stop.

    Best-effort: exceptions from individual session lookups are swallowed so
    that a stale or already-completed session ID does not block the caller.
    """
    if not session_ids:
        return

    for child_sid in session_ids:
        try:
            from flocks.session.session_loop import SessionLoop

            SessionLoop.abort(child_sid)
            log.info("raptor.delegate.child_aborted", {
                "child_session_id": child_sid,
            })
        except Exception as exc:
            log.debug("raptor.delegate.abort_child_failed", {
                "child_session_id": child_sid,
                "error": str(exc),
            })


# ---------------------------------------------------------------------------
# Abort-aware concurrent gather
# ---------------------------------------------------------------------------

async def abort_aware_gather(
    coros: List[Coroutine[Any, Any, None]],
    abort_event: Optional[asyncio.Event] = None,
    child_session_ids: Optional[List[str]] = None,
) -> None:
    """Run *coros* concurrently; cancel all tasks if *abort_event* fires.

    Parameters
    ----------
    coros:
        Coroutines to execute in parallel (tool executions).
    abort_event:
        Optional abort signal from the parent ``SessionRunner``.  When set,
        the watcher task cancels all in-flight coroutines as soon as the event
        fires.
    child_session_ids:
        Optional mutable list.  After all tasks finish, the caller may inspect
        it for child session IDs that were recorded during execution (populated
        externally by ``RaptorStreamProcessor``).
    """
    if not coros:
        return

    tasks = [asyncio.create_task(c) for c in coros]

    async def _abort_watcher() -> None:
        if abort_event is None:
            return
        try:
            await abort_event.wait()
            for t in tasks:
                if not t.done():
                    t.cancel()
            # Also abort any known child sessions.
            if child_session_ids:
                await abort_child_sessions(child_session_ids)
        except asyncio.CancelledError:
            pass

    watcher = asyncio.create_task(_abort_watcher())
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass

