"""Apply declarative agent isolation requirements to a session."""

from __future__ import annotations

from pathlib import Path

from flocks.agent.agent import AgentInfo
from flocks.session import message_pages
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

        def claim_metadata(locked_metadata: dict) -> dict:
            # Built from the locked row: a metadata write another process
            # committed while the claim waited for its locks is kept.
            return {
                **locked_metadata,
                DEDICATED_AGENT_POLICY_METADATA_KEY: {
                    "agent": agent.name,
                    "version": 1,
                },
            }

        updates: dict[str, object] = {
            "agent": agent.name,
            "metadata": claim_metadata,
        }
        if agent.session_directory:
            directory = Path(agent.session_directory).expanduser()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            updates["directory"] = str(directory.resolve())
        if agent.memory_enabled is not None:
            updates["memory_enabled"] = agent.memory_enabled

        session_id = current.id

        async def lock_messages(tx) -> None:
            # Message writes take this lock before reading the claim, so no
            # user message from another process can land between the history
            # check below and the claim commit.
            try:
                await message_pages.lock_session(tx, session_id)
            except message_pages.SessionMessagesDeletedError as exc:
                raise AgentSessionPolicyError("Dedicated agent session is not active") from exc

        async def verify_history(tx, locked: SessionInfo) -> None:
            # The checks above read this process's cache; repeat them on the
            # committed rows while the message lock is held.
            locked_metadata = locked.metadata if isinstance(locked.metadata, dict) else {}
            locked_policy = locked_metadata.get(DEDICATED_AGENT_POLICY_METADATA_KEY)
            locked_agent = (
                locked_policy.get("agent")
                if isinstance(locked_policy, dict) and locked_policy.get("version") == 1
                else None
            )
            if locked_agent is not None and locked_agent != agent.name:
                raise AgentSessionPolicyError(
                    f"Session {session_id} is dedicated to agent {locked_agent!r}"
                )
            has_history = locked_agent is None and await tx.fetch_one(
                "SELECT 1 FROM session_message_pages WHERE session_id = ? LIMIT 1",
                (session_id,),
            )
            has_foreign_input = await tx.fetch_one(
                "SELECT 1 FROM session_message_pages WHERE session_id = ? "
                "AND info ->> 'role' = 'user' AND (info ->> 'agent') IS DISTINCT FROM ? LIMIT 1",
                (session_id, agent.name),
            )
            if has_history or has_foreign_input:
                raise AgentSessionPolicyError(
                    f"Agent {agent.name!r} requires a new, empty session"
                )

        updated = await Session._update_locked(
            current.project_id,
            current.id,
            pre_lock=lock_messages,
            transaction_check=verify_history,
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
