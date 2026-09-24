"""Process-wide cap on in-flight model requests.

Interactive sessions, foreground subagents, scheduled/background tasks and
workflow ``llm`` nodes all talk to the same provider credentials. Without a
shared budget one turn that fans out to N subagents (or a burst of workflow
runs) saturates the upstream concurrency limit and every caller gets HTTP
429 at the same time, then retries at the same time.

Callers that take a slot today: ``SessionRunner._call_llm`` (every session
turn, including subagents and task/background sessions) and
``flocks.workflow.llm.LLMClient.ask`` (workflow ``llm`` nodes and python
nodes). Auxiliary model calls (title generation, compaction summaries, goal
evaluation, memory flush) still go straight to the provider.

Waiters are served strictly by (priority, arrival); there is no aging, so
background work only proceeds while interactive traffic leaves a slot free
(or once the wait-timeout safety valve trips).

What the cap actually bounds. A response that only dispatches sibling
``delegate_task`` calls -- the fan-out this exists for -- stays inside it: the
parent keeps its slot for the whole response and gives it up before waiting
for the children, so they queue normally.

An inline tool breaks that. It blocks the streaming loop and may call the
model itself (``run_workflow``, a non-parallel ``delegate_task``), so the
session hands its slot back when one starts and never takes it again for that
response. From then on its stream keeps generating without holding a slot --
including any sibling delegate_task dispatched later in the same response --
so the number of generating requests can exceed the cap until that stream
ends. Closing the gap means executing tools only after the stream completes,
which the session runner deliberately does not do.

The limiter is deliberately cross-loop: session code runs on the server
loop while workflow code runs on the dedicated background loop in
``flocks.workflow._async_runtime``. ``asyncio.Semaphore`` is loop-bound, so
slots are handed over with ``loop.call_soon_threadsafe`` and all bookkeeping
sits behind a ``threading.Lock``.

Resolution order for a provider's limit:

1. ``provider.<id>.options.maxConcurrentRequests`` in ``flocks.json``
2. ``concurrency.maxConcurrentRequests`` in ``flocks.json``
3. ``FLOCKS_LLM_MAX_CONCURRENT_REQUESTS`` environment variable
4. :data:`DEFAULT_MAX_CONCURRENT_REQUESTS`

``0`` disables the cap for that provider.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from flocks.utils.log import Log

log = Log.create(service="provider.concurrency")

PRIORITY_INTERACTIVE = 0
PRIORITY_BACKGROUND = 1

DEFAULT_MAX_CONCURRENT_REQUESTS = 8
# Longest pause the limiter applies after an upstream 429. Daily-quota style
# ``retry-after`` values (minutes/hours) must not freeze every caller.
MAX_RATE_LIMIT_COOLDOWN_S = 30.0
# Safety valve: a waiter that has not been granted a slot after this long
# proceeds anyway (over the cap) and logs, so a leaked slot can never hang
# every session in the process.
DEFAULT_SLOT_WAIT_TIMEOUT_S = 600.0

_ENV_MAX_CONCURRENT = "FLOCKS_LLM_MAX_CONCURRENT_REQUESTS"
_ENV_SLOT_WAIT_TIMEOUT = "FLOCKS_LLM_SLOT_WAIT_TIMEOUT_S"
_ENV_MAX_PARALLEL_SUBAGENTS = "FLOCKS_SUBAGENT_MAX_PARALLEL"

DEFAULT_MAX_PARALLEL_SUBAGENTS = 4


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        log.warn("provider.concurrency.env_invalid", {"name": name, "value": raw})
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        log.warn("provider.concurrency.env_invalid", {"name": name, "value": raw})
        return default


@dataclass
class _Waiter:
    priority: int
    seq: int
    loop: asyncio.AbstractEventLoop
    future: "asyncio.Future[None]"


class LlmSlot:
    """Handle for one granted request slot. ``release`` is idempotent."""

    __slots__ = ("_limiter", "_released", "provider_id", "priority", "granted_at")

    def __init__(self, limiter: "ConcurrencyLimiter", *, priority: int) -> None:
        self._limiter = limiter
        self._released = False
        self.provider_id = limiter.name
        self.priority = priority
        self.granted_at = time.monotonic()

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._limiter._release()

    async def __aenter__(self) -> "LlmSlot":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self.release()


class ConcurrencyLimiter:
    """Priority-ordered, cross-loop counting semaphore with a 429 cooldown."""

    def __init__(self, name: str, limit: int) -> None:
        self.name = name
        self._limit = max(0, int(limit))
        self._active = 0
        self._lock = threading.Lock()
        self._waiters: List[_Waiter] = []
        self._seq = itertools.count()
        self._cooldown_until = 0.0
        self._cooldown_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # Configuration / inspection
    # ------------------------------------------------------------------
    @property
    def limit(self) -> int:
        return self._limit

    def set_limit(self, limit: int) -> None:
        limit = max(0, int(limit))
        with self._lock:
            if limit == self._limit:
                return
            self._limit = limit
        # A raised limit may unblock queued waiters.
        self._grant_waiters()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            remaining = max(0.0, self._cooldown_until - time.monotonic())
            return {
                "name": self.name,
                "limit": self._limit,
                "active": self._active,
                "waiting": len(self._waiters),
                "cooldownRemainingS": round(remaining, 3),
                "cooldownReason": self._cooldown_reason if remaining > 0 else None,
            }

    # ------------------------------------------------------------------
    # Rate-limit cooldown
    # ------------------------------------------------------------------
    def report_rate_limited(self, cooldown_s: float, *, reason: str = "rate_limit") -> float:
        """Pause new requests for ``cooldown_s`` (capped) after an upstream 429.

        Returns the cooldown actually applied in seconds.
        """
        try:
            cooldown = float(cooldown_s)
        except (TypeError, ValueError):
            cooldown = 0.0
        cooldown = max(0.0, min(cooldown, MAX_RATE_LIMIT_COOLDOWN_S))
        if cooldown <= 0:
            return 0.0
        until = time.monotonic() + cooldown
        with self._lock:
            if until > self._cooldown_until:
                self._cooldown_until = until
                self._cooldown_reason = reason
        log.warn("provider.concurrency.cooldown", {
            "provider_id": self.name,
            "cooldown_s": round(cooldown, 3),
            "reason": reason,
            "active": self._active,
            "waiting": len(self._waiters),
        })
        return cooldown

    def cooldown_remaining(self) -> float:
        with self._lock:
            return max(0.0, self._cooldown_until - time.monotonic())

    # ------------------------------------------------------------------
    # Acquire / release
    # ------------------------------------------------------------------
    async def acquire(
        self,
        *,
        priority: int = PRIORITY_INTERACTIVE,
        timeout_s: Optional[float] = None,
    ) -> LlmSlot:
        """Wait for a slot, then wait out any active cooldown, then return it."""
        loop = asyncio.get_running_loop()
        granted = False
        with self._lock:
            if self._limit == 0 or (self._active < self._limit and not self._waiters):
                self._active += 1
                granted = True
            else:
                waiter = _Waiter(
                    priority=int(priority),
                    seq=next(self._seq),
                    loop=loop,
                    future=loop.create_future(),
                )
                self._insert_waiter(waiter)

        if not granted:
            wait_timeout = (
                _env_float(_ENV_SLOT_WAIT_TIMEOUT, DEFAULT_SLOT_WAIT_TIMEOUT_S)
                if timeout_s is None
                else timeout_s
            )
            waited_from = time.monotonic()
            try:
                if wait_timeout is not None and wait_timeout > 0:
                    await asyncio.wait_for(asyncio.shield(waiter.future), timeout=wait_timeout)
                else:
                    await waiter.future
            except asyncio.TimeoutError:
                # Safety valve: never hang a caller forever on a leaked slot.
                # If the waiter was already popped, the slot is ours (delivery
                # is merely pending on this loop); otherwise proceed over the
                # cap and say so loudly.
                with self._lock:
                    still_queued = self._remove_waiter(waiter)
                    if still_queued:
                        self._active += 1
                log.error("provider.concurrency.wait_timeout", {
                    "provider_id": self.name,
                    "waited_s": round(time.monotonic() - waited_from, 3),
                    "limit": self._limit,
                    "active": self._active,
                    "proceeding_over_limit": still_queued,
                })
            except asyncio.CancelledError:
                with self._lock:
                    still_queued = self._remove_waiter(waiter)
                if not still_queued:
                    future = waiter.future
                    if future.done() and not future.cancelled():
                        # The slot was handed to us while we were being
                        # cancelled: give it back.
                        self._release()
                    else:
                        # Popped but not delivered yet. ``_set_result`` runs
                        # later on this loop and passes the slot on when it
                        # finds the future cancelled.
                        future.cancel()
                raise
            else:
                log.debug("provider.concurrency.slot_granted_after_wait", {
                    "provider_id": self.name,
                    "waited_s": round(time.monotonic() - waited_from, 3),
                    "priority": priority,
                })

        slot = LlmSlot(self, priority=priority)
        try:
            await self._wait_cooldown()
        except BaseException:
            slot.release()
            raise
        return slot

    async def _wait_cooldown(self) -> None:
        while True:
            remaining = self.cooldown_remaining()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 1.0))

    def _release(self) -> None:
        with self._lock:
            if self._limit != 0 and self._active > self._limit:
                # The cap was lowered while this slot was held: retire the
                # slot instead of handing it on so ``active`` converges.
                self._active -= 1
                return
            waiter = self._pop_next_waiter()
            if waiter is None:
                self._active = max(0, self._active - 1)
                return
        # Ownership of the slot moves to the waiter: ``_active`` is unchanged.
        self._deliver(waiter)

    def _grant_waiters(self) -> None:
        while True:
            with self._lock:
                if self._limit != 0 and self._active >= self._limit:
                    return
                waiter = self._pop_next_waiter()
                if waiter is None:
                    return
                self._active += 1
            self._deliver(waiter)

    def _deliver(self, waiter: _Waiter) -> None:
        def _set_result() -> None:
            future = waiter.future
            if future.done():
                # Cancelled between pop and delivery: pass the slot along.
                self._release()
                return
            future.set_result(None)

        try:
            waiter.loop.call_soon_threadsafe(_set_result)
        except RuntimeError:
            # The waiter's loop is closed (shutdown). Free the slot instead of
            # leaking it.
            log.warn("provider.concurrency.waiter_loop_closed", {"provider_id": self.name})
            self._release()

    # ---- waiter queue helpers (caller holds ``_lock``) ----------------
    def _insert_waiter(self, waiter: _Waiter) -> None:
        key = (waiter.priority, waiter.seq)
        index = len(self._waiters)
        for position, queued in enumerate(self._waiters):
            if (queued.priority, queued.seq) > key:
                index = position
                break
        self._waiters.insert(index, waiter)

    def _pop_next_waiter(self) -> Optional[_Waiter]:
        while self._waiters:
            waiter = self._waiters.pop(0)
            if waiter.future.done():
                continue
            return waiter
        return None

    def _remove_waiter(self, waiter: _Waiter) -> bool:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            return False
        return True


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------
_registry_lock = threading.Lock()
_limiters: Dict[str, ConcurrencyLimiter] = {}


def _normalize_provider_id(provider_id: Optional[str]) -> str:
    value = str(provider_id or "").strip()
    return value or "default"


async def _load_config() -> Optional[Any]:
    try:
        from flocks.config.config import Config

        return await Config.get()
    except Exception as exc:  # pragma: no cover - defensive, config is optional
        log.debug("provider.concurrency.config_unavailable", {"error": str(exc)})
        return None


def _read_int_option(container: Any, *names: str) -> Optional[int]:
    if container is None:
        return None
    for name in names:
        value = None
        if isinstance(container, dict):
            value = container.get(name)
        else:
            value = getattr(container, name, None)
            if value is None:
                extra = getattr(container, "model_extra", None)
                if isinstance(extra, dict):
                    value = extra.get(name)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def resolve_provider_request_limit(config: Any, provider_id: Optional[str]) -> int:
    """Resolve the per-provider cap from config, env, then the built-in default."""
    pid = _normalize_provider_id(provider_id)
    providers = getattr(config, "provider", None) if config is not None else None
    if isinstance(providers, dict):
        provider_cfg = providers.get(pid)
        options = getattr(provider_cfg, "options", None) if provider_cfg is not None else None
        value = _read_int_option(options, "max_concurrent_requests", "maxConcurrentRequests")
        if value is not None:
            return max(0, value)

    concurrency_cfg = getattr(config, "concurrency", None) if config is not None else None
    value = _read_int_option(concurrency_cfg, "max_concurrent_requests", "maxConcurrentRequests")
    if value is not None:
        return max(0, value)

    return max(0, _env_int(_ENV_MAX_CONCURRENT, DEFAULT_MAX_CONCURRENT_REQUESTS))


def resolve_max_parallel_subagents(config: Any) -> int:
    """Resolve the per-turn foreground subagent cap (0 disables it)."""
    concurrency_cfg = getattr(config, "concurrency", None) if config is not None else None
    value = _read_int_option(concurrency_cfg, "max_parallel_subagents", "maxParallelSubagents")
    if value is not None:
        return max(0, value)
    return max(0, _env_int(_ENV_MAX_PARALLEL_SUBAGENTS, DEFAULT_MAX_PARALLEL_SUBAGENTS))


def get_provider_limiter(provider_id: Optional[str], *, limit: Optional[int] = None) -> ConcurrencyLimiter:
    """Return the process-wide limiter for ``provider_id`` (created on first use)."""
    pid = _normalize_provider_id(provider_id)
    with _registry_lock:
        limiter = _limiters.get(pid)
        if limiter is None:
            initial = (
                limit
                if limit is not None
                else max(0, _env_int(_ENV_MAX_CONCURRENT, DEFAULT_MAX_CONCURRENT_REQUESTS))
            )
            limiter = ConcurrencyLimiter(pid, initial)
            _limiters[pid] = limiter
    if limit is not None:
        limiter.set_limit(limit)
    return limiter


async def acquire_llm_slot(
    provider_id: Optional[str],
    *,
    priority: int = PRIORITY_INTERACTIVE,
    timeout_s: Optional[float] = None,
) -> LlmSlot:
    """Resolve the configured cap for ``provider_id`` and wait for a slot."""
    config = await _load_config()
    limit = resolve_provider_request_limit(config, provider_id)
    limiter = get_provider_limiter(provider_id, limit=limit)
    return await limiter.acquire(priority=priority, timeout_s=timeout_s)


def report_provider_rate_limited(
    provider_id: Optional[str],
    cooldown_s: float,
    *,
    reason: str = "rate_limit",
) -> float:
    """Pause new requests to ``provider_id`` after an upstream 429."""
    return get_provider_limiter(provider_id).report_rate_limited(cooldown_s, reason=reason)


def limiter_snapshots() -> List[Dict[str, Any]]:
    with _registry_lock:
        limiters = list(_limiters.values())
    return [limiter.snapshot() for limiter in limiters]


def reset_limiters_for_testing() -> None:
    with _registry_lock:
        _limiters.clear()


__all__ = [
    "PRIORITY_INTERACTIVE",
    "PRIORITY_BACKGROUND",
    "DEFAULT_MAX_CONCURRENT_REQUESTS",
    "DEFAULT_MAX_PARALLEL_SUBAGENTS",
    "MAX_RATE_LIMIT_COOLDOWN_S",
    "ConcurrencyLimiter",
    "LlmSlot",
    "acquire_llm_slot",
    "get_provider_limiter",
    "limiter_snapshots",
    "report_provider_rate_limited",
    "reset_limiters_for_testing",
    "resolve_max_parallel_subagents",
    "resolve_provider_request_limit",
]
