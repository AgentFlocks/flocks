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

from flocks.updater.handoff_progress import summarize_error

MAX_RECORD_BYTES = 16_384
_CREDENTIAL_ASSIGNMENT = re.compile(  # secret-guard: allow - compiled redaction expression, not a credential
    r"(?i)(\b(?:password|passwd|pwd|token|api[_-]?key|secret)\s*=\s*)"
    r"(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|[^\s;&]+)"
)


def _display_text(value: str) -> str:
    return summarize_error(_CREDENTIAL_ASSIGNMENT.sub(lambda match: match.group(1) + "***", value))


def read_installation_result(locale: str = "zh-CN") -> dict | None:
    root = Path(os.environ.get("FLOCKS_ROOT") or Path.home() / ".flocks").expanduser()
    path = root / "run" / "installation-result.json"
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
