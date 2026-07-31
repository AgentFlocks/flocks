"""delegate_task tool for direct subagent delegation."""

from __future__ import annotations

import asyncio
import time
from typing import Optional, List, Dict, Any

from flocks.tool.registry import (
    ToolRegistry,
    ToolCategory,
    ToolParameter,
    ParameterType,
    ToolResult,
    ToolContext,
)
from flocks.session.session import Session
from flocks.session.message import Message, MessageRole
from flocks.session.session_loop import SessionLoop
# 使用轻量级元数据查询，避免循环依赖
from flocks.agent.registry import is_delegatable
from flocks.skill.skill import Skill
from flocks.hooks.execution import execute_with_hooks
from flocks.hooks.pipeline import HookPipeline
from flocks.tool.subagent_result import (
    _extract_message_error,
    format_sync_subagent_result,
)
from flocks.utils.log import Log

log = Log.create(service="tool.delegate_task")


async def _run_subagent_with_hooks(
    *,
    ctx: ToolContext,
    child_session_id: str,
    child_agent: str,
    workspace: str,
    prompt: str,
    description: str,
    resumed: bool,
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
    callbacks: Optional[Any] = None,
) -> Any:
    """Run one child session with paired SubagentStart/SubagentStop hooks."""
    from flocks.hooks.pipeline import HookPipeline

    common_payload = {
        "sessionID": ctx.session_id,
        "workspace": workspace,
        "parentSessionID": ctx.session_id,
        "parentMessageID": ctx.message_id,
        "childSessionID": child_session_id,
        "agentType": child_agent,
        "prompt": prompt,
        "description": description,
        "resumed": resumed,
    }
    try:
        await HookPipeline.run_subagent_start(common_payload)
    except Exception as exc:
        log.debug("delegate_task.hook.subagent_start.error", {
            "child_session_id": child_session_id,
            "error": str(exc),
        })

    started_at = time.perf_counter()
    try:
        result = await SessionLoop.run(
            child_session_id,
            provider_id=provider_id,
            model_id=model_id,
            callbacks=callbacks,
        )
    except asyncio.CancelledError:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        try:
            await HookPipeline.run_subagent_stop({
                **common_payload,
                "status": "interrupted",
                "durationMs": duration_ms,
                "summary": None,
                "error": "Sub-agent execution was interrupted",
            })
        except Exception as exc:
            log.debug("delegate_task.hook.subagent_stop.error", {
                "child_session_id": child_session_id,
                "error": str(exc),
            })
        raise
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        try:
            await HookPipeline.run_subagent_stop({
                **common_payload,
                "status": "error",
                "durationMs": duration_ms,
                "summary": None,
                "error": str(exc),
            })
        except Exception as hook_exc:
            log.debug("delegate_task.hook.subagent_stop.error", {
                "child_session_id": child_session_id,
                "error": str(hook_exc),
            })
        raise

    summary = None
    last_message = getattr(result, "last_message", None)
    if last_message is not None:
        try:
            summary = await Message.get_text_content(last_message)
        except Exception as exc:
            log.debug("delegate_task.hook.subagent_summary.error", {
                "child_session_id": child_session_id,
                "error": str(exc),
            })
    result_error = getattr(result, "error", None)
    message_error = (
        _extract_message_error(last_message)
        if last_message is not None
        else None
    )
    message_finish = (
        getattr(last_message, "finish", None)
        if last_message is not None
        else None
    )
    result_metadata = getattr(result, "metadata", None)
    interrupted = (
        isinstance(result_metadata, dict)
        and bool(result_metadata.get("aborted"))
    )
    if interrupted:
        status = "interrupted"
        stop_error = result_error or "Sub-agent execution was interrupted"
    elif (
        getattr(result, "action", None) == "error"
        or result_error
        or message_error
        or message_finish == "error"
    ):
        status = "error"
        stop_error = (
            result_error
            or message_error
            or "Sub-agent execution failed"
        )
    else:
        status = "completed"
        stop_error = None
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    try:
        await HookPipeline.run_subagent_stop({
            **common_payload,
            "status": status,
            "durationMs": duration_ms,
            "summary": summary,
            "error": stop_error,
        })
    except Exception as exc:
        log.debug("delegate_task.hook.subagent_stop.error", {
            "child_session_id": child_session_id,
            "error": str(exc),
        })
    return result


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
                prev_key = inp.get("subagent_type")
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
    if session_id:
        return f"continue task {session_id}"
    return "delegate task"


# ------------------------------------------------------------------
# Tool definition
# ------------------------------------------------------------------

DESCRIPTION = """Spawn a task using a directly selected subagent.

Use this tool when:
- A specialized agent clearly matches the task
- You need to explore code in parallel
- Independent work can run in parallel
- Isolating research or noisy intermediate work improves context quality

Do not delegate trivial edits, direct one-tool operations, or tightly coupled
work that requires continuous coordination in the current context.

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
SUBAGENT_TYPE is required for new tasks. Omit it only when session_id continues an existing task.
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
            name="subagent_type",
            type=ParameterType.STRING,
            description="Delegatable agent name. Required for new tasks; omit when continuing with session_id.",
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
            description="Deprecated command name retained for caller compatibility",
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
    description = _derive_task_description(description, prompt, subagent_type, session_id)
    if not subagent_type and not session_id:
        return ToolResult(success=False, error="Must provide either subagent_type or session_id.")

    await ctx.ask(
        permission="delegate_task",
        patterns=[subagent_type or "continue"],
        always=["*"],
        metadata={
            "description": description,
            "subagent_type": subagent_type,
        },
    )

    # Dedup: if an identical delegate_task already completed in this session,
    # return the previous result to prevent the LLM from re-delegating.
    if not session_id:
        agent_key = subagent_type
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

    explicit_model = _parse_model(model)
    agent_to_use: Optional[str] = None

    if session_id:
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

        result = await _run_subagent_with_hooks(
            ctx=ctx,
            child_session_id=session.id,
            child_agent=session.agent or ctx.agent,
            workspace=getattr(session, "directory", None) or "",
            prompt=prompt,
            description=description,
            resumed=True,
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

    if subagent_type:
        # 使用轻量级元数据查询，避免循环依赖
        # 不再调用 Agent.get()，而是使用 is_delegatable()
        if not is_delegatable(subagent_type):
            return ToolResult(
                success=False,
                error=f'Agent "{subagent_type}" cannot be delegated to (it may be a primary agent or restricted).',
            )
        agent_to_use = subagent_type

    system_parts = []
    if skill_result["content"]:
        system_parts.append(skill_result["content"])
    system_content = "\n\n".join(system_parts) if system_parts else ""
    full_prompt = f"{system_content}\n\n{prompt}" if system_content else prompt

    # Sync execution
    parent_session = await Session.get_by_id(ctx.session_id)
    if not parent_session:
        return ToolResult(success=False, error="Parent session not found")

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
    if explicit_model and explicit_model.get("providerID") and explicit_model.get("modelID"):
        create_kwargs.update(
            provider=explicit_model["providerID"],
            model=explicit_model["modelID"],
            model_pinned=True,
        )
    created = await Session.create(**create_kwargs)
    try:
        from flocks.session.execution_profile import upsert_session_execution_profile

        await upsert_session_execution_profile(
            created.id,
            patch={
                "entry": "delegate",
                "parent_session_id": parent_session.id,
                "default_agent": agent_to_use,
            },
            source="delegate_task.child_metadata",
        )
    except Exception:
        pass
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
    parent_profile_snapshot: dict[str, Any] = {}
    try:
        from flocks.session.execution_profile import get_session_execution_profile

        profile = await get_session_execution_profile(parent_session.id)
        if isinstance(profile, dict):
            parent_profile_snapshot = dict(profile)
    except Exception:
        pass
    if parent_profile_snapshot:
        try:
            from flocks.session.execution_profile import upsert_session_execution_profile

            inherited_patch: dict[str, Any] = {}
            if parent_profile_snapshot.get("permission_mode"):
                inherited_patch["permission_mode"] = parent_profile_snapshot.get("permission_mode")
            if parent_profile_snapshot.get("runtime_mode"):
                inherited_patch["runtime_mode"] = parent_profile_snapshot.get("runtime_mode")
            if inherited_patch:
                await upsert_session_execution_profile(
                    created.id,
                    patch=inherited_patch,
                    source="delegate_task.inherit_parent_profile",
                )
        except Exception:
            pass
    child_payload = {
        "operation": "session.child.run",
        "parent_session_id": parent_session.id,
        "child_session_id": created.id,
        "parent_session_profile": parent_profile_snapshot,
    }
    result = await execute_with_hooks(
        child_payload,
        lambda: _run_subagent_with_hooks(
            ctx=ctx,
            child_session_id=created.id,
            child_agent=agent_to_use,
            workspace=runtime_directory,
            prompt=full_prompt,
            description=description,
            resumed=False,
            provider_id=(explicit_model or {}).get("providerID"),
            model_id=(explicit_model or {}).get("modelID"),
            callbacks=forwarder.build_callbacks(
                event_publish_callback=ctx.event_publish_callback,
            ),
        ),
        before=HookPipeline.run_session_child_before,
        after=HookPipeline.run_session_child_after,
    )
    tool_result = await format_sync_subagent_result(
        description=description,
        session_id=created.id,
        loop_result=result,
        metadata=forwarder.final_metadata,
    )
    result_status = str((tool_result.metadata or {}).get("status") or ("completed" if tool_result.success else "error"))
    ctx.metadata({"title": description, "metadata": {**forwarder.final_metadata, "status": result_status}})
    return tool_result
