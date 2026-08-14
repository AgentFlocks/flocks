"""Backward-compatible alias for :mod:`flocks.tool.agent.delegate_task`."""

from __future__ import annotations

from typing import Optional

from flocks.tool.agent.delegate_task import delegate_task_tool
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


DESCRIPTION = """Compatibility alias for delegate_task.

Use delegate_task directly for new prompts. Existing workflows may continue
using task; it accepts the same single-subagent arguments and forwards them to
delegate_task.
"""


@ToolRegistry.register_function(
    name="task",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    native=False,
    parameters=[
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description="Optional short task description (3-5 words)",
            required=False,
        ),
        ToolParameter(
            name="prompt",
            type=ParameterType.STRING,
            description="Detailed prompt for the subagent.",
            required=True,
        ),
        ToolParameter(
            name="subagent_type",
            type=ParameterType.STRING,
            description=("Delegatable agent name. Required for new tasks; omit when continuing with session_id."),
            required=False,
        ),
        ToolParameter(
            name="load_skills",
            type=ParameterType.ARRAY,
            description="Optional skill names to inject into the delegated agent",
            required=False,
            default=[],
        ),
        ToolParameter(
            name="session_id",
            type=ParameterType.STRING,
            description="Existing subagent session to continue",
            required=False,
        ),
        ToolParameter(
            name="command",
            type=ParameterType.STRING,
            description="Deprecated command name retained for compatibility",
            required=False,
        ),
        ToolParameter(
            name="model",
            type=ParameterType.STRING,
            description="Optional model override (provider/model or model)",
            required=False,
        ),
    ],
)
async def task_tool(
    ctx: ToolContext,
    description: Optional[str] = None,
    prompt: Optional[str] = None,
    subagent_type: Optional[str] = None,
    load_skills: Optional[list] = None,
    run_in_background: bool = False,
    session_id: Optional[str] = None,
    command: Optional[str] = None,
    model: Optional[str] = None,
) -> ToolResult:
    """Forward a legacy ``task`` call to ``delegate_task``."""
    return await delegate_task_tool(
        ctx=ctx,
        prompt=prompt,
        load_skills=load_skills,
        description=description,
        run_in_background=run_in_background,
        subagent_type=subagent_type,
        session_id=session_id,
        command=command,
        model=model,
    )
