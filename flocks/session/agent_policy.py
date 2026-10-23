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
    """Claim or validate an agent session without weakening prior isolation."""
    async with Session.lifecycle_lock(session.id):
        current = await Session.get_by_id_unfiltered(session.id)
        if current is None:
            if not agent.require_dedicated_session:
                # Internal unit-style dispatchers may supply an ephemeral
                # ordinary session object. Dedicated agents always fail closed.
                return session
            raise AgentSessionPolicyError("Dedicated agent session is not active")
        if getattr(current, "status", "active") != "active":
            raise AgentSessionPolicyError("Dedicated agent session is not active")

        current_metadata = getattr(current, "metadata", None)
        policy = (
            current_metadata.get(DEDICATED_AGENT_POLICY_METADATA_KEY)
            if isinstance(current_metadata, dict)
            else None
        )
        claimed_agent = (
            policy.get("agent")
            if isinstance(policy, dict) and policy.get("version") == 1
            else None
        )
        if claimed_agent is not None and claimed_agent != agent.name:
            raise AgentSessionPolicyError(
                f"Session {current.id} is dedicated to agent {claimed_agent!r}"
            )
        if not agent.require_dedicated_session:
            return current

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
        always_load_tool_names=(
            set()
            if agent.prompt_profile == "isolated"
            else get_always_load_tool_names()
        ),
    )
    return updated
