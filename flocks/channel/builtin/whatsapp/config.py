"""Configuration helpers for the WhatsApp channel."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_BRIDGE_PORT = 3100
DEFAULT_TEXT_BATCH_DELAY_SECONDS = 3.0
DEFAULT_SEND_CHUNK_DELAY_MS = 300
DEFAULT_MESSAGE_LIMIT = 4096
DEFAULT_SEND_TIMEOUT_MS = 60000

VALID_MODES = {"bot", "self-chat"}
VALID_DM_POLICIES = {"open", "allowlist", "disabled"}
VALID_GROUP_POLICIES = {"open", "allowlist", "disabled"}
VALID_GROUP_TRIGGERS = {"mention", "all"}

_PHONE_RE = re.compile(r"^\+?\d{6,20}$")
_JID_RE = re.compile(r"^[\w.+-]+@(?:s\.whatsapp\.net|g\.us|lid|broadcast|newsletter)$")


def coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def coerce_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def coerce_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return default
    return parsed


def coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def default_state_dir() -> Path:
    raw = os.getenv("FLOCKS_WHATSAPP_DATA_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".flocks" / "workspace" / "channels" / "whatsapp"


def default_session_path() -> Path:
    return default_state_dir() / "session"


def default_media_cache_dir() -> Path:
    return default_state_dir() / "media"


def default_bridge_dir() -> Path:
    return Path(__file__).resolve().parent / "bridge"


def find_executable(name: str) -> Optional[str]:
    return shutil.which(name)


def normalize_jid(value: str) -> str:
    raw = coerce_str(value)
    if not raw:
        return ""
    if ":" in raw and "@" in raw:
        raw = re.sub(r":\d+@", "@", raw, count=1)
    if _JID_RE.fullmatch(raw):
        return raw
    phone = raw.lstrip("+").replace(" ", "")
    if _PHONE_RE.fullmatch(phone):
        return f"{phone}@s.whatsapp.net"
    return raw


def strip_jid(value: str) -> str:
    raw = normalize_jid(value)
    if "@" in raw:
        return raw.split("@", 1)[0].split(":", 1)[0]
    return raw.lstrip("+")


def identifier_aliases(*values: Any) -> list[str]:
    """Return stable WhatsApp identity aliases while preserving order."""
    aliases: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, (list, tuple, set)):
            items: Iterable[Any] = value
        else:
            items = (value,)
        for item in items:
            raw = coerce_str(item)
            if not raw:
                continue
            normalized = normalize_jid(raw)
            stripped = strip_jid(normalized)
            for alias in (raw, normalized, stripped):
                if alias and alias not in seen:
                    aliases.append(alias)
                    seen.add(alias)
    return aliases


def parse_target(raw: str) -> str:
    value = coerce_str(raw)
    for prefix in ("whatsapp:", "wa:", "user:", "group:"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):].strip()
            break
    return normalize_jid(value)


def matches_identifier(candidate: str | list[str], allowed: list[str]) -> bool:
    if not allowed:
        return False
    if "*" in allowed:
        return True
    aliases = set(identifier_aliases(candidate))
    for entry in allowed:
        aliases_for_entry = set(identifier_aliases(entry))
        if aliases & aliases_for_entry:
            return True
    return False


def format_env_list(items: list[str]) -> str:
    return ",".join(item for item in items if item)
