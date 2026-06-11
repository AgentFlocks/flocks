"""Compatibility alias for delegate_task.

The runtime keeps ``task`` as a registered tool name for workflow/backward
compatibility, but all scheduling behavior lives in ``delegate_task``.
Background subagent execution is disabled; run synchronously and emit
multiple sibling tool calls in one assistant turn for parallel work.
"""

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

Use delegate_task directly for new prompts. Workflows may continue using task;
it accepts the same single-subagent shape and forwards it to delegate_task.
Background subagent execution is disabled; run synchronously and emit multiple
sibling tool calls in one assistant turn for parallel work.
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
            description="Delegatable agent name. Mutually exclusive with category.",
            required=False,
        ),
        ToolParameter(
            name="category",
            type=ParameterType.STRING,
            description="Delegate category. Mutually exclusive with subagent_type.",
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
            description="Optional command name for tracking",
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
    category: Optional[str] = None,
    load_skills: Optional[list] = None,
    run_in_background: bool = False,
    session_id: Optional[str] = None,
    command: Optional[str] = None,
    model: Optional[str] = None,
) -> ToolResult:
    """Forward legacy task calls to delegate_task."""
    return await delegate_task_tool(
        ctx=ctx,
        prompt=prompt,
        load_skills=load_skills,
        description=description,
        run_in_background=run_in_background,
        category=category,
        subagent_type=subagent_type,
        session_id=session_id,
        command=command,
        model=model,
    )
