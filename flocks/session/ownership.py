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
ALTER TABLE session_execution_leases ADD COLUMN IF NOT EXISTS abort_requested BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE session_execution_leases ADD COLUMN IF NOT EXISTS abort_generation BIGINT;
ALTER TABLE session_execution_leases ADD COLUMN IF NOT EXISTS abort_acked_generation BIGINT;
"""

#: How often a running session looks for an abort asked for on another node.
ABORT_POLL_INTERVAL_S = 1.0

# Called with a session id when another node asked to abort a session this
# process runs; the server installs its own abort path (see set_abort_handler).
_abort_handler: Any = None


def set_abort_handler(handler: Any) -> None:
    """Install what runs when another node asks this one to abort a session."""
    global _abort_handler
    _abort_handler = handler


@dataclass(frozen=True)
class AbortTarget:
    """The run an abort request was recorded for: its owner and lease generation."""

    session_id: str
    owner_id: str
    generation: int


async def request_abort_of_run(session_id: str) -> AbortTarget | None:
    """Record an abort for the run executing *session_id* right now, wherever it runs.

    The request names that run's generation: the run that holds the lease when
    it is recorded is the one asked to stop, and no later run of the session
    acts on it.  None when no run holds a live lease.
    """
    async with Storage.transaction() as db:
        row = await db.fetch_one(
            "UPDATE session_execution_leases SET abort_requested = TRUE, abort_generation = generation "
            "WHERE session_id = ? AND expires_at > clock_timestamp() RETURNING owner_id, generation",
            (session_id,),
        )
    if row is None:
        return None
    return AbortTarget(session_id, str(row[0]), int(row[1]))


async def request_abort(session_id: str) -> bool:
    """Ask whichever node runs *session_id* to abort it.  True when one is running it."""
    return await request_abort_of_run(session_id) is not None


#: What an abort came to, as the caller is told it.
ABORT_STOPPED = "stopped"  # the run it named has ended
ABORT_REQUESTED = "requested"  # its node took the request and is stopping it
ABORT_UNCONFIRMED = "unconfirmed"  # recorded, but its node has not answered: lost, or stuck
ABORT_NOT_RUNNING = "not_running"  # no run of the session was executing


async def confirm_abort(target: AbortTarget, *, timeout: float = 3.0, interval: float = 0.1) -> str:
    """Wait a little for the run *target* names to stop, and say how far it got.

    The run's node acknowledges a request when its lease loop picks it up
    (every ABORT_POLL_INTERVAL_S) and releases the lease when the run ends; a
    node that does neither within *timeout* is not answering, and the run
    stays where it is until its lease expires.
    """
    deadline = time.monotonic() + max(timeout, 0.0)
    acknowledged = False
    while True:
        async with Storage.transaction() as db:
            row = await db.fetch_one(
                "SELECT generation, expires_at > clock_timestamp(), abort_acked_generation "
                "FROM session_execution_leases WHERE session_id = ?",
                (target.session_id,),
            )
        if row is None or int(row[0]) != target.generation or not row[1]:
            return ABORT_STOPPED
        acknowledged = acknowledged or (row[2] is not None and int(row[2]) == target.generation)
        if time.monotonic() >= deadline:
            return ABORT_REQUESTED if acknowledged else ABORT_UNCONFIRMED
        await asyncio.sleep(interval)


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
    abort_handled: bool = False
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
                    expires_at = EXCLUDED.expires_at,
                    abort_requested = FALSE,
                    abort_generation = NULL,
                    abort_acked_generation = NULL
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

    async def _abort_requested(self) -> bool:
        async with Storage.transaction() as db:
            row = await db.fetch_one(
                "SELECT abort_requested, abort_generation FROM session_execution_leases "
                "WHERE session_id = ? AND owner_id = ? AND generation = ?",
                (self.session_id, self.owner_id, self.generation),
            )
        # A request names the run it was made for; one made for an earlier
        # run of the session is not this run's.
        return bool(row is not None and row[0] and (row[1] is None or int(row[1]) == self.generation))

    async def _acknowledge_abort(self) -> None:
        """Tell whoever asked that this run took the request (see confirm_abort)."""
        async with Storage.transaction() as db:
            await db.execute(
                "UPDATE session_execution_leases SET abort_acked_generation = generation "
                "WHERE session_id = ? AND owner_id = ? AND generation = ?",
                (self.session_id, self.owner_id, self.generation),
            )

    async def _handle_abort_request(self) -> None:
        """Another node asked to abort this session: abort it here, where it runs."""
        self.abort_handled = True
        try:
            await self._acknowledge_abort()
        except Exception:
            pass  # the abort itself matters more than saying so
        handler = _abort_handler
        if handler is not None:
            try:
                outcome = handler(self.session_id)
                if asyncio.iscoroutine(outcome):
                    await outcome
                return
            except Exception:
                pass
        # No abort path installed (or it failed): stop the run outright.
        if self._owner_task is not None and not self._owner_task.done():
            self._owner_task.cancel()

    async def _renew(self) -> None:
        last_renewal = time.monotonic()
        try:
            while True:
                remaining = self.deadline - time.monotonic()
                await asyncio.sleep(min(ABORT_POLL_INTERVAL_S, self.duration / 3, max(0.01, remaining)))
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    break
                if not self.abort_handled:
                    try:
                        if await asyncio.wait_for(self._abort_requested(), timeout=remaining):
                            await self._handle_abort_request()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass  # the next poll asks again
                if time.monotonic() - last_renewal < self.duration / 3:
                    continue
                started = time.monotonic()
                remaining = self.deadline - started
                if remaining <= 0:
                    break
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
                last_renewal = started
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
