"""Built-in `device_context` tool.

Lets the Agent query the machine-room / device / tool hierarchy on demand,
rather than having everything pre-baked into the system prompt.

Usage (Agent-side):
  Call ``device_context`` with no arguments to get a structured view of:
    机房 → 设备名称 → 该设备对应的工具列表

Output is Markdown so the model can parse and cite device names naturally.
"""
from __future__ import annotations

from flocks.tool.registry import ToolCategory, ToolContext, ToolResult, ToolRegistry
from flocks.utils.log import Log

log = Log.create(service="tool.device.device_context_tool")


@ToolRegistry.register_function(
    name="device_context",
    description=(
        "查询已接入安全设备的机房结构、设备列表及各设备对应工具名称。"
        "当用户涉及特定设备操作时，先调用此工具确认设备名称与工具前缀的映射关系，"
        "再选择对应设备的工具执行任务。"
    ),
    category=ToolCategory.SYSTEM,
    parameters=[],
)
async def device_context(ctx: ToolContext) -> ToolResult:
    """Return the machine-room → device → tool hierarchy as Markdown."""
    try:
        from flocks.tool.device.prompt import build_device_context_section

        content = await build_device_context_section()
        if not content:
            return ToolResult(
                success=True,
                output="当前没有已接入的安全设备。请前往「设备接入」页面添加设备后再试。",
            )
        return ToolResult(success=True, output=content)
    except Exception as exc:
        log.warn("tool.device_context.failed", {"error": str(exc)})
        return ToolResult(success=False, error=f"查询设备上下文失败: {exc}")
