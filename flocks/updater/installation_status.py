"""Read a deployment tool's last installation result without loading that tool.

The optional, versioned record contains display text only. Reading it never
activates an extension, switches storage, or executes a recovery command.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from flocks.updater.handoff_progress import redact_credentials, summarize_error

MAX_RECORD_BYTES = 16_384
# A name that says what it holds, however it is written: PGPASSWORD=, "password": , api_key: , auth_token=.
_CREDENTIAL_NAMED = re.compile(  # secret-guard: allow - compiled redaction expression, not a credential
    r"""(?i)(["']?\b[a-z0-9_.-]*(?:password|passwd|pwd|token|api[_-]?key|secret|credential|authorization)[a-z0-9_.-]*["']?\s*[=:]\s*)"""
    r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:(?:bearer|basic|token)\s+)?[^\s;&,}\]]+)"""
)
_CREDENTIAL_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]{8,}")  # secret-guard: allow - compiled redaction expression


def scrub_credentials(value: str) -> str:
    """Mask credentials wherever an install's output has them: what is shown of it keeps its lines."""
    value = _CREDENTIAL_NAMED.sub(lambda match: match.group(1) + "***", value)
    value = _CREDENTIAL_SCHEME.sub(lambda match: match.group(1) + " ***", value)
    return redact_credentials(value)


def _display_text(value: str) -> str:
    return summarize_error(scrub_credentials(value))


def installation_result_path() -> Path:
    root = Path(os.environ.get("FLOCKS_ROOT") or Path.home() / ".flocks").expanduser()
    return root / "run" / "installation-result.json"


def installation_result_id() -> str | None:
    """The id of the record there is, whatever it says (each one the installer writes has its own);
    None when there is none. Raises when there is one that cannot be read."""
    path = installation_result_path()
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        record = json.loads(stream.read(MAX_RECORD_BYTES + 1))
    return str(record.get("id")) if isinstance(record, dict) else ""


def read_installation_result(locale: str = "zh-CN") -> dict | None:
    path = installation_result_path()
    try:
        if path.is_symlink():
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            return None
        record = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1:
        return None
    if record.get("phase") != "failed" or not isinstance(record.get("id"), str):
        return None
    language = "zh-CN" if locale.lower().startswith("zh") else "en-US"
    texts = record.get("messages")
    text = texts.get(language) if isinstance(texts, dict) else None
    if not isinstance(text, dict):
        return None
    if not all(isinstance(text.get(key), str) and text[key].strip() for key in ("title", "message")):
        return None
    recovery = record.get("recovery")
    if not isinstance(recovery, str):
        return None
    result = {
        "id": record["id"][:160],
        "phase": "failed",
        "recovery": recovery if recovery in {"restored", "unchanged", "retained", "failed"} else "failed",
        "updated_at": str(record.get("updated_at") or "")[:80],
        "title": _display_text(text["title"]),
        "message": _display_text(text["message"]),
        "retry_hint": _display_text(str(text.get("retry_hint") or "")),
        "detail": _display_text(str(record.get("detail") or "")),
    }
    return result
