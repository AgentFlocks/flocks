"""Shared ToolContext builder for workflow execution entrypoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import os
from typing import Any, Optional

from fastapi import HTTPException

from flocks.identity.subject import get_current_subject
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.tool import ToolContext
from flocks.workflow.fs_store import find_workspace_root


async def build_workflow_tool_context(
    *,
    workflow_id: str,
    action_name: str,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    agent: Optional[str] = None,
    parent_context: Optional[ToolContext] = None,
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

    delegation_context: dict[str, Any] = {}
    delegated_session = bool(
        parent_session and getattr(parent_session, "delegation_context_required", False)
    )
    if delegated_session:
        try:
            from flocks.security.capability_pool import (
                build_capability_ceiling,
                normalize_capability_ceiling,
            )
            from flocks.security.delegation_context import load_delegation_security_context

            stored_context = await load_delegation_security_context(effective_session_id)
            stored_ceiling = (
                normalize_capability_ceiling(stored_context.get("parent_ceiling"))
                if isinstance(stored_context, dict)
                else None
            )
            if stored_ceiling is not None:
                # The persisted server-created ceiling is authoritative for a
                # marked child.  A direct workflow caller can at most further
                # narrow it with a valid source ceiling; it can never replace
                # it or broaden it.
                delegation_context = stored_context
                if parent_context is not None and isinstance(parent_context.extra, dict):
                    source_ceiling = parent_context.extra.get("_capability_pool")
                    if source_ceiling is None:
                        source_ceiling = parent_context.extra.get("parent_ceiling")
                    if source_ceiling is not None:
                        source = normalize_capability_ceiling(source_ceiling)
                        if source is None:
                            delegation_context = {"parent_ceiling": {"invalid": True}}
                        else:
                            narrowed_ceiling = build_capability_ceiling(
                                tools=source["tools"],
                                context={**source, "parent_ceiling": stored_ceiling},
                            )
                            if normalize_capability_ceiling(narrowed_ceiling) is None:
                                delegation_context = {"parent_ceiling": {"invalid": True}}
                            else:
                                delegation_context = {
                                    **stored_context,
                                    "parent_ceiling": narrowed_ceiling,
                                }
            else:
                delegation_context = {"parent_ceiling": {"invalid": True}}
        except Exception:
            # Direct workflow routes do not pass through SessionLoop.  A
            # marked child therefore has to fail closed here as well when its
            # internal record cannot be read.
            delegation_context = {"parent_ceiling": {"invalid": True}}
    elif parent_context is not None and isinstance(parent_context.extra, dict):
        from flocks.security.capability_pool import sanitize_parent_ceiling

        source_ceiling = parent_context.extra.get("_capability_pool")
        if source_ceiling is None:
            source_ceiling = parent_context.extra.get("parent_ceiling")
        if source_ceiling is not None:
            delegation_context["parent_ceiling"] = sanitize_parent_ceiling(source_ceiling)

    subject_payload: dict[str, Any] = {}
    current_subject = get_current_subject()
    if current_subject is not None:
        try:
            subject_payload = current_subject.model_dump()
        except Exception:
            subject_payload = {}
    if not subject_payload and parent_session and getattr(parent_session, "owner_subject_id", None):
        subject_payload = {
            "subject_id": str(parent_session.owner_subject_id),
            "subject_type": "human",
            "entry": "unknown",
            "permission_mode": str(getattr(parent_session, "permission_mode", "default_interactive") or "default_interactive"),
        }

    return ToolContext(
        session_id=effective_session_id,
        message_id=effective_message_id,
        agent=effective_agent or "rex",
        event_publish_callback=event_publish_callback,
        extra={
            "workspace_dir": workspace_dir,
            "main_session_key": effective_session_id,
            "entry": str(subject_payload.get("entry") or "unknown"),
            "subject": subject_payload,
            **delegation_context,
        },
    )
