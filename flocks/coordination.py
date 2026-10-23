"""
What processes sharing one database have to agree on, answered for one process.

The runtime keeps its data in SQLite and runs in one process, so each question
here has the single-process answer: no other process is running a session, a
cluster-wide singleton is always this process's, a shared value is simply the
stored one, and a WebUI page or a workflow gets no direct database handle.

A database backend that lets several processes or nodes share the data
replaces this module as a whole (see ``flocks.db_backend``) with answers taken
from that database.  Callers ask here instead of reaching into the database.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional


async def sessions_running_elsewhere() -> list[str]:
    """Ids of the sessions another process is running right now; none, in one process."""
    return []


class ClusterSingleton:
    """A named lease only one process may hold at a time; here it is always this process's."""

    def __init__(self, name: str, *, check_timeout_s: float = 5.0) -> None:
        self.name = name
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    async def try_acquire(self) -> bool:
        self._held = True
        return True

    async def check(self) -> bool:
        return self._held

    async def release(self) -> None:
        self._held = False


async def wait_for_singleton(
    singleton: ClusterSingleton,
    *,
    retry_s: float,
    stop: asyncio.Event,
) -> Optional[bool]:
    """``True`` once *singleton* is held, ``None`` when *stop* was set first."""
    if stop.is_set():
        return None
    return True if await singleton.try_acquire() else None


# The value each key settled on in this process.  Callers that both find
# nothing stored -- on one event loop or several -- agree here on one value
# before either stores it; no lock is held across the store's awaits.
_settled: dict[str, Any] = {}
_settled_lock = threading.Lock()


async def shared_setdefault(key: str, value: Any) -> Any:
    """The value stored under *key*, storing *value* there first when there is none."""
    from flocks.storage.storage import Storage

    existing = await Storage.get(key)
    with _settled_lock:
        settled = _settled.setdefault(key, value if existing is None else existing)
    if existing is None:
        await Storage.set(key, settled)
    return settled


def page_api_database() -> Any:
    """``ctx.database`` for a WebUI page's API handlers: none without a shared database."""
    return None


def workflow_services(cancel_checker: Optional[Callable[[], bool]]) -> dict[str, Any]:
    """Objects a backend hands workflow python nodes by name, next to ``tool`` and ``llm``: none here.

    Each lists the methods a node may call (``exposed_methods``) and may set a
    time limit, in seconds, for a call from an isolated node (``rpc_timeout_s``).
    """
    return {}


def is_unique_violation(exc: BaseException) -> bool:
    """Whether *exc* says the row would duplicate a unique key (SQLSTATE 23505, or SQLite's IntegrityError)."""
    if getattr(exc, "sqlstate", None) == "23505":
        return True
    import sqlite3

    return isinstance(exc, sqlite3.IntegrityError) and "UNIQUE" in str(exc).upper()


__all__ = [
    "ClusterSingleton",
    "is_unique_violation",
    "page_api_database",
    "sessions_running_elsewhere",
    "shared_setdefault",
    "wait_for_singleton",
    "workflow_services",
]
