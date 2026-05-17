"""Device Integration HTTP routes.

Thin HTTP layer only: parse requests, delegate to ``flocks.tool.device``,
return responses. No business logic or SQL here.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import List, Optional

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException, status as http_status

from flocks.tool.device import (
    DEFAULT_GROUP_ID,
    MULTI_GROUP_ENABLED,
    DeviceGroup,
    DeviceGroupCreate,
    DeviceGroupUpdate,
    DeviceIntegration,
    DeviceIntegrationCreate,
    DeviceIntegrationUpdate,
    DeviceTestResult,
)
from flocks.tool.device.secrets import delete_secrets, persist_fields, resolve_for_runtime
from flocks.tool.device.store import (
    create_group,
    delete_group,
    fetch_device,
    get_group,
    group_exists,
    list_devices,
    list_groups,
    row_to_device,
    storage_key_to_service_id,
    update_group,
)
from flocks.tool.device.sync import sync_service_tool_state
from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="device.routes")
router = APIRouter()


# ===========================================================================
# Group routes
# ===========================================================================

@router.get("/groups", response_model=List[DeviceGroup])
async def route_list_groups():
    return await list_groups()


@router.post("/groups", response_model=DeviceGroup, status_code=http_status.HTTP_201_CREATED)
async def route_create_group(body: DeviceGroupCreate):
    if not MULTI_GROUP_ENABLED:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="多机房管理尚未启用。当前版本仅支持单一机房（默认机房，可重命名）。",
        )
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    try:
        return await create_group(body.name.strip(), body.description, body.sort_order)
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail=f"机房名称 '{body.name}' 已存在")


@router.get("/groups/{group_id}", response_model=DeviceGroup)
async def route_get_group(group_id: str):
    group = await get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.patch("/groups/{group_id}", response_model=DeviceGroup)
async def route_update_group(group_id: str, body: DeviceGroupUpdate):
    """Rename the default room (or any room when multi-group is enabled)."""
    try:
        result = await update_group(group_id, body.name, body.description, body.sort_order)
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail=f"机房名称 '{body.name}' 已存在")
    if result is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return result


@router.delete("/groups/{group_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def route_delete_group(group_id: str):
    if not MULTI_GROUP_ENABLED:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="多机房管理尚未启用，无法删除机房。",
        )
    if group_id == DEFAULT_GROUP_ID:
        raise HTTPException(status_code=400, detail="不能删除默认机房")
    device_count = await delete_group(group_id)
    if device_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"机房中还有 {device_count} 台设备，请先转移或删除后再尝试",
        )


# ===========================================================================
# Device routes
# ===========================================================================

@router.get("", response_model=List[DeviceIntegration])
async def route_list_devices(group_id: Optional[str] = None):
    return await list_devices(group_id)


@router.get("/{device_id}", response_model=DeviceIntegration)
async def route_get_device(device_id: str):
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
    return row_to_device(row)


@router.post("", response_model=DeviceIntegration, status_code=http_status.HTTP_201_CREATED)
async def route_create_device(body: DeviceIntegrationCreate):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if not body.storage_key.strip():
        raise HTTPException(status_code=400, detail="storage_key is required")

    group_id = DEFAULT_GROUP_ID if not MULTI_GROUP_ENABLED else (body.group_id or DEFAULT_GROUP_ID)
    if not await group_exists(group_id):
        raise HTTPException(status_code=400, detail=f"Group '{group_id}' does not exist")

    service_id = (body.service_id or "").strip() or storage_key_to_service_id(body.storage_key)
    device_id = str(uuid.uuid4())
    db_fields = persist_fields(device_id, body.storage_key, body.fields)

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
                device_id, group_id, body.name.strip(), body.storage_key, service_id,
                int(body.enabled), int(body.verify_ssl), json.dumps(db_fields), now, now,
            ),
        )
        await db.commit()

    result = await route_get_device(device_id)
    await sync_service_tool_state(service_id)
    return result


@router.put("/{device_id}", response_model=DeviceIntegration)
async def route_update_device(device_id: str, body: DeviceIntegrationUpdate):
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")

    prior_fields: dict = json.loads(row["fields"] or "{}")

    new_name = (body.name.strip() if body.name and body.name.strip() else None) or row["name"]
    new_enabled = body.enabled if body.enabled is not None else bool(row["enabled"])
    new_ssl = body.verify_ssl if body.verify_ssl is not None else bool(row["verify_ssl"])

    if body.group_id is not None and MULTI_GROUP_ENABLED and body.group_id != row["group_id"]:
        if not await group_exists(body.group_id):
            raise HTTPException(status_code=400, detail=f"Group '{body.group_id}' does not exist")
        new_group_id = body.group_id
    else:
        new_group_id = row["group_id"] or DEFAULT_GROUP_ID

    new_fields = (
        persist_fields(device_id, row["storage_key"], body.fields, prior_db_fields=prior_fields)
        if body.fields is not None
        else prior_fields
    )

    now = int(time.time() * 1000)
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            """
            UPDATE device_integrations
            SET name=?, group_id=?, enabled=?, verify_ssl=?, fields=?, updated_at=?
            WHERE id=?
            """,
            (new_name, new_group_id, int(new_enabled), int(new_ssl), json.dumps(new_fields), now, device_id),
        )
        await db.commit()

    result = await route_get_device(device_id)
    await sync_service_tool_state(row["service_id"])
    return result


@router.delete("/{device_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def route_delete_device(device_id: str):
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
    db_fields: dict = json.loads(row["fields"] or "{}")
    delete_secrets(device_id, db_fields)
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute("DELETE FROM device_integrations WHERE id = ?", (device_id,))
        await db.commit()
    await sync_service_tool_state(row["service_id"])


@router.post("/{device_id}/test", response_model=DeviceTestResult)
async def route_test_device(device_id: str):
    """Test connectivity by issuing a GET on the device's ``base_url``.

    HTTP 4xx → reachable (success); HTTP 5xx / connect error / timeout → failure.
    """
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")

    db_fields: dict = json.loads(row["fields"] or "{}")
    base_url = (resolve_for_runtime(db_fields).get("base_url") or "").strip()
    if not base_url:
        return DeviceTestResult(success=False, message="未配置设备地址（base_url），请先填写")

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(verify=bool(row["verify_ssl"]), timeout=10.0) as client:
            resp = await client.get(base_url)
        latency_ms = int((time.monotonic() - start) * 1000)
        result = DeviceTestResult(
            success=resp.status_code < 500,
            message=f"HTTP {resp.status_code}，延迟 {latency_ms}ms",
            latency_ms=latency_ms,
        )
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
            success=False,
            message="连接超时（10s），请检查网络或设备地址",
            latency_ms=latency_ms,
        )
    except Exception as exc:
        result = DeviceTestResult(success=False, message=f"测试失败：{exc}")

    now = int(time.time() * 1000)
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            "UPDATE device_integrations SET status=?, message=?, latency_ms=?, checked_at=?, updated_at=? WHERE id=?",
            ("ok" if result.success else "error", result.message, result.latency_ms, now, now, device_id),
        )
        await db.commit()
    return result
