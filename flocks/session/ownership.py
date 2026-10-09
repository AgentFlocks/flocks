"""Short PostgreSQL transactions fence session runners and orphan recovery."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from flocks.storage.storage import Storage
from flocks.task.node import process_owner_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_execution_leases (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    generation BIGINT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_execution_expiry ON session_execution_leases(expires_at);
"""


class SessionOwnershipLost(RuntimeError):
    """This runner's execution lease expired or was replaced."""


_current: ContextVar[SessionExecutionLease | None] = ContextVar("session_execution_lease", default=None)


@dataclass
class SessionExecutionLease:
    session_id: str
    owner_id: str
    generation: int
    duration: float
    deadline: float
    lost: bool = False
    released: bool = False
    _renewal: asyncio.Task | None = field(default=None, repr=False)
    _owner_task: asyncio.Task | None = field(default=None, repr=False)
    _context_token: Any = field(default=None, repr=False)

    @classmethod
    async def acquire(cls, session_id: str, *, duration: float = 30.0) -> SessionExecutionLease | None:
        if duration <= 0:
            raise ValueError("Session lease duration must be positive")
        owner = f"{process_owner_id()}:{uuid.uuid4().hex}"
        started = time.monotonic()
        async with Storage.transaction() as db:
            row = await db.fetch_one(
                """INSERT INTO session_execution_leases(session_id, owner_id, generation, expires_at)
                VALUES (?, ?, 1, clock_timestamp() + (? * interval '1 second'))
                ON CONFLICT (session_id) DO UPDATE SET owner_id = EXCLUDED.owner_id,
                    generation = session_execution_leases.generation + 1,
                    expires_at = EXCLUDED.expires_at
                WHERE session_execution_leases.expires_at <= clock_timestamp()
                RETURNING generation""",
                (session_id, owner, duration),
            )
        if row is None:
            return None
        lease = cls(session_id, owner, int(row[0]), duration, started + duration)
        if time.monotonic() >= lease.deadline:
            await lease.release()
            return None
        return lease

    def require_owned(self) -> None:
        if self.lost or self.released or time.monotonic() >= self.deadline:
            self.lost = True
            raise SessionOwnershipLost(f"Session execution ownership lost: {self.session_id}")

    async def verify(self, db: Any) -> None:
        self.require_owned()
        row = await db.fetch_one(
            "SELECT 1 FROM session_execution_leases WHERE session_id = ? AND owner_id = ? "
            "AND generation = ? AND expires_at > clock_timestamp() FOR UPDATE",
            (self.session_id, self.owner_id, self.generation),
        )
        if row is None:
            self.lost = True
            raise SessionOwnershipLost(f"Session execution ownership lost: {self.session_id}")
        self.require_owned()

    async def _renew_once(self) -> bool:
        async with Storage.transaction() as db:
            row = await db.fetch_one(
                "UPDATE session_execution_leases SET expires_at = clock_timestamp() + (? * interval '1 second') "
                "WHERE session_id = ? AND owner_id = ? AND generation = ? AND expires_at > clock_timestamp() RETURNING 1",
                (self.duration, self.session_id, self.owner_id, self.generation),
            )
        return row is not None

    async def _renew(self) -> None:
        try:
            while True:
                await asyncio.sleep(min(self.duration / 3, max(0.01, self.deadline - time.monotonic())))
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    break
                started = time.monotonic()
                try:
                    if not await asyncio.wait_for(self._renew_once(), timeout=remaining):
                        break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # The old deadline remains authoritative through transient
                    # failures, including a stalled pool acquisition.
                    continue
                self.deadline = started + self.duration
        except asyncio.CancelledError:
            raise
        self.lost = True
        if self._owner_task is not None and not self._owner_task.done():
            self._owner_task.cancel()

    async def release(self) -> None:
        self.released = True
        async def release_row() -> None:
            async with Storage.transaction() as db:
                await db.execute(
                    "UPDATE session_execution_leases SET expires_at = clock_timestamp() "
                    "WHERE session_id = ? AND owner_id = ? AND generation = ?",
                    (self.session_id, self.owner_id, self.generation),
                )
        await asyncio.wait_for(release_row(), timeout=5.0)

    async def __aenter__(self) -> SessionExecutionLease:
        self.require_owned()
        self._owner_task = asyncio.current_task()
        self._context_token = _current.set(self)
        self._renewal = asyncio.create_task(self._renew(), name=f"session-lease-{self.session_id}")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        if self._renewal is not None:
            self._renewal.cancel()
            await asyncio.gather(self._renewal, return_exceptions=True)
        _current.reset(self._context_token)
        try:
            await self.release()
        except Exception:
            # Expiry still makes this row reclaimable when PostgreSQL returns.
            pass
        if self.lost and (exc is None or isinstance(exc, asyncio.CancelledError)):
            raise SessionOwnershipLost(f"Session execution ownership lost: {self.session_id}")
        return False


def current_lease(session_id: str) -> SessionExecutionLease | None:
    lease = _current.get()
    return lease if lease is not None and lease.session_id == session_id else None


@contextmanager
def outside_execution() -> Iterator[None]:
    """Write as an independent caller, not as the execution that scheduled this work.

    Tasks copy the context they start in, so work a runner leaves behind (a
    delegated child's result) would keep verifying that runner's lease after it
    was released. Callers keep their own guards, such as Session.run_active_write."""
    previous = _current.set(None)
    try:
        yield
    finally:
        _current.reset(previous)


def require_execution_allowed(session_id: str) -> None:
    """Check deadlines before starting work, even before renewal timers resume."""
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
    async with Storage.transaction() as db:
        row = await db.fetch_one(
            "SELECT 1 FROM session_execution_leases WHERE session_id = ? AND expires_at > clock_timestamp()",
            (session_id,),
        )
    return row is not None
