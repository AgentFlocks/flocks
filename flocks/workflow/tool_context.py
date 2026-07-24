"""Shared ToolContext builder for workflow execution entrypoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import os
from typing import Any, Optional

from fastapi import HTTPException

from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.session.execution_profile import (
    get_session_execution_profile,
    upsert_session_execution_profile,
)
from flocks.tool import ToolContext
from flocks.workflow.fs_store import find_workspace_root


async def build_workflow_tool_context(
    *,
    workflow_id: str,
    action_name: str,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    agent: Optional[str] = None,
    event_publish_callback: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]] = None,
    execution_context: Mapping[str, Any] | None = None,
) -> ToolContext:
    """Build a real ToolContext for workflow execution.

    Prefer the caller-provided session/message. When absent, create a temporary
    parent session and synthetic user message so workflow-internal tools such as
    ``task`` / ``delegate_task`` can resolve a valid parent session.
    """

    effective_session_id = str(session_id or "").strip()
    effective_message_id = str(message_id or "").strip()
    effective_agent = str(agent or "").strip()

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

    try:
        # Workflow runtime carries provenance metadata only; mode resolution is
        # fully Pro-owned.
        from flocks.hooks.pipeline import HookPipeline

        await upsert_session_execution_profile(
            effective_session_id,
            patch={
                "entry": "workflow",
                "default_agent": effective_agent or "rex",
            },
            source="workflow.runtime.tool_context",
        )
        profile = await get_session_execution_profile(effective_session_id)
        await HookPipeline.run_action_before(
            {
                "operation": "session.mode.initialize",
                "session_id": effective_session_id,
                "entry": "workflow",
                "workflow_context": {
                    "source": "workflow_runtime",
                    "workflow_id": workflow_id,
                    "action_name": action_name,
                },
                "session_execution_profile": profile or {},
            }
        )
    except Exception:
        pass
    session_profile = await get_session_execution_profile(effective_session_id)

    extra = {
        "workspace_dir": workspace_dir,
        "main_session_key": effective_session_id,
        "session_execution_profile": session_profile or {},
        "workflow_context": {
            "source": "workflow_runtime",
            "workflow_id": workflow_id,
            "action_name": action_name,
        },
    }
    if isinstance(execution_context, Mapping):
        # Generic opaque context transport for nested workflow work.  No OSS
        # component interprets this carrier as identity, authorization, or
        # permission information.
        extra["execution_context"] = dict(execution_context)

    return ToolContext(
        session_id=effective_session_id,
        message_id=effective_message_id,
        agent=effective_agent or "rex",
        event_publish_callback=event_publish_callback,
        extra=extra,
    )
