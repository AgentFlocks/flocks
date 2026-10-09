"""Small tools for live collaboration and explicit waiting."""
import asyncio
import math

from flocks.session.agent_inbox import AgentInbox, AgentMessage, authorize_message
from flocks.session.execution_mode import (
    PLAN_DELEGATABLE_AGENT_NAMES,
    SessionExecutionMode,
    runtime_execution_mode,
)
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session, SessionInfo
from flocks.tool.registry import ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext


async def _check_plan_recipient(ctx: ToolContext, target: SessionInfo) -> None:
    if runtime_execution_mode(ctx.extra.get("execution_mode")) != SessionExecutionMode.PLAN:
        return

    history = await Message.list(target.id)
    last_user = next((m for m in reversed(history) if m.role == MessageRole.USER), None)
    agent = getattr(last_user, "agent", None) or target.agent
    mode = runtime_execution_mode(getattr(last_user or target, "executionMode", None))
    if agent in PLAN_DELEGATABLE_AGENT_NAMES or mode == SessionExecutionMode.PLAN:
        return

    # Reporting up the actual delegation chain is not delegating Build work.
    source = await Session.get_by_id(ctx.session_id)
    visited = {ctx.session_id}
    while source and source.parent_id and source.parent_id not in visited:
        if source.parent_id == target.id:
            return
        visited.add(source.parent_id)
        source = await Session.get_by_id(source.parent_id)

    allowed = ", ".join(sorted(PLAN_DELEGATABLE_AGENT_NAMES))
    raise ValueError(
        f"Plan mode may only send work to read-only recipients ({allowed} or Plan mode); "
        "reports to parent or ancestor sessions are allowed."
    )


@ToolRegistry.register_function(
    name="send_to_session", group="代理协作", native=True, category=ToolCategory.SYSTEM,
    description="Send information or a follow-up to an agent session. It reads this after its current tool batch without abandoning its task. Use this for intermediate reports to your parent too. Delivery does not require an acknowledgement.",
    parameters=[
        ToolParameter(name="session_id", type=ParameterType.STRING, required=True, description="Recipient session ID"),
        ToolParameter(name="content", type=ParameterType.STRING, required=True, description="Message content"),
    ],
)
async def send_to_session(ctx: ToolContext, session_id: str, content: str) -> ToolResult:
    try:
        if not content.strip():
            raise ValueError("Message cannot be empty")
        target = await authorize_message(ctx.session_id, session_id)
        await _check_plan_recipient(ctx, target)
        await ctx.ask(permission="send_to_session", patterns=[session_id])
        target = await authorize_message(ctx.session_id, session_id)
        await _check_plan_recipient(ctx, target)
        if ctx.aborted:
            raise ValueError("Execution stopped")
        inbox = AgentInbox.get(session_id)
        inbox.parent_id, inbox.category = target.parent_id, target.category
        message = AgentMessage(ctx.session_id, ctx.agent, content)
        AgentInbox.post(session_id, message, publisher=ctx.event_publish_callback)
        return ToolResult(success=True, output={"status": "sent", "session_id": session_id, "message_id": message.id})
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))


@ToolRegistry.register_function(
    name="sleep", group="代理协作", native=True, category=ToolCategory.SYSTEM,
    description="Wait for an agent message, child result or timeout while other agents continue. Use only when you need their input. Runs after the other tool calls in its batch. Timeout does not cancel children.",
    parameters=[ToolParameter(name="timeout_s", type=ParameterType.NUMBER, default=60, description="Maximum seconds to wait")],
)
async def sleep(ctx: ToolContext, timeout_s: float = 60) -> ToolResult:
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        return ToolResult(success=False, error="timeout_s must be positive and finite")
    inbox = AgentInbox.bind(ctx.session_id)
    incoming = asyncio.create_task(inbox.ready.wait())
    aborted = asyncio.create_task(ctx.abort.wait())
    try:
        done, _ = await asyncio.wait([incoming, aborted], timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
        if ctx.aborted or inbox.stopped:
            raise asyncio.CancelledError()
        return ToolResult(success=True, output={"wake_reason": "message" if incoming in done else "timeout"})
    finally:
        incoming.cancel()
        aborted.cancel()
        await asyncio.gather(incoming, aborted, return_exceptions=True)
