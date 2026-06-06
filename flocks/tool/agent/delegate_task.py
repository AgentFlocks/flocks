"""
delegate_task tool - category or subagent-based delegation (Oh-My-Flocks parity).
"""

from __future__ import annotations

import asyncio
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
from flocks.task.background import get_background_manager, ResumeInput
from flocks.session.session import Session
from flocks.session.message import Message, MessageRole
from flocks.session.session_loop import SessionLoop
# 使用轻量级元数据查询，避免循环依赖
from flocks.agent.registry import is_delegatable
from flocks.skill.skill import Skill
from flocks.config.config import Config
from flocks.tool.subagent_result import format_sync_subagent_result
from flocks.utils.log import Log

log = Log.create(service="tool.delegate_task")


async def _subagent_session_permissions(agent_name: str) -> list:
    """Build session permission rules for a delegated subagent."""
    from flocks.agent.registry import Agent
    from flocks.session.session import PermissionRule as SessionPermissionRule

    def deny_nested_delegation() -> list:
        return [
            SessionPermissionRule(permission="delegate_task", action="deny", pattern="*"),
            SessionPermissionRule(permission="task", action="deny", pattern="*"),
        ]

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
        rules.extend(deny_nested_delegation())
        return rules

    if agent_name == "prometheus":
        rules.extend([
            SessionPermissionRule(permission="question", action="allow", pattern="*"),
            SessionPermissionRule(permission="edit", action="deny", pattern="*"),
            SessionPermissionRule(permission="edit", action="allow", pattern=".flocks/plans/*"),
        ])
    elif not rules:
        rules.append(SessionPermissionRule(permission="question", action="deny", pattern="*"))
    rules.extend(deny_nested_delegation())
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


async def _run_delegate_task_batch(
    *,
    ctx: ToolContext,
    tasks: List[Dict[str, Any]],
    prompt: Optional[str],
    load_skills: Optional[List[str]],
    description: Optional[str],
    run_in_background: Optional[bool],
    category: Optional[str],
    subagent_type: Optional[str],
    session_id: Optional[str],
    command: Optional[str],
    model: Optional[str],
) -> ToolResult:
    """Run independent delegate_task items concurrently."""
    if not isinstance(tasks, list) or not tasks:
        return ToolResult(success=False, error="tasks must be a non-empty array")

    max_concurrency = min(max(len(tasks), 1), 3)
    semaphore = asyncio.Semaphore(max_concurrency)
    child_states: List[Dict[str, Any]] = [
        {
            "index": idx,
            "description": str(item.get("description") or description or f"subtask {idx + 1}"),
            "status": "pending",
        }
        for idx, item in enumerate(tasks)
    ]

    def publish_batch_metadata() -> None:
        ctx.metadata({
            "title": description or f"{len(tasks)} parallel subagents",
            "metadata": {
                "parallel": True,
                "children": child_states,
            },
        })

    async def run_one(idx: int, item: Dict[str, Any]) -> ToolResult:
        if not isinstance(item, dict):
            child_states[idx]["status"] = "error"
            child_states[idx]["error"] = "task item must be an object"
            publish_batch_metadata()
            return ToolResult(success=False, error="task item must be an object")

        item_prompt = item.get("prompt") or prompt
        item_category = item.get("category") or category
        item_subagent = item.get("subagent_type") or subagent_type
        item_session = item.get("session_id") or session_id
        item_description = item.get("description") or description
        item_skills = item.get("load_skills", load_skills)
        item_command = item.get("command") or command
        item_model = item.get("model") or model

        if not item_prompt:
            child_states[idx]["status"] = "error"
            child_states[idx]["error"] = "prompt is required"
            publish_batch_metadata()
            return ToolResult(success=False, error="prompt is required")

        child_states[idx]["status"] = "running"
        publish_batch_metadata()

        def child_metadata_callback(metadata: Dict[str, Any]) -> None:
            child_states[idx]["metadata"] = dict(metadata)
            if metadata.get("sessionId"):
                child_states[idx]["sessionId"] = metadata.get("sessionId")
            if metadata.get("status"):
                child_states[idx]["status"] = metadata.get("status")
            publish_batch_metadata()

        child_ctx = ToolContext(
            session_id=ctx.session_id,
            message_id=ctx.message_id,
            agent=ctx.agent,
            call_id=f"{ctx.call_id or 'delegate_task'}:{idx}",
            extra=dict(ctx.extra),
            abort_event=ctx.abort,
            permission_callback=ctx._permission_callback,
            metadata_callback=child_metadata_callback,
            event_publish_callback=ctx.event_publish_callback,
        )

        async with semaphore:
            try:
                result = await delegate_task_tool(
                    child_ctx,
                    prompt=str(item_prompt),
                    load_skills=item_skills,
                    description=item_description,
                    run_in_background=bool(run_in_background),
                    category=item_category,
                    subagent_type=item_subagent,
                    session_id=item_session,
                    command=item_command,
                    model=item_model,
                )
            except Exception as exc:
                result = ToolResult(success=False, error=str(exc))

        child_states[idx]["status"] = (
            "running" if result.success and run_in_background else "completed" if result.success else "error"
        )
        child_states[idx]["title"] = result.title
        child_states[idx]["output"] = result.output
        if result.error:
            child_states[idx]["error"] = result.error
        if result.metadata.get("sessionId"):
            child_states[idx]["sessionId"] = result.metadata["sessionId"]
        publish_batch_metadata()
        return result

    results = await asyncio.gather(
        *(run_one(idx, item) for idx, item in enumerate(tasks)),
    )
    output_sections = []
    for idx, result in enumerate(results):
        label = child_states[idx].get("description") or f"subtask {idx + 1}"
        body = result.output if result.success else result.error
        output_sections.append(f"## {label}\n\n{body or '(no output)'}")

    success = all(result.success for result in results)
    title = description or f"{len(tasks)} parallel subagents"
    if run_in_background:
        title = f"{title} (background)"
    return ToolResult(
        success=success,
        output="\n\n".join(output_sections),
        title=title,
        metadata={
            "parallel": True,
            "background": bool(run_in_background),
            "children": child_states,
        },
    )

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
- For independent parallel work needed this turn, prefer OpenCode-style sibling
  tool calls: emit multiple foreground delegate_task/task calls in the same
  assistant response. The runtime executes them concurrently.
- tasks=[...] remains supported for workflow/backward compatibility.

REQUIRED: prompt, unless tasks is provided.
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
            description="Full detailed prompt for the agent. Required unless tasks is provided.",
            required=False,
        ),
        ToolParameter(
            name="tasks",
            type=ParameterType.ARRAY,
            description=(
                "Optional compatibility batch of independent subagent tasks. "
                "Prefer multiple sibling foreground delegate_task/task tool calls "
                "for OpenCode-style parallelism. Each item may include prompt, "
                "description, category, subagent_type, session_id, command, and load_skills."
            ),
            required=False,
            json_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "description": {"type": "string"},
                        "category": {"type": "string"},
                        "subagent_type": {"type": "string"},
                        "session_id": {"type": "string"},
                        "command": {"type": "string"},
                        "model": {"type": "string"},
                        "load_skills": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "additionalProperties": False,
                },
                "minItems": 1,
            },
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
    tasks: Optional[List[Dict[str, Any]]] = None,
    load_skills: Optional[List[str]] = None,
    description: Optional[str] = None,
    run_in_background: Optional[bool] = False,
    category: Optional[str] = None,
    subagent_type: Optional[str] = None,
    session_id: Optional[str] = None,
    command: Optional[str] = None,
    model: Optional[str] = None,
) -> ToolResult:
    if run_in_background is True:
        return ToolResult(
            success=False,
            error=(
                "Background subagent execution is disabled. "
                "Use foreground delegate_task/task calls; emit multiple sibling calls "
                "in the same assistant turn for parallel work."
            ),
        )

    if tasks:
        return await _run_delegate_task_batch(
            ctx=ctx,
            tasks=tasks,
            prompt=prompt,
            load_skills=load_skills,
            description=description,
            run_in_background=run_in_background,
            category=category,
            subagent_type=subagent_type,
            session_id=session_id,
            command=command,
            model=model,
        )

    if not prompt:
        return ToolResult(success=False, error="prompt is required")

    if run_in_background is None:
        run_in_background = False
    load_skills = [str(name).strip() for name in (load_skills or []) if str(name).strip()]
    description = _derive_task_description(description, prompt, subagent_type, category, session_id)
    if category and subagent_type:
        return ToolResult(success=False, error="Provide EITHER category OR subagent_type, not both.")
    if not category and not subagent_type and not session_id:
        return ToolResult(success=False, error="Must provide either category or subagent_type.")

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
        if run_in_background:
            parent_session = await Session.get_by_id(ctx.session_id)
            manager = get_background_manager()
            task = await manager.resume(
                ResumeInput(
                    session_id=session_id,
                    prompt=prompt,
                    parent_session_id=ctx.session_id,
                    parent_message_id=ctx.message_id,
                    parent_agent=ctx.agent,
                    parent_call_id=ctx.call_id,
                    parent_model={
                        "providerID": getattr(parent_session, "provider", None),
                        "modelID": getattr(parent_session, "model", None),
                    } if parent_session else None,
                )
            )
            ctx.metadata({"title": f"Continue: {description}", "metadata": {"sessionId": task.session_id, "taskId": task.id, "status": task.status}})
            output = (
                "Background task continued.\n\n"
                f"Task ID: {task.id}\n"
                f"Description: {task.description}\n"
                f"Agent: {task.agent}\n"
                f"Status: {task.status}\n"
                "Completion will be injected into the parent session automatically.\n\n"
                f'<task_metadata>\nsession_id: {task.session_id}\n</task_metadata>'
            )
            return ToolResult(
                success=True,
                output=output,
                title=description,
                metadata={
                    "sessionId": task.session_id,
                    "taskId": task.id,
                    "status": task.status,
                    "background": True,
                },
            )
        # Sync continuation
        session = await Session.get_by_id(session_id)
        if not session:
            return ToolResult(success=False, error=f"Session {session_id} not found")
        await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content=prompt,
            agent=session.agent or ctx.agent,
        )
        from flocks.session.session_loop import LoopCallbacks
        result = await SessionLoop.run(
            session.id,
            callbacks=LoopCallbacks(
                event_publish_callback=ctx.event_publish_callback,
            ),
        )
        ctx.metadata({"title": f"Continue: {description}", "metadata": {"sessionId": session.id}})
        return await format_sync_subagent_result(
            description=description,
            session_id=session.id,
            loop_result=result,
            metadata={"sessionId": session.id},
        )

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

    if run_in_background:
        parent_session = await Session.get_by_id(ctx.session_id)
        if not parent_session:
            return ToolResult(success=False, error="Parent session not found")

        create_kwargs = dict(
            project_id=parent_session.project_id,
            directory=parent_session.directory,
            title=f"{description} (@{agent_to_use} subagent)",
            parent_id=parent_session.id,
            agent=agent_to_use,
            permission=await _subagent_session_permissions(agent_to_use),
            category="task",
        )
        if explicit_model and category_model and category_model.get("providerID") and category_model.get("modelID"):
            create_kwargs.update(
                provider=category_model["providerID"],
                model=category_model["modelID"],
                model_pinned=True,
            )
        created = await Session.create(**create_kwargs)
        await Message.create(
            session_id=created.id,
            role=MessageRole.USER,
            content=full_prompt,
            agent=agent_to_use,
        )

        manager = get_background_manager()
        runtime_provider = None if explicit_model else (category_model or {}).get("providerID")
        runtime_model = None if explicit_model else (category_model or {}).get("modelID")
        task = await manager.run_existing_session(
            session_id=created.id,
            description=description,
            agent=agent_to_use,
            allow_user_questions=False,
            parent_session_id=ctx.session_id,
            parent_message_id=ctx.message_id,
            parent_call_id=ctx.call_id,
            parent_agent=ctx.agent,
            parent_model={
                "providerID": getattr(parent_session, "provider", None),
                "modelID": getattr(parent_session, "model", None),
            },
            provider_id=runtime_provider,
            model_id=runtime_model,
        )
        ctx.metadata({"title": description, "metadata": {"sessionId": task.session_id, "taskId": task.id, "status": task.status}})
        output = (
            "Background task launched successfully.\n\n"
            f"Task ID: {task.id}\n"
            f"Description: {task.description}\n"
            f"Agent: {task.agent}\n"
            f"Status: {task.status}\n"
            "Completion will be injected into the parent session automatically.\n\n"
            f'<task_metadata>\nsession_id: {task.session_id}\n</task_metadata>'
        )
        return ToolResult(
            success=True,
            output=output,
            title=description,
            metadata={
                "sessionId": task.session_id,
                "taskId": task.id,
                "status": task.status,
                "background": True,
            },
        )

    # Sync execution
    parent_session = await Session.get_by_id(ctx.session_id)
    if not parent_session:
        return ToolResult(success=False, error="Parent session not found")

    create_kwargs = dict(
        project_id=parent_session.project_id,
        directory=parent_session.directory,
        title=f"{description} (@{agent_to_use} subagent)",
        parent_id=parent_session.id,
        agent=agent_to_use,
        permission=await _subagent_session_permissions(agent_to_use),
        category="task",
    )
    if category_model and category_model.get("providerID") and category_model.get("modelID"):
        create_kwargs.update(
            provider=category_model["providerID"],
            model=category_model["modelID"],
            model_pinned=bool(explicit_model),
        )
    created = await Session.create(**create_kwargs)
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
    result = await SessionLoop.run(
        created.id,
        provider_id=(category_model or {}).get("providerID"),
        model_id=(category_model or {}).get("modelID"),
        callbacks=forwarder.build_callbacks(
            event_publish_callback=ctx.event_publish_callback,
        ),
    )
    tool_result = await format_sync_subagent_result(
        description=description,
        session_id=created.id,
        loop_result=result,
        metadata=forwarder.final_metadata,
    )
    result_status = "completed" if tool_result.success else "error"
    ctx.metadata({"title": description, "metadata": {**forwarder.final_metadata, "status": result_status}})
    return tool_result
