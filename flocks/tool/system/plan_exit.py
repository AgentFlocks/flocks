"""Plan completion and Build handoff modeled after OpenCode's plan_exit tool."""

from __future__ import annotations

from typing import Any

from flocks.session.execution_mode import SessionExecutionMode
from flocks.session.message import Message, MessageRole
from flocks.session.plan_file import context_plan_file
from flocks.tool.registry import (
    ToolCategory,
    ToolContext,
    ToolRegistry,
    ToolResult,
)
from flocks.tool.system.question import question_tool


START_IMPLEMENTING = "开始实施"
CONTINUE_PLANNING = "调整计划"

DESCRIPTION = """Finish a completed plan and ask the user whether to implement it.

Call this only after presenting a decision-complete plan. Approval starts a new
Build turn in the current session loop to implement the approved plan.
Declining keeps the session in Plan so the plan can be refined.
"""


async def _publish(ctx: ToolContext, event_type: str, properties: dict[str, Any]) -> None:
    if ctx.event_publish_callback:
        await ctx.event_publish_callback(event_type, properties)


async def _turn_model_and_variant(
    ctx: ToolContext,
) -> tuple[dict[str, str] | None, str | None]:
    """Copy the active Plan turn model like OpenCode's synthetic Build turn."""

    try:
        messages = await Message.list(ctx.session_id)
        last_user = next(
            (
                message
                for message in reversed(messages)
                if getattr(message, "role", None) == MessageRole.USER
            ),
            None,
        )
    except Exception:
        last_user = None

    model = getattr(last_user, "model", None)
    if not isinstance(model, dict) or not all(
        model.get(key) for key in ("providerID", "modelID")
    ):
        model = ctx.extra.get("model")
    if not isinstance(model, dict) or not all(
        model.get(key) for key in ("providerID", "modelID")
    ):
        model = None
    return model, getattr(last_user, "variant", None)


@ToolRegistry.register_function(
    name="plan_exit",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[],
)
async def plan_exit_tool(ctx: ToolContext) -> ToolResult:
    """Ask for plan approval and continue immediately in Build mode."""

    plan = context_plan_file(ctx)
    if plan is None or not plan.path.is_file():
        return ToolResult(
            success=False,
            error="Write the session plan file before calling plan_exit.",
        )
    try:
        if not plan.path.read_text(encoding="utf-8").strip():
            return ToolResult(
                success=False,
                error="The session plan file is empty. Complete it before calling plan_exit.",
            )
    except OSError as exc:
        return ToolResult(success=False, error=f"Could not read the session plan file: {exc}")

    confirmation = await question_tool(
        ctx,
        questions=[
            {
                "header": "Plan complete",
                "question": (
                    f"The plan at {plan.relative_path} is complete. Would you like "
                    "to switch to Build and start implementing?"
                ),
                "type": "choice",
                "options": [
                    {
                        "label": START_IMPLEMENTING,
                        "description": "Switch to Build and implement the approved plan now.",
                    },
                    {
                        "label": CONTINUE_PLANNING,
                        "description": "Stay in Plan and describe what should be changed.",
                        "allowText": True,
                    },
                ],
                "multiple": False,
                "custom": False,
            }
        ],
    )
    if not confirmation.success:
        return confirmation
    if confirmation.metadata.get("deferred"):
        return confirmation

    answers = confirmation.metadata.get("answers") or []
    selected = answers[0] if answers else []
    if START_IMPLEMENTING not in selected:
        feedback = "\n".join(
            str(value).strip()
            for value in selected
            if str(value).strip() and value != CONTINUE_PLANNING
        )
        output = (
            "The user chose to remain in Plan. Continue refining the plan "
            "using their feedback."
        )
        metadata = {
            "approved": False,
            "executionMode": SessionExecutionMode.PLAN.value,
        }
        if feedback:
            output = f"{output}\n\nUser feedback:\n{feedback}"
            metadata["feedback"] = feedback
        return ToolResult(
            success=True,
            output=output,
            title="Continue planning",
            metadata=metadata,
        )

    build_model, build_variant = await _turn_model_and_variant(ctx)
    build_message = await Message.create(
        session_id=ctx.session_id,
        role=MessageRole.USER,
        content=(
            f"The plan at {plan.relative_path} has been approved. "
            "Switch to Build mode, read that file, and implement it now."
        ),
        agent=ctx.agent,
        model=build_model,
        variant=build_variant,
        executionMode=SessionExecutionMode.BUILD,
        synthetic=True,
        part_metadata={
            "planImplementation": True,
            "planPath": plan.relative_path,
        },
    )
    await _publish(
        ctx,
        "session.execution_mode.changed",
        {
            "sessionID": ctx.session_id,
            "executionMode": SessionExecutionMode.BUILD.value,
            "reason": "plan-approved",
        },
    )
    return ToolResult(
        success=True,
        output=(
            "The plan was approved. Continue immediately in Build mode and "
            "implement the approved plan."
        ),
        title="Plan approved",
        metadata={
            "approved": True,
            "executionMode": SessionExecutionMode.BUILD.value,
            "buildMessageID": build_message.id,
            "planPath": plan.relative_path,
        },
    )
