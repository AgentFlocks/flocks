"""One more attempt for a transaction PostgreSQL aborted to break a deadlock."""

from __future__ import annotations

from typing import Awaitable, Callable, TypeVar

from flocks.utils.log import Log

log = Log.create(service="db.retry")

DEADLOCK_DETECTED = "40P01"

T = TypeVar("T")


async def retry_deadlock_once(operation: Callable[[], Awaitable[T]], *, name: str) -> T:
    """Run *operation*, one whole transaction, again if it was chosen as a deadlock victim.

    PostgreSQL rolls the victim back entirely and lets the other side go on, so
    the second attempt starts from a consistent state and normally gets through.
    """
    try:
        return await operation()
    except Exception as error:
        if getattr(error, "sqlstate", None) != DEADLOCK_DETECTED:
            raise
        log.warn("db.deadlock.retry", {"operation": name, "error": str(error)})
    return await operation()


__all__ = ["DEADLOCK_DETECTED", "retry_deadlock_once"]
