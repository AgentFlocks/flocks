"""Plan completion and Build handoff modeled after OpenCode's plan_exit tool."""

from __future__ import annotations

from typing import Any

from flocks.session.execution_mode import SessionExecutionMode
from flocks.session.interaction_queue import InteractionQueue, QueueFullError
from flocks.session.message import Message, MessageRole
from flocks.session.plan_file import context_plan_file
from flocks.tool.registry import (
    ToolCategory,
    ToolContext,
    ToolRegistry,
    ToolResult,
)
from flocks.tool.system.question import question_tool


START_IMPLEMENTING = "Start implementing"
CONTINUE_PLANNING = "Continue planning"

DESCRIPTION = """Finish a completed plan and ask the user whether to implement it.

Call this only after presenting a decision-complete plan. Approval queues a new
Build turn that implements the approved plan. Declining keeps the session in
Plan so the plan can be refined.
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
    """Ask for plan approval and queue implementation in Build mode."""

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
                        "description": "Stay in Plan and continue refining the plan.",
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
        return ToolResult(
            success=True,
            output="The user chose to remain in Plan. Continue refining the plan using their feedback.",
            title="Continue planning",
            metadata={"approved": False, "executionMode": SessionExecutionMode.PLAN.value},
        )

    try:
        queued_model, queued_variant = await _turn_model_and_variant(ctx)
        queued = await InteractionQueue.enqueue(
            ctx.session_id,
            parts=[
                {
                    "type": "text",
                    "text": (
                        f"The plan at {plan.relative_path} has been approved. "
                        "Switch to Build mode, read that file, and implement it now."
                    ),
                }
            ],
            agent=ctx.agent,
            model=queued_model,
            variant=queued_variant,
            display_text="Start implementing the approved plan",
            execution_mode=SessionExecutionMode.BUILD,
        )
    except QueueFullError as exc:
        return ToolResult(
            success=False,
            error=f"Could not start implementation because the prompt queue is full: {exc}",
        )

    queued_items = await InteractionQueue.list(ctx.session_id)
    await _publish(
        ctx,
        "session.prompt_queue.updated",
        {
            "sessionID": ctx.session_id,
            "items": [item.model_dump() for item in queued_items],
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
            "The plan was approved. A Build turn has been queued and will implement "
            "the approved plan after this Plan turn finishes."
        ),
        title="Plan approved",
        metadata={
            "approved": True,
            "executionMode": SessionExecutionMode.BUILD.value,
            "queueID": queued.id,
            "planPath": plan.relative_path,
        },
    )
