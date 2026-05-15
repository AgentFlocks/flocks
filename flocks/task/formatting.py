"""Helpers for rendering task timestamps consistently."""

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_task_timezone_name(task: Any) -> Optional[str]:
    trigger = getattr(task, "trigger", None)
    tz_name = getattr(trigger, "timezone", None) if trigger is not None else None
    return tz_name or None


def format_task_datetime(dt: datetime, tz_name: Optional[str] = None) -> str:
    normalized = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    label = tz_name
    if tz_name:
        try:
            normalized = normalized.astimezone(ZoneInfo(tz_name))
        except ZoneInfoNotFoundError:
            label = f"{tz_name} (invalid timezone)"
    if not label:
        label = normalized.tzname() or "UTC"
    return f"{normalized.isoformat(sep=' ', timespec='seconds')} ({label})"
