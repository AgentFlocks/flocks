"""Built-in device connectivity test tool.

This is the canonical status-writing entry point for Rex-assisted device tests.
It reuses the existing device test path so card state stays consistent with the
legacy ``POST /api/devices/{id}/test`` endpoint.
"""
from __future__ import annotations

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

log = Log.create(service="tool.device.connectivity_tool")


@ToolRegistry.register_function(
    name="device_connectivity_test",
    description=(
        "对指定安全设备执行标准连通性检测，并将结果写回设备卡片状态。"
        "Rex 在执行设备测试时应第一步调用此工具。"
    ),
    description_cn="测试设备连通性并更新设备卡片状态",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="device_id",
            type=ParameterType.STRING,
            description="目标设备实例的唯一 ID，来自当前设备上下文或 device_context。",
            required=True,
        ),
    ],
)
async def device_connectivity_test(ctx: ToolContext, device_id: str) -> ToolResult:
    """Run the standard connectivity probe and persist the card status."""
    target = (device_id or "").strip()
    if not target:
        return ToolResult(success=False, error="device_id 不能为空")

    log.info(
        "tool.device_connectivity_test.start",
        {"device_id": target, "session_id": ctx.session_id},
    )

    try:
        result = await test_device(target)
    except DeviceNotFoundError:
        return ToolResult(
            success=False,
            error=f"设备 {target!r} 未找到，请通过 device_context 确认 device_id。",
        )
    except Exception as exc:
        log.warn(
            "tool.device_connectivity_test.failed",
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
