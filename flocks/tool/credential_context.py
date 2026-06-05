"""Device credential context for per-call credential override.

Enables Method-A multi-instance device support: when an Agent calls a device
API tool with an optional ``device_id`` kwarg, the ToolRegistry activates a
per-coroutine credential override so that ``SecretManager.get()`` and
``ConfigWriter.get_api_service_raw()`` return that device's specific values
instead of the shared values from ``.secret.json`` / ``flocks.json``.

No external imports – this module is intentionally dependency-free so it can
be safely imported by both ``flocks.security`` and ``flocks.config`` without
creating circular dependencies.

Usage (set up by ToolRegistry.execute when device_id is present):

    async with activate_device_credentials(device_id) as active:
        result = await tool.func(ctx, **kwargs)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Dict, NamedTuple, Optional

# ContextVars – per-coroutine, so concurrent calls don't interfere.

# {secret_id: plaintext_value} – checked by SecretManager.get()
_secret_override: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "device_secret_override", default=None
)

# Raw api_service config dict – checked by ConfigWriter.get_api_service_raw()
_config_override: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "device_config_override", default=None
)

# The service_id this config override belongs to (scoping guard).
# Matched by get_config_override() for the bare service_id alias.
_config_override_service: ContextVar[Optional[str]] = ContextVar(
    "device_config_override_service", default=None
)

# The storage_key for the same override (versioned name, e.g. "sangfor_af_v8_0_48").
# Many handlers set SERVICE_ID to the full storage_key, not the bare service_id,
# so we keep both to avoid a key mismatch causing credential lookup to silently
# fall back to the global default config (wrong IP, wrong credentials).
_config_override_storage_key: ContextVar[Optional[str]] = ContextVar(
    "device_config_override_storage_key", default=None
)

# The currently active device_id (for logging / introspection)
_active_device_id: ContextVar[Optional[str]] = ContextVar(
    "active_device_id", default=None
)

# Per-device SSL verification preference. ``None`` means "no device override
# active – fall back to the provider config / framework default".
_verify_ssl_override: ContextVar[Optional[bool]] = ContextVar(
    "device_verify_ssl_override", default=None
)


# ---------------------------------------------------------------------------
# Internal result type for _build_overrides
# ---------------------------------------------------------------------------

class _DeviceOverrides(NamedTuple):
    """Resolved credential and config data for a single device call."""
    secret_ovr: Optional[Dict[str, str]]
    config_ovr: Optional[Dict[str, Any]]
    service_id: Optional[str]   # bare alias  e.g. "sangfor_af"
    storage_key: Optional[str]  # versioned   e.g. "sangfor_af_v8_0_48"


# ---------------------------------------------------------------------------
# Read helpers (called from SecretManager / ConfigWriter)
# ---------------------------------------------------------------------------

def get_secret_override(secret_id: str) -> Optional[str]:
    """Return device-specific secret value if an override is active."""
    override = _secret_override.get()
    return override.get(secret_id) if override else None


def get_config_override(service_id: str) -> Optional[Dict[str, Any]]:
    """Return device-specific config dict if an override is active for *service_id*.

    Device handlers reference their provider config under two different naming
    conventions:

    * **Bare service_id** (e.g. ``"sangfor_af"``) — produced by
      ``storage_key_to_service_id()`` and stored in ``_config_override_service``.
    * **Versioned storage_key** (e.g. ``"sangfor_af_v8_0_48"``) — the full key
      stored in the DB and in ``_config_override_storage_key``.

    Both are checked so that a handler whose ``SERVICE_ID`` uses either form
    still resolves the correct per-device config rather than silently falling
    back to the global default (wrong IP / wrong credentials).
    """
    override = _config_override.get()
    if override is None:
        return None
    expected_service = _config_override_service.get()
    expected_storage = _config_override_storage_key.get()
    # Match on bare service_id alias OR full versioned storage_key — whichever
    # the calling handler's SERVICE_ID constant happens to use.
    matches_service = expected_service is not None and service_id == expected_service
    matches_storage = expected_storage is not None and service_id == expected_storage
    if matches_service or matches_storage:
        return override
    return None


def get_active_device_id() -> Optional[str]:
    """Return the device_id currently active in this coroutine, or None."""
    return _active_device_id.get()


def get_verify_ssl_override() -> Optional[bool]:
    """Return the per-device ``verify_ssl`` toggle, or ``None`` if no device
    credential override is active for this coroutine."""
    return _verify_ssl_override.get()


# ---------------------------------------------------------------------------
# Activation (called from ToolRegistry.execute)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def activate_device_credentials(device_id: str) -> AsyncIterator[bool]:
    """Async context manager: resolve device credentials and set ContextVars.

    Yields ``True`` if activation succeeded, ``False`` if the device was not
    found / disabled (caller may still continue with default credentials).
    """
    ovr = await _build_overrides(device_id)
    secret_ovr, config_ovr, service_id, storage_key = ovr
    if (secret_ovr is None and config_ovr is None) or service_id is None:
        yield False
        return

    t1 = _secret_override.set(secret_ovr or {})
    t2 = _config_override.set(config_ovr or {})
    t3 = _config_override_service.set(service_id)
    t4 = _active_device_id.set(device_id)
    # ``config_ovr`` always carries a ``verify_ssl`` key once we've reached
    # this point (see :func:`_build_overrides`); fall back to ``False`` so a
    # missing value still produces a defined override (rather than leaving
    # the previous coroutine's value visible).
    verify_ssl = bool((config_ovr or {}).get("verify_ssl", False))
    t5 = _verify_ssl_override.set(verify_ssl)
    # Also store the raw storage_key so handlers whose SERVICE_ID includes the
    # version suffix (e.g. "sangfor_af_v8_0_48") can still match the override
    # via get_config_override() without re-querying the DB.
    t6 = _config_override_storage_key.set(storage_key or None)
    try:
        yield True
    finally:
        _secret_override.reset(t1)
        _config_override.reset(t2)
        _config_override_service.reset(t3)
        _active_device_id.reset(t4)
        _verify_ssl_override.reset(t5)
        _config_override_storage_key.reset(t6)


async def _build_overrides(device_id: str) -> _DeviceOverrides:
    """Build secret and config override dicts for *device_id*.

    Returns a :class:`_DeviceOverrides` named-tuple.  All fields are ``None``
    when the device is not found or disabled.

    * ``service_id``  — bare alias derived by ``storage_key_to_service_id``
                        (e.g. ``"sangfor_af"``).
    * ``storage_key`` — full versioned key as stored in the DB
                        (e.g. ``"sangfor_af_v8_0_48"``).

    Both keys are propagated to ContextVars so :func:`get_config_override`
    can match whichever form a handler's ``SERVICE_ID`` uses.
    """
    _null = _DeviceOverrides(None, None, None, None)
    try:
        from flocks.tool.device.store import get_device_credentials
        creds = await get_device_credentials(device_id)
    except Exception:
        return _null

    if creds is None:
        return _null

    storage_key: str = creds.get("storage_key", "")
    service_id: str = creds.get("service_id", "")
    resolved_fields: Dict[str, str] = creds.get("fields", {})
    verify_ssl: bool = bool(creds.get("verify_ssl", False))

    # Load credential_fields from _provider.yaml for the storage_key.
    # This gives us the mapping: field_key → (secret_id, config_key, storage).
    credential_fields = _load_credential_fields(storage_key)

    secret_ovr: Dict[str, str] = {}
    config_ovr: Dict[str, Any] = {}

    if credential_fields:
        for field in credential_fields:
            fkey = field.get("key", "")
            value = resolved_fields.get(fkey)
            if value is None:
                continue
            storage = field.get("storage", "secret")
            if storage == "secret":
                sid = field.get("secret_id") or fkey
                # 1. Put the actual value into secret_ovr so SecretManager.get(sid) returns it.
                secret_ovr[sid] = value
                # 2. Also put the {secret:sid} placeholder into config_ovr so that handlers
                #    using ConfigWriter.get_api_service_raw() → _resolve_ref() still work.
                ckey = field.get("config_key") or fkey
                placeholder = f"{{secret:{sid}}}"
                config_ovr[ckey] = placeholder
                if ckey != fkey:
                    config_ovr[fkey] = placeholder
            else:
                # config field – store the plain value under all expected key names
                ckey = field.get("config_key") or fkey
                config_ovr[ckey] = value
                if ckey != fkey:
                    config_ovr[fkey] = value
    else:
        # Fallback: no credential_fields metadata – use field values as-is
        for k, v in resolved_fields.items():
            secret_ovr[k] = v
            config_ovr[k] = v

    config_ovr["enabled"] = True
    # Forward the per-device SSL verification toggle. Tool handlers read this
    # via ``ConfigWriter.get_api_service_raw(...)["verify_ssl"]`` (canonical)
    # or the legacy ``ssl_verify`` alias; populate both so older handlers
    # that haven't been migrated still see the right value.
    config_ovr["verify_ssl"] = verify_ssl
    config_ovr["ssl_verify"] = verify_ssl

    return _DeviceOverrides(
        secret_ovr=secret_ovr or None,
        config_ovr=config_ovr or None,
        service_id=service_id or None,
        storage_key=storage_key or None,
    )


def _load_credential_fields(storage_key: str) -> list[Dict[str, Any]]:
    """Load credential_fields from _provider.yaml for *storage_key*.

    Returns an empty list on any failure (best-effort, non-fatal).
    """
    try:
        from flocks.tool.schema.api_service_schema import _load_api_service_metadata_data
        meta = _load_api_service_metadata_data(storage_key)
        if not isinstance(meta, dict):
            return []
        fields = meta.get("credential_fields")
        return fields if isinstance(fields, list) else []
    except Exception:
        return []
