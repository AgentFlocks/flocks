"""Secret lookup helpers for n8n integration tools."""

from __future__ import annotations

import os
from typing import Optional


def resolve_n8n_api_key(*, explicit: Optional[str] = None, secret_ref: Optional[str] = None) -> Optional[str]:
    """Resolve an n8n API key without persisting it.

    ``explicit`` exists for tool execution contexts that already hold a
    transient secret value.  Production paths should prefer ``secret_ref`` and
    environment/secret-manager backed injection.
    """

    if explicit:
        return explicit
    ref = (secret_ref or "N8N_API_KEY").strip() or "N8N_API_KEY"
    return os.environ.get(ref)


def redact_secret(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= 10:
        return "<redacted>"
    return f"{text[:4]}...{text[-4:]}"

