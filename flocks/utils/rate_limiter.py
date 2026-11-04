"""
Async token-bucket rate limiter.

Provides a reusable rate limiter for any subsystem that needs to throttle
outgoing requests (channel delivery, external API calls, MCP invocations, …).

Usage::

    limiter = AsyncTokenBucket(rate=20.0, burst=5)
    await limiter.acquire()   # blocks until a token is available
"""

from __future__ import annotations

import asyncio
import time


class AsyncTokenBucket:
    """Token-bucket rate limiter safe for concurrent asyncio use.

    Sleeps *outside* the internal lock so that other coroutines can
    still acquire tokens while one coroutine is waiting.
    """

    __slots__ = ("_rate", "_burst", "_tokens", "_last_refill", "_lock")

    def __init__(self, rate: float, burst: int = 1) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        wait = 0.0
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            # Always consume the token, even when it has to be borrowed: the
            # deficit is what makes the next caller wait its own full share.
            # Zeroing the balance instead let the refill earned during this
            # caller's sleep be spent again immediately by the next caller,
            # so two sends slipped through on every rate boundary.
            self._tokens -= 1
            if self._tokens < 0:
                wait = -self._tokens / self._rate

        if wait > 0:
            await asyncio.sleep(wait)
