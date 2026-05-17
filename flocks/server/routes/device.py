"""Device Integration routes — CRUD for named device instances backed by SQLite.

Data model
==========

::

    device_groups (机房 / 组)
        id, name (UNIQUE), description, sort_order, ...
    device_integrations (设备实例)
        id, group_id (FK → device_groups.id),
        storage_key, service_id, name, enabled, verify_ssl,
        fields (JSON, secrets as {secret:device_<uuid>_<key>} placeholders),
        status, message, latency_ms, checked_at, ...

Sensitive values
----------------

Credentials marked as ``storage: secret`` in ``_provider.yaml`` are
**never** stored in plaintext in this DB.  Instead they are written to
``.secret.json`` via :class:`SecretManager` (mode 0600, same mechanism
used by ``channels`` / ``api_services``) and the DB column ``fields``
holds an opaque ``{secret:device_<device_id>_<field>}`` placeholder.

Group cardinality
-----------------

The current product spec locks the system to **exactly one** group
("默认机房", renamable).  Future multi-room support is gated by
:data:`MULTI_GROUP_ENABLED` — flip it (or set the env
``FLOCKS_DEVICE_MULTI_GROUP=1``) and the create/delete-group endpoints
become functional.  The data layer is already multi-group ready.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="device.routes")
router = APIRouter()


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

#: When False, the API surface forbids creating or deleting groups; the
#: system maintains exactly one row in ``device_groups`` (the default
#: room, renamable). Future-proofed extension point — toggling this on
#: (or exporting ``FLOCKS_DEVICE_MULTI_GROUP=1``) opens the multi-group
#: routes without any schema migration.
MULTI_GROUP_ENABLED: bool = os.environ.get("FLOCKS_DEVICE_MULTI_GROUP", "").lower() in {"1", "true", "yes"}

DEFAULT_GROUP_ID = "default-room"
DEFAULT_GROUP_NAME = "默认机房"


# ---------------------------------------------------------------------------
# DDL — registered once so Storage.init() picks them up automatically.
# Each DDL is wrapped by Storage in its own try/except, so the additive
# ALTERs below silently no-op on fresh DBs where the column already exists.
# ---------------------------------------------------------------------------

_DDL_GROUPS = """
CREATE TABLE IF NOT EXISTS device_groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
"""

_DDL_DEVICES = """
CREATE TABLE IF NOT EXISTS device_integrations (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    service_id  TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    verify_ssl  INTEGER NOT NULL DEFAULT 0,
    fields      TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'unknown',
    message     TEXT,
    latency_ms  INTEGER,
    checked_at  INTEGER,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_device_storage_key ON device_integrations(storage_key);
CREATE INDEX IF NOT EXISTS idx_device_group       ON device_integrations(group_id);
"""

# Upgrade hooks for installations created before group_id existed.
# Storage.register_ddl runs each DDL in its own try/except, so the
# "duplicate column" failure on fresh installs is harmless.
_DDL_UPGRADE_GROUP_ID = "ALTER TABLE device_integrations ADD COLUMN group_id TEXT NOT NULL DEFAULT '';"

Storage.register_ddl(_DDL_GROUPS)
Storage.register_ddl(_DDL_DEVICES)
Storage.register_ddl(_DDL_UPGRADE_GROUP_ID)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DeviceGroup(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    sort_order: int = 0
    created_at: int
    updated_at: int


class DeviceGroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    sort_order: int = 0


class DeviceGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None


class DeviceIntegration(BaseModel):
    id: str
    group_id: str
    name: str
    storage_key: str
    service_id: str
    enabled: bool = True
    verify_ssl: bool = False
    #: Non-sensitive fields are returned as-is. Sensitive fields are
    #: replaced by their masked preview (e.g. ``sk-***abc``) so the
    #: frontend can show "value already set" without leaking the
    #: plaintext.  An empty string means "no value set yet".
    fields: Dict[str, str] = Field(default_factory=dict)
    #: For each field key, whether a value is currently stored. Useful
    #: for the frontend's "leave blank = keep existing value" UX.
    fields_set: Dict[str, bool] = Field(default_factory=dict)
    status: str = "unknown"
    message: Optional[str] = None
    latency_ms: Optional[int] = None
    checked_at: Optional[int] = None
    created_at: int
    updated_at: int


class DeviceIntegrationCreate(BaseModel):
    name: str
    storage_key: str
    #: Optional; defaults to the single default group when omitted (or
    #: when multi-group is disabled).
    group_id: Optional[str] = None
    #: Optional; derived from ``storage_key`` by stripping ``_v<version>``.
    service_id: Optional[str] = None
    enabled: bool = True
    verify_ssl: bool = False
    fields: Dict[str, str] = Field(default_factory=dict)


class DeviceIntegrationUpdate(BaseModel):
    name: Optional[str] = None
    group_id: Optional[str] = None
    enabled: Optional[bool] = None
    verify_ssl: Optional[bool] = None
    #: Partial update: keys that are *absent* keep their existing value;
    #: keys with empty-string value keep their existing secret (for the
    #: "leave password blank = keep current" UX); non-empty values
    #: overwrite.
    fields: Optional[Dict[str, str]] = None


class DeviceTestResult(BaseModel):
    success: bool
    message: str
    latency_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# Helpers — schema, secret extraction, runtime resolution
# ---------------------------------------------------------------------------

#: Fallback set used when no schema is available (e.g. legacy storage_keys).
_FALLBACK_SECRET_KEYS = {"api_key", "secret", "password", "token", "client_secret", "access_token"}


def _storage_key_to_service_id(storage_key: str) -> str:
    """Strip version suffix: ``sangfor_af_v8_0_106`` → ``sangfor_af``."""
    return re.sub(r"_v[\w.]+$", "", storage_key, flags=re.IGNORECASE)


def _device_secret_id(device_id: str, field_key: str) -> str:
    """Canonical secret id for one device's one credential field.

    Pattern: ``device_<device_uuid>_<field_key>``.  This keeps every
    device's secrets isolated and avoids any collision with the
    ``{provider_id}_api_key`` convention used by old ``api_services``.
    """
    return f"device_{device_id}_{field_key}"


def _secret_keys_for(storage_key: str) -> set[str]:
    """Return the set of credential field keys that must be persisted to
    ``.secret.json`` (NOT to the SQL ``fields`` column) for the given
    plugin's ``_provider.yaml`` schema.

    Falls back to a hard-coded sensitive-name list when the schema is
    unavailable (legacy installs / not-yet-installed plugins).
    """
    try:
        from flocks.server.routes.provider import (
            _build_api_service_credential_schema,
            _load_api_service_metadata_data,
        )
        meta = _load_api_service_metadata_data(storage_key) or {}
        schema = _build_api_service_credential_schema(storage_key, meta)
        return {f.key for f in schema if f.storage == "secret"}
    except Exception:
        return set(_FALLBACK_SECRET_KEYS)


def _persist_fields(
    device_id: str,
    storage_key: str,
    incoming: Dict[str, str],
    prior_db_fields: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Persist credentials, returning the JSON-serialisable dict for the
    DB ``fields`` column.

    Behaviour:

    * **Sensitive** field (per ``_provider.yaml`` schema):

      - empty/missing value → keep existing secret (placeholder
        preserved from ``prior_db_fields``);
      - non-empty value → write to SecretManager, store a
        ``{secret:device_<id>_<key>}`` placeholder in the DB.

    * **Non-sensitive** field: stored as plaintext in the DB.

    The "leave blank = keep current" rule is the standard UX for secret
    fields and prevents accidental wipe-outs on PATCH-style updates.
    """
    from flocks.security import get_secret_manager

    secret_keys = _secret_keys_for(storage_key)
    prior = prior_db_fields or {}
    secrets = get_secret_manager()
    out: Dict[str, str] = {}

    # First, carry over all fields from `prior` (so unspecified keys
    # remain untouched on partial updates).
    out.update(prior)

    for key, value in (incoming or {}).items():
        if key in secret_keys:
            # Sensitive field
            if not value or value.strip() == "":
                # Keep existing — out[key] already holds the prior
                # placeholder if any; otherwise leave it absent.
                continue
            secret_id = _device_secret_id(device_id, key)
            try:
                secrets.set(secret_id, value)
            except Exception as exc:
                log.warn("device.secret.set_error", {"secret_id": secret_id, "error": str(exc)})
                continue
            out[key] = f"{{secret:{secret_id}}}"
        else:
            # Non-sensitive field — store plaintext.
            out[key] = value

    return out


def _delete_associated_secrets(device_id: str, db_fields: Dict[str, str]) -> None:
    """Remove every ``.secret.json`` entry referenced by this device's
    ``{secret:device_<id>_<field>}`` placeholders.  Idempotent.
    """
    from flocks.security import get_secret_manager

    secrets = get_secret_manager()
    prefix = f"device_{device_id}_"
    for raw in db_fields.values():
        if isinstance(raw, str) and raw.startswith("{secret:") and raw.endswith("}"):
            secret_id = raw[len("{secret:"):-1]
            if secret_id.startswith(prefix):
                try:
                    secrets.delete(secret_id)
                except Exception as exc:
                    log.warn("device.secret.delete_error", {"secret_id": secret_id, "error": str(exc)})


def _mask_for_display(db_fields: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, bool]]:
    """Return ``(display_fields, fields_set)`` for the frontend.

    * Sensitive placeholders → resolved → masked (e.g. ``sk-***abc``)
    * Plaintext fields → returned as-is
    * ``fields_set[key]`` indicates whether a value is configured
    """
    from flocks.security import get_secret_manager
    from flocks.security.secrets import SecretManager as _SM

    secrets = get_secret_manager()
    display: Dict[str, str] = {}
    has_value: Dict[str, bool] = {}

    for key, raw in db_fields.items():
        if isinstance(raw, str) and raw.startswith("{secret:") and raw.endswith("}"):
            secret_id = raw[len("{secret:"):-1]
            real = secrets.get(secret_id) or ""
            display[key] = _SM.mask(real) if real else ""
            has_value[key] = bool(real)
        else:
            display[key] = raw if isinstance(raw, str) else ""
            has_value[key] = bool(display[key])
    return display, has_value


def _resolve_for_runtime(db_fields: Dict[str, str]) -> Dict[str, str]:
    """Resolve placeholders to plaintext for actual API calls.

    Use ONLY at the call site immediately before invoking the
    downstream API — never store or return the result.
    """
    from flocks.security import get_secret_manager

    secrets = get_secret_manager()
    out: Dict[str, str] = {}
    for key, raw in db_fields.items():
        if isinstance(raw, str) and raw.startswith("{secret:") and raw.endswith("}"):
            secret_id = raw[len("{secret:"):-1]
            out[key] = secrets.get(secret_id) or ""
        else:
            out[key] = raw if isinstance(raw, str) else ""
    return out


def _row_to_device(row: aiosqlite.Row) -> DeviceIntegration:
    raw_fields: Dict[str, str] = json.loads(row["fields"] or "{}")
    display, has_value = _mask_for_display(raw_fields)
    return DeviceIntegration(
        id=row["id"],
        group_id=row["group_id"] or DEFAULT_GROUP_ID,
        name=row["name"],
        storage_key=row["storage_key"],
        service_id=row["service_id"],
        enabled=bool(row["enabled"]),
        verify_ssl=bool(row["verify_ssl"]),
        fields=display,
        fields_set=has_value,
        status=row["status"] or "unknown",
        message=row["message"],
        latency_ms=row["latency_ms"],
        checked_at=row["checked_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_group(row: aiosqlite.Row) -> DeviceGroup:
    return DeviceGroup(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        sort_order=row["sort_order"] or 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _fetch_device_raw(device_id: str) -> Optional[aiosqlite.Row]:
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM device_integrations WHERE id = ?", (device_id,)
        ) as cur:
            return await cur.fetchone()


async def _group_exists(group_id: str) -> bool:
    async with Storage.connect(Storage.get_db_path()) as db:
        async with db.execute(
            "SELECT 1 FROM device_groups WHERE id = ?", (group_id,)
        ) as cur:
            return (await cur.fetchone()) is not None


async def ensure_default_group() -> None:
    """Create the default room on first run. Idempotent.

    Called from the server startup hook.  Subsequent renames done by
    the user via PATCH are preserved (we only insert if missing).
    """
    async with Storage.connect(Storage.get_db_path()) as db:
        async with db.execute(
            "SELECT 1 FROM device_groups WHERE id = ?", (DEFAULT_GROUP_ID,)
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            return
        now = int(time.time() * 1000)
        await db.execute(
            """
            INSERT INTO device_groups (id, name, description, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME, "默认机房，可重命名", now, now),
        )
        await db.commit()
    log.info("device.default_group.created", {"id": DEFAULT_GROUP_ID, "name": DEFAULT_GROUP_NAME})


# ---------------------------------------------------------------------------
# Public runtime helper — used by future Agent tools / provider.py
# ---------------------------------------------------------------------------

async def get_device_credentials(device_id: str) -> Optional[Dict[str, Any]]:
    """Public helper for downstream callers.

    Returns a dict with ``fields`` resolved to plaintext, plus metadata.
    Returns ``None`` if device not found or disabled.
    """
    row = await _fetch_device_raw(device_id)
    if row is None:
        return None
    if not bool(row["enabled"]):
        return None
    raw_fields: Dict[str, str] = json.loads(row["fields"] or "{}")
    return {
        "id": row["id"],
        "name": row["name"],
        "storage_key": row["storage_key"],
        "service_id": row["service_id"],
        "verify_ssl": bool(row["verify_ssl"]),
        "fields": _resolve_for_runtime(raw_fields),
    }


# ===========================================================================
# Group routes
# ===========================================================================

@router.get("/groups", response_model=List[DeviceGroup])
async def list_groups():
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM device_groups ORDER BY sort_order ASC, created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_group(r) for r in rows]


@router.post("/groups", response_model=DeviceGroup, status_code=status.HTTP_201_CREATED)
async def create_group(body: DeviceGroupCreate):
    if not MULTI_GROUP_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="多机房管理尚未启用。当前版本仅支持单一机房（默认机房，可重命名）。",
        )
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    now = int(time.time() * 1000)
    group_id = str(uuid.uuid4())
    try:
        async with Storage.connect(Storage.get_db_path()) as db:
            await db.execute(
                """
                INSERT INTO device_groups (id, name, description, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (group_id, body.name.strip(), body.description, body.sort_order, now, now),
            )
            await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail=f"机房名称 '{body.name}' 已存在")
    return await get_group(group_id)


@router.get("/groups/{group_id}", response_model=DeviceGroup)
async def get_group(group_id: str):
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM device_groups WHERE id = ?", (group_id,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return _row_to_group(row)


@router.patch("/groups/{group_id}", response_model=DeviceGroup)
async def update_group(group_id: str, body: DeviceGroupUpdate):
    """Rename the default room (or any room when multi-group is enabled)."""
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM device_groups WHERE id = ?", (group_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Group not found")
        current = _row_to_group(row)
        new_name = (body.name.strip() if body.name is not None else current.name) or current.name
        new_desc = body.description if body.description is not None else current.description
        new_sort = body.sort_order if body.sort_order is not None else current.sort_order
        now = int(time.time() * 1000)
        try:
            await db.execute(
                """
                UPDATE device_groups
                SET name=?, description=?, sort_order=?, updated_at=?
                WHERE id=?
                """,
                (new_name, new_desc, new_sort, now, group_id),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise HTTPException(status_code=409, detail=f"机房名称 '{new_name}' 已存在")
    return await get_group(group_id)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: str):
    if not MULTI_GROUP_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="多机房管理尚未启用，无法删除机房。",
        )
    if group_id == DEFAULT_GROUP_ID:
        raise HTTPException(status_code=400, detail="不能删除默认机房")
    async with Storage.connect(Storage.get_db_path()) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM device_integrations WHERE group_id = ?", (group_id,)
        ) as cur:
            row = await cur.fetchone()
        device_count = row[0] if row else 0
        if device_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"机房中还有 {device_count} 台设备，请先转移或删除后再尝试",
            )
        await db.execute("DELETE FROM device_groups WHERE id = ?", (group_id,))
        await db.commit()


# ===========================================================================
# Device routes
# ===========================================================================

@router.get("", response_model=List[DeviceIntegration])
async def list_devices(group_id: Optional[str] = None):
    """List devices. Optionally filter by ``?group_id=``."""
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        if group_id:
            cur = await db.execute(
                "SELECT * FROM device_integrations WHERE group_id = ? ORDER BY created_at DESC",
                (group_id,),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM device_integrations ORDER BY created_at DESC"
            )
        rows = await cur.fetchall()
        await cur.close()
    return [_row_to_device(r) for r in rows]


@router.get("/{device_id}", response_model=DeviceIntegration)
async def get_device(device_id: str):
    row = await _fetch_device_raw(device_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return _row_to_device(row)


@router.post("", response_model=DeviceIntegration, status_code=status.HTTP_201_CREATED)
async def create_device(body: DeviceIntegrationCreate):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if not body.storage_key.strip():
        raise HTTPException(status_code=400, detail="storage_key is required")

    group_id = body.group_id or DEFAULT_GROUP_ID
    if not MULTI_GROUP_ENABLED:
        # Lock to the default room regardless of what the client sent.
        group_id = DEFAULT_GROUP_ID
    if not await _group_exists(group_id):
        raise HTTPException(status_code=400, detail=f"Group '{group_id}' does not exist")

    service_id = (body.service_id or "").strip() or _storage_key_to_service_id(body.storage_key)
    device_id = str(uuid.uuid4())
    db_fields = _persist_fields(device_id, body.storage_key, body.fields, prior_db_fields=None)

    now = int(time.time() * 1000)
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            """
            INSERT INTO device_integrations
                (id, group_id, name, storage_key, service_id, enabled, verify_ssl,
                 fields, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?)
            """,
            (
                device_id,
                group_id,
                body.name.strip(),
                body.storage_key,
                service_id,
                int(body.enabled),
                int(body.verify_ssl),
                json.dumps(db_fields),
                now,
                now,
            ),
        )
        await db.commit()
    return await get_device(device_id)


@router.put("/{device_id}", response_model=DeviceIntegration)
async def update_device(device_id: str, body: DeviceIntegrationUpdate):
    row = await _fetch_device_raw(device_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    prior_fields: Dict[str, str] = json.loads(row["fields"] or "{}")
    storage_key = row["storage_key"]

    new_name = body.name.strip() if (body.name is not None and body.name.strip()) else row["name"]
    new_enabled = body.enabled if body.enabled is not None else bool(row["enabled"])
    new_verify_ssl = body.verify_ssl if body.verify_ssl is not None else bool(row["verify_ssl"])

    new_group_id = row["group_id"]
    if body.group_id is not None:
        if not MULTI_GROUP_ENABLED:
            # Ignore group changes when multi-group is disabled, but do
            # not error — the client may legitimately echo group_id back.
            new_group_id = row["group_id"] or DEFAULT_GROUP_ID
        elif body.group_id != row["group_id"]:
            if not await _group_exists(body.group_id):
                raise HTTPException(status_code=400, detail=f"Group '{body.group_id}' does not exist")
            new_group_id = body.group_id

    if body.fields is not None:
        new_fields = _persist_fields(device_id, storage_key, body.fields, prior_db_fields=prior_fields)
    else:
        new_fields = prior_fields

    now = int(time.time() * 1000)
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            """
            UPDATE device_integrations
            SET name=?, group_id=?, enabled=?, verify_ssl=?, fields=?, updated_at=?
            WHERE id=?
            """,
            (new_name, new_group_id, int(new_enabled), int(new_verify_ssl),
             json.dumps(new_fields), now, device_id),
        )
        await db.commit()
    return await get_device(device_id)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(device_id: str):
    row = await _fetch_device_raw(device_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    db_fields: Dict[str, str] = json.loads(row["fields"] or "{}")
    _delete_associated_secrets(device_id, db_fields)
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute("DELETE FROM device_integrations WHERE id = ?", (device_id,))
        await db.commit()


@router.post("/{device_id}/test", response_model=DeviceTestResult)
async def test_device(device_id: str):
    """Test connectivity for a device integration instance.

    Currently does a simple ``GET`` on ``base_url``; HTTP 4xx is treated
    as "host reachable", 5xx / connect error / timeout as failure.
    """
    import httpx

    row = await _fetch_device_raw(device_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    db_fields: Dict[str, str] = json.loads(row["fields"] or "{}")
    runtime = _resolve_for_runtime(db_fields)
    base_url = (runtime.get("base_url") or "").strip()
    verify_ssl = bool(row["verify_ssl"])

    if not base_url:
        return DeviceTestResult(success=False, message="未配置设备地址（base_url），请先填写")

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(verify=verify_ssl, timeout=10.0) as client:
            resp = await client.get(base_url)
        latency_ms = int((time.monotonic() - start) * 1000)
        success = resp.status_code < 500
        msg = f"HTTP {resp.status_code}，延迟 {latency_ms}ms"
        result = DeviceTestResult(success=success, message=msg, latency_ms=latency_ms)
    except httpx.ConnectError:
        latency_ms = int((time.monotonic() - start) * 1000)
        result = DeviceTestResult(
            success=False,
            message=f"无法连接到 {base_url}，请检查地址是否正确",
            latency_ms=latency_ms,
        )
    except httpx.TimeoutException:
        latency_ms = int((time.monotonic() - start) * 1000)
        result = DeviceTestResult(
            success=False, message="连接超时（10s），请检查网络或设备地址", latency_ms=latency_ms,
        )
    except Exception as e:
        result = DeviceTestResult(success=False, message=f"测试失败：{e}")

    now = int(time.time() * 1000)
    status_val = "ok" if result.success else "error"
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            "UPDATE device_integrations SET status=?, message=?, latency_ms=?, checked_at=?, updated_at=? WHERE id=?",
            (status_val, result.message, result.latency_ms, now, now, device_id),
        )
        await db.commit()
    return result


# ===========================================================================
# Migration: flocks.json → device_integrations table
# ===========================================================================

async def migrate_device_integrations_from_config() -> None:
    """One-time, idempotent migration from ``flocks.json`` to SQL.

    For every ``api_services`` entry in ``flocks.json`` that

      * has ``integration_type: device`` declared in its ``_provider.yaml``;
      * has at least one credential field actually configured;
      * has NOT been migrated before (per-storage_key marker in the
        ``storage`` table key ``device.migration.done``);

    create a corresponding row in ``device_integrations``, assigning it
    to the default room and isolating secrets to ``.secret.json`` via
    :func:`_persist_fields`.

    Safe to call on every startup: it does nothing once each
    storage_key has been processed once.  The marker ensures a user
    who later deletes the migrated device on the UI is not forced to
    re-import on the next restart.
    """
    try:
        from flocks.config.config_writer import ConfigWriter
        from flocks.security import get_secret_manager
        from flocks.server.routes.provider import (
            _load_api_service_metadata_data,
            _build_api_service_credential_schema,
            _get_api_service_secret_candidates,
        )
    except Exception as exc:
        log.warn("device.migrate.import_error", {"error": str(exc)})
        return

    raw_services: Dict[str, Any] = ConfigWriter.list_api_services_raw()
    if not raw_services:
        return

    marker = await Storage.get("device.migration.done") or {}
    if not isinstance(marker, dict):
        marker = {}

    secrets = get_secret_manager()
    new_marker = dict(marker)

    for storage_key, raw_cfg in raw_services.items():
        if not isinstance(raw_cfg, dict):
            continue
        try:
            meta = _load_api_service_metadata_data(storage_key) or {}
        except Exception:
            continue
        if meta.get("integration_type") != "device":
            continue
        if marker.get(storage_key):
            log.info("device.migrate.skip_already_done", {"storage_key": storage_key})
            continue

        schema = _build_api_service_credential_schema(storage_key, meta)
        plain_values: Dict[str, str] = {}

        for field in schema:
            if field.storage == "config":
                for candidate_key in (
                    field.config_key,
                    field.key,
                    "baseUrl" if field.key == "base_url" else None,
                ):
                    if candidate_key and isinstance(raw_cfg.get(candidate_key), str) and raw_cfg[candidate_key]:
                        plain_values[field.key] = raw_cfg[candidate_key]
                        break
            else:
                for secret_id in _get_api_service_secret_candidates(
                    storage_key, raw_cfg, field_name=field.key
                ):
                    val = secrets.get(secret_id)
                    if val:
                        plain_values[field.key] = val
                        break

        # Legacy fallbacks for tools that don't declare a schema.
        for legacy_key, field_name in [
            ("base_url", "base_url"),
            ("baseUrl", "base_url"),
            ("username", "username"),
        ]:
            if field_name not in plain_values and isinstance(raw_cfg.get(legacy_key), str) and raw_cfg[legacy_key]:
                plain_values[field_name] = raw_cfg[legacy_key]

        if not any(v for v in plain_values.values()):
            new_marker[storage_key] = int(time.time() * 1000)
            log.info("device.migrate.skip_empty", {"storage_key": storage_key})
            continue

        service_name = meta.get("name") or storage_key.replace("_", " ").title()
        default_name = f"{service_name}（迁移）"
        enabled = bool(raw_cfg.get("enabled", False))
        verify_ssl = bool(raw_cfg.get("verify_ssl") or raw_cfg.get("verifySsl") or False)
        service_id = _storage_key_to_service_id(storage_key)
        now = int(time.time() * 1000)
        device_id = str(uuid.uuid4())

        # Re-extract secrets into device-scoped IDs (so the migrated rows
        # use the new ``device_<uuid>_<field>`` namespace and we don't
        # share secret IDs with the legacy api_services entries).
        db_fields = _persist_fields(device_id, storage_key, plain_values, prior_db_fields=None)

        try:
            async with Storage.connect(Storage.get_db_path()) as db:
                await db.execute(
                    """
                    INSERT INTO device_integrations
                        (id, group_id, name, storage_key, service_id, enabled, verify_ssl,
                         fields, status, message, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unknown', '从 flocks.json 自动迁移', ?, ?)
                    """,
                    (
                        device_id,
                        DEFAULT_GROUP_ID,
                        default_name,
                        storage_key,
                        service_id,
                        int(enabled),
                        int(verify_ssl),
                        json.dumps(db_fields),
                        now,
                        now,
                    ),
                )
                await db.commit()
            new_marker[storage_key] = now
            log.info("device.migrate.created", {
                "storage_key": storage_key, "id": device_id, "name": default_name,
            })
        except Exception as exc:
            log.warn("device.migrate.insert_error", {"storage_key": storage_key, "error": str(exc)})

    if new_marker != marker:
        try:
            await Storage.set("device.migration.done", new_marker)
        except Exception as exc:
            log.warn("device.migrate.marker_save_error", {"error": str(exc)})


# ===========================================================================
# Startup hook
# ===========================================================================

async def device_startup() -> None:
    """Single entry point invoked from server lifespan.

    Order matters:

    1. Ensure the default group exists (FK target for device rows).
    2. Migrate legacy ``flocks.json`` entries into the SQL table.
    """
    await ensure_default_group()
    await migrate_device_integrations_from_config()
