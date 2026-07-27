"""Built-in device management tool for Rex.

``device_manage`` is the single system-tool entrypoint for device and template
discovery, non-secret config updates, and standard connectivity checks. Its
``connectivity_test`` action reuses the existing device test path, so card state
stays consistent with the ``POST /api/devices/{id}/test`` endpoint.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from flocks.tool.device.intake import (
    DeviceNotFoundError,
    create_device,
    test_device,
    update_device,
)
from flocks.tool.device.models import (
    DeviceIntegrationCreate,
    DeviceIntegrationUpdate,
)
from flocks.tool.device.store import fetch_device
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
        "action=list_templates 用于列出已有设备模板、安装状态和配置字段；"
        "action=create 用于从已安装模板创建设备实例，仅接受非敏感配置；"
        "action=update 用于启停设备或更新模板声明的配置字段；"
        "action=connectivity_test 用于测试指定设备连通性并更新设备卡片状态。"
    ),
    description_cn="列出设备、按模板更新设备或测试设备连通性",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description=(
                "操作类型：list 列出设备实例；list_templates 列出已有设备模板；"
                "create 从已安装模板创建设备实例；update 启停设备或更新模板字段；"
                "connectivity_test 测试设备连通性。"
            ),
            required=True,
            enum=[
                "list",
                "list_templates",
                "create",
                "update",
                "connectivity_test",
            ],
        ),
        ToolParameter(
            name="storage_key",
            type=ParameterType.STRING,
            description="已安装设备模板的 storage_key。action=create 时必填。",
            required=False,
        ),
        ToolParameter(
            name="device_name",
            type=ParameterType.STRING,
            description="新设备实例名称。action=create 时必填。",
            required=False,
        ),
        ToolParameter(
            name="group_id",
            type=ParameterType.STRING,
            description="目标机房 ID。action=create 时可选，默认使用默认机房。",
            required=False,
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
                "要创建或更新的模板配置字段，例如 "
                "{\"base_url\":\"https://device.local\"}。"
                "禁止传入 api_key、secret、password、token、cookie 等敏感字段。"
            ),
            required=False,
        ),
        ToolParameter(
            name="verify_ssl",
            type=ParameterType.BOOLEAN,
            description="是否开启 SSL 证书验证。action=create 或 update 时使用。",
            required=False,
        ),
        ToolParameter(
            name="enabled",
            type=ParameterType.BOOLEAN,
            description="是否启用设备。仅 action=update 时使用。",
            required=False,
        ),
    ],
)
async def device_manage(
    ctx: ToolContext,
    action: str,
    storage_key: Optional[str] = None,
    device_name: Optional[str] = None,
    group_id: Optional[str] = None,
    device_id: Optional[str] = None,
    fields: Optional[dict[str, Any]] = None,
    verify_ssl: Optional[bool] = None,
    enabled: Optional[bool] = None,
) -> ToolResult:
    """List devices/templates, update non-secret config, or run a probe."""
    normalized_action = (action or "").strip()
    if normalized_action == "list":
        return await _list_devices()
    if normalized_action == "list_templates":
        return await _list_device_templates()
    if normalized_action == "create":
        return await _create_device_from_template(
            storage_key,
            device_name,
            group_id,
            fields,
            verify_ssl,
        )
    if normalized_action == "update":
        return await _update_device_config(
            ctx,
            device_id,
            fields,
            verify_ssl,
            enabled,
        )
    if normalized_action == "connectivity_test":
        return await _connectivity_test(ctx, device_id)
    return ToolResult(
        success=False,
        error=(
            "未知 action，请使用 list、list_templates、create、update 或 "
            "connectivity_test。"
        ),
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


async def _list_device_templates() -> ToolResult:
    """Return the same device template index consumed by the access page."""
    try:
        from flocks.tool.device.plugin_index import list_device_templates

        templates = await asyncio.to_thread(list_device_templates, refresh=False)
        return ToolResult(
            success=True,
            output=[template.model_dump(mode="json") for template in templates],
            metadata={
                "template_count": len(templates),
                "installed_count": sum(template.installed for template in templates),
            },
            title="设备模板列表",
        )
    except Exception as exc:
        log.warn("tool.device_manage.list_templates_failed", {"error": str(exc)})
        return ToolResult(success=False, error=f"查询设备模板失败: {exc}")


async def _create_device_from_template(
    storage_key: Optional[str],
    device_name: Optional[str],
    group_id: Optional[str],
    fields: Optional[dict[str, Any]],
    verify_ssl: Optional[bool],
) -> ToolResult:
    target_storage_key = (storage_key or "").strip()
    name = (device_name or "").strip()
    if not target_storage_key:
        return ToolResult(success=False, error="action=create 时 storage_key 不能为空。")
    if not name:
        return ToolResult(success=False, error="action=create 时 device_name 不能为空。")

    try:
        from flocks.tool.device.plugin_index import list_device_templates

        templates = await asyncio.to_thread(list_device_templates, refresh=False)
        template = next(
            (item for item in templates if item.storage_key == target_storage_key),
            None,
        )
    except Exception as exc:
        log.warn("tool.device_manage.create_template_lookup_failed", {"error": str(exc)})
        return ToolResult(success=False, error=f"查询设备模板失败: {exc}")

    if template is None:
        return ToolResult(
            success=False,
            error=(
                f"未找到 storage_key={target_storage_key!r} 的设备模板，"
                "请先调用 device_manage(action='list_templates')。"
            ),
        )
    if not template.installed:
        return ToolResult(
            success=False,
            error=(
                f"模板 {template.name!r} 尚未安装。请先在 FlockHub 安装 "
                f"plugin_id={template.plugin_id!r}，然后重新查询模板。"
            ),
        )

    schema = {
        str(field.get("key") or "").strip(): field
        for field in template.credential_schema
        if str(field.get("key") or "").strip()
    }
    normalized_fields = {
        str(key).strip(): "" if value is None else str(value)
        for key, value in (fields or {}).items()
    }
    unknown = sorted(set(normalized_fields).difference(schema))
    if unknown:
        return ToolResult(
            success=False,
            error="模板未声明字段：" + ", ".join(f"`{key}`" for key in unknown),
        )

    sensitive_fields = sorted(
        key
        for key, field in schema.items()
        if field.get("storage") == "secret"
        or field.get("sensitive") is True
        or field.get("input_type") == "password"
        or key.lower() in _SENSITIVE_FIELD_KEYS
    )
    rejected = sorted(set(normalized_fields).intersection(sensitive_fields))
    if rejected:
        return ToolResult(
            success=False,
            error=(
                "拒绝写入敏感字段："
                + ", ".join(f"`{key}`" for key in rejected)
                + "。请创建设备后在设备接入页面填写。"
            ),
        )

    body = DeviceIntegrationCreate(
        name=name,
        storage_key=template.storage_key,
        service_id=template.service_id,
        group_id=(group_id or "").strip() or None,
        verify_ssl=bool(verify_ssl) if verify_ssl is not None else False,
        fields=normalized_fields,
    )
    try:
        created = await create_device(body)
    except ValueError as exc:
        return ToolResult(success=False, error=f"创建设备失败: {exc}")
    except Exception as exc:
        log.warn("tool.device_manage.create_failed", {"error": str(exc)})
        return ToolResult(success=False, error=f"创建设备失败: {exc}")

    output = created.model_dump(mode="json")
    output["device_id"] = created.id
    output["sensitive_fields_to_complete"] = sensitive_fields
    return ToolResult(
        success=True,
        output=output,
        metadata={"device_id": created.id, "sensitive_fields": sensitive_fields},
        title="设备已创建",
    )


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
    }
)


async def _normalize_update_fields(
    device_id: str,
    fields: Optional[dict[str, Any]],
) -> tuple[dict[str, str], Optional[str]]:
    if fields is None:
        return {}, None
    if not isinstance(fields, dict):
        return {}, "fields 必须是对象，例如 {\"base_url\":\"https://device.local\"}。"
    if not fields:
        return {}, None

    row = await fetch_device(device_id)
    if row is None:
        return {}, f"设备 {device_id!r} 未找到。"

    from flocks.tool.device.plugin_index import list_device_templates

    templates = await asyncio.to_thread(list_device_templates, refresh=False)
    template = next(
        (
            item
            for item in templates
            if item.storage_key == row["storage_key"] and item.installed
        ),
        None,
    )
    if template is None:
        return {}, "未找到该设备对应的已安装模板，无法校验 fields。"

    schema = {
        str(field.get("key") or "").strip(): field
        for field in template.credential_schema
        if str(field.get("key") or "").strip()
    }
    normalized = {
        str(key).strip(): "" if value is None else str(value)
        for key, value in fields.items()
    }
    unknown = sorted(set(normalized).difference(schema))
    if unknown:
        return {}, "模板未声明字段：" + ", ".join(f"`{key}`" for key in unknown)

    rejected = sorted(
        key
        for key in normalized
        if key.lower() in _SENSITIVE_FIELD_KEYS
        or schema[key].get("input_type") == "password"
    )

    if rejected:
        return {}, (
            "拒绝通过 device_manage(action='update') 写入敏感字段："
            + ", ".join(f"`{key}`" for key in sorted(rejected))
            + "。请在设备接入页面的配置表单中填写密钥、密码、Token 或 Cookie。"
        )

    return normalized, None


async def _update_device_config(
    ctx: ToolContext,
    device_id: Optional[str],
    fields: Optional[dict[str, Any]],
    verify_ssl: Optional[bool],
    enabled: Optional[bool],
) -> ToolResult:
    target = (device_id or "").strip()
    if not target:
        return ToolResult(
            success=False,
            error="action=update 时 device_id 不能为空。",
        )

    normalized_fields, field_error = await _normalize_update_fields(target, fields)
    if field_error:
        return ToolResult(success=False, error=field_error)
    if not normalized_fields and verify_ssl is None and enabled is None:
        return ToolResult(
            success=False,
            error="action=update 至少需要提供 fields、verify_ssl 或 enabled。",
        )

    log.info(
        "tool.device_manage.update.start",
        {
            "device_id": target,
            "session_id": ctx.session_id,
            "fields": sorted(normalized_fields),
            "verify_ssl": verify_ssl,
            "enabled": enabled,
        },
    )

    try:
        updated = await update_device(
            target,
            DeviceIntegrationUpdate(
                fields=normalized_fields or None,
                verify_ssl=verify_ssl,
                enabled=enabled,
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
            "enabled": updated.enabled,
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
