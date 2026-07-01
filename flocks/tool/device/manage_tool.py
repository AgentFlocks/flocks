"""Built-in device management tool for Rex.

``device_manage`` is the single system-tool entrypoint for device discovery and
standard connectivity checks. Its ``connectivity_test`` action reuses the
existing device test path, so card state stays consistent with the
``POST /api/devices/{id}/test`` endpoint.
"""
from __future__ import annotations

from typing import Optional

from flocks.tool.device.intake import DeviceNotFoundError, test_device
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
        "action=connectivity_test 用于测试指定设备连通性并更新设备卡片状态。"
    ),
    description_cn="列出设备或测试设备连通性",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description="操作类型：list 列出设备；connectivity_test 测试设备连通性。",
            required=True,
            enum=["list", "connectivity_test"],
        ),
        ToolParameter(
            name="device_id",
            type=ParameterType.STRING,
            description="目标设备实例 ID。action=connectivity_test 时必填。",
            required=False,
        ),
    ],
)
async def device_manage(
    ctx: ToolContext,
    action: str,
    device_id: Optional[str] = None,
) -> ToolResult:
    """List devices or run a standard connectivity probe."""
    normalized_action = (action or "").strip()
    if normalized_action == "list":
        return await _list_devices()
    if normalized_action == "connectivity_test":
        return await _connectivity_test(ctx, device_id)
    return ToolResult(
        success=False,
        error="未知 action，请使用 list 或 connectivity_test。",
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
