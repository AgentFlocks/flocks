"""Configuration helpers for the Slack channel."""

from __future__ import annotations

import re
from typing import Any, Optional


_TARGET_PREFIX_RE = re.compile(r"^(?:slack|channel|dm|user):", re.IGNORECASE)


def coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def strip_target_prefix(raw: str) -> str:
    value = raw.strip()
    while value:
        next_value = _TARGET_PREFIX_RE.sub("", value, count=1).strip()
        if next_value == value:
            return value
        value = next_value
    return value


def resolve_bot_token(config: dict[str, Any]) -> str:
    return coerce_str(config.get("botToken") or config.get("slackBotToken"))


def resolve_app_token(config: dict[str, Any]) -> str:
    return coerce_str(config.get("appToken") or config.get("slackAppToken"))


def resolve_home_channel(config: dict[str, Any]) -> str:
    return coerce_str(config.get("homeChannel") or config.get("home_channel"))


def should_reply_in_thread(config: dict[str, Any]) -> bool:
    return coerce_bool(config.get("replyInThread"), True)


def should_reply_broadcast(config: dict[str, Any]) -> bool:
    return coerce_bool(config.get("replyBroadcast"), False)


def allow_bot_messages(config: dict[str, Any]) -> str:
    value = coerce_str(config.get("allowBots")).lower()
    if value in {"none", "mentions", "all"}:
        return value
    return "none"


def normalize_target(raw: str, *, fallback: Optional[str] = None) -> str:
    target = strip_target_prefix(raw or "")
    if target:
        return target
    return fallback or ""
