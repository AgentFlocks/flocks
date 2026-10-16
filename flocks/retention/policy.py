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

import os
from typing import Any, Optional

from pydantic import BaseModel, Field

from flocks.utils.log import set_log_retention_days_override

MAX_RETENTION_DAYS = 3650
DEFAULT_LOG_DAYS = 30
CONFIG_SECTION = "dataRetention"


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


def apply_log_retention(policy: RetentionPolicy) -> None:
    """Make the logger's own startup / day-rollover cleanup follow the policy."""
    set_log_retention_days_override(policy.log_days)


async def preload_log_retention() -> None:
    """Apply the saved log-retention period before the logger's first cleanup.

    Startup calls ``Log.init`` (which prunes old logs) before the retention
    scheduler loads the policy, so without this a saved ``0`` (keep forever) or a
    period longer than ``FLOCKS_LOG_RETENTION_DAYS`` would still lose logs on
    that first prune. Best-effort: a config read failure leaves the env default.
    """
    try:
        apply_log_retention(await load_policy())
    except Exception:
        pass
