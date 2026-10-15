"""Is the database this process writes to usable right now?"""

from __future__ import annotations

from typing import Tuple

#: Readiness probe name (see flocks.plugin.readiness).
DATABASE_PROBE = "database"


async def database_ready() -> Tuple[bool, str]:
    """Ready when the configured database answers and accepts writes.

    A standby answers queries but refuses writes, and every request that saves
    anything would fail on it; behind a load balancer that is the same as
    being down, so it reports not ready.
    """

    from flocks.storage.storage import Storage

    async with Storage.transaction(ensure_init=False) as db:
        row = await db.fetch_one("SELECT pg_is_in_recovery()")
    if row is None:
        return False, "database returned no row"
    if bool(row[0]):
        return False, "database is a read-only standby"
    return True, "database primary"


__all__ = ["DATABASE_PROBE", "database_ready"]
