"""Shared ToolContext builder for workflow execution entrypoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import os
from typing import Any, Optional

from fastapi import HTTPException

from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.tool import ToolContext
from flocks.utils.log import Log
from flocks.workflow.fs_store import find_workspace_root

log = Log.create(service="workflow.tool_context")


async def build_workflow_tool_context(
    *,
    workflow_id: str,
    action_name: str,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    agent: Optional[str] = None,
    event_publish_callback: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]] = None,
) -> ToolContext:
    """Build a real ToolContext for workflow execution.

    Prefer the caller-provided session/message. When absent, create a temporary
    parent session and synthetic user message so workflow-internal tools such as
    ``task`` / ``delegate_task`` can resolve a valid parent session.
    """

    effective_session_id = str(session_id or "").strip()
    effective_message_id = str(message_id or "").strip()
    effective_agent = str(agent or "").strip()
    created_temp_parent = not effective_session_id

    workspace_dir = os.getcwd()
    project_id = "default"
    try:
        from flocks.project.instance import Instance

        workspace_dir = str(getattr(Instance, "directory", None) or workspace_dir)
        project = getattr(Instance, "project", None)
        if project is not None and getattr(project, "id", None):
            project_id = str(project.id)
    except Exception:
        workspace_dir = str(find_workspace_root())

    parent_session = None
    if effective_session_id:
        parent_session = await Session.get_by_id(effective_session_id)
        if not parent_session:
            raise HTTPException(status_code=400, detail=f"Parent session not found: {effective_session_id}")
        workspace_dir = str(getattr(parent_session, "directory", None) or workspace_dir)
        if getattr(parent_session, "project_id", None):
            project_id = str(parent_session.project_id)
        if not effective_agent:
            effective_agent = str(getattr(parent_session, "agent", None) or "rex")
    else:
        parent_session = await Session.create(
            project_id=project_id,
            directory=workspace_dir,
            title=f"Workflow {action_name}: {workflow_id}",
            agent=effective_agent or "rex",
            category="task",
            metadata={
                "workflowTempParent": True,
                "hideFromSessionManager": True,
                "workflowId": workflow_id,
                "workflowAction": action_name,
            },
        )
        effective_session_id = parent_session.id
        workspace_dir = str(getattr(parent_session, "directory", None) or workspace_dir)
        if not effective_agent:
            effective_agent = str(getattr(parent_session, "agent", None) or "rex")

    if not effective_message_id:
        message = await Message.create(
            session_id=effective_session_id,
            role=MessageRole.USER,
            content=f"[Workflow {action_name}] {workflow_id}",
            agent=effective_agent or "rex",
            synthetic=True,
        )
        effective_message_id = message.id

    return ToolContext(
        session_id=effective_session_id,
        message_id=effective_message_id,
        agent=effective_agent or "rex",
        event_publish_callback=event_publish_callback,
        extra={
            "workspace_dir": workspace_dir,
            "main_session_key": effective_session_id,
            "workflow_temp_parent": created_temp_parent,
        },
    )


async def cleanup_workflow_tool_context(tool_context: Optional[ToolContext]) -> bool:
    """Purge an unused temporary workflow parent session.

    Parents with delegated child sessions are retained so returned task session
    IDs remain valid. Caller-provided sessions are never modified.
    """
    if tool_context is None:
        return False
    extra = getattr(tool_context, "extra", None)
    if not isinstance(extra, dict) or extra.get("workflow_temp_parent") is not True:
        return False

    session_id = str(getattr(tool_context, "session_id", "") or "").strip()
    if not session_id:
        return False

    try:
        session = await Session.get_by_id(session_id)
        metadata = getattr(session, "metadata", None) if session is not None else None
        if session is None or not isinstance(metadata, dict) or metadata.get("workflowTempParent") is not True:
            return False

        if extra.get("workflow_child_session_created") is True:
            return False

        await Session.delete(session.project_id, session.id)

        # Session.delete is intentionally a soft delete. Trigger parents without
        # child tasks are implementation details, so remove their residual rows
        # to keep high-frequency trigger traffic storage-bounded.
        from flocks.storage.storage import Storage

        await Storage.delete(f"session:{session.project_id}:{session.id}")
        await Storage.delete(f"message:{session.id}")
        await Storage.delete(f"todo:{session.id}")
        await Storage.delete(f"goal:{session.id}")
        await Storage.delete(f"session_diff:{session.id}")
        await Storage.clear(prefix=f"message_diff:{session.id}:")
        await Storage.clear(prefix=f"system_prompts:{session.id}:")

        Message.invalidate_cache(session.id)
        return True
    except Exception as exc:
        log.warning(
            "workflow.tool_context.cleanup_failed",
            {"session_id": session_id, "error": str(exc)},
        )
        return False
