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

When the server has registered its loop, workflow coroutines run there instead
of on the dedicated thread (see ``flocks.workflow._async_runtime``). They are
submitted inside :func:`budget_scope`, and a slot taken in a scope (including
from tasks the scoped coroutine starts) draws from that scope's budget on the
current loop, so the server loop itself still has no budget for interactive
sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import weakref
from dataclasses import dataclass
from typing import Dict, Iterator, Optional

from flocks.utils.log import Log

__all__ = [
    "LlmSlot",
    "budget_scope",
    "get_loop_budget",
    "llm_slot",
    "loop_budget_snapshot",
    "set_loop_budget",
    "set_scope_budget",
]

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

# Scope name -> limit, and per-loop budgets of each scope (created lazily on the
# loop that first takes a slot in that scope).
_scope_limits: Dict[str, int] = {}
_scoped_budgets: "Dict[str, weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopBudget]]" = {}
_current_scope: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "flocks_llm_budget_scope", default=None
)


def set_scope_budget(scope: str, limit: Optional[int]) -> None:
    """Cap concurrent LLM calls made inside :func:`budget_scope` (``0`` lifts it)."""
    try:
        resolved = int(limit) if limit is not None else 0
    except (TypeError, ValueError):
        resolved = 0
    resolved = max(0, resolved)
    _scope_limits[scope] = resolved
    for budget in list(_scoped_budgets.get(scope, {}).values()):
        budget.limit = resolved


@contextlib.contextmanager
def budget_scope(scope: str) -> Iterator[None]:
    """Make LLM slots taken in this context (and tasks it starts) use *scope*'s budget."""
    token = _current_scope.set(scope)
    try:
        yield
    finally:
        _current_scope.reset(token)


def _scoped_budget(loop: asyncio.AbstractEventLoop) -> Optional[_LoopBudget]:
    scope = _current_scope.get()
    if scope is None:
        return None
    limit = _scope_limits.get(scope, 0)
    if limit <= 0:
        return None
    per_loop = _scoped_budgets.setdefault(scope, weakref.WeakKeyDictionary())
    budget = per_loop.get(loop)
    if budget is None:
        budget = per_loop[loop] = _LoopBudget(limit=limit)
    return budget


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
        budget = _budgets.get(loop) or _scoped_budget(loop)
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
                # The budget actually waited on: the loop's own, or the scope's
                # (loop_budget_snapshot only knows loop budgets).
                log.warn("llm_budget.slot.slow_acquire", {
                    "waited_s": round(waited, 1),
                    "acquired": acquired,
                    "limit": budget.limit,
                    "in_flight": budget.in_flight,
                    "waiting": budget.waiting,
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
