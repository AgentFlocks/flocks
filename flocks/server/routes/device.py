"""Device Integration HTTP routes.

Thin HTTP layer only: parse requests, delegate to ``flocks.tool.device``,
return responses. No business logic or SQL lives here.
"""
from __future__ import annotations

from typing import Any, List, Optional

import aiosqlite
from fastapi import APIRouter, HTTPException, Request, status as http_status
from pydantic import BaseModel, Field

from flocks.audit import emit_audit_event
from flocks.server.auth import get_request_ip, get_request_user_agent, require_user
from flocks.tool.device import (
    DEFAULT_GROUP_ID,
    MULTI_GROUP_ENABLED,
    DeviceGroup,
    DeviceGroupCreate,
    DeviceGroupUpdate,
    DeviceCredentialResponse,
    DeviceIntegration,
    DeviceIntegrationCreate,
    DeviceIntegrationUpdate,
    DeviceTemplate,
    DeviceTestRequest,
    DeviceTestResult,
    CustomDeviceTemplateCreate,
)
from flocks.tool.device.intake import (
    DeviceNotFoundError,
    create_device,
    delete_device,
    ensure_user_device_instances,
    test_device,
    update_device,
)
from flocks.tool.device.plugin_index import (
    create_custom_device_template,
    list_device_templates,
)
from flocks.tool.device.store import (
    create_group,
    delete_device_tool_setting,
    delete_group,
    fetch_device,
    get_group,
    list_device_tool_settings,
    list_devices,
    list_groups,
    row_to_device,
    set_device_tool_enabled,
    update_group,
)

router = APIRouter()


async def _emit_device_audit_fallback(event_type: str, payload: dict[str, Any]) -> None:
    """Persist device audit even when the default sink is still a no-op."""
    try:
        from flocks.audit import NullAuditSink, get_sink

        sink_cls = get_sink()
        if sink_cls is not NullAuditSink:
            return
    except Exception:
        return

    try:
        from flockspro.audit.service import AuditEvent
        from flockspro.audit.sinks import SqliteAuditSink
    except Exception:
        # OSS or flockspro not installed: nothing to persist.
        return

    failed = bool(payload.get("error") or payload.get("reason"))
    event = AuditEvent(
        event_type=event_type,
        category="device",
        action="credentials_reveal",
        status="error" if failed else "ok",
        result="failed" if failed else "success",
        user_id=str(payload.get("user_id")) if payload.get("user_id") else None,
        user_name=str(payload.get("username")) if payload.get("username") else None,
        resource_type="device",
        resource_id=str(payload.get("device_id")) if payload.get("device_id") else None,
        ip=str(payload.get("ip")) if payload.get("ip") else None,
        payload=payload,
        metadata=payload,
    )
    await SqliteAuditSink().write(event)


async def _emit_device_audit(event_type: str, payload: dict[str, Any]) -> None:
    try:
        await emit_audit_event(event_type, payload)
    except Exception:
        # Audit failures must not block credential reveal.
        pass
    try:
        await _emit_device_audit_fallback(event_type, payload)
    except Exception:
        pass


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
async def route_list_devices(group_id: Optional[str] = None, refresh: bool = False):
    await ensure_user_device_instances(refresh_templates=refresh)
    return await list_devices(group_id)


@router.get("/templates", response_model=List[DeviceTemplate])
async def route_list_device_templates(refresh: bool = False):
    return list_device_templates(refresh=refresh)


@router.post(
    "/templates/custom",
    response_model=DeviceTemplate,
    status_code=http_status.HTTP_201_CREATED,
)
async def route_create_custom_device_template(body: CustomDeviceTemplateCreate):
    try:
        return create_custom_device_template(body)
    except FileExistsError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/{device_id}", response_model=DeviceIntegration)
async def route_get_device(device_id: str):
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
    return row_to_device(row)


class DeviceCredentialRevealRequest(BaseModel):
    field: Optional[str] = Field(
        None,
        description="Reveal only this credential key when provided.",
    )


@router.post("/{device_id}/credentials", response_model=DeviceCredentialResponse)
async def route_get_device_credentials(
    device_id: str,
    request: Request,
    body: Optional[DeviceCredentialRevealRequest] = None,
):
    current_user = require_user(request)
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
    db_fields: dict = json.loads(row["fields"] or "{}")
    requested_field = (body.field or "").strip() if body else ""
    resolved_fields = resolve_for_runtime(db_fields)
    if requested_field:
        if requested_field not in resolved_fields:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Credential field '{requested_field}' not found",
            )
        response_fields = {requested_field: resolved_fields[requested_field]}
        revealed_keys = [requested_field]
    else:
        response_fields = resolved_fields
        revealed_keys = sorted(resolved_fields.keys())

    # Record the reveal action without ever logging the plaintext values.
    await _emit_device_audit(
        "device.credentials_reveal",
        {
            "action": "credentials_reveal",
            "actor_id": current_user.id,
            "actor_name": current_user.username,
            "user_id": current_user.id,
            "username": current_user.username,
            "device_id": device_id,
            "storage_key": row["storage_key"],
            "field_keys": revealed_keys,
            "ip": get_request_ip(request),
            "user_agent": get_request_user_agent(request),
        },
    )
    return DeviceCredentialResponse(fields=response_fields)


@router.post("", response_model=DeviceIntegration, status_code=http_status.HTTP_201_CREATED)
async def route_create_device(body: DeviceIntegrationCreate):
    try:
        return await create_device(body)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.put("/{device_id}", response_model=DeviceIntegration)
async def route_update_device(device_id: str, body: DeviceIntegrationUpdate):
    try:
        return await update_device(device_id, body)
    except DeviceNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete("/{device_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def route_delete_device(device_id: str):
    try:
        await delete_device(device_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")


# ===========================================================================
# Per-device tool settings routes
# ===========================================================================

class DeviceToolInfo(BaseModel):
    """Tool information with per-device enabled state."""
    name: str
    description: str
    description_cn: Optional[str] = None
    enabled_global: bool = Field(
        ...,
        description="全局工具开关状态（影响所有同版本设备）",
    )
    enabled_device: Optional[bool] = Field(
        None,
        description=(
            "本设备的工具开关覆盖值。null 表示未设置覆盖，遵从全局状态；"
            "true/false 表示该设备有独立的启用/禁用设置。"
        ),
    )
    enabled_effective: bool = Field(
        ...,
        description="最终生效状态（per-device 覆盖 > 全局 > 出厂默认）",
    )


class DeviceToolUpdateRequest(BaseModel):
    enabled: bool = Field(..., description="启用或禁用此设备上的工具")


@router.get("/{device_id}/tools", response_model=List[DeviceToolInfo])
async def route_list_device_tools(device_id: str):
    """列出设备对应插件的所有工具，并附带该设备的独立开关状态。

    返回的 ``enabled_effective`` 字段反映实际执行时的生效状态：
    - 若存在 per-device 覆盖（enabled_device 非 null），以它为准；
    - 否则沿用全局 tool_settings（enabled_global）。
    """
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found"
        )

    from flocks.tool.registry import ToolRegistry

    storage_key: str = row["storage_key"]
    ToolRegistry.init()

    # Collect tools that belong to this device's plugin (matching provider).
    device_tools = [
        t for t in ToolRegistry.list_tools()
        if t.provider == storage_key and t.source == "device"
    ]

    # Read per-device overrides once: {tool_name: enabled_bool}.
    per_device = await list_device_tool_settings(device_id)

    result: List[DeviceToolInfo] = []
    for t in device_tools:
        enabled_device: Optional[bool] = per_device.get(t.name)

        enabled_global = t.enabled
        enabled_effective = (
            enabled_device if enabled_device is not None else enabled_global
        )
        result.append(
            DeviceToolInfo(
                name=t.name,
                description=t.description,
                description_cn=t.description_cn,
                enabled_global=enabled_global,
                enabled_device=enabled_device,
                enabled_effective=enabled_effective,
            )
        )

    return result


@router.patch("/{device_id}/tools/{tool_name}", response_model=DeviceToolInfo)
async def route_update_device_tool(
    device_id: str, tool_name: str, body: DeviceToolUpdateRequest
):
    """设置或清除某工具在指定设备上的独立开关。

    - ``enabled=false`` → 仅在该设备上禁用工具，不影响同版本其他设备；
    - ``enabled=true``  → 移除 per-device 覆盖，恢复遵从全局工具开关。
    """
    row = await fetch_device(device_id)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found"
        )

    from flocks.tool.registry import ToolRegistry

    ToolRegistry.init()
    storage_key: str = row["storage_key"]
    tool = ToolRegistry.get(tool_name)
    if tool is None or tool.info.provider != storage_key:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_name}' does not belong to this device",
        )

    if body.enabled:
        # Removing the override restores global behaviour.
        await delete_device_tool_setting(device_id, tool_name)
        enabled_device = None
    else:
        await set_device_tool_enabled(device_id, tool_name, False)
        enabled_device = False

    enabled_global = tool.info.enabled
    enabled_effective = (
        enabled_device if enabled_device is not None else enabled_global
    )
    return DeviceToolInfo(
        name=tool_name,
        description=tool.info.description,
        description_cn=tool.info.description_cn,
        enabled_global=enabled_global,
        enabled_device=enabled_device,
        enabled_effective=enabled_effective,
    )


@router.post("/{device_id}/test", response_model=DeviceTestResult)
async def route_test_device(device_id: str, body: Optional[DeviceTestRequest] = None):
    try:
        return await test_device(device_id, body)
    except DeviceNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
