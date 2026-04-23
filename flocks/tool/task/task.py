"""
Task Tool - Subagent execution

Launches specialized subagents for complex, multi-step tasks.
Supports both synchronous (blocking) and background (async) execution.

Model resolution priority for child sessions:
  1. Explicit ``model`` param (WebUI override, format: "provider/model" or "model")
  2. Agent-specific model from AgentInfo.model (set in flocks.json agent config)
  3. Parent session's pinned model/provider
  4. Global default LLM (``default_models.llm`` in config)
  5. Environment / hardcoded fallback
"""

from typing import Optional, Dict, Tuple

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.agent.registry import is_delegatable
from flocks.task.background import get_background_manager, LaunchInput, ResumeInput
from flocks.session.session import Session
from flocks.session.message import Message, MessageRole
from flocks.session.session_loop import SessionLoop
from flocks.utils.log import Log


log = Log.create(service="tool.task")


# ------------------------------------------------------------------
# Model resolution helpers
# ------------------------------------------------------------------

async def _resolve_child_model(
    agent_name: str,
    parent_session,
    model_override: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], str]:
    """Resolve ``(provider_id, model_id, source)`` for a child subagent session.

    Priority:
      1. ``model_override`` — explicit param from WebUI (format "provider/model" or "model")
      2. Agent model override from Storage (set via WebUI)
      3. Agent-specific model from ``AgentInfo.model``
      4. Parent session's pinned model/provider
      5. Global default LLM from config
    """
    provider: Optional[str] = None
    model: Optional[str] = None
    source = "unknown"

    # 1. Explicit override (WebUI)
    if model_override:
        if "/" in model_override:
            provider, model = model_override.split("/", 1)
        else:
            model = model_override
        source = "explicit"

    # 2. Agent model override from Storage (set via WebUI)
    if not model:
        try:
            from flocks.storage.storage import Storage
            overrides = await Storage.read("agent/model_overrides")
            if isinstance(overrides, dict) and agent_name in overrides:
                override = overrides[agent_name]
                override_provider = override.get('providerID')
                override_model = override.get('modelID')
                if override_provider and override_model:
                    provider = override_provider
                    model = override_model
                    source = "agent_override"
        except Exception:
            pass

    # 3. Agent-level model from registry
    if not model:
        try:
            from flocks.agent.registry import Agent
            agent_info = await Agent.get(agent_name)
            if agent_info and agent_info.model:
                provider = provider or agent_info.model.provider_id
                model = agent_info.model.model_id
                source = "agent"
        except Exception:
            pass

    # 4. Inherit only explicit parent pins
    if (not model or not provider) and parent_session and Session.has_pinned_model(parent_session):
        provider = provider or getattr(parent_session, "provider", None)
        model = model or getattr(parent_session, "model", None)
        if provider and model:
            source = "parent_session"

    # 5. Global default LLM
    if not model or not provider:
        try:
            from flocks.config.config import Config
            default_llm = await Config.resolve_default_llm()
            if default_llm:
                provider = provider or default_llm.get("provider_id")
                model = model or default_llm.get("model_id")
                if provider and model and source == "unknown":
                    source = "config"
        except Exception:
            pass

    return provider, model, source


def _model_dict(provider: Optional[str], model: Optional[str]) -> Optional[Dict[str, str]]:
    """Build the ``model`` dict expected by ``LaunchInput``."""
    if not provider and not model:
        return None
    d: Dict[str, str] = {}
    if provider:
        d["providerID"] = provider
    if model:
        d["modelID"] = model
    return d


# ------------------------------------------------------------------
# Tool definition
# ------------------------------------------------------------------

DESCRIPTION = """Launch a new agent to handle complex, multi-step tasks autonomously.

Use this tool when:
- The task requires multiple steps or research
- You need to explore code in parallel
- The task can be delegated to a specialized agent

Available subagent types:
- hephaestus: Autonomous deep worker for goal-oriented, end-to-end task execution
- explore: Fast code exploration agent for quick searches
- librarian: Specialized codebase understanding, remote codebases, official documentation
- hephaestus: Autonomous deep worker for goal-oriented execution
- oracle: Read-only consultation, debugging hard problems, architecture design

Usage notes:
- Provide a clear description (3-5 words)
- Provide detailed prompt with context
- run_in_background=true: returns task_id immediately, collect results later with background_output
- run_in_background=false: waits for completion and returns results inline
- Pass session_id to continue a previous agent with full context"""


@ToolRegistry.register_function(
    name="task",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description="A short (3-5 words) description of the task",
            required=True,
        ),
        ToolParameter(
            name="prompt",
            type=ParameterType.STRING,
            description="The task for the agent to perform",
            required=True,
        ),
        ToolParameter(
            name="subagent_type",
            type=ParameterType.STRING,
            description="The type of specialized agent to use",
            required=True,
        ),
        ToolParameter(
            name="run_in_background",
            type=ParameterType.BOOLEAN,
            description="true=async (returns task_id, collect with background_output), false=sync (waits for result)",
            required=False,
        ),
        ToolParameter(
            name="session_id",
            type=ParameterType.STRING,
            description="Existing task session to continue (preserves full context)",
            required=False,
        ),
        ToolParameter(
            name="model",
            type=ParameterType.STRING,
            description="Optional model override for the subagent (format: 'provider/model' or 'model')",
            required=False,
        ),
    ],
)
async def task_tool(
    ctx: ToolContext,
    description: str,
    prompt: str,
    subagent_type: str,
    run_in_background: Optional[bool] = False,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
) -> ToolResult:
    if not description:
        return ToolResult(success=False, error="description is required")
    if not prompt:
        return ToolResult(success=False, error="prompt is required")

    normalized = subagent_type.lower() if subagent_type else ""
    if not normalized:
        return ToolResult(success=False, error="subagent_type is required")

    if not is_delegatable(normalized):
        return ToolResult(
            success=False,
            error=f'Agent "{subagent_type}" cannot be delegated to (it may be a primary agent or restricted).',
        )

    await ctx.ask(
        permission="task",
        patterns=[normalized],
        always=["*"],
        metadata={"description": description, "subagent_type": normalized},
    )

    # Resolve parent session once (needed for model inheritance + session creation)
    parent_session = await Session.get_by_id(ctx.session_id)

    # Resolve effective model for the child agent
    child_provider, child_model, child_source = await _resolve_child_model(
        normalized, parent_session, model_override=model,
    )
    child_model_pinned = (
        child_source in {"explicit", "parent_session"}
        and bool(child_provider and child_model)
    )

    log.info("task.model_resolved", {
        "subagent": normalized,
        "provider": child_provider,
        "model": child_model,
        "source": child_source,
        "model_pinned": child_model_pinned,
        "override": model,
        "parent_provider": getattr(parent_session, "provider", None) if parent_session else None,
        "parent_model": getattr(parent_session, "model", None) if parent_session else None,
    })

    # --- Background resume of existing session ---
    if session_id and run_in_background:
        manager = get_background_manager()
        task = await manager.resume(
            ResumeInput(
                session_id=session_id,
                prompt=prompt,
                parent_session_id=ctx.session_id,
                parent_message_id=ctx.message_id,
                parent_agent=ctx.agent,
            )
        )
        ctx.metadata({"title": f"Continue: {description}", "metadata": {"sessionId": task.session_id}})
        output = (
            "Background task continued.\n\n"
            f"Task ID: {task.id}\n"
            f"Description: {task.description}\n"
            f"Agent: {task.agent}\n"
            f"Status: {task.status}\n\n"
            f'Use `background_output` with task_id="{task.id}" to check progress.\n\n'
            f"<task_metadata>\nsession_id: {task.session_id}\n</task_metadata>"
        )
        return ToolResult(success=True, output=output, title=description, metadata={"sessionId": task.session_id})

    # --- Sync continue of existing session ---
    if session_id:
        session = await Session.get_by_id(session_id)
        if not session:
            return ToolResult(success=False, error=f"Session {session_id} not found")
        await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content=prompt,
            agent=session.agent or normalized,
        )
        from flocks.session.session_loop import LoopCallbacks as _LoopCbs
        result = await SessionLoop.run(
            session.id,
            callbacks=_LoopCbs(event_publish_callback=ctx.event_publish_callback),
        )
        output_text = ""
        if result.last_message:
            output_text = await Message.get_text_content(result.last_message)
        output = f"{output_text}\n\n<task_metadata>\nsession_id: {session.id}\n</task_metadata>"
        ctx.metadata({"title": f"Continue: {description}", "metadata": {"sessionId": session.id}})
        return ToolResult(success=True, output=output, title=description, metadata={"sessionId": session.id})

    # --- Background launch (new session) ---
    if run_in_background:
        manager = get_background_manager()
        task = await manager.launch(
            LaunchInput(
                description=description,
                prompt=prompt,
                agent=normalized,
                parent_session_id=ctx.session_id,
                parent_message_id=ctx.message_id,
                parent_agent=ctx.agent,
                model=_model_dict(child_provider, child_model) if child_model_pinned else None,
                model_pinned=child_model_pinned,
            )
        )
        ctx.metadata({"title": description, "metadata": {"sessionId": task.session_id}})
        output = (
            "Background task launched successfully.\n\n"
            f"Task ID: {task.id}\n"
            f"Description: {task.description}\n"
            f"Agent: {task.agent}\n"
            f"Status: {task.status}\n\n"
            f'Use `background_output` with task_id="{task.id}" to check progress.\n\n'
            f"<task_metadata>\nsession_id: {task.session_id}\n</task_metadata>"
        )
        return ToolResult(success=True, output=output, title=description, metadata={"sessionId": task.session_id})

    # --- Sync launch (new session) ---
    if not parent_session:
        return ToolResult(success=False, error="Parent session not found")

    try:
        create_kwargs = dict(
            project_id=parent_session.project_id,
            directory=parent_session.directory,
            title=f"{description} (@{normalized} subagent)",
            parent_id=parent_session.id,
            agent=normalized,
            permission=[{"permission": "question", "action": "deny", "pattern": "*"}],
            category="task",
        )
        if child_model_pinned:
            create_kwargs.update(
                model=child_model,
                provider=child_provider,
                model_pinned=True,
            )
        created = await Session.create(**create_kwargs)
        await Message.create(
            session_id=created.id,
            role=MessageRole.USER,
            content=prompt,
            agent=normalized,
        )

        from flocks.session.features.activity_forwarder import ActivityForwarder

        forwarder = ActivityForwarder(
            parent_ctx=ctx,
            child_session_id=created.id,
            description=description,
        )
        ctx.metadata({"title": description, "metadata": {"sessionId": created.id, "status": "running"}})

        result = await SessionLoop.run(
            created.id,
            callbacks=forwarder.build_callbacks(
                event_publish_callback=ctx.event_publish_callback,
            ),
        )
        output_text = ""
        if result.last_message:
            output_text = await Message.get_text_content(result.last_message)
        output = f"{output_text}\n\n<task_metadata>\nsession_id: {created.id}\n</task_metadata>"
        ctx.metadata({"title": description, "metadata": forwarder.final_metadata})
        return ToolResult(success=True, output=output, title=description, metadata=forwarder.final_metadata)

    except Exception as e:
        log.error("task.execute.error", {"error": str(e)})
        return ToolResult(success=False, error=f"Task execution failed: {str(e)}", title=description)
