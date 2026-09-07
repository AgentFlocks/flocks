"""Serialization for global configuration transactions."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar, cast


T = TypeVar("T")


class AsyncReentrantLock:
    """An asyncio lock that permits nested acquisition by the current task."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[Any] | None = None
        self._depth = 0

    async def acquire(self) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Configuration mutations require an asyncio task")
        if self._owner is task:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = task
        self._depth = 1

    def release(self) -> None:
        task = asyncio.current_task()
        if task is None or self._owner is not task:
            raise RuntimeError("Configuration mutation lock released by a non-owner")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        await self.acquire()
        try:
            yield
        finally:
            self.release()


GLOBAL_CONFIG_MUTATION_LOCK = AsyncReentrantLock()


def serialized_config_mutation(
    function: Callable[..., Awaitable[T]],
) -> Callable[..., Awaitable[T]]:
    """Serialize an async route or helper that mutates global configuration."""

    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> T:
        async with GLOBAL_CONFIG_MUTATION_LOCK.hold():
            return await function(*args, **kwargs)

    return cast(Callable[..., Awaitable[T]], wrapped)
