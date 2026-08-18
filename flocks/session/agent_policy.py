"""Apply declarative agent isolation requirements to a session."""

from __future__ import annotations

from pathlib import Path

from flocks.agent.agent import AgentInfo
from flocks.session.callable_state import initialize_session_callable_tools
from flocks.session.message import Message
from flocks.session.session import (
    DEDICATED_AGENT_POLICY_METADATA_KEY,
    Session,
    SessionInfo,
)
from flocks.tool.catalog import get_always_load_tool_names


class AgentSessionPolicyError(ValueError):
    """Raised when an isolated agent is selected in a non-empty session."""


async def prepare_session_for_agent(
    session: SessionInfo,
    agent: AgentInfo,
) -> SessionInfo:
    """Claim an empty session for an isolated agent and enforce its policy."""
    if not agent.require_dedicated_session:
        return session

    async with Session.lifecycle_lock(session.id):
        current = await Session.get(session.project_id, session.id)
        if current is None or current.status != "active":
            raise AgentSessionPolicyError("Dedicated agent session is not active")

        policy = current.metadata.get(DEDICATED_AGENT_POLICY_METADATA_KEY)
        policy_is_current = (
            isinstance(policy, dict)
            and policy.get("agent") == agent.name
            and policy.get("version") == 1
        )
        messages = await Message.list(session.id, include_archived=True)
        foreign_user_messages = [
            message
            for message in messages
            if getattr(message, "role", None) == "user"
            and getattr(message, "agent", None) != agent.name
        ]
        if (not policy_is_current and messages) or foreign_user_messages:
            raise AgentSessionPolicyError(
                f"Agent {agent.name!r} requires a new, empty session"
            )

        metadata = dict(current.metadata)
        metadata[DEDICATED_AGENT_POLICY_METADATA_KEY] = {
            "agent": agent.name,
            "version": 1,
        }
        updates: dict[str, object] = {
            "agent": agent.name,
            "metadata": metadata,
        }
        if agent.session_directory:
            directory = Path(agent.session_directory).expanduser()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            updates["directory"] = str(directory.resolve())
        if agent.memory_enabled is not None:
            updates["memory_enabled"] = agent.memory_enabled

        updated = await Session._update_locked(
            current.project_id,
            current.id,
            **updates,
        )
        if updated is None:
            raise AgentSessionPolicyError("Failed to apply the agent session policy")

    await initialize_session_callable_tools(
        updated.id,
        agent.tools or [],
        always_load_tool_names=get_always_load_tool_names(),
    )
    return updated
