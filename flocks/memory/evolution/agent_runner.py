"""Temporary Agent Session runner for Memory evolution."""

from __future__ import annotations

import asyncio
from typing import Optional

from flocks.agent.registry import Agent
from flocks.session.message import Message, MessageRole
from flocks.session.session import PermissionRule, Session
from flocks.session.session_loop import SessionLoop
from flocks.utils.log import Log


log = Log.create(service="memory.evolution.agent")


async def run_evolution_agent(
    *,
    agent_name: str,
    prompt: str,
    project_id: str,
    directory: str,
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
    parent_session_id: Optional[str] = None,
    write_permission_patterns: Optional[list[str]] = None,
) -> None:
    """Run a hidden evolution Agent in a disposable full Session Loop."""
    agent = await Agent.get(agent_name)
    if agent is None:
        await Agent.refresh()
        agent = await Agent.get(agent_name)
    if agent is None:
        raise RuntimeError(f"evolution agent not found: {agent_name}")

    from flocks.session.core.session_state import (
        get_main_session_id,
        set_main_session,
    )

    previous_main_session_id = get_main_session_id()
    permissions = [
        PermissionRule(
            permission="question",
            action="deny",
            pattern="*",
        )
    ]
    if write_permission_patterns is not None:
        permissions.extend(
            [
                PermissionRule(
                    permission="edit",
                    action="deny",
                    pattern="*",
                ),
                *[
                    PermissionRule(
                        permission="edit",
                        action="allow",
                        pattern=pattern,
                    )
                    for pattern in write_permission_patterns
                ],
                PermissionRule(
                    permission="bash",
                    action="deny",
                    pattern="*",
                ),
            ]
        )

    session = await Session.create(
        project_id=project_id,
        directory=directory,
        title=f"[Evolution] {agent_name}",
        parent_id=parent_session_id,
        agent=agent_name,
        category="task",
        memory_enabled=False,
        permission=permissions,
        metadata={
            "ephemeral": True,
            "evolution": agent_name,
            "hideFromSessionManager": True,
        },
    )
    if parent_session_id is None:
        set_main_session(previous_main_session_id)

    try:
        message_model = (
            {
                "providerID": provider_id,
                "modelID": model_id,
            }
            if provider_id and model_id
            else None
        )
        await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content=prompt,
            agent=agent_name,
            model=message_model,
        )
        result = await SessionLoop.run(
            session_id=session.id,
            provider_id=provider_id,
            model_id=model_id,
            agent_name=agent_name,
            working_directory=directory,
        )
        if result.action == "error":
            raise RuntimeError(
                result.error or f"{agent_name} evolution Agent failed"
            )
    finally:
        try:
            await asyncio.shield(Session.delete(project_id, session.id))
        except Exception as exc:
            log.warn(
                "evolution_agent.cleanup_failed",
                {
                    "agent": agent_name,
                    "session_id": session.id,
                    "error": str(exc),
                },
            )
        if parent_session_id is None:
            set_main_session(previous_main_session_id)
