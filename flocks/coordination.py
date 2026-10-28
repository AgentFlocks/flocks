"""
Questions whose answer depends on the database backend, answered here for the runtime's own.

The runtime keeps its data in SQLite and runs in one process, so each question
here has that answer: no other process is running a session, a shared value is
simply the stored one, and a WebUI page or a workflow gets no direct database
handle.

Another database backend replaces this module as a whole (see
``flocks.db_backend``) with its own answers.  Callers ask here instead of
reaching into the database.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional


async def sessions_running_elsewhere() -> list[str]:
    """Ids of the sessions another process is running right now; none, in one process."""
    return []


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
    "is_unique_violation",
    "page_api_database",
    "sessions_running_elsewhere",
    "shared_setdefault",
    "workflow_services",
]
