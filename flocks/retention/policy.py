"""Data retention policy: how many days logs, messages and sessions are kept.

Stored in flocks.json as ``dataRetention`` (``logDays`` / ``messageDays`` /
``sessionDays``); ``0`` keeps that kind of data forever. A key that is not
set falls back to the behaviour before the policy existed: logs follow
FLOCKS_LOG_RETENTION_DAYS (30 days when unset), messages and sessions are
never deleted.

The message and session periods are independent: a session goes once its
last activity is ``sessionDays`` old, and inside the sessions that remain,
whole turns older than ``messageDays`` go.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from pydantic import BaseModel, Field

from flocks.utils.log import set_log_cleanup_managed, set_log_retention_days_override

MAX_RETENTION_DAYS = 3650
DEFAULT_LOG_DAYS = 30
CONFIG_SECTION = "dataRetention"
# Moved by every policy save; see policy_unchanged.
POLICY_VERSION_KEY = "data_retention:policy_version"


class RetentionPolicy(BaseModel):
    """Retention days per kind of data; 0 keeps that kind forever."""

    model_config = {"populate_by_name": True}

    log_days: int = Field(..., alias="logDays", ge=0, le=MAX_RETENTION_DAYS)
    message_days: int = Field(..., alias="messageDays", ge=0, le=MAX_RETENTION_DAYS)
    session_days: int = Field(..., alias="sessionDays", ge=0, le=MAX_RETENTION_DAYS)

    def to_config(self) -> dict[str, int]:
        return {
            "logDays": self.log_days,
            "messageDays": self.message_days,
            "sessionDays": self.session_days,
        }


def default_log_days() -> int:
    """Log retention from FLOCKS_LOG_RETENTION_DAYS; 30 when unset or unreadable, 0 for 0 or less."""
    raw = os.getenv("FLOCKS_LOG_RETENTION_DAYS")
    if raw is None:
        return DEFAULT_LOG_DAYS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_LOG_DAYS
    return min(max(value, 0), MAX_RETENTION_DAYS)


def _configured_days(value: Optional[int], default: int) -> int:
    if value is None or value < 0:
        return default
    return min(value, MAX_RETENTION_DAYS)


def policy_from_config(config: Any) -> RetentionPolicy:
    """Effective policy for a loaded ConfigInfo, tolerating hand-edited values."""
    section = getattr(config, "data_retention", None)
    return RetentionPolicy(
        logDays=_configured_days(getattr(section, "log_days", None), default_log_days()),
        messageDays=_configured_days(getattr(section, "message_days", None), 0),
        sessionDays=_configured_days(getattr(section, "session_days", None), 0),
    )


async def load_policy() -> RetentionPolicy:
    from flocks.config.config import Config

    return policy_from_config(await Config.get())


async def load_saved_policy() -> RetentionPolicy:
    """The policy as saved right now: every configuration layer, never a cached copy.

    A cache another request refilled before a save would pair an old policy
    with the version the save moved (see policy_unchanged).
    """
    from flocks.config.config import Config
    from flocks.config.runtime import runtime_config

    override = runtime_config.get()
    return policy_from_config(override if override is not None else await Config.load_fresh())


def apply_log_retention(policy: RetentionPolicy) -> None:
    """Make the logger's own startup / day-rollover cleanup follow the policy."""
    set_log_retention_days_override(policy.log_days)


async def preload_log_retention() -> None:
    """Hand log cleanup over to the data retention scheduler before the logger starts.

    Called before ``Log.init`` by every Flocks process (CLI and server). From
    then on the logger deletes nothing on its own, neither on startup nor at
    day rollover, where it only knows the period it read once: expired logs go
    only in the scheduler's rounds, under the policy as last saved anywhere and
    the shared policy version (see policy_unchanged). The saved period is still
    applied for what asks the logger.
    """
    set_log_cleanup_managed(True)
    try:
        apply_log_retention(await load_saved_policy())
    except Exception:
        pass


def _version_of(row: Any) -> int:
    if row is None or row[0] is None:
        return 0
    return int(json.loads(row[0]) if isinstance(row[0], str) else row[0])


async def policy_version() -> int:
    """The shared version every policy save moves; a cleanup reads it before the policy."""
    from flocks.storage.storage import Storage

    async with Storage.transaction() as db:
        row = await db.fetch_one("SELECT value FROM runtime_state WHERE key = ?", (POLICY_VERSION_KEY,))
    return _version_of(row)


async def bump_policy_version() -> int:
    """Move the shared version after a new policy was written to flocks.json.

    Waits for any deletion holding the version (``policy_unchanged``) to finish,
    so once this returns no worker deletes under an older policy.
    """
    from flocks.storage.storage import Storage

    async with Storage.transaction() as db:
        row = await db.fetch_one(
            """INSERT INTO runtime_state(key, value, value_type) VALUES (?, '1'::jsonb, 'json')
            ON CONFLICT (key) DO UPDATE SET value = to_jsonb((runtime_state.value #>> '{}')::bigint + 1),
                updated_at = clock_timestamp()
            RETURNING value""",
            (POLICY_VERSION_KEY,),
        )
    return _version_of(row)


@asynccontextmanager
async def policy_unchanged(version: int) -> AsyncIterator[bool]:
    """Hold the shared policy version across one deletion; yield whether it is still ``version``.

    A save moves the version only after this lets go, and a deletion guarded
    after that sees the new version, so every worker orders policy changes and
    deletions the same way: none deletes under a policy an admin has replaced.
    """
    from flocks.storage.storage import Storage

    async with Storage.transaction() as db:
        await db.execute(
            "INSERT INTO runtime_state(key, value, value_type) VALUES (?, '0'::jsonb, 'json') "
            "ON CONFLICT (key) DO NOTHING",
            (POLICY_VERSION_KEY,),
        )
        row = await db.fetch_one(
            "SELECT value FROM runtime_state WHERE key = ? FOR SHARE", (POLICY_VERSION_KEY,),
        )
        yield _version_of(row) == version
