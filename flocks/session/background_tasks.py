"""Track cancellable asynchronous work associated with sessions."""

import asyncio
from typing import Any, Optional


_pending_tasks: set[asyncio.Task[Any]] = set()
_tasks_by_session: dict[str, set[asyncio.Task[Any]]] = {}


def track_background_task(
    task: asyncio.Task[Any],
    *,
    session_id: Optional[str] = None,
) -> None:
    """Keep a task alive and optionally associate it with a session."""

    _pending_tasks.add(task)
    if session_id:
        _tasks_by_session.setdefault(session_id, set()).add(task)

    def discard(completed: asyncio.Task[Any]) -> None:
        _pending_tasks.discard(completed)
        if not session_id:
            return
        session_tasks = _tasks_by_session.get(session_id)
        if session_tasks is None:
            return
        session_tasks.discard(completed)
        if not session_tasks:
            _tasks_by_session.pop(session_id, None)

    task.add_done_callback(discard)


async def cancel_session_background_tasks(session_id: str) -> None:
    """Cancel and join tracked work before a lifecycle transition commits."""

    current_task = asyncio.current_task()
    tasks = [
        task
        for task in _tasks_by_session.get(session_id, ())
        if task is not current_task and not task.done()
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def pending_background_tasks() -> set[asyncio.Task[Any]]:
    """Return the live task set for compatibility with route-level drains."""

    return _pending_tasks


def has_pending_session_tasks(session_id: str) -> bool:
    """Return whether tracked asynchronous work is still active for a session."""

    return any(not task.done() for task in _tasks_by_session.get(session_id, ()))
