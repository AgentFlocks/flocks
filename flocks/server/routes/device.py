"""Device Integration HTTP routes.

Thin HTTP layer only: parse requests, delegate to ``flocks.tool.device``,
return responses. No business logic or SQL lives here.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import List, Optional

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException, status as http_status
from pydantic import BaseModel, Field

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
    delete_device_row,
    delete_group,
    fetch_device,
    get_group,
    group_exists,
    insert_device,
    list_devices,
    list_groups,
    record_test_result,
    row_to_device,
    storage_key_to_service_id,
    update_device_row,
    update_group,
)
from flocks.tool.device.sync import sync_service_tool_state

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
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        return await create_group(name, body.description, body.sort_order)
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail=f"机房名称 '{name}' 已存在")


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
        attempted = (body.name or "").strip() or "(unchanged)"
        raise HTTPException(status_code=409, detail=f"机房名称 '{attempted}' 已存在")
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
    name = body.name.strip()
    storage_key = body.storage_key.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not storage_key:
        raise HTTPException(status_code=400, detail="storage_key is required")

    group_id = DEFAULT_GROUP_ID if not MULTI_GROUP_ENABLED else (body.group_id or DEFAULT_GROUP_ID)
    if not await group_exists(group_id):
        raise HTTPException(status_code=400, detail=f"Group '{group_id}' does not exist")

    service_id = (body.service_id or "").strip() or storage_key_to_service_id(storage_key)
    device_id = str(uuid.uuid4())
    db_fields = persist_fields(device_id, storage_key, body.fields)

    await insert_device(
        device_id=device_id,
        group_id=group_id,
        name=name,
        storage_key=storage_key,
        service_id=service_id,
        enabled=body.enabled,
        verify_ssl=body.verify_ssl,
        db_fields=db_fields,
    )
    await sync_service_tool_state(service_id)
    return await route_get_device(device_id)


@router.put("/{device_id}", response_model=DeviceIntegration)
async def route_update_device(device_id: str, body: DeviceIntegrationUpdate):
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")

    prior_fields: dict = json.loads(row["fields"] or "{}")

    stripped_name = body.name.strip() if body.name else ""
    new_name = stripped_name or row["name"]
    new_enabled = body.enabled if body.enabled is not None else bool(row["enabled"])
    new_ssl = body.verify_ssl if body.verify_ssl is not None else bool(row["verify_ssl"])

    if body.group_id and MULTI_GROUP_ENABLED and body.group_id != row["group_id"]:
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

    await update_device_row(
        device_id,
        name=new_name,
        group_id=new_group_id,
        enabled=new_enabled,
        verify_ssl=new_ssl,
        db_fields=new_fields,
    )
    # Recompute ``service_id`` from the row's ``storage_key`` instead of
    # trusting the stored column. Rows created before the descriptor-
    # aware ``storage_key_to_service_id`` fix may carry a too-greedy
    # value (e.g. ``onesig`` instead of ``onesig_v2_5_3_D20250710_api``);
    # using the column directly would route this sync to the wrong key
    # bucket and leave ``api_services[storage_key].enabled`` stale,
    # which in turn keeps tools wrongly exposed to the LLM.
    await sync_service_tool_state(storage_key_to_service_id(row["storage_key"]))
    return await route_get_device(device_id)


@router.delete("/{device_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def route_delete_device(device_id: str):
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
    # Capture storage_key BEFORE deletion: once the row is gone the DB query
    # inside sync_service_tool_state can no longer see it, so if this was the
    # last instance for that storage_key the tool would never get disabled.
    storage_key: str = row["storage_key"]
    # Always derive service_id from the live storage_key — see comment in
    # ``route_update_device`` for why we don't trust the stored column.
    service_id: str = storage_key_to_service_id(storage_key)
    db_fields: dict = json.loads(row["fields"] or "{}")

    delete_secrets(device_id, db_fields)
    await delete_device_row(device_id)
    await sync_service_tool_state(service_id, deleted_storage_keys=[storage_key])


class DeviceTestRequest(BaseModel):
    """Optional body for ``POST /devices/{id}/test``.

    All fields are optional. When supplied, they take precedence over the
    persisted device row so the user can validate unsaved edits (e.g. flip
    the SSL toggle in the form and re-test before clicking 保存).
    """
    base_url: Optional[str] = Field(None, description="Override the persisted base_url for this probe only")
    verify_ssl: Optional[bool] = Field(None, description="Override the persisted verify_ssl for this probe only")


@router.post("/{device_id}/test", response_model=DeviceTestResult)
async def route_test_device(device_id: str, body: Optional[DeviceTestRequest] = None):
    """Connectivity test: GET on the device's ``base_url``.

    HTTP 4xx → reachable (success); HTTP 5xx / connect error / timeout → failure.

    Optionally accepts a JSON body with ``base_url`` / ``verify_ssl`` overrides
    so the WebUI can probe with the form's current (unsaved) values.
    """
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")

    db_fields: dict = json.loads(row["fields"] or "{}")
    persisted_base_url = (resolve_for_runtime(db_fields).get("base_url") or "").strip()

    # Form overrides take priority over the DB row so the toggle on screen is
    # what gets used for the probe.
    override_base_url = (body.base_url.strip() if body and body.base_url else "")
    base_url = override_base_url or persisted_base_url
    if not base_url:
        return DeviceTestResult(success=False, message="未配置设备地址（base_url），请先填写")

    if body is not None and body.verify_ssl is not None:
        verify_ssl = bool(body.verify_ssl)
    else:
        verify_ssl = bool(row["verify_ssl"])

    result = await _probe(base_url, verify_ssl=verify_ssl)
    await record_test_result(
        device_id,
        success=result.success,
        message=result.message,
        latency_ms=result.latency_ms,
    )
    return result


async def _probe(base_url: str, *, verify_ssl: bool) -> DeviceTestResult:
    """Single HTTP GET probe; uniformly returns a DeviceTestResult."""
    start = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - start) * 1000)

    try:
        async with httpx.AsyncClient(verify=verify_ssl, timeout=10.0) as client:
            resp = await client.get(base_url)
        ms = elapsed()
        return DeviceTestResult(
            success=resp.status_code < 500,
            message=f"HTTP {resp.status_code}，延迟 {ms}ms",
            latency_ms=ms,
        )
    except httpx.ConnectError:
        return DeviceTestResult(
            success=False,
            message=f"无法连接到 {base_url}，请检查地址是否正确",
            latency_ms=elapsed(),
        )
    except httpx.TimeoutException:
        return DeviceTestResult(
            success=False,
            message="连接超时（10s），请检查网络或设备地址",
            latency_ms=elapsed(),
        )
    except Exception as exc:
        return DeviceTestResult(success=False, message=f"测试失败：{exc}")
