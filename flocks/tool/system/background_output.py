"""
background_output tool - query background task status/output.
"""

from typing import Optional

from flocks.tool.registry import (
    ToolRegistry,
    ToolCategory,
    ToolParameter,
    ParameterType,
    ToolResult,
    ToolContext,
)
from flocks.task.background import get_background_manager, BackgroundTask
from flocks.session.session import Session
from flocks.session.message import Message


def _find_task(manager, task_id: str) -> Optional[BackgroundTask]:
    """Find task by task_id, or fall back to session_id prefix match."""
    task = manager.get_task(task_id)
    if task:
        return task
    # LLMs often pass session_id instead of task_id — search by session_id
    for t in manager.list_tasks():
        if t.session_id and (t.session_id == task_id or t.session_id.startswith(task_id)):
            return t
    return None


@ToolRegistry.register_function(
    name="background_output",
    description="Check background task status/output by task_id or session_id. Optionally block until complete.",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Background task ID (bg_xxx) or session ID (ses_xxx)",
            required=True,
        ),
        ToolParameter(
            name="block",
            type=ParameterType.BOOLEAN,
            description="If true, wait for completion (optional)",
            required=False,
        ),
        ToolParameter(
            name="timeout_ms",
            type=ParameterType.INTEGER,
            description="Max wait time in milliseconds when block=true",
            required=False,
        ),
    ],
)
async def background_output_tool(
    ctx: ToolContext,
    task_id: str,
    block: Optional[bool] = False,
    timeout_ms: Optional[int] = None,
) -> ToolResult:
    await ctx.ask(
        permission="background_output",
        patterns=[task_id],
        always=["*"],
        metadata={"task_id": task_id},
    )
    manager = get_background_manager()

    # Try direct lookup first, then session_id fallback
    task = _find_task(manager, task_id)

    if task and block:
        waited = await manager.wait_for(task.id, timeout_ms)
        task = waited or _find_task(manager, task.id)

    if task:
        output = (
            f"Task ID: {task.id}\n"
            f"Status: {task.status}\n"
            f"Agent: {task.agent}\n"
            f"Description: {task.description}\n"
            f"Session ID: {task.session_id}\n"
        )
        if task.error:
            output += f"\nError: {task.error}\n"
        if task.output:
            output += f"\nOutput:\n{task.output}\n"
        return ToolResult(
            success=True,
            output=output,
            title=task.description,
            metadata={"status": task.status, "sessionId": task.session_id},
        )

    # No background task found — if it looks like a session_id, query the session directly.
    # This handles the case where the task ran synchronously (not in background).
    if task_id.startswith("ses_"):
        session = await Session.get_by_id(task_id)
        if not session:
            # Try prefix match (LLMs sometimes truncate IDs)
            return ToolResult(
                success=False,
                error=(
                    f'No background task or session found for "{task_id}". '
                    "The task may have already completed synchronously. "
                    "Check the task tool output above for results."
                ),
            )
        # Fetch last assistant message from the session
        messages = await Message.list(session.id)
        last_assistant = None
        for msg in reversed(messages):
            if msg.role == "assistant":
                last_assistant = msg
                break
        output_text = ""
        if last_assistant:
            output_text = await Message.get_text_content(last_assistant)
        output = (
            f"Session ID: {session.id}\n"
            f"Agent: {session.agent}\n"
            f"Title: {session.title}\n"
            f"Status: completed (ran synchronously, not in background)\n"
        )
        if output_text:
            output += f"\nOutput:\n{output_text}\n"
        else:
            output += "\nNo output (the subagent session may have encountered an error).\n"
        return ToolResult(
            success=True,
            output=output,
            title=session.title,
            metadata={"status": "completed", "sessionId": session.id},
        )

    return ToolResult(
        success=False,
        error=f'Task "{task_id}" not found. Use the task_id (bg_xxx) returned by the task tool when run_in_background=true.',
    )
