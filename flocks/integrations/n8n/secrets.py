"""Secret lookup helpers for n8n integration tools."""

from __future__ import annotations

import os
from typing import Optional

from flocks.security import get_secret_manager, resolve_secret_value


def normalize_secret_ref(secret_ref: Optional[str]) -> str:
    ref = (secret_ref or "N8N_API_KEY").strip() or "N8N_API_KEY"
    if ref.startswith("{secret:") and ref.endswith("}"):
        return ref[len("{secret:"):-1].strip() or "N8N_API_KEY"
    return ref


def resolve_n8n_api_key(*, explicit: Optional[str] = None, secret_ref: Optional[str] = None) -> Optional[str]:
    """Resolve an n8n API key without exposing it in workflow JSON.

    ``explicit`` exists for tool execution contexts that already hold a
    transient secret value.  Production paths should prefer ``secret_ref`` and
    environment/secret-manager backed injection.
    """

    if explicit:
        return explicit
    ref = normalize_secret_ref(secret_ref)
    return os.environ.get(ref) or resolve_secret_value(ref)


def store_n8n_api_key(secret_ref: str, api_key: str) -> str:
    """Persist an n8n API key in Flocks' local SecretManager and return its id."""

    normalized = normalize_secret_ref(secret_ref)
    get_secret_manager().set(normalized, api_key.strip())
    return normalized


def delete_n8n_api_key(secret_ref: str) -> bool:
    return get_secret_manager().delete(normalize_secret_ref(secret_ref))


def redact_secret(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= 10:
        return "<redacted>"
    return f"{text[:4]}...{text[-4:]}"
