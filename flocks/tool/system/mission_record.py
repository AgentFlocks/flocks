"""Record normalized progress and evidence for the current Mission."""

from __future__ import annotations

import json
from typing import List, Optional

from flocks.memory.mission import MissionStore
from flocks.session.callable_state import remove_session_callable_tools
from flocks.session.session import Session
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


DESCRIPTION = """Record durable progress or evidence for the Mission bound to this session.

Use progress for an execution attempt, finding for a candidate or confirmed
result, validation for a passed/failed verification, artifact for a large
file that must remain traceable, and checkpoint for current state and the next
action. Mission files are protected and cannot be edited directly."""


@ToolRegistry.register_function(
    name="mission_record",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    native=False,
    always_load=False,
    tags=["mission", "evidence", "progress"],
    parameters=[
        ToolParameter(
            name="kind",
            type=ParameterType.STRING,
            description="Record type",
            required=True,
            enum=["progress", "finding", "validation", "artifact", "checkpoint"],
        ),
        ToolParameter(
            name="summary",
            type=ParameterType.STRING,
            description="Concise result or state summary",
            required=True,
        ),
        ToolParameter(
            name="details",
            type=ParameterType.STRING,
            description="Supporting details",
            required=False,
        ),
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Related Mission task id",
            required=False,
        ),
        ToolParameter(
            name="finding_id",
            type=ParameterType.STRING,
            description="Finding id to create or update",
            required=False,
        ),
        ToolParameter(
            name="status",
            type=ParameterType.STRING,
            description="Kind-specific status",
            required=False,
        ),
        ToolParameter(
            name="source_refs",
            type=ParameterType.ARRAY,
            description="Contract, evidence, Finding, or Artifact references",
            required=False,
            json_schema={
                "type": "array",
                "items": {"type": "string"},
            },
        ),
        ToolParameter(
            name="artifact_path",
            type=ParameterType.STRING,
            description="Existing file to copy into immutable Mission artifacts",
            required=False,
        ),
    ],
)
async def mission_record_tool(
    ctx: ToolContext,
    kind: str,
    summary: str,
    details: Optional[str] = None,
    task_id: Optional[str] = None,
    finding_id: Optional[str] = None,
    status: Optional[str] = None,
    source_refs: Optional[List[str]] = None,
    artifact_path: Optional[str] = None,
) -> ToolResult:
    """Write a protected Mission record."""
    session = await Session.get_by_id(ctx.session_id)
    if session is None or not session.mission_id:
        return ToolResult(
            success=False,
            error="No Mission is bound to this session.",
        )
    if not summary.strip():
        return ToolResult(success=False, error="summary must not be empty")

    try:
        store = MissionStore(session.directory)
        result = store.record(
            session.mission_id,
            session_id=session.id,
            kind=kind,
            summary=summary.strip(),
            details=details,
            task_id=task_id,
            finding_id=finding_id,
            status=status,
            source_refs=source_refs,
            artifact_path=artifact_path,
        )
        state = store.load(session.mission_id)
        if state["meta"]["status"] in {"completed", "aborted"}:
            await remove_session_callable_tools(session.id, {"mission_record"})
    except (FileNotFoundError, OSError, ValueError) as exc:
        return ToolResult(success=False, error=str(exc))

    return ToolResult(
        success=True,
        output=json.dumps(result, ensure_ascii=False, indent=2),
        title=f"Mission {kind} recorded",
        metadata=result,
    )
