"""
Who may run a task execution.

One process runs the scheduled tasks of an installation, and the task manager
hands each execution to one dispatcher, so there is never a second runner to
lose an execution to: the checks below always pass.

A database backend whose processes share the task queue replaces this module
(``flocks.db_backend``) with leases that a runner renews while it works and
that tell it when another process has taken the execution over.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


def current_execution_cancelled() -> bool:
    """Whether the task execution this code runs in was taken away: never, here."""
    return False


def require_current_execution() -> None:
    """Fail when the task execution this code runs in is no longer ours: it always is, here."""


@contextmanager
def outside_task_execution() -> Iterator[None]:
    """Start work that outlives the task execution it was started from."""
    yield


class ExecutionOwnershipError(RuntimeError):
    """This dispatcher has no authority to run or finish the execution."""


__all__ = [
    "ExecutionOwnershipError", "current_execution_cancelled",
    "outside_task_execution", "require_current_execution",
]
