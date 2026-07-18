"""Trusted per-execution capability context construction.

Root sessions have no persisted parent delegation record.  They still need a
server-derived effective ceiling before their tools can execute or delegate.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Optional

from flocks.session.callable_schema import list_session_callable_tool_infos


async def resolve_execution_agent(
    requested_agent: str | None,
    session_agent: str | None,
) -> str:
    """Return the requested execution agent or the trusted session fallback."""
    explicit = str(requested_agent or "").strip()
    fallback = str(session_agent or "").strip() or "rex"
    candidate = explicit or fallback
    if explicit:
        from flocks.agent.registry import Agent

        if await Agent.get(candidate) is None:
            raise ValueError(f"Unknown execution agent: {candidate}")
    return candidate


async def build_root_execution_security_context(
    *,
    session_id: str,
    agent_name: str,
    workspace: str,
    supplied_context: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Bind this root execution to its current server-derived capability pool.

    The callable schema already applies agent declarations, enabled-tool state,
    inherited workflow ceilings, and registered visibility filters.  Reusing
    that result makes direct execution and LLM-driven execution share exactly
    the same boundary.
    """
    context = deepcopy(dict(supplied_context or {}))
    context["agent"] = agent_name
    context["workspace"] = workspace
    context["sessionID"] = session_id
    declared_tool_names = None
    try:
        # A direct HTTP/workflow/legacy root may be the first execution for a
        # session, before ``SessionRunner`` initializes its callable-tool
        # state.  Resolve the same agent baseline that Runner would use so we
        # do not accidentally turn an uninitialized root into an empty ceiling.
        from flocks.agent.registry import Agent

        agent = await Agent.get(agent_name)
        if agent is not None:
            declared_tool_names = getattr(agent, "tools", None)
    except Exception:
        # Schema resolution remains the authority.  If the optional agent
        # lookup is unavailable, it safely falls back to the persisted session
        # callable state (or its restrictive always-load default).
        declared_tool_names = None
    result = await list_session_callable_tool_infos(
        session_id=session_id,
        declared_tool_names=declared_tool_names,
        capability_context=context,
    )
    ceiling = deepcopy(result.capability_ceiling)
    context["_capability_pool"] = deepcopy(ceiling)
    context["parent_ceiling"] = ceiling
    return context


__all__ = [
    "build_root_execution_security_context",
    "resolve_execution_agent",
]
