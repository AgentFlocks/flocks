"""Per-event-loop concurrency budget for provider LLM calls.

flocks runs LLM calls on more than one event loop in a single process: the
uvicorn server loop serves interactive sessions, while the dedicated
``flocks-workflow-llm-loop`` thread runs workflow ``llm.ask`` calls and the
subagent sessions that workflow nodes start through ``delegate_task``. All of
them share the same provider key and the same upstream concurrency quota, so a
workflow with a few parallel nodes can eat the whole quota and every
interactive session then sees rate limits, pool timeouts or hangs.

A budget is attached to a loop, not to a provider or to a single workflow run:
the workflow runtime gives its one shared loop a limit
(``workflow.llm.max_concurrency``, default 8), so every workflow running in the
process draws from those slots together; the server loop has none. LLM call
sites take an :class:`LlmSlot` around the request, which is a no-op on a loop
without a budget and otherwise holds one of the loop's slots for the whole
request, including a streamed response.
"""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass
from typing import Optional

from flocks.utils.log import Log

__all__ = ["LlmSlot", "llm_slot", "set_loop_budget", "get_loop_budget", "loop_budget_snapshot"]

log = Log.create(service="provider.llm_budget")

# Waiting this long for a slot is worth a log line: it is long enough to eat a
# visible slice of a workflow node's timeout, short enough to catch saturation
# before the node dies.
SLOW_ACQUIRE_LOG_SECONDS = 15.0


@dataclass
class _LoopBudget:
    limit: int
    semaphore: Optional[asyncio.Semaphore] = None
    semaphore_limit: int = 0
    in_flight: int = 0
    waiting: int = 0

    def current_semaphore(self) -> asyncio.Semaphore:
        # (Re)built lazily on the owning loop so it binds there; a limit change
        # swaps the object and holders release the one they acquired.
        if self.semaphore is None or self.semaphore_limit != self.limit:
            self.semaphore = asyncio.Semaphore(self.limit)
            self.semaphore_limit = self.limit
        return self.semaphore


_budgets: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopBudget]" = weakref.WeakKeyDictionary()


def set_loop_budget(loop: asyncio.AbstractEventLoop, limit: Optional[int]) -> None:
    """Cap concurrent LLM calls made on *loop*; ``None`` / ``0`` lifts the cap."""
    try:
        resolved = int(limit) if limit is not None else 0
    except (TypeError, ValueError):
        resolved = 0
    if resolved <= 0:
        _budgets.pop(loop, None)
        return
    budget = _budgets.get(loop)
    if budget is None:
        _budgets[loop] = _LoopBudget(limit=resolved)
    else:
        budget.limit = resolved


def _resolve_loop(loop: Optional[asyncio.AbstractEventLoop]) -> Optional[asyncio.AbstractEventLoop]:
    if loop is not None:
        return loop
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def get_loop_budget(loop: Optional[asyncio.AbstractEventLoop] = None) -> int:
    """Return the cap for *loop* (default: the running loop); ``0`` means unlimited."""
    target = _resolve_loop(loop)
    budget = _budgets.get(target) if target is not None else None
    return budget.limit if budget is not None else 0


def loop_budget_snapshot(loop: Optional[asyncio.AbstractEventLoop] = None) -> dict:
    """Observability helper: limit / in-flight / waiting for a loop."""
    target = _resolve_loop(loop)
    budget = _budgets.get(target) if target is not None else None
    if budget is None:
        return {"limit": 0, "in_flight": 0, "waiting": 0}
    return {"limit": budget.limit, "in_flight": budget.in_flight, "waiting": budget.waiting}


class LlmSlot:
    """One slot of the running loop's LLM budget.

    ``async with llm_slot():`` for the common case; ``await acquire()`` /
    ``release()`` when the protected region already has its own try/finally.
    Acquire before any first-response timeout clock starts: queueing behind
    other workflow calls is not the model being unresponsive.
    """

    def __init__(self) -> None:
        self._budget: Optional[_LoopBudget] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._held = False

    async def acquire(self) -> None:
        if self._held:
            return
        loop = asyncio.get_running_loop()
        budget = _budgets.get(loop)
        if budget is None or budget.limit <= 0:
            self._held = True
            return
        semaphore = budget.current_semaphore()
        budget.waiting += 1
        waiting_since = loop.time()
        acquired = False
        try:
            await semaphore.acquire()
            acquired = True
        finally:
            budget.waiting -= 1
            if acquired:
                budget.in_flight += 1
            # Queueing is invisible from the caller's side: the wait is not
            # part of any LLM timeout, but it does count towards the workflow
            # node timeout, so a saturated budget shows up as "node timed out"
            # with nothing in the log pointing at the queue. Say so once per
            # slow acquire. The snapshot is taken after this caller's own
            # counters moved, so it counts this call too; ``waiting`` is the
            # queue depth to judge saturation by, while ``in_flight`` can still
            # read one short when several holders hand slots over in the same
            # loop iteration. ``acquired`` is False when the wait ended in a
            # cancel (a node timeout killing a queued call) instead of a slot.
            waited = loop.time() - waiting_since
            if waited >= SLOW_ACQUIRE_LOG_SECONDS:
                log.warn("llm_budget.slot.slow_acquire", {
                    "waited_s": round(waited, 1),
                    "acquired": acquired,
                    **loop_budget_snapshot(loop),
                })
        self._budget = budget
        self._semaphore = semaphore
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        budget, semaphore = self._budget, self._semaphore
        self._budget = self._semaphore = None
        if budget is not None and semaphore is not None:
            budget.in_flight -= 1
            semaphore.release()

    async def __aenter__(self) -> "LlmSlot":
        await self.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.release()


def llm_slot() -> LlmSlot:
    """Return an unacquired :class:`LlmSlot` for the running loop."""
    return LlmSlot()
