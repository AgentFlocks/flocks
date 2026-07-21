"""Built-in device management tool for Rex.

``device_manage`` is the single system-tool entrypoint for device discovery,
non-secret config updates, and standard connectivity checks. Its
``connectivity_test`` action reuses the existing device test path, so card state
stays consistent with the ``POST /api/devices/{id}/test`` endpoint.
"""
from __future__ import annotations

from typing import Any, Optional

from flocks.tool.device.intake import DeviceNotFoundError, test_device, update_device
from flocks.tool.device.models import DeviceIntegrationUpdate
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log

log = Log.create(service="tool.device.manage_tool")


@ToolRegistry.register_function(
    name="device_manage",
    description=(
        "管理已接入安全设备。action=list 用于列出机房、设备、device_id 和工具集；"
        "action=update 用于写入/更新已有设备实例的非敏感配置字段；"
        "action=connectivity_test 用于测试指定设备连通性并更新设备卡片状态。"
    ),
    description_cn="列出设备、更新非敏感配置或测试设备连通性",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description="操作类型：list 列出设备；update 更新已有设备非敏感配置；connectivity_test 测试设备连通性。",
            required=True,
            enum=["list", "update", "connectivity_test"],
        ),
        ToolParameter(
            name="device_id",
            type=ParameterType.STRING,
            description="目标设备实例 ID。action=update 或 connectivity_test 时必填。",
            required=False,
        ),
        ToolParameter(
            name="fields",
            type=ParameterType.OBJECT,
            description=(
                "要更新的非敏感设备配置字段，例如 {\"base_url\":\"https://device.local\"}。"
                "禁止传入 api_key、secret、password、token、cookie、auth_state 等敏感字段。"
            ),
            required=False,
        ),
        ToolParameter(
            name="verify_ssl",
            type=ParameterType.BOOLEAN,
            description="是否开启 SSL 证书验证。仅 action=update 时使用。",
            required=False,
        ),
    ],
)
async def device_manage(
    ctx: ToolContext,
    action: str,
    device_id: Optional[str] = None,
    fields: Optional[dict[str, Any]] = None,
    verify_ssl: Optional[bool] = None,
) -> ToolResult:
    """List devices, update non-secret config, or run a standard probe."""
    normalized_action = (action or "").strip()
    if normalized_action == "list":
        return await _list_devices()
    if normalized_action == "update":
        return await _update_device_config(ctx, device_id, fields, verify_ssl)
    if normalized_action == "connectivity_test":
        return await _connectivity_test(ctx, device_id)
    return ToolResult(
        success=False,
        error="未知 action，请使用 list、update 或 connectivity_test。",
    )


async def _list_devices() -> ToolResult:
    try:
        from flocks.tool.device.prompt import build_device_manage_list_section

        content = await build_device_manage_list_section()
        if not content:
            return ToolResult(
                success=True,
                output=(
                    "当前没有已接入的安全设备。"
                    "设备不存在时，请提醒用户前往设备接入页面添加设备。"
                ),
            )
        return ToolResult(success=True, output=content)
    except Exception as exc:
        log.warn("tool.device_manage.list_failed", {"error": str(exc)})
        return ToolResult(success=False, error=f"查询设备列表失败: {exc}")


_SENSITIVE_FIELD_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "cookie",
        "auth_state",
    }
)


def _normalize_update_fields(
    fields: Optional[dict[str, Any]],
) -> tuple[dict[str, str], Optional[str]]:
    if fields is None:
        return {}, None
    if not isinstance(fields, dict):
        return {}, "fields 必须是对象，例如 {\"base_url\":\"https://device.local\"}。"

    normalized: dict[str, str] = {}
    rejected: list[str] = []
    for raw_key, raw_value in fields.items():
        key = str(raw_key).strip()
        if not key:
            return {}, "fields 不能包含空字段名。"
        if key.lower() in _SENSITIVE_FIELD_KEYS:
            rejected.append(key)
            continue
        normalized[key] = "" if raw_value is None else str(raw_value)

    if rejected:
        return {}, (
            "拒绝通过 device_manage(action='update') 写入敏感字段："
            + ", ".join(f"`{key}`" for key in sorted(rejected))
            + "。请在设备接入页面的配置表单中填写密钥、密码、Token、Cookie 或 auth_state。"
        )

    return normalized, None


async def _update_device_config(
    ctx: ToolContext,
    device_id: Optional[str],
    fields: Optional[dict[str, Any]],
    verify_ssl: Optional[bool],
) -> ToolResult:
    target = (device_id or "").strip()
    if not target:
        return ToolResult(
            success=False,
            error="action=update 时 device_id 不能为空。",
        )

    normalized_fields, field_error = _normalize_update_fields(fields)
    if field_error:
        return ToolResult(success=False, error=field_error)
    if not normalized_fields and verify_ssl is None:
        return ToolResult(
            success=False,
            error="action=update 至少需要提供 fields 或 verify_ssl。",
        )

    log.info(
        "tool.device_manage.update.start",
        {
            "device_id": target,
            "session_id": ctx.session_id,
            "fields": sorted(normalized_fields),
            "verify_ssl": verify_ssl,
        },
    )

    try:
        updated = await update_device(
            target,
            DeviceIntegrationUpdate(
                fields=normalized_fields or None,
                verify_ssl=verify_ssl,
            ),
        )
    except DeviceNotFoundError:
        return ToolResult(
            success=False,
            error=f"设备 {target!r} 未找到，请通过 device_manage(action='list') 确认 device_id。",
        )
    except ValueError as exc:
        return ToolResult(success=False, error=f"设备配置更新失败: {exc}")
    except Exception as exc:
        log.warn(
            "tool.device_manage.update_failed",
            {"device_id": target, "error": str(exc)},
        )
        return ToolResult(success=False, error=f"设备配置更新失败: {exc}")

    return ToolResult(
        success=True,
        output={
            "device_id": updated.id,
            "name": updated.name,
            "storage_key": updated.storage_key,
            "service_id": updated.service_id,
            "enabled": updated.enabled,
            "verify_ssl": updated.verify_ssl,
            "fields": updated.fields,
            "fields_set": updated.fields_set,
            "updated_fields": sorted(normalized_fields),
        },
        metadata={
            "device_id": updated.id,
            "updated_fields": sorted(normalized_fields),
            "verify_ssl": updated.verify_ssl,
        },
        title="设备配置已更新",
    )


async def _connectivity_test(ctx: ToolContext, device_id: Optional[str]) -> ToolResult:
    target = (device_id or "").strip()
    if not target:
        return ToolResult(
            success=False,
            error="action=connectivity_test 时 device_id 不能为空。",
        )

    log.info(
        "tool.device_manage.connectivity_test.start",
        {"device_id": target, "session_id": ctx.session_id},
    )

    try:
        result = await test_device(target)
    except DeviceNotFoundError:
        return ToolResult(
            success=False,
            error=f"设备 {target!r} 未找到，请通过 device_manage(action='list') 确认 device_id。",
        )
    except Exception as exc:
        log.warn(
            "tool.device_manage.connectivity_test_failed",
            {"device_id": target, "error": str(exc)},
        )
        return ToolResult(success=False, error=f"设备连通性检测失败: {exc}")

    status = "ok" if result.success else "error"
    return ToolResult(
        success=True,
        output={
            "device_id": target,
            "connected": result.success,
            "status": status,
            "message": result.message,
            "latency_ms": result.latency_ms,
        },
        metadata={
            "device_id": target,
            "status": status,
            "latency_ms": result.latency_ms,
            "card_status_updated": True,
        },
        title="设备连通性检测完成",
    )
