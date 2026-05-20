"""Database access for device_groups and device_integrations.

All SQL lives here. Route handlers and migration logic call these helpers
instead of opening connections themselves, keeping the HTTP layer thin and
the data layer the single source of truth.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import aiosqlite

from flocks.storage.storage import Storage
from flocks.utils.log import Log

from .models import (
    DEFAULT_GROUP_ID,
    DEFAULT_GROUP_NAME,
    DeviceGroup,
    DeviceIntegration,
)
from .secrets import mask_for_display, resolve_for_runtime

log = Log.create(service="tool.device.store")

# ---------------------------------------------------------------------------
# Device revision counter – incremented on every write so callers (e.g. the
# session runner's system-prompt cache) know when to rebuild device context.
# ---------------------------------------------------------------------------
_revision_lock = threading.Lock()
_device_revision: int = 0


def device_revision() -> int:
    """Return the current device revision (monotonically increasing integer)."""
    return _device_revision


def _bump_revision() -> None:
    global _device_revision
    with _revision_lock:
        _device_revision += 1


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def storage_key_to_service_id(storage_key: str) -> str:
    """Strip the version suffix: ``sangfor_af_v8_0_106`` → ``sangfor_af``."""
    return re.sub(r"_v[\w.]+$", "", storage_key, flags=re.IGNORECASE)


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Row → model converters
# ---------------------------------------------------------------------------

def row_to_device(row: aiosqlite.Row) -> DeviceIntegration:
    raw_fields: Dict[str, str] = json.loads(row["fields"] or "{}")
    display, has_value = mask_for_display(raw_fields)
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


def row_to_group(row: aiosqlite.Row) -> DeviceGroup:
    return DeviceGroup(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        sort_order=row["sort_order"] or 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Group operations
# ---------------------------------------------------------------------------

async def list_groups() -> List[DeviceGroup]:
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM device_groups ORDER BY sort_order ASC, created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
    return [row_to_group(r) for r in rows]


async def get_group(group_id: str) -> Optional[DeviceGroup]:
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM device_groups WHERE id = ?", (group_id,)
        ) as cur:
            row = await cur.fetchone()
    return row_to_group(row) if row else None


async def group_exists(group_id: str) -> bool:
    async with Storage.connect(Storage.get_db_path()) as db:
        async with db.execute(
            "SELECT 1 FROM device_groups WHERE id = ?", (group_id,)
        ) as cur:
            return (await cur.fetchone()) is not None


async def create_group(name: str, description: Optional[str], sort_order: int) -> DeviceGroup:
    group_id = str(uuid.uuid4())
    now = _now_ms()
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO device_groups (id, name, description, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (group_id, name, description, sort_order, now, now),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM device_groups WHERE id = ?", (group_id,)
        ) as cur:
            row = await cur.fetchone()
    _bump_revision()
    return row_to_group(row)


async def update_group(
    group_id: str,
    name: Optional[str],
    description: Optional[str],
    sort_order: Optional[int],
) -> Optional[DeviceGroup]:
    current = await get_group(group_id)
    if current is None:
        return None
    new_name = (name.strip() if name else "") or current.name
    new_desc = description if description is not None else current.description
    new_sort = sort_order if sort_order is not None else current.sort_order
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            "UPDATE device_groups SET name=?, description=?, sort_order=?, updated_at=? WHERE id=?",
            (new_name, new_desc, new_sort, _now_ms(), group_id),
        )
        await db.commit()
    _bump_revision()
    return await get_group(group_id)


async def delete_group(group_id: str) -> int:
    """Delete a group; return the number of devices that prevented deletion (0 = deleted)."""
    async with Storage.connect(Storage.get_db_path()) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM device_integrations WHERE group_id = ?", (group_id,)
        ) as cur:
            row = await cur.fetchone()
        device_count: int = row[0] if row else 0
        if device_count == 0:
            await db.execute("DELETE FROM device_groups WHERE id = ?", (group_id,))
            await db.commit()
            _bump_revision()
    return device_count


# ---------------------------------------------------------------------------
# Device read operations
# ---------------------------------------------------------------------------

async def list_devices(group_id: Optional[str] = None) -> List[DeviceIntegration]:
    sql = "SELECT * FROM device_integrations"
    params: tuple = ()
    if group_id:
        sql += " WHERE group_id = ?"
        params = (group_id,)
    sql += " ORDER BY created_at DESC"

    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
    return [row_to_device(r) for r in rows]


async def fetch_device(device_id: str) -> Optional[aiosqlite.Row]:
    """Return the raw DB row (for callers that need the full record incl. fields blob)."""
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM device_integrations WHERE id = ?", (device_id,)
        ) as cur:
            return await cur.fetchone()


# ---------------------------------------------------------------------------
# Device write operations
# ---------------------------------------------------------------------------

async def insert_device(
    *,
    device_id: str,
    group_id: str,
    name: str,
    storage_key: str,
    service_id: str,
    enabled: bool,
    verify_ssl: bool,
    db_fields: Dict[str, str],
    status: str = "unknown",
    message: Optional[str] = None,
) -> None:
    """Insert a new device row. ``device_id`` and ``db_fields`` must already be
    derived by the caller (so secrets can be persisted under their final id).
    """
    now = _now_ms()
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            """
            INSERT INTO device_integrations
                (id, group_id, name, storage_key, service_id, enabled, verify_ssl,
                 fields, status, message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id, group_id, name, storage_key, service_id,
                int(enabled), int(verify_ssl), json.dumps(db_fields),
                status, message, now, now,
            ),
        )
        await db.commit()
    _bump_revision()


async def update_device_row(
    device_id: str,
    *,
    name: str,
    group_id: str,
    enabled: bool,
    verify_ssl: bool,
    db_fields: Dict[str, str],
) -> None:
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            """
            UPDATE device_integrations
            SET name=?, group_id=?, enabled=?, verify_ssl=?, fields=?, updated_at=?
            WHERE id=?
            """,
            (name, group_id, int(enabled), int(verify_ssl),
             json.dumps(db_fields), _now_ms(), device_id),
        )
        await db.commit()
    _bump_revision()


async def delete_device_row(device_id: str) -> None:
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute("DELETE FROM device_integrations WHERE id = ?", (device_id,))
        await db.commit()
    _bump_revision()


async def record_test_result(
    device_id: str,
    *,
    success: bool,
    message: str,
    latency_ms: Optional[int],
) -> None:
    """Persist the outcome of a connectivity test."""
    now = _now_ms()
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            "UPDATE device_integrations SET status=?, message=?, latency_ms=?, checked_at=?, updated_at=? WHERE id=?",
            ("ok" if success else "error", message, latency_ms, now, now, device_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Default group bootstrapping
# ---------------------------------------------------------------------------

async def ensure_default_group() -> None:
    """Create the default room on first run. Idempotent.

    Only inserts if the row is missing; subsequent user renames are preserved.
    """
    async with Storage.connect(Storage.get_db_path()) as db:
        async with db.execute(
            "SELECT 1 FROM device_groups WHERE id = ?", (DEFAULT_GROUP_ID,)
        ) as cur:
            if await cur.fetchone():
                return
        now = _now_ms()
        await db.execute(
            """
            INSERT INTO device_groups (id, name, description, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME, "默认机房，可重命名", now, now),
        )
        await db.commit()
        _bump_revision()
    log.info("tool.device.default_group.created", {"id": DEFAULT_GROUP_ID})


# ---------------------------------------------------------------------------
# Public helper for downstream callers (Agent tools, etc.)
# ---------------------------------------------------------------------------

async def get_device_credentials(device_id: str) -> Optional[Dict[str, Any]]:
    """Return plaintext credentials for *device_id*, or None if not found / disabled.

    The single safe entry-point for code that needs to call a device's API.
    """
    row = await fetch_device(device_id)
    if row is None or not bool(row["enabled"]):
        return None
    raw_fields: Dict[str, str] = json.loads(row["fields"] or "{}")
    return {
        "id": row["id"],
        "name": row["name"],
        "storage_key": row["storage_key"],
        "service_id": row["service_id"],
        "verify_ssl": bool(row["verify_ssl"]),
        "fields": resolve_for_runtime(raw_fields),
    }
