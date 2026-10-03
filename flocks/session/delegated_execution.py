"""Run an independent child and deliver its result through the agent inbox."""
import asyncio
import time

from flocks.session.agent_inbox import AgentInbox, AgentMessage
from flocks.session.core.task_utils import fire_and_forget
from flocks.session.message import Message
from flocks.tool.registry import ToolResult
from flocks.utils.log import Log

log = Log.create(service="agent.delegate")


def start_child(ctx, child_id, child_agent, execute):
    parent_inbox = AgentInbox.get(ctx.session_id)
    child_inbox = AgentInbox.get(child_id)
    child_inbox.parent_id = ctx.session_id
    tool_finished = ctx.extra.get("tool_finished")

    async def run():
        # The receipt must be stored before child completion updates its card.
        if tool_finished:
            await tool_finished.wait()
        if ctx.aborted or parent_inbox.stopped or child_inbox.stopped:
            raise asyncio.CancelledError()
        return await execute()

    task = fire_and_forget(run(), label="agent.child", name=f"child:{child_id}")
    AgentInbox._children[child_id] = (ctx.session_id, task)

    async def finish(done):
        if tool_finished:
            await tool_finished.wait()
        try:
            result = done.result()
        except asyncio.CancelledError:
            result = ToolResult(success=False, error="Sub-agent stopped")
        except Exception as exc:
            result = ToolResult(success=False, error=str(exc))
        status = "completed" if result.success else "error"
        try:
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
        except Exception as exc:
            log.warn("child.card_update_failed", {"error": str(exc), "session_id": child_id})
        if not ctx.aborted and not parent_inbox.stopped:
            AgentInbox.post(ctx.session_id, AgentMessage(
                child_id, child_agent,
                f"Task {status}:\n{result.output if result.success else result.error}",
            ), publisher=ctx.event_publish_callback, inbox=parent_inbox)

    def completed(done):
        AgentInbox._children.pop(child_id, None)
        AgentInbox.wake(child_id, ctx.event_publish_callback)
        fire_and_forget(finish(done), label="agent.child_result")

    task.add_done_callback(completed)
