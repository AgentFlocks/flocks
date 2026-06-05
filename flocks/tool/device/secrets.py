"""Credential management for device integrations.

Sensitive values (fields marked ``storage: secret`` in ``_provider.yaml``) are
never written to SQL. Instead they are stored in ``.secret.json`` via
SecretManager (mode 0600) and the SQL ``fields`` column holds an opaque
``{secret:device_<uuid>_<key>}`` placeholder.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Optional, Tuple

from flocks.tool.schema.api_service_schema import _extract_secret_id as _parse_placeholder
from flocks.utils.log import Log

log = Log.create(service="tool.device.secrets")

_PLACEHOLDER_PREFIX = "{secret:"
_PLACEHOLDER_SUFFIX = "}"

#: Fallback when a plugin's ``_provider.yaml`` exposes no credential schema.
_FALLBACK_SECRET_KEYS: FrozenSet[str] = frozenset(
    {"api_key", "secret", "password", "token", "client_secret", "access_token"}
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _secret_id(device_id: str, field_key: str) -> str:
    """``device_<uuid>_<field_key>`` — unique, device-scoped secret name."""
    return f"device_{device_id}_{field_key}"


def _secret_keys_for(storage_key: str) -> FrozenSet[str]:
    """Field keys that must go to ``.secret.json`` rather than SQL.

    Reads the plugin's ``_provider.yaml`` credential schema; falls back to a
    hard-coded sensitive-name list for legacy or missing schemas.
    """
    try:
        from flocks.tool.schema.api_service_schema import (
            _build_api_service_credential_schema,
            _load_api_service_metadata_data,
        )
        meta = _load_api_service_metadata_data(storage_key) or {}
        schema = _build_api_service_credential_schema(storage_key, meta)
        return frozenset(f.key for f in schema if f.storage == "secret")
    except Exception:
        return _FALLBACK_SECRET_KEYS


def _normalize_config_field(storage_key: str, key: str, value: str) -> str:
    """Canonicalize non-secret device fields before storing them in SQL."""
    if key not in {"base_url", "baseUrl"}:
        return value
    if storage_key != "tdp_api" and not storage_key.startswith("tdp_api_v"):
        return value

    normalized = value.strip().rstrip("/")
    for suffix in ("/config/api", "/api/v1"):
        if normalized.lower().endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            break
    return normalized


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def persist_fields(
    device_id: str,
    storage_key: str,
    incoming: Dict[str, str],
    prior_db_fields: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Persist credentials and return the ``fields`` dict for SQL storage.

    Rules:
    - **Sensitive, non-empty** → write to SecretManager, store placeholder.
    - **Sensitive, empty/absent** → keep existing placeholder (no-op).
    - **Non-sensitive** → store plaintext.
    - Keys absent from ``incoming`` inherit from ``prior_db_fields``.
    """
    from flocks.security import get_secret_manager

    secret_keys = _secret_keys_for(storage_key)
    secrets = get_secret_manager()
    result = dict(prior_db_fields or {})

    for key, value in (incoming or {}).items():
        if key in secret_keys:
            if not value or not value.strip():
                continue  # keep existing placeholder
            sid = _secret_id(device_id, key)
            try:
                secrets.set(sid, value)
            except Exception as exc:
                log.warn("tool.device.secret.set_error", {"id": sid, "error": str(exc)})
                continue
            result[key] = f"{_PLACEHOLDER_PREFIX}{sid}{_PLACEHOLDER_SUFFIX}"
        else:
            result[key] = _normalize_config_field(storage_key, key, value)

    return result


def delete_secrets(device_id: str, db_fields: Dict[str, str]) -> None:
    """Remove all SecretManager entries owned by *device_id*. Idempotent."""
    from flocks.security import get_secret_manager

    secrets = get_secret_manager()
    prefix = f"device_{device_id}_"
    for raw in db_fields.values():
        sid = _parse_placeholder(raw)
        if sid and sid.startswith(prefix):
            try:
                secrets.delete(sid)
            except Exception as exc:
                log.warn("tool.device.secret.delete_error", {"id": sid, "error": str(exc)})


def mask_for_display(db_fields: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, bool]]:
    """Return ``(display_fields, fields_set)`` safe to send to the frontend.

    Sensitive placeholders are resolved then masked (e.g. ``sk-***abc``).
    ``fields_set[key]`` is True when a value is actually configured.
    """
    from flocks.security import get_secret_manager
    from flocks.security.secrets import SecretManager

    secrets = get_secret_manager()
    display: Dict[str, str] = {}
    has_value: Dict[str, bool] = {}

    for key, raw in db_fields.items():
        sid = _parse_placeholder(raw)
        if sid is not None:
            real = secrets.get(sid) or ""
            display[key] = SecretManager.mask(real) if real else ""
            has_value[key] = bool(real)
        else:
            display[key] = raw if isinstance(raw, str) else ""
            has_value[key] = bool(display[key])

    return display, has_value


def resolve_for_runtime(db_fields: Dict[str, str]) -> Dict[str, str]:
    """Resolve ``{secret:…}`` placeholders to plaintext.

    Call ONLY at the moment of making an outbound API request.
    Never store or return the result through a public interface.
    """
    from flocks.security import get_secret_manager

    secrets = get_secret_manager()
    out: Dict[str, str] = {}
    for key, raw in db_fields.items():
        sid = _parse_placeholder(raw)
        if sid is not None:
            out[key] = secrets.get(sid) or ""
        else:
            out[key] = raw if isinstance(raw, str) else ""
    return out
