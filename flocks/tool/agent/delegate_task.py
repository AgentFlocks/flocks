"""delegate_task tool for direct subagent delegation."""

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
from flocks.session.session import Session
from flocks.session.message import Message, MessageRole
from flocks.session.session_loop import SessionLoop
# 使用轻量级元数据查询，避免循环依赖
from flocks.agent.registry import is_delegatable
from flocks.skill.skill import Skill
from flocks.hooks.execution import execute_with_hooks
from flocks.hooks.pipeline import HookPipeline
from flocks.tool.subagent_result import (
    format_sync_subagent_result,
)
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
) -> str:
    normalized = " ".join((description or "").split())
    if normalized:
        return normalized

    prompt_line = " ".join((prompt or "").split())
    if prompt_line:
        return prompt_line[:57].rstrip() + "..." if len(prompt_line) > 60 else prompt_line

    if subagent_type:
        return f"delegate to {subagent_type}"
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
- Every call creates an independent child session, even for the same agent or prompt.
- Interactive calls return the child session ID immediately; the child keeps running.
- Continue your own work. Call sleep only when you need an agent message or result.
- Use send_to_session(session_id=..., content=...) for corrections and follow-ups.
- Children use the same tool to report intermediate results to their parent.
- A final result is delivered automatically. Reports do not require acknowledgement.
- Workflow and direct program callers still receive results synchronously.
- Do not pass run_in_background; interactive delegation is already asynchronous.

REQUIRED: prompt.
LOAD_SKILLS is optional and defaults to [].
DESCRIPTION is optional and will be auto-derived when omitted.
SUBAGENT_TYPE is required.
"""

@ToolRegistry.register_function(
    name="delegate_task",
    group="代理协作",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    native=True,
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
            description="Delegatable agent name. Each call creates a new session.",
            required=True,
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
    # `run_in_background=True` at the schema layer for any caller. This guard
    # also protects direct in-process callers that bypass the registry.
    run_in_background: bool = False,
    subagent_type: Optional[str] = None,
    command: Optional[str] = None,
    model: Optional[str] = None,
) -> ToolResult:
    if run_in_background:
        return ToolResult(
            success=False,
            error=(
                "Omit run_in_background; interactive delegation is already asynchronous."
            ),
        )

    if not prompt:
        return ToolResult(success=False, error="prompt is required")

    load_skills = [str(name).strip() for name in (load_skills or []) if str(name).strip()]
    description = _derive_task_description(description, prompt, subagent_type)
    if not subagent_type:
        return ToolResult(success=False, error="subagent_type is required")

    await ctx.ask(
        permission="delegate_task",
        patterns=[subagent_type or "continue"],
        always=["*"],
        metadata={
            "description": description,
            "subagent_type": subagent_type,
        },
    )

    if ctx.aborted:
        return ToolResult(success=False, error="Execution stopped")
    interactive = bool(ctx.extra.get("agent_execution_session")) and not ctx.extra.get("workflow_context")
    from flocks.session.agent_inbox import AgentInbox
    parent_inbox = AgentInbox.get(ctx.session_id) if interactive else None

    skill_result = await _resolve_skill_content(load_skills)
    if skill_result["error"]:
        return ToolResult(success=False, error=skill_result["error"])

    explicit_model = _parse_model(model)
    agent_to_use: Optional[str] = None

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
    child_payload = {
        "operation": "subagent.run",
        "parent_session_id": parent_session.id,
        "child_session_id": None,
        "workspace": runtime_directory,
        "prompt": full_prompt,
        "description": description,
        "agent_type": agent_to_use,
    }

    async def _run_child_effect() -> ToolResult:
        created = await Session.create(**create_kwargs)
        child_inbox = AgentInbox.get(created.id) if interactive else None
        child_payload["child_session_id"] = created.id
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
        collaboration_prompt = (
            f"Your session ID: {created.id}. Parent session ID: {ctx.session_id}. "
            "Use send_to_session to report useful intermediate results while continuing your work.\n\n"
            + full_prompt
        ) if interactive else full_prompt
        await Message.create(
            session_id=created.id,
            role=MessageRole.USER,
            content=collaboration_prompt,
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
        async def execute_child():
            if ctx.aborted or (parent_inbox and parent_inbox.stopped) or (child_inbox and child_inbox.stopped):
                raise asyncio.CancelledError()
            loop_result = await SessionLoop.run(
                created.id, provider_id=(explicit_model or {}).get("providerID"),
                model_id=(explicit_model or {}).get("modelID"),
                callbacks=forwarder.build_callbacks(event_publish_callback=ctx.event_publish_callback),
            )
            if interactive and loop_result.action != "stop":
                raise RuntimeError(loop_result.error or "Child did not complete")
            return await format_sync_subagent_result(
                description=description, session_id=created.id, loop_result=loop_result,
                metadata=forwarder.final_metadata,
            )

        if interactive:
            from flocks.session.delegated_execution import start_child
            if ctx.aborted:
                raise asyncio.CancelledError()

            async def execute_with_child_hooks():
                return await execute_with_hooks(
                    child_payload, execute_child,
                    before=HookPipeline.run_subagent_before, after=HookPipeline.run_subagent_after,
                )

            start_child(ctx, created.id, agent_to_use, execute_with_child_hooks)
            return ToolResult(success=True, title=description,
                output={"status": "submitted", "child_session_id": created.id},
                metadata={"sessionId": created.id, "submitted": True, "status": "running"})
        return await execute_child()

    if interactive:
        return await _run_child_effect()

    result = await execute_with_hooks(
        child_payload,
        _run_child_effect,
        before=HookPipeline.run_subagent_before,
        after=HookPipeline.run_subagent_after,
    )
    ctx.metadata({"title": description, "metadata": {
        **result.metadata, "status": result.metadata.get("status") or ("completed" if result.success else "error")}})
    return result
