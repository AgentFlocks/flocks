"""Run an independent child and deliver its result through the agent inbox."""
import asyncio
import time

from flocks.session.agent_inbox import AgentInbox, AgentMessage
from flocks.session.background_tasks import track_background_task
from flocks.session.core.task_utils import fire_and_forget
from flocks.session.message import Message
from flocks.session.session import Session, SessionInactiveError, SessionNotFoundError
from flocks.tool.registry import ToolResult
from flocks.tool.subagent_result import format_sync_subagent_result
from flocks.utils.log import Log

log = Log.create(service="agent.delegate")


def start_child(ctx, child_id, child_agent, execute):
    parent_inbox = AgentInbox.bind(ctx.session_id)
    child_inbox = AgentInbox.bind(child_id)
    child_inbox.parent_id = ctx.session_id
    generation = Session.lifecycle_generation(ctx.session_id)
    tool_finished = ctx.extra.get("tool_finished")

    async def run():
        # The receipt must be stored before child completion updates its card.
        if tool_finished:
            await tool_finished.wait()
        if ctx.aborted or parent_inbox.stopped or child_inbox.stopped:
            raise asyncio.CancelledError()
        return await execute()

    async def deliver(result):
        status = "completed" if result.success else "error"

        async def update_receipt():
            for part in await Message.parts(ctx.message_id, ctx.session_id):
                if part.type != "tool" or part.callID != ctx.call_id:
                    continue
                metadata = {**(part.state.metadata or {}), **result.metadata,
                            "submitted": True, "status": status,
                            "result": result.output if result.success else result.error,
                            "finished_at": int(time.time() * 1000)}
                part = part.model_copy(update={"state": part.state.model_copy(update={"metadata": metadata})})
                await Message.store_part(ctx.session_id, ctx.message_id, part)
                if ctx.event_publish_callback:
                    await ctx.event_publish_callback("message.part.updated", {
                        "part": part.model_dump(mode="json", by_alias=True)})
                break

        try:
            await Session.run_active_write(ctx.session_id, update_receipt, expected_generation=generation)
        except (SessionInactiveError, SessionNotFoundError):
            return
        except Exception as exc:
            log.warn("child.card_update_failed", {"error": str(exc), "session_id": child_id})
        await report(result)

    async def report(result):
        status = "completed" if result.success else "error"
        if not ctx.aborted and not parent_inbox.stopped:
            AgentInbox.post(ctx.session_id, AgentMessage(
                child_id, child_agent,
                f"Task {status}:\n{result.output if result.success else result.error}",
            ), publisher=ctx.event_publish_callback, inbox=parent_inbox)

    def bind_followup_result():
        # A follow-up is a new execution, independent of the initial tool's
        # cancellation token. Capture its destination now, not at completion.
        destination = AgentInbox._inboxes.get(ctx.session_id)
        followup_generation = Session.lifecycle_generation(ctx.session_id)

        async def deliver_followup(loop_result):
            result = await format_sync_subagent_result(
                description=child_agent, session_id=child_id, loop_result=loop_result,
            )

            async def post_result():
                if destination is None:
                    return
                status = "completed" if result.success else "error"
                AgentInbox.post(ctx.session_id, AgentMessage(
                    child_id, child_agent,
                    f"Task {status}:\n{result.output if result.success else result.error}",
                ), publisher=ctx.event_publish_callback, inbox=destination)

            try:
                await Session.run_active_write(
                    ctx.session_id, post_result, expected_generation=followup_generation,
                )
            except (SessionInactiveError, SessionNotFoundError):
                return

        return deliver_followup

    child_inbox.result_callback_factory = bind_followup_result

    async def finish(done):
        if tool_finished:
            await tool_finished.wait()
        try:
            result = done.result()
        except asyncio.CancelledError:
            result = ToolResult(success=False, error="Sub-agent stopped")
        except Exception as exc:
            result = ToolResult(success=False, error=str(exc))
        await deliver(result)

    def track(task):
        track_background_task(task, session_id=ctx.session_id)
        track_background_task(task, session_id=child_id)

    def completed(done):
        if AgentInbox._children.get(child_id) != (ctx.session_id, done):
            return
        finalizer = fire_and_forget(finish(done), label="agent.child_result")
        AgentInbox._children[child_id] = (ctx.session_id, finalizer)
        AgentInbox.track_finalizer(finalizer)
        track(finalizer)

        def finished(delivered):
            if AgentInbox._children.get(child_id) == (ctx.session_id, delivered):
                AgentInbox._children.pop(child_id, None)
                AgentInbox.wake(child_id, ctx.event_publish_callback)

        finalizer.add_done_callback(finished)

    task = fire_and_forget(run(), label="agent.child", name=f"child:{child_id}")
    AgentInbox._children[child_id] = (ctx.session_id, task)
    track(task)
    task.add_done_callback(completed)
