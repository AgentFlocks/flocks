"""
delegate_task tool - category or subagent-based delegation (Oh-My-Flocks parity).
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any

from flocks.tool.registry import (
    ToolRegistry,
    ToolCategory,
    ToolParameter,
    ParameterType,
    ToolResult,
    ToolContext,
)
from flocks.tool.delegate_task_constants import (
    DEFAULT_CATEGORIES,
    CATEGORY_PROMPT_APPENDS,
    CATEGORY_DESCRIPTIONS,
)
from flocks.session.session import Session
from flocks.session.message import Message, MessageRole
from flocks.session.session_loop import SessionLoop
# 使用轻量级元数据查询，避免循环依赖
from flocks.agent.registry import is_delegatable
from flocks.pentest.policy import PentestDelegationPolicy
from flocks.pentest.store import PentestStateError, PentestStateStore
from flocks.skill.skill import Skill
from flocks.config.config import Config
from flocks.tool.subagent_result import format_sync_subagent_result
from flocks.utils.log import Log

log = Log.create(service="tool.delegate_task")


async def _subagent_session_permissions(agent_name: str) -> list:
    """Build session permission rules for a delegated subagent."""
    from flocks.agent.registry import Agent
    from flocks.session.session import PermissionRule as SessionPermissionRule

    try:
        agent = await Agent.get(agent_name)
    except Exception as exc:
        log.debug("delegate_task.subagent_permission_agent_load_failed", {
            "agent": agent_name,
            "error": str(exc),
        })
        agent = None
    rules: list = []
    if agent_name != "prometheus":
        rules.append(SessionPermissionRule(permission="question", action="deny", pattern="*"))

    agent_permissions = getattr(agent, "permission", None)
    if agent and agent_permissions:
        for rule in agent_permissions:
            raw_level = getattr(rule, "level", None) or getattr(rule, "action", None) or "allow"
            level = raw_level.value if hasattr(raw_level, "value") else str(raw_level)
            rules.append(
                SessionPermissionRule(
                    permission=getattr(rule, "permission", None) or "*",
                    action=level,
                    pattern=getattr(rule, "pattern", None) or "*",
                )
            )
        return rules

    if agent_name == "prometheus":
        rules.extend([
            SessionPermissionRule(permission="question", action="allow", pattern="*"),
            SessionPermissionRule(permission="edit", action="deny", pattern="*"),
            SessionPermissionRule(permission="edit", action="allow", pattern=".flocks/plans/*"),
        ])
    elif not rules:
        rules.append(SessionPermissionRule(permission="question", action="deny", pattern="*"))
    return rules


def _parse_model(model: Optional[str]) -> Optional[Dict[str, str]]:
    if not model:
        return None
    if "/" in model:
        provider_id, model_id = model.split("/", 1)
        return {"providerID": provider_id, "modelID": model_id}
    return {"modelID": model}


def _validate_category_model(category_model: Optional[Dict[str, str]], category: Optional[str]) -> Optional[Dict[str, str]]:
    """Validate that the category model's provider is available and has the model registered.

    Returns the original model dict when valid, or None to signal the caller
    should fall back to the parent session's model (via _resolve_model priority chain).
    """
    if not category_model:
        return None

    provider_id = category_model.get("providerID")
    model_id = category_model.get("modelID")
    if not provider_id or not model_id:
        return category_model

    try:
        from flocks.provider.provider import Provider
        provider = Provider.get(provider_id)
        if not provider:
            log.warn("delegate_task.category_model_fallback", {
                "category": category,
                "provider": provider_id,
                "model": model_id,
                "reason": "provider not registered",
            })
            return None

        if not provider.is_configured():
            log.warn("delegate_task.category_model_fallback", {
                "category": category,
                "provider": provider_id,
                "model": model_id,
                "reason": "provider not configured",
            })
            return None

        registered_ids = {m.id for m in provider.get_models()}
        if model_id not in registered_ids:
            log.warn("delegate_task.category_model_fallback", {
                "category": category,
                "provider": provider_id,
                "model": model_id,
                "reason": "model not found in provider",
                "available_models": list(registered_ids)[:10],
            })
            return None

    except Exception as exc:
        log.warn("delegate_task.category_model_validate_error", {
            "category": category,
            "error": str(exc),
        })
        return None

    return category_model


async def _find_completed_delegate(
    session_id: str,
    current_message_id: str,
    agent_key: Optional[str],
    description: str,
) -> Optional[ToolResult]:
    """Return a previous ToolResult if an identical delegate_task already completed."""
    try:
        from flocks.session.message import ToolPart
        messages = await Message.list(session_id)
        for msg in messages:
            if msg.id == current_message_id:
                continue
            parts = await Message.parts(msg.id, session_id)
            for p in parts:
                if not isinstance(p, ToolPart):
                    continue
                if p.tool != "delegate_task":
                    continue
                state = p.state
                if getattr(state, "status", None) != "completed":
                    continue
                inp = getattr(state, "input", {})
                prev_key = inp.get("subagent_type") or inp.get("category")
                if prev_key == agent_key and inp.get("description") == description:
                    output = getattr(state, "output", "")
                    if isinstance(output, dict):
                        import json as _json
                        output = _json.dumps(output, ensure_ascii=False)
                    meta = getattr(state, "metadata", {}) or {}
                    return ToolResult(
                        success=True,
                        output=f"[Already completed — returning previous result]\n\n{output}",
                        title=description,
                        metadata=meta,
                    )
    except Exception as exc:
        log.debug("delegate_task.dedup_check_failed", {"error": str(exc)})
    return None


async def _resolve_skill_content(skill_names: List[str]) -> Dict[str, Any]:
    skill_names = [str(name).strip() for name in (skill_names or []) if str(name).strip()]
    if len(skill_names) == 0:
        return {"content": None, "error": None}
    resolved: List[str] = []
    missing: List[str] = []
    for name in skill_names:
        skill = await Skill.get(name)
        # Treat disabled skills the same as missing ones — do not reveal to the
        # LLM that the skill exists but is toggled off, as that would invite it
        # to retry via a different code path.
        if not skill or Skill.is_disabled(skill.name):
            missing.append(name)
            continue
        try:
            with open(skill.location, "r", encoding="utf-8") as f:
                resolved.append(f.read())
        except Exception as exc:
            return {"content": None, "error": f"Failed to load skill {name}: {exc}"}
    if missing:
        # Only surface enabled skills to the LLM — listing disabled ones in
        # an error message would invite the model to retry with them.
        all_skills = await Skill.list_enabled()
        available = ", ".join(s.name for s in all_skills) or "none"
        return {"content": None, "error": f"Skills not found: {', '.join(missing)}. Available: {available}"}
    return {"content": "\n\n".join(resolved), "error": None}


def _derive_task_description(
    description: Optional[str],
    prompt: str,
    subagent_type: Optional[str] = None,
    category: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    normalized = " ".join((description or "").split())
    if normalized:
        return normalized

    prompt_line = " ".join((prompt or "").split())
    if prompt_line:
        return prompt_line[:57].rstrip() + "..." if len(prompt_line) > 60 else prompt_line

    if subagent_type:
        return f"delegate to {subagent_type}"
    if category:
        return f"delegate {category} task"
    if session_id:
        return f"continue task {session_id}"
    return "delegate task"


# ------------------------------------------------------------------
# Tool definition
# ------------------------------------------------------------------

DESCRIPTION = """Spawn agent task with category-based or direct agent selection. "

Use this tool when:
- The task requires multiple steps or research
- You need to explore code in parallel
- The task can be delegated to a specialized agent

Usage notes:
- Provide a clear description (3-5 words)
- Provide detailed prompt with context
- Pass session_id to continue a previous agent with full context
- Background subagent execution is disabled. Do not set run_in_background=true.
- Foreground execution is always used: the tool waits for completion and returns results inline.
- For independent parallel work needed this turn, emit multiple sibling
  foreground delegate_task/task tool calls in the same assistant response.
  The runtime executes them concurrently and the webui renders each as its
  own DelegateTaskCard.

REQUIRED: prompt.
LOAD_SKILLS is optional and defaults to [].
DESCRIPTION is optional and will be auto-derived when omitted.
USE EITHER subagent_type OR category — NEVER both simultaneously.
"""

@ToolRegistry.register_function(
    name="delegate_task",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="load_skills",
            type=ParameterType.ARRAY,
            description="Optional. Skill names to inject into the agent. Defaults to []. Omit for direct subagent delegation unless specific skills are clearly needed.",
            required=False,
            default=[],
        ),
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description="Optional. Short task description (3-5 words). If omitted, one will be derived from the prompt.",
            required=False,
        ),
        ToolParameter(
            name="prompt",
            type=ParameterType.STRING,
            description="Full detailed prompt for the subagent.",
            required=True,
        ),
        ToolParameter(
            name="category",
            type=ParameterType.STRING,
            description="Category name. Mutually exclusive with subagent_type — use ONE or the other, never both.",
            required=False,
        ),
        ToolParameter(
            name="subagent_type",
            type=ParameterType.STRING,
            description="Agent name. Mutually exclusive with category — use ONE or the other, never both. Must be a delegatable agent",
            required=False,
        ),
        ToolParameter(
            name="session_id",
            type=ParameterType.STRING,
            description="Existing task session to continue",
            required=False,
        ),
        ToolParameter(
            name="command",
            type=ParameterType.STRING,
            description="Optional command name for tracking",
            required=False,
        ),
        ToolParameter(
            name="model",
            type=ParameterType.STRING,
            description="Optional model override (provider/model or model)",
            required=False,
        ),
    ],
)
async def delegate_task_tool(
    ctx: ToolContext,
    prompt: Optional[str] = None,
    load_skills: Optional[List[str]] = None,
    description: Optional[str] = None,
    # Internal-only: not exposed in the public schema. The registry rejects
    # `run_in_background=True` at the schema layer for any caller, but legacy
    # in-process call paths (e.g. `task.py` alias) may still pass it through.
    # This guard is the second line of defense.
    run_in_background: bool = False,
    category: Optional[str] = None,
    subagent_type: Optional[str] = None,
    session_id: Optional[str] = None,
    command: Optional[str] = None,
    model: Optional[str] = None,
) -> ToolResult:
    if run_in_background:
        return ToolResult(
            success=False,
            error=(
                "Background subagent execution is disabled. "
                "Use foreground delegate_task/task calls; emit multiple sibling calls "
                "in the same assistant turn for parallel work."
            ),
        )

    if not prompt:
        return ToolResult(success=False, error="prompt is required")

    load_skills = [str(name).strip() for name in (load_skills or []) if str(name).strip()]
    description = _derive_task_description(description, prompt, subagent_type, category, session_id)
    if category and subagent_type:
        return ToolResult(success=False, error="Provide EITHER category OR subagent_type, not both.")
    if not category and not subagent_type and not session_id:
        return ToolResult(success=False, error="Must provide either category or subagent_type.")

    parent_session = await Session.get_by_id(ctx.session_id)
    if not parent_session:
        return ToolResult(success=False, error="Parent session not found")

    await ctx.ask(
        permission="delegate_task",
        patterns=[category or subagent_type or "continue"],
        always=["*"],
        metadata={"description": description, "category": category, "subagent_type": subagent_type},
    )

    # Dedup: if an identical delegate_task already completed in this session,
    # return the previous result to prevent the LLM from re-delegating.
    if not session_id:
        agent_key = subagent_type or category
        prev = await _find_completed_delegate(ctx.session_id, ctx.message_id, agent_key, description)
        if prev is not None:
            log.info("delegate_task.dedup_hit", {
                "session_id": ctx.session_id,
                "agent_key": agent_key,
                "description": description,
            })
            return prev

    skill_result = await _resolve_skill_content(load_skills)
    if skill_result["error"]:
        return ToolResult(success=False, error=skill_result["error"])

    cfg = await Config.get()
    category_configs = {**DEFAULT_CATEGORIES, **(cfg.categories or {})}
    category_prompt_append = None
    category_model = None
    explicit_model = _parse_model(model)
    agent_to_use: Optional[str] = None

    if session_id:
        # Sync continuation
        session = await Session.get_by_id(session_id)
        if not session:
            return ToolResult(success=False, error=f"Session {session_id} not found")
        pentest_store = None
        if getattr(parent_session, "session_mode", "chat") == "pentest":
            if session.pentest_run_id != parent_session.pentest_run_id:
                return ToolResult(
                    success=False,
                    error="Cannot continue a Session from another pentest run",
                )
            if session.parent_id != parent_session.id:
                return ToolResult(
                    success=False,
                    error="Pentest agents may continue only their direct child Sessions",
                )
            try:
                pentest_store = PentestStateStore.from_session(parent_session)
                pentest_store.record_agent_resumed(
                    session_id=session.id,
                    task=description,
                )
            except PentestStateError as exc:
                return ToolResult(success=False, error=str(exc))
        await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content=prompt,
            agent=session.agent or ctx.agent,
        )
        from flocks.session.session_loop import LoopCallbacks
        try:
            result = await SessionLoop.run(
                session.id,
                callbacks=LoopCallbacks(
                    event_publish_callback=ctx.event_publish_callback,
                ),
            )
        except Exception as exc:
            if pentest_store is not None:
                pentest_store.record_agent_finished(
                    session_id=session.id,
                    status="interrupted",
                    error=str(exc),
                )
            raise
        ctx.metadata({"title": f"Continue: {description}", "metadata": {"sessionId": session.id}})
        tool_result = await format_sync_subagent_result(
            description=description,
            session_id=session.id,
            loop_result=result,
            metadata={"sessionId": session.id},
        )
        if pentest_store is not None:
            pentest_store.record_agent_finished(
                session_id=session.id,
                status="completed" if tool_result.success else "interrupted",
                error=None if tool_result.success else tool_result.error,
            )
        return tool_result

    if category:
        agent_to_use = "rex-junior"
        config = category_configs.get(category)
        if not config:
            available = ", ".join(category_configs.keys())
            return ToolResult(success=False, error=f'Unknown category "{category}". Available: {available}')
        raw_model = explicit_model or _parse_model(config.get("model") if isinstance(config, dict) else getattr(config, "model", None))
        category_model = _validate_category_model(raw_model, category)
        if raw_model and not category_model:
            log.info("delegate_task.using_parent_model", {
                "category": category,
                "original_model": raw_model,
                "reason": "category model unavailable, inheriting parent session model",
            })
        category_prompt_append = (
            (config.get("prompt_append") if isinstance(config, dict) else getattr(config, "prompt_append", None))
            or CATEGORY_PROMPT_APPENDS.get(category)
        )
    elif subagent_type:
        # 使用轻量级元数据查询，避免循环依赖
        # 不再调用 Agent.get()，而是使用 is_delegatable()
        if not is_delegatable(subagent_type):
            # 针对特殊 Agent 提供更友好的错误提示
            if subagent_type.lower() in ["sisyphus-junior", "rex-junior"]:
                return ToolResult(
                    success=False,
                    error=f'Cannot use subagent_type="{subagent_type}" directly. Use category parameter instead.',
                )
            else:
                return ToolResult(
                    success=False,
                    error=f'Agent "{subagent_type}" cannot be delegated to (it may be a primary agent or restricted).',
                )
        agent_to_use = subagent_type
        category_model = explicit_model

    system_parts = []
    if skill_result["content"]:
        system_parts.append(skill_result["content"])
    if category_prompt_append:
        system_parts.append(category_prompt_append)
    system_content = "\n\n".join(system_parts) if system_parts else ""
    full_prompt = f"{system_content}\n\n{prompt}" if system_content else prompt

    # Sync execution
    pentest_role = None
    pentest_finding_id = None
    pentest_store = None
    if getattr(parent_session, "session_mode", "chat") == "pentest":
        if category:
            return ToolResult(
                success=False,
                error="Pentest sessions must delegate to an explicit pentest agent",
            )
        try:
            pentest_role, pentest_finding_id = PentestDelegationPolicy.validate(
                parent_session=parent_session,
                child_agent=agent_to_use or "",
                prompt=prompt,
                skills=load_skills,
            )
            pentest_store = PentestStateStore.from_session(parent_session)
        except PentestStateError as exc:
            return ToolResult(success=False, error=str(exc))

    from flocks.project.instance import Instance

    runtime_directory = Instance.get_directory() or parent_session.directory

    create_kwargs = dict(
        project_id=parent_session.project_id,
        directory=runtime_directory,
        title=f"{description} (@{agent_to_use} subagent)",
        parent_id=parent_session.id,
        agent=agent_to_use,
        permission=await _subagent_session_permissions(agent_to_use),
        category="task",
    )
    if pentest_store is not None:
        create_kwargs.update(
            session_mode="pentest",
            pentest_run_id=parent_session.pentest_run_id,
            pentest_root=parent_session.pentest_root,
            pentest_role=pentest_role,
            pentest_config=parent_session.pentest_config,
            metadata={
                "pentest_finding_id": pentest_finding_id,
                "pentest_task": description,
                "pentest_skills": load_skills,
            },
        )
    if category_model and category_model.get("providerID") and category_model.get("modelID"):
        create_kwargs.update(
            provider=category_model["providerID"],
            model=category_model["modelID"],
            model_pinned=bool(explicit_model),
        )
    created = await Session.create(**create_kwargs)
    if pentest_store is not None:
        try:
            pentest_store.record_agent_started(
                session_id=created.id,
                parent_session_id=parent_session.id,
                role=pentest_role or "",
                task=description,
                skills=load_skills,
            )
        except PentestStateError as exc:
            await Session.delete(created.project_id, created.id)
            return ToolResult(success=False, error=str(exc))
    if ctx.extra.get("workflow_temp_parent") is True:
        ctx.extra["workflow_child_session_created"] = True
    await Message.create(
        session_id=created.id,
        role=MessageRole.USER,
        content=full_prompt,
        agent=agent_to_use,
    )
    from flocks.session.features.activity_forwarder import ActivityForwarder

    forwarder = ActivityForwarder(
        parent_ctx=ctx,
        child_session_id=created.id,
        description=description,
    )
    ctx.metadata({"title": description, "metadata": {"sessionId": created.id, "status": "running"}})
    try:
        result = await SessionLoop.run(
            created.id,
            provider_id=(category_model or {}).get("providerID"),
            model_id=(category_model or {}).get("modelID"),
            callbacks=forwarder.build_callbacks(
                event_publish_callback=ctx.event_publish_callback,
            ),
        )
    except Exception as exc:
        if pentest_store is not None:
            pentest_store.record_agent_finished(
                session_id=created.id,
                status="interrupted",
                error=str(exc),
            )
        raise
    tool_result = await format_sync_subagent_result(
        description=description,
        session_id=created.id,
        loop_result=result,
        metadata=forwarder.final_metadata,
    )
    result_status = "completed" if tool_result.success else "error"
    if pentest_store is not None:
        pentest_store.record_agent_finished(
            session_id=created.id,
            status="completed" if tool_result.success else "interrupted",
            error=None if tool_result.success else tool_result.error,
        )
    ctx.metadata({"title": description, "metadata": {**forwarder.final_metadata, "status": result_status}})
    return tool_result
