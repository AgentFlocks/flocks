"""Filename helpers shared by channel media integrations."""

from __future__ import annotations

import re
import unicodedata

_INVALID_FILENAME_CHARS_RE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


def sanitize_filename(name: str, *, fallback: str = "attachment", max_chars: int = 120) -> str:
    """Return a filesystem-safe filename while preserving Unicode text."""
    cleaned = unicodedata.normalize("NFC", str(name or "")).strip()
    cleaned = cleaned.replace("\\", "/").split("/")[-1].strip()
    cleaned = _INVALID_FILENAME_CHARS_RE.sub("_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in {".", ".."}:
        cleaned = ""
    return cleaned[:max_chars] or fallback
