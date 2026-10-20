"""Is the database this process writes to usable right now?

Three users ask:

* the readiness endpoint (``/ready``), for the load balancer: a node whose
  database is gone, or turned into a standby, keeps running but cannot save
  anything, so it is taken out of rotation;
* the background loops that start work on their own (task queue, workflow
  schedules): they start nothing while the database is not usable, because
  work begun then fails half-way (``execution_allowed``);
* the monitor that keeps the answer current when no load balancer asks.

Coming back is not the same as being back: a database that just recovered (a
failover in progress, a flapping network) is let in again only once it has
answered as a writable primary for ``FLOCKS_READY_RECOVERY_STABLE_S`` seconds
(default 10).  At startup there is nothing to recover from, so the first good
answer counts.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional, Tuple

from flocks.utils.log import Log

log = Log.create(service="db.health")

#: Readiness probe name (see flocks.plugin.readiness).
DATABASE_PROBE = "database"

#: How often the monitor asks when nothing else does.
MONITOR_INTERVAL_S = 5.0

_DEFAULT_STABLE_S = 10.0


def recovery_stable_seconds() -> float:
    raw = os.environ.get("FLOCKS_READY_RECOVERY_STABLE_S", "").strip()
    try:
        return max(0.0, float(raw)) if raw else _DEFAULT_STABLE_S
    except ValueError:
        return _DEFAULT_STABLE_S


async def database_ready() -> Tuple[bool, str]:
    """Ready when the configured database answers and accepts writes.

    A standby answers queries but refuses writes, and so does a session the
    server puts in read-only mode; every request that saves anything would
    fail on either, which behind a load balancer is the same as being down.
    Raises when the database does not answer (the probe runner counts that
    as not ready).
    """

    from flocks.storage.storage import Storage

    async with Storage.transaction(ensure_init=False) as db:
        row = await db.fetch_one("SELECT pg_is_in_recovery(), current_setting('transaction_read_only')")
    if row is None:
        return False, "database returned no row"
    if bool(row[0]):
        return False, "database is a read-only standby"
    if len(row) > 1 and str(row[1]).lower() == "on":
        return False, "database session is read-only"
    return True, "database primary"


class _Tracker:
    """The database's recent answers, and whether they are enough to let work in."""

    def __init__(self) -> None:
        self.failed_once = False
        self.ok_since: Optional[float] = None
        self.allowed = True  # nothing observed yet: no reason to hold work back
        self.detail = "not checked yet"

    def record(self, ok: bool, detail: str, now: float) -> Tuple[bool, str]:
        if not ok:
            if self.allowed:
                log.warn("db.health.not_usable", {"detail": detail})
            self.failed_once, self.ok_since, self.allowed, self.detail = True, None, False, detail
            return False, detail
        if self.ok_since is None:
            self.ok_since = now
        stable_for = now - self.ok_since
        needed = recovery_stable_seconds() if self.failed_once else 0.0
        if stable_for < needed:
            self.allowed = False
            self.detail = f"database recovering: writable for {stable_for:.0f}s of the {needed:.0f}s required"
            return False, self.detail
        if not self.allowed:
            log.info("db.health.usable_again", {"detail": detail, "stable_s": round(stable_for, 1)})
        self.allowed, self.detail = True, detail
        return True, detail


_tracker = _Tracker()


async def check_database() -> Tuple[bool, str]:
    """``database_ready`` through the recovery window; what /ready and the gate use."""
    try:
        ok, detail = await asyncio.wait_for(database_ready(), timeout=2.0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        ok, detail = False, f"database unreachable ({type(exc).__name__})"
    return _tracker.record(ok, detail, time.monotonic())


def execution_allowed() -> bool:
    """Whether background loops may start new work: the database was usable when last checked."""
    return _tracker.allowed


def execution_gate_detail() -> str:
    return _tracker.detail


async def run_database_monitor(interval: Optional[float] = None) -> None:
    """Keep the answer current until cancelled, whether or not /ready is asked."""
    period = MONITOR_INTERVAL_S if interval is None else interval
    while True:
        await check_database()
        await asyncio.sleep(period)


def reset_database_health() -> None:
    """Forget what was observed.  For tests."""
    global _tracker
    _tracker = _Tracker()


__all__ = [
    "DATABASE_PROBE",
    "check_database",
    "database_ready",
    "execution_allowed",
    "execution_gate_detail",
    "recovery_stable_seconds",
    "reset_database_health",
    "run_database_monitor",
]
