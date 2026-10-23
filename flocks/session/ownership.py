"""Who runs a session right now, within this process.

A runner (and a manual compaction, and orphan repair) holds a session's
execution lease for as long as it works on the session, so a second one does
not start meanwhile and data retention leaves the session alone.  One process
runs an installation's sessions, so the leases live in this process: a lease
cannot be lost to another runner, and there is no other node to ask to abort.

A database backend whose processes share sessions replaces this module
(``flocks.db_backend``) with leases kept in the database, renewed while the run
goes on and fenced inside the transactions that write the session.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from flocks.task.node import process_owner_id

#: How often a running session looks for an abort asked for on another node.
ABORT_POLL_INTERVAL_S = 1.0

_abort_handler: Any = None


def set_abort_handler(handler: Any) -> None:
    """Install what runs when another node asks this one to abort a session (none can, here)."""
    global _abort_handler
    _abort_handler = handler


@dataclass(frozen=True)
class AbortTarget:
    """The run an abort request was recorded for: its owner and lease generation."""

    session_id: str
    owner_id: str
    generation: int


async def request_abort_of_run(session_id: str) -> AbortTarget | None:
    """Record an abort for a run of *session_id* on another node: there is none."""
    return None


async def request_abort(session_id: str) -> bool:
    return await request_abort_of_run(session_id) is not None


#: What an abort came to, as the caller is told it.
ABORT_STOPPED = "stopped"  # the run it named has ended
ABORT_REQUESTED = "requested"  # its node took the request and is stopping it
ABORT_UNCONFIRMED = "unconfirmed"  # recorded, but its node has not answered: lost, or stuck
ABORT_NOT_RUNNING = "not_running"  # no run of the session was executing


async def confirm_abort(target: AbortTarget, *, timeout: float = 3.0, interval: float = 0.1) -> str:
    return ABORT_STOPPED


class SessionOwnershipLost(RuntimeError):
    """This runner's execution lease expired or was replaced."""


_current: ContextVar[SessionExecutionLease | None] = ContextVar("session_execution_lease", default=None)
#: The lease each session's current holder took; a released one is removed.
_held: dict[str, SessionExecutionLease] = {}
_generations: dict[str, int] = {}


@dataclass
class SessionExecutionLease:
    session_id: str
    owner_id: str
    generation: int
    duration: float
    #: Until when a lease nobody has entered (``async with``) yet stays taken.
    deadline: float
    lost: bool = False
    released: bool = False
    _entered: bool = field(default=False, repr=False)
    _context_token: Any = field(default=None, repr=False)

    def _live(self) -> bool:
        return not self.released and (self._entered or time.monotonic() < self.deadline)

    @classmethod
    async def acquire(cls, session_id: str, *, duration: float = 30.0) -> SessionExecutionLease | None:
        """Take the session's lease; None while another holder has it."""
        if duration <= 0:
            raise ValueError("Session lease duration must be positive")
        holder = _held.get(session_id)
        if holder is not None and holder._live():
            return None
        generation = _generations.get(session_id, 0) + 1
        _generations[session_id] = generation
        lease = cls(
            session_id,
            f"{process_owner_id()}:{uuid.uuid4().hex}",
            generation,
            duration,
            time.monotonic() + duration,
        )
        _held[session_id] = lease
        return lease

    def require_owned(self) -> None:
        if self.lost or self.released:
            self.lost = True
            raise SessionOwnershipLost(f"Session execution ownership lost: {self.session_id}")

    async def verify(self, db: Any) -> None:
        self.require_owned()

    async def release(self) -> None:
        self.released = True
        if _held.get(self.session_id) is self:
            del _held[self.session_id]

    async def __aenter__(self) -> SessionExecutionLease:
        self.require_owned()
        self._entered = True
        self._context_token = _current.set(self)
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        _current.reset(self._context_token)
        await self.release()
        return False


def current_lease(session_id: str) -> SessionExecutionLease | None:
    lease = _current.get()
    return lease if lease is not None and lease.session_id == session_id else None


@contextmanager
def outside_execution() -> Iterator[None]:
    """Write as an independent caller, not as the execution that scheduled this work.

    Tasks copy the context they start in, so work a runner leaves behind (a
    delegated child's result) would otherwise still count as that runner's
    after it ended. Callers keep their own guards, such as Session.run_active_write."""
    previous = _current.set(None)
    try:
        yield
    finally:
        _current.reset(previous)


def require_execution_allowed(session_id: str) -> None:
    """Fail before starting work when the run this code belongs to no longer holds its session."""
    from flocks.task.lease import require_current_execution
    require_current_execution()
    lease = current_lease(session_id)
    if lease is not None:
        lease.require_owned()


async def require_current_ownership(db: Any, session_id: str) -> None:
    lease = current_lease(session_id)
    if lease is not None:
        await lease.verify(db)


async def is_owned(session_id: str) -> bool:
    """Whether a runner holds *session_id*'s lease right now."""
    holder = _held.get(session_id)
    return holder is not None and holder._live()
