"""Temporary Agent Session runner for Memory evolution."""

from __future__ import annotations

import asyncio
from typing import Optional

from flocks.agent.registry import Agent
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
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
    session = await Session.create(
        project_id=project_id,
        directory=directory,
        title=f"[Evolution] {agent_name}",
        parent_id=parent_session_id,
        agent=agent_name,
        category="task",
        memory_enabled=False,
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
        if result.error:
            raise RuntimeError(result.error)
        if result.action != "stop":
            raise RuntimeError(
                f"{agent_name} evolution Agent ended with action: {result.action}"
            )
        if result.metadata.get("aborted"):
            raise RuntimeError(f"{agent_name} evolution Agent was aborted")
        last_message = result.last_message
        if last_message is None or last_message.role != "assistant":
            raise RuntimeError(
                f"{agent_name} evolution Agent ended without a final assistant message"
            )
        if last_message.error:
            raise RuntimeError(
                f"{agent_name} evolution Agent failed: {last_message.error}"
            )
        if last_message.finish != "stop":
            raise RuntimeError(
                f"{agent_name} evolution Agent ended with finish reason: "
                f"{last_message.finish or 'missing'}"
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
