"""Configuration and protocol helpers for the LINE channel."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Any, Optional

from flocks.channel.base import ChatType


LINE_API_ROOT = "https://api.line.me"
LINE_DATA_API_ROOT = "https://api-data.line.me"
LINE_REPLY_TOKEN_TTL_SECONDS = 50.0
LINE_TEXT_BUBBLE_LIMIT = 5000
LINE_SAFE_TEXT_LIMIT = 4500
LINE_MAX_MESSAGES_PER_CALL = 5


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
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def coerce_int(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def csv_or_list(value: Any) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def verify_line_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    """Verify LINE's X-Line-Signature header against the raw request body."""
    if not body or not signature or not channel_secret:
        return False
    try:
        digest = hmac.new(
            channel_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        expected = base64.b64encode(digest).decode("utf-8")
    except Exception:
        return False
    return hmac.compare_digest(expected, signature)


def resolve_credentials(config: dict[str, Any]) -> tuple[str, str]:
    """Return ``(channel_access_token, channel_secret)`` from config."""
    token = (
        coerce_str(config.get("channelAccessToken"))
        or coerce_str(config.get("channel_access_token"))
        or coerce_str(config.get("accessToken"))
    )
    secret = (
        coerce_str(config.get("channelSecret"))
        or coerce_str(config.get("channel_secret"))
    )
    return token, secret


def resolve_api_roots(config: dict[str, Any]) -> tuple[str, str]:
    api_root = coerce_str(config.get("apiRoot") or config.get("api_root"))
    data_api_root = coerce_str(config.get("dataApiRoot") or config.get("data_api_root"))
    return (api_root.rstrip("/") or LINE_API_ROOT, data_api_root.rstrip("/") or LINE_DATA_API_ROOT)


def normalize_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def resolve_source(source: dict[str, Any]) -> tuple[str, ChatType]:
    source_type = coerce_str(source.get("type"))
    if source_type == "group":
        return coerce_str(source.get("groupId")), ChatType.GROUP
    if source_type == "room":
        return coerce_str(source.get("roomId")), ChatType.GROUP
    return coerce_str(source.get("userId")), ChatType.DIRECT


def source_allowed(source: dict[str, Any], config: dict[str, Any]) -> bool:
    """Apply optional LINE source allowlists before generic Flocks allowFrom."""
    if coerce_bool(config.get("allowAll"), False) or coerce_bool(config.get("allowAllUsers"), False):
        return True

    allowed_users = csv_or_list(config.get("allowedUsers") or config.get("allowed_users"))
    allowed_groups = csv_or_list(config.get("allowedGroups") or config.get("allowed_groups"))
    allowed_rooms = csv_or_list(config.get("allowedRooms") or config.get("allowed_rooms"))
    if not allowed_users and not allowed_groups and not allowed_rooms:
        return True

    source_type = coerce_str(source.get("type"))
    if source_type == "user":
        return coerce_str(source.get("userId")) in allowed_users
    if source_type == "group":
        return coerce_str(source.get("groupId")) in allowed_groups
    if source_type == "room":
        return coerce_str(source.get("roomId")) in allowed_rooms
    return False


def strip_target_prefixes(raw: str) -> str:
    value = raw.strip()
    while value:
        next_value = re.sub(r"^(?:line|user|group|room):", "", value, count=1, flags=re.I).strip()
        if next_value == value:
            return value
        value = next_value
    return value


def normalize_target(raw: str) -> Optional[str]:
    value = strip_target_prefixes(raw)
    return value or None

