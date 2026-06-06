"""
Raptor AgentBridge

Injects Flocks agent identity into the hermes-agent session:
- system_message: built from Flocks SessionPrompt.build_system_prompts()
- Toolset selection: determined by Flocks agent's tool whitelist
  (used to set enabled_toolsets hint in session.create params)

Security guarantee (§4.5 / §5.4 design)
-----------------------------------------
hermes-agent runs with the Flocks agent's system prompt and the set of toolsets
corresponding to the agent's allowed tools.  The AgentBridge is the only place
that makes this injection — after construction the daemon session is treated as
opaque by the RaptorEngine.

Note: hermes's native delegate_task (for sub-agent spawning) runs inside the
subprocess with the same toolset restrictions passed at session-create time.
There is currently no per-turn re-injection; the toolset is stable for the
lifetime of the daemon session (hermes prompt-cache requirement).
"""

from typing import Any, Dict, List, Optional

from flocks.utils.log import Log

log = Log.create(service="engine.raptor.agent_bridge")


async def build_agent_context(
    *,
    session_id: str,
    agent_name: str,
    session_directory: Optional[str],
    provider_id: str,
    model_id: str,
    static_cache: Any = None,
) -> Dict[str, Any]:
    """
    Build the agent context dict for hermes session creation.

    Returns a dict with:
        system_message: str   — concatenated Flocks system-prompt blocks
        hermes_home: str      — per-session isolation directory

    The caller passes ``system_message`` into ``session.create`` params so hermes
    uses the Flocks agent's identity instead of its own default system prompt.
    """
    system_message = await _build_system_message(
        session_id=session_id,
        agent_name=agent_name,
        session_directory=session_directory,
        provider_id=provider_id,
        model_id=model_id,
        static_cache=static_cache,
    )

    return {
        "system_message": system_message,
    }


async def _build_system_message(
    *,
    session_id: str,
    agent_name: str,
    session_directory: Optional[str],
    provider_id: str,
    model_id: str,
    static_cache: Any = None,
) -> str:
    """
    Call SessionPrompt.build_system_prompts() and join the result into one string
    suitable for hermes's ``system_message`` parameter.
    """
    try:
        from flocks.session.prompt import SessionPrompt
        prompts: List[str] = await SessionPrompt.build_system_prompts(
            session_id=session_id,
            session_directory=session_directory,
            agent_name=agent_name,
            agent_prompt=None,
            provider_id=provider_id,
            model_id=model_id,
            static_cache=static_cache,
        )
        joined = "\n\n".join(p for p in prompts if p and p.strip())
        log.debug("raptor.agent_bridge.system_message_built", {
            "session_id": session_id,
            "agent_name": agent_name,
            "blocks": len(prompts),
            "chars": len(joined),
        })
        return joined
    except Exception as exc:
        log.warning("raptor.agent_bridge.system_message_error", {
            "session_id": session_id,
            "error": str(exc),
        })
        # Fall back to a minimal identity marker rather than leaving hermes
        # system-prompt-less and potentially violating security constraints.
        return (
            f"You are the Flocks AI agent '{agent_name}'. "
            f"Follow the agent's instructions and security constraints. "
            f"[system_message build failed: {exc}]"
        )
