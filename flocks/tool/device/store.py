"""Database access for device_groups and device_integrations.

All DB operations live here. Route handlers call these functions instead
of touching SQL directly, keeping the HTTP layer thin.
"""
from __future__ import annotations

import json
import re
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
# Key derivation
# ---------------------------------------------------------------------------

def storage_key_to_service_id(storage_key: str) -> str:
    """Strip the version suffix: ``sangfor_af_v8_0_106`` → ``sangfor_af``."""
    return re.sub(r"_v[\w.]+$", "", storage_key, flags=re.IGNORECASE)


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
# Group queries
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
    now = int(time.time() * 1000)
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            """
            INSERT INTO device_groups (id, name, description, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (group_id, name, description, sort_order, now, now),
        )
        await db.commit()
    return (await get_group(group_id))  # type: ignore[return-value]


async def update_group(
    group_id: str,
    name: Optional[str],
    description: Optional[str],
    sort_order: Optional[int],
) -> Optional[DeviceGroup]:
    current = await get_group(group_id)
    if current is None:
        return None
    new_name = (name.strip() if name else current.name) or current.name
    new_desc = description if description is not None else current.description
    new_sort = sort_order if sort_order is not None else current.sort_order
    now = int(time.time() * 1000)
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            "UPDATE device_groups SET name=?, description=?, sort_order=?, updated_at=? WHERE id=?",
            (new_name, new_desc, new_sort, now, group_id),
        )
        await db.commit()
    return await get_group(group_id)


async def delete_group(group_id: str) -> int:
    """Delete a group. Returns the number of devices still in it (0 = deleted)."""
    async with Storage.connect(Storage.get_db_path()) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM device_integrations WHERE group_id = ?", (group_id,)
        ) as cur:
            row = await cur.fetchone()
        device_count: int = row[0] if row else 0
        if device_count == 0:
            await db.execute("DELETE FROM device_groups WHERE id = ?", (group_id,))
            await db.commit()
    return device_count


# ---------------------------------------------------------------------------
# Device queries
# ---------------------------------------------------------------------------

async def list_devices(group_id: Optional[str] = None) -> List[DeviceIntegration]:
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
    return [row_to_device(r) for r in rows]


async def fetch_device(device_id: str) -> Optional[aiosqlite.Row]:
    """Return the raw DB row (for route handlers that need the full record)."""
    async with Storage.connect(Storage.get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM device_integrations WHERE id = ?", (device_id,)
        ) as cur:
            return await cur.fetchone()


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
        now = int(time.time() * 1000)
        await db.execute(
            """
            INSERT INTO device_groups (id, name, description, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME, "默认机房，可重命名", now, now),
        )
        await db.commit()
    log.info("device.default_group.created", {"id": DEFAULT_GROUP_ID})


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
