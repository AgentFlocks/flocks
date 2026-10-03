"""Behavioral coverage of independent delegation and boundary-delivered messages."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.session.agent_inbox import AgentInbox, AgentMessage, authorize_message
from flocks.session.delegated_execution import start_child
from flocks.session.interaction_queue import InteractionQueue
from flocks.session.message import Message, MessageRole, ToolPart, ToolStateCompleted
from flocks.session.runner import StepResult
from flocks.session.session import Session
from flocks.session.session_loop import SessionLoop, LoopCallbacks, LoopResult
from flocks.tool.agent.communication import send_to_session, sleep
from flocks.tool.agent.delegate_task import delegate_task_tool
from flocks.tool.registry import ToolContext, ToolResult


@pytest.fixture(autouse=True)
async def collaboration_cleanup():
    yield
    jobs = [job for _, job in AgentInbox._children.values()] + list(AgentInbox._wakes.values())
    for job in jobs:
        job.cancel()
    await asyncio.gather(*jobs, return_exceptions=True)
    await asyncio.sleep(0)
    AgentInbox._children.clear()
    AgentInbox._inboxes.clear()
    AgentInbox._wakes.clear()
    SessionLoop._active_loops.clear()


async def sessions(tmp_path):
    parent = await Session.create(project_id="p", directory=str(tmp_path), agent="rex")
    child = await Session.create(project_id="p", directory=str(tmp_path), agent="explore", parent_id=parent.id)
    return parent, child


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_each_delegate_creates_independent_session_even_with_identical_prompt(tmp_path, monkeypatch):
    parent, _ = await sessions(tmp_path)
    monkeypatch.setattr("flocks.tool.agent.delegate_task.is_delegatable", lambda _: True)
    monkeypatch.setattr(AgentInbox, "wake", lambda *args: None)
    release = asyncio.Event()
    started = []

    async def run(sid, **kwargs):
        started.append(sid)
        await release.wait()
        return LoopResult(action="stop")

    monkeypatch.setattr(SessionLoop, "run", run)
    receipts = await asyncio.gather(*[
        delegate_task_tool(ToolContext(parent.id, f"msg-{i}", call_id=f"call-{i}",
                           extra={"agent_execution_session": True}),
                           subagent_type="explore", prompt="Same task", description="Same task")
        for i in range(2)
    ])
    ids = [r.output["child_session_id"] for r in receipts]
    assert all(r.success for r in receipts)
    assert ids[0] != ids[1]
    await until(lambda: len(started) == 2)
    assert set(started) == set(ids)
    assert all(not AgentInbox._children[sid][1].done() for sid in ids)
    for sid in ids:
        child = await Session.get_by_id(sid)
        assert child.parent_id == parent.id and child.agent == "explore"
    release.set()
    await until(lambda: len(AgentInbox.get(parent.id).messages) == 2)
    assert not AgentInbox._children


async def test_messages_enter_real_loop_after_batch_and_before_next_decision(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    await Message.create(child.id, MessageRole.USER, "Finish original task")
    monkeypatch.setattr("flocks.session.session_loop.Provider.resolve_model_info", lambda *a: (0, 0, None))
    monkeypatch.setattr("flocks.session.lifecycle.title.SessionTitle.ensure_title", lambda *a, **k: None)
    monkeypatch.setattr(SessionLoop, "_prepare_auto_turn", AsyncMock(return_value=False))
    monkeypatch.setattr(SessionLoop, "_run_turn_after_hook", AsyncMock(return_value=False))
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []
    published = AsyncMock()

    async def step(ctx, callbacks, messages, last_user):
        seen.append([await Message.get_text_content(m) for m in messages])
        if len(seen) == 1:
            entered.set()
            await release.wait()
        await Message.create(child.id, MessageRole.ASSISTANT, "done", parentID=last_user.id, finish="stop")
        return StepResult(action="stop")

    monkeypatch.setattr(SessionLoop, "_process_step_with_failover", step)
    job = asyncio.create_task(SessionLoop.run(child.id, provider_id="p", model_id="m",
        callbacks=LoopCallbacks(event_publish_callback=published)))
    await asyncio.wait_for(entered.wait(), 2)
    sent = await send_to_session(ToolContext(parent.id, "msg", agent="rex"), child.id, "Also investigate Y")
    assert sent.success
    assert not await Message.get(child.id, sent.output["message_id"])
    release.set()
    result = await asyncio.wait_for(job, 3)
    assert result.action == "stop"
    assert len(seen) == 2
    assert "Finish original task" in seen[1]
    assert any("Also investigate Y" in text and "TODO" in text for text in seen[1])
    parts = await Message.parts(sent.output["message_id"], child.id)
    assert parts[0].metadata["communication"]["source_agent"] == "rex"
    assert parts[0].metadata["displayText"] == "Also investigate Y"
    assert any(c.args[0] == "message.part.updated" for c in published.await_args_list)


async def test_progress_wakes_sleep_but_human_queue_does_not(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    monkeypatch.setattr(AgentInbox, "wake", lambda *args: None)
    context = ToolContext(parent.id, "msg", extra={"sleep_allowed": True})
    wait = asyncio.create_task(sleep(context, 1))
    await InteractionQueue.enqueue(parent.id, parts=[{"type": "text", "text": "Later human task"}])
    await asyncio.sleep(0)
    assert not wait.done()
    sent = await send_to_session(ToolContext(child.id, "report", agent="explore"), parent.id, "Halfway done")
    assert sent.success
    assert (await wait).output["wake_reason"] == "message"
    assert len(await InteractionQueue.list(parent.id)) == 1
    assert not await Message.list(parent.id)


async def test_sleep_timeout_and_stop_do_not_leave_waiters(tmp_path):
    parent, _ = await sessions(tmp_path)
    ctx = ToolContext(parent.id, "msg", extra={"sleep_allowed": True})
    assert (await sleep(ctx, .001)).output == {"wake_reason": "timeout"}
    waiting = asyncio.create_task(sleep(ctx, 60))
    await asyncio.sleep(0)
    ctx.abort.set()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not (await sleep(ToolContext(parent.id, "msg"), 1)).success


async def test_idle_recipient_wakes_without_forcing_parent_to_wait(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    deliveries = []

    async def run(sid, **kwargs):
        await AgentInbox.drain(sid, kwargs["callbacks"])
        deliveries.append(sid)
        return LoopResult(action="stop")

    monkeypatch.setattr(SessionLoop, "run", run)
    await send_to_session(ToolContext(child.id, "msg", agent="explore"), parent.id, "Progress after parent replied")
    await until(lambda: len(deliveries) == 1)
    assert deliveries == [parent.id]
    assert not AgentInbox.get(parent.id).messages


async def test_stop_cancels_submitted_children_and_drops_late_results(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    finished = asyncio.Event()
    ctx = ToolContext(parent.id, "msg", extra={"tool_finished": finished})
    execute = AsyncMock(return_value=ToolResult(success=True, output="late"))
    start_child(ctx, child.id, "explore", execute)
    task = AgentInbox._children[child.id][1]
    old_inbox = AgentInbox.get(parent.id)
    SessionLoop.abort(parent.id)
    finished.set()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(.01)
    execute.assert_not_awaited()
    assert old_inbox.stopped and not old_inbox.messages
    assert not AgentInbox.get(parent.id).messages


async def test_stop_reaches_grandchild_after_child_has_finished(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    grandchild = await Session.create(project_id="p", directory=str(tmp_path), parent_id=child.id)
    monkeypatch.setattr(AgentInbox, "wake", lambda *args: None)
    release = asyncio.Event()

    async def pending():
        await release.wait()
        return ToolResult(success=True)

    async def first():
        start_child(ToolContext(child.id, "child-msg"), grandchild.id, "explore", pending)
        return ToolResult(success=True, output="done")

    start_child(ToolContext(parent.id, "parent-msg"), child.id, "explore", first)
    await until(lambda: grandchild.id in AgentInbox._children and child.id not in AgentInbox._children)
    grandchild_job = AgentInbox._children[grandchild.id][1]
    SessionLoop.abort(parent.id)
    await asyncio.gather(grandchild_job, return_exceptions=True)
    assert grandchild_job.cancelled()


async def test_result_updates_completed_receipt_without_polling(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    monkeypatch.setattr(AgentInbox, "wake", lambda *args: None)
    message = await Message.create(parent.id, MessageRole.ASSISTANT, "")
    part = ToolPart(sessionID=parent.id, messageID=message.id, callID="call", tool="delegate_task",
        state=ToolStateCompleted(input={}, output="submitted", title="task",
                                 metadata={"sessionId": child.id, "submitted": True, "status": "running"},
                                 time={"start": 1, "end": 2}))
    await Message.store_part(parent.id, message.id, part)
    publisher = AsyncMock()
    start_child(ToolContext(parent.id, message.id, call_id="call", event_publish_callback=publisher),
                child.id, "explore", AsyncMock(return_value=ToolResult(success=True, output="actual result")))
    await until(lambda: bool(AgentInbox.get(parent.id).messages))
    updated = next(p for p in await Message.parts(message.id, parent.id) if p.type == "tool")
    assert updated.state.status == "completed"
    assert updated.state.metadata["status"] == "completed"
    assert updated.state.metadata["result"] == "actual result"
    publisher.assert_awaited_once()


async def test_siblings_can_message_but_unrelated_sessions_need_acl(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    sibling = await Session.create(project_id="p", directory=str(tmp_path), parent_id=parent.id)
    await authorize_message(child.id, sibling.id)
    other = await Session.create(project_id="p", directory=str(tmp_path))
    monkeypatch.setattr("flocks.session.policy.SessionPolicy._resolve_user", lambda _: None)
    with pytest.raises(ValueError, match="unavailable"):
        await authorize_message(child.id, other.id)
    stopped = ToolContext(child.id, "msg")
    stopped.abort.set()
    assert not (await send_to_session(stopped, parent.id, "late instruction")).success


async def test_parent_can_finish_with_live_child(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    child_job = asyncio.create_task(asyncio.Event().wait())
    AgentInbox._children[child.id] = (parent.id, child_job)
    monkeypatch.setattr(SessionLoop, "_run_loop", AsyncMock(return_value=LoopResult(action="stop")))
    result = await asyncio.wait_for(SessionLoop.run(parent.id, provider_id="p", model_id="m"), 1)
    assert result.action == "stop"
    assert not child_job.done()


async def test_projection_preserves_recipient_agent_and_plan_permissions(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    monkeypatch.setattr(AgentInbox, "wake", lambda *args: None)
    await Message.create(child.id, MessageRole.USER, "original", agent="explore", executionMode="plan",
                         tools={"bash": False}, system="Keep this constraint")
    await send_to_session(ToolContext(parent.id, "msg", agent="rex"), child.id, "followup")
    await AgentInbox.drain(child.id, LoopCallbacks())
    added = (await Message.list(child.id))[-1]
    assert added.agent == "explore"
    assert added.executionMode == "plan"
    assert added.tools == {"bash": False}
    assert added.system == "Keep this constraint"


async def test_arrow_stops_live_children_when_parent_is_idle(tmp_path, monkeypatch):
    from flocks.server.routes import session as routes
    parent, child = await sessions(tmp_path)
    item = await InteractionQueue.enqueue(parent.id, parts=[{"type": "text", "text": "replacement"}])
    child_job = asyncio.create_task(asyncio.Event().wait())
    AgentInbox._children[child.id] = (parent.id, child_job)
    monkeypatch.setattr(routes, "require_user", lambda _: SimpleNamespace())
    monkeypatch.setattr(routes, "_require_session_write_access", lambda *a: None)
    monkeypatch.setattr(routes, "_publish_prompt_queue", AsyncMock())
    drain = AsyncMock()
    monkeypatch.setattr(routes, "_schedule_prompt_queue_drain", drain)
    result = await routes.run_prompt_queue_item_now(parent.id, item.id, SimpleNamespace())
    await asyncio.gather(child_job, return_exceptions=True)
    assert result["status"] == "accepted" and child_job.cancelled()
    assert (await InteractionQueue.list(parent.id))[0].id == item.id
    drain.assert_awaited_once()


async def test_communication_tools_are_available_to_restricted_subagent(tmp_path):
    from flocks.session.callable_schema import list_session_callable_tool_infos
    from flocks.tool.registry import ToolRegistry
    parent, child = await sessions(tmp_path)
    ToolRegistry.init()
    schema = await list_session_callable_tool_infos(child.id, declared_tool_names=["read"])
    assert {"send_to_session", "sleep", "read"} <= {tool.name for tool in schema.tool_infos}


async def test_real_stream_returns_submission_before_child_finishes(tmp_path, monkeypatch):
    from flocks.session.streaming.stream_events import ToolCallEvent
    from flocks.session.streaming.stream_processor import StreamProcessor
    from flocks.tool.registry import ToolRegistry
    parent, _ = await sessions(tmp_path)
    assistant = await Message.create(parent.id, MessageRole.ASSISTANT, "")
    ToolRegistry.init()
    monkeypatch.setattr("flocks.tool.agent.delegate_task.is_delegatable", lambda _: True)
    monkeypatch.setattr(AgentInbox, "wake", lambda *a: None)
    started, release = asyncio.Event(), asyncio.Event()

    async def child_run(*args, **kwargs):
        started.set()
        await release.wait()
        return LoopResult(action="stop")

    monkeypatch.setattr(SessionLoop, "run", child_run)
    processor = StreamProcessor(session_id=parent.id, assistant_message=assistant,
                                agent=SimpleNamespace(name="rex"))
    monkeypatch.setattr(processor, "_resolve_sandbox_meta", AsyncMock(return_value={"blocked": False, "error": None, "extra": {}}))
    await processor.process_event(ToolCallEvent(tool_call_id="delegate", tool_name="delegate_task",
        input={"subagent_type": "explore", "prompt": "work"}))
    await asyncio.wait_for(processor.drain_parallel_tool_calls(), 2)
    await asyncio.wait_for(started.wait(), 2)
    receipt = next(p for p in await Message.parts(assistant.id, parent.id) if p.type == "tool")
    assert receipt.state.status == "completed"
    assert receipt.state.metadata["status"] == "running"
    assert not release.is_set()
    release.set()
    await until(lambda: bool(AgentInbox.get(parent.id).messages))
    updated = next(p for p in await Message.parts(assistant.id, parent.id) if p.type == "tool")
    assert updated.state.metadata["status"] == "completed"


async def test_stop_before_wake_starts_does_not_block_replacement(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    received = []

    async def run(sid, **kwargs):
        received.extend(m.content for m in AgentInbox.get(sid).messages)
        await AgentInbox.drain(sid, kwargs["callbacks"])
        return LoopResult(action="stop")

    monkeypatch.setattr(SessionLoop, "run", run)
    AgentInbox.post(parent.id, AgentMessage(child.id, "explore", "old"))
    old_wake = AgentInbox._wakes[parent.id]
    SessionLoop.abort(parent.id)
    AgentInbox.post(parent.id, AgentMessage(child.id, "explore", "new"))
    new_wake = AgentInbox._wakes[parent.id]
    assert old_wake is not new_wake
    await asyncio.gather(old_wake, new_wake, return_exceptions=True)
    assert received == ["new"]
    assert parent.id not in AgentInbox._wakes


async def test_stopping_one_unstarted_child_leaves_its_sibling_running(tmp_path, monkeypatch):
    parent, first = await sessions(tmp_path)
    second = await Session.create(project_id="p", directory=str(tmp_path), parent_id=parent.id)
    monkeypatch.setattr(AgentInbox, "wake", lambda *a: None)
    started = asyncio.Event()
    finished = asyncio.Event()

    async def wait():
        started.set()
        await finished.wait()
        return ToolResult(success=True)

    start_child(ToolContext(parent.id, "first"), first.id, "explore", wait)
    first_job = AgentInbox._children[first.id][1]
    start_child(ToolContext(parent.id, "second"), second.id, "explore", wait)
    second_job = AgentInbox._children[second.id][1]
    SessionLoop.abort(first.id)
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.gather(first_job, return_exceptions=True)
    assert first_job.cancelled() and not second_job.done()
    finished.set()
    await second_job
    await until(lambda: len(AgentInbox.get(parent.id).messages) == 2)


async def test_stop_tree_cancels_pending_followup_to_completed_child(tmp_path, monkeypatch):
    parent, child = await sessions(tmp_path)
    AgentInbox.get(child.id).parent_id = parent.id
    # The original delegated job has already finished; only its new inbox wake exists.
    AgentInbox.post(child.id, AgentMessage(parent.id, "rex", "followup"))
    wake = AgentInbox._wakes[child.id]
    SessionLoop.abort(parent.id)
    await asyncio.gather(wake, return_exceptions=True)
    assert wake.cancelled()
    assert not AgentInbox.get(child.id).messages


async def test_cancel_during_loop_startup_releases_active_slot(tmp_path, monkeypatch):
    parent, _ = await sessions(tmp_path)
    entered = asyncio.Event()

    async def cleanup(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("flocks.session.orphan_tools.abort_orphan_running_parts", cleanup)
    job = asyncio.create_task(SessionLoop.run(parent.id, provider_id="p", model_id="m"))
    await asyncio.wait_for(entered.wait(), 1)
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)
    assert not SessionLoop.is_running(parent.id)
