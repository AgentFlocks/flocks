"""Todo tools backed by the session todo store."""

import json
from typing import Any, Dict, List

from pydantic import ValidationError

from flocks.session.features.todo import Todo, TodoInfo
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log


log = Log.create(service="tool.todo")

ACTIVE_TODO_STATUSES = {"pending", "in_progress"}
TERMINAL_TODO_STATUSES = {"completed", "cancelled"}
VERIFICATION_KEYWORDS = ("verif", "verify", "validation", "test", "check", "验证", "测试", "检查")

TODO_ITEM_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Unique identifier for the todo item",
        },
        "content": {
            "type": "string",
            "description": "Brief description of the task",
        },
        "activeForm": {
            "type": "string",
            "description": "Optional active/progressive form used while the task is in progress",
        },
        "status": {
            "type": "string",
            "description": "Current status of the task",
            "enum": ["pending", "in_progress", "completed", "cancelled"],
        },
        "priority": {
            "type": "string",
            "description": "Priority level of the task",
            "enum": ["high", "medium", "low"],
        },
    },
    "required": ["id", "content", "status"],
    "additionalProperties": False,
}


TODO_DESCRIPTION = """Use this tool to read or manage a structured task list for your current SecOps session. This helps track progress, organize complex tasks, and demonstrate thoroughness.

When to Use This Tool:
1. Complex multi-step tasks (3+ distinct steps)
2. Non-trivial tasks requiring careful planning
3. User explicitly requests todo list
4. User provides multiple tasks

When NOT to Use:
1. Single, straightforward tasks
2. Trivial tasks with no organizational benefit
3. Tasks completable in < 3 trivial steps

Task States:
- pending: Not yet started
- in_progress: Currently working on
- completed: Finished successfully

Usage:
- Create specific, actionable items
- Break complex tasks into manageable steps
- Update status in real-time
- Mark complete IMMEDIATELY after finishing
- Only ONE task in_progress at a time

Read example:
{
  "action": "read"
}

Write example:
{
  "action": "write",
  "todos": [
    {"id": "investigate", "content": "Investigate the alert", "activeForm": "Investigating the alert", "status": "in_progress"},
    {"id": "verify", "content": "Verify the fix", "status": "pending"}
  ]
}

Invalid input example:
{
  "action": "write",
  "todos": ["1. Investigate the alert", "2. Verify the fix"]
}"""


def _validation_error_message(index: int, error: ValidationError) -> str:
    """Return a concise validation message for a todo item."""
    issues: List[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        suffix = f".{location}" if location else ""
        issues.append(f"todos[{index}]{suffix}: {item.get('msg', 'invalid value')}")
    return "; ".join(issues) if issues else f"todos[{index}] is invalid"


def _normalize_todos(raw_todos: Any) -> List[TodoInfo]:
    """Validate todo payloads strictly so malformed tool calls fail loudly."""
    if not isinstance(raw_todos, list):
        raise ValueError("todos must be an array of structured todo objects")
    if not raw_todos:
        raise ValueError("todos must not be empty")

    normalized: List[TodoInfo] = []
    for index, todo in enumerate(raw_todos):
        if not isinstance(todo, dict):
            raise ValueError(
                f"todos[{index}] must be an object with id, content, status"
            )
        try:
            item = TodoInfo(**todo)
        except ValidationError as exc:
            raise ValueError(_validation_error_message(index, exc)) from exc

        if not item.id.strip():
            raise ValueError(f"todos[{index}].id must not be empty")
        if not item.content.strip():
            raise ValueError(f"todos[{index}].content must not be empty")
        if item.activeForm is not None:
            item.activeForm = item.activeForm.strip() or item.content
        normalized.append(item)

    return normalized


def _serialize_todos(todos: List[TodoInfo]) -> List[Dict[str, Any]]:
    return [todo.model_dump(exclude_none=True) for todo in todos]


def _all_terminal(todos: List[TodoInfo]) -> bool:
    return bool(todos) and all(todo.status in TERMINAL_TODO_STATUSES for todo in todos)


def _verification_nudge_needed(todos: List[TodoInfo]) -> bool:
    if len(todos) < 3 or not _all_terminal(todos):
        return False
    for todo in todos:
        haystack = f"{todo.content} {todo.activeForm or ''}".lower()
        if any(keyword in haystack for keyword in VERIFICATION_KEYWORDS):
            return False
    return True


async def _original_user_request(session_id: str) -> str:
    """Return the latest non-synthetic user request that triggered planning."""
    from flocks.session.message import Message, MessageRole

    for message in reversed(await Message.list(session_id)):
        if message.role != MessageRole.USER or getattr(message, "synthetic", False):
            continue
        content = await Message.get_text_content(message)
        if content and content.strip():
            return content.strip()
    return "Complete the current multi-step user task."


async def _sync_mission(
    ctx: ToolContext,
    todos: List[TodoInfo],
) -> Dict[str, Any] | None:
    """Create or update the Mission bound to this session."""
    from flocks.memory.mission import MissionStore
    from flocks.session.callable_state import add_session_callable_tools
    from flocks.session.session import Session

    session = await Session.get_by_id(ctx.session_id)
    if session is None or not session.memory_enabled:
        return None

    store = MissionStore(session.directory)
    mission_id = session.mission_id
    if mission_id:
        try:
            current = store.load(mission_id)
        except (FileNotFoundError, ValueError):
            current = None
        if current and current["meta"]["status"] in {"completed", "aborted"}:
            return {
                "missionID": mission_id,
                "status": current["meta"]["status"],
                "closed": True,
            }
    elif len(todos) >= 3:
        mission_id = store.generate_id(session.id)
        store.create(
            mission_id=mission_id,
            session_id=session.id,
            original_request=await _original_user_request(session.id),
            todos=todos,
        )
        updated = await Session.update(
            session.project_id,
            session.id,
            mission_id=mission_id,
        )
        if updated is None:
            raise RuntimeError("Failed to bind the new Mission to the session")
        await add_session_callable_tools(session.id, {"mission_record"})

    if not mission_id:
        return None

    result = store.sync_todos(
        mission_id,
        todos,
        session_id=session.id,
    )
    state = result["state"]
    return {
        "missionID": mission_id,
        "status": state["meta"]["status"],
        "revision": state["meta"]["revision"],
        "completed": result["completed"],
        "completionGaps": result["gaps"],
    }


@ToolRegistry.register_function(
    name="todo",
    description=TODO_DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description="Action to perform: read current todos or write the full todo list",
            required=True,
            enum=["read", "write"],
        ),
        ToolParameter(
            name="todos",
            type=ParameterType.ARRAY,
            description="For action=write: array of todo items with id, content, and status fields",
            required=False,
            json_schema={
                "type": "array",
                "items": TODO_ITEM_JSON_SCHEMA,
                "minItems": 1,
            },
        ),
    ]
)
async def todo_tool(
    ctx: ToolContext,
    action: str,
    todos: List[Dict[str, Any]] | None = None,
) -> ToolResult:
    """
    Read or update the todo list.
    
    Args:
        ctx: Tool context
        action: read or write
        todos: List of todo items for write
        
    Returns:
        ToolResult with current or updated todos
    """
    await ctx.ask(
        permission="todo",
        patterns=["*"],
        always=["*"],
        metadata={}
    )

    if action == "read":
        current_todos = await Todo.get(ctx.session_id)
        serialized_todos = _serialize_todos(current_todos)
        pending_count = sum(
            1 for todo in current_todos if todo.status in ACTIVE_TODO_STATUSES
        )

        return ToolResult(
            success=True,
            output=json.dumps(serialized_todos, ensure_ascii=False, indent=2),
            title=f"{pending_count} todos",
            metadata={
                "action": "read",
                "todos": serialized_todos
            }
        )

    if action != "write":
        return ToolResult(
            success=False,
            error=f"Unsupported todo action: {action!r}. Expected 'read' or 'write'.",
        )

    if todos is None:
        return ToolResult(
            success=False,
            error="todos is required when action='write'",
        )

    old_todos = await Todo.get(ctx.session_id)
    normalized_todos = _normalize_todos(todos)
    mission_result = await _sync_mission(ctx, normalized_todos)
    if _all_terminal(normalized_todos):
        await Todo.update_active(ctx.session_id, [])
    else:
        await Todo.update_active(
            ctx.session_id,
            normalized_todos,
        )

    old_serialized = _serialize_todos(old_todos)
    new_serialized = _serialize_todos(normalized_todos)
    verification_nudge_needed = _verification_nudge_needed(normalized_todos)
    pending_count = sum(
        1 for todo in normalized_todos if todo.status in ACTIVE_TODO_STATUSES
    )
    output_payload = {
        "oldTodos": old_serialized,
        "newTodos": new_serialized,
        "verificationNudgeNeeded": verification_nudge_needed,
        "mission": mission_result,
    }
    
    return ToolResult(
        success=True,
        output=json.dumps(output_payload, ensure_ascii=False, indent=2),
        title=f"{pending_count} todos",
        metadata={
            "action": "write",
            "todos": new_serialized,
            "oldTodos": old_serialized,
            "newTodos": new_serialized,
            "verificationNudgeNeeded": verification_nudge_needed,
            "mission": mission_result,
        }
    )
