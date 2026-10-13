"""A cancelled execution stops its subagent sessions and is not revived by them.

Cancellation by whoever awaits a session loop (a workflow node timeout
cancelling the delegate_task future, a background-task watchdog) meets the
agent inbox here: background children report back through the inbox, so the
cancelled session has to stop listening, and ``only_category`` has to hold
through the inbox tree, not only for loops that happen to be running.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks.session.runner as runner_mod
from flocks.provider.llm_budget import llm_slot, loop_budget_snapshot, set_loop_budget
from flocks.provider.provider import ChatMessage
from flocks.session.agent_inbox import AgentInbox, AgentMessage
from flocks.session.delegated_execution import start_child
from flocks.session.message import Message, MessageRole
from flocks.session.runner import SessionRunner, StepResult
from flocks.session.session import Session
from flocks.session.session_loop import LoopResult, SessionLoop
from flocks.tool.agent.communication import send_to_session, sleep
from flocks.tool.agent.delegate_task import delegate_task_tool
from flocks.tool.registry import ToolContext, ToolResult


@pytest.fixture(autouse=True)
async def collaboration_cleanup():
    yield
    for session_id in list(AgentInbox._inboxes):
        SessionLoop.abort(session_id)
    for _ in range(2):
        jobs = [job for _, job in AgentInbox._children.values()] + list(AgentInbox._wakes.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await asyncio.sleep(0)
    AgentInbox._children.clear()
    AgentInbox._inboxes.clear()
    AgentInbox._wakes.clear()
    SessionLoop._active_loops.clear()


@pytest.fixture
async def loops(monkeypatch):
    """Session loops that stay busy until aborted (or finish at once if in ``finish``).

    Sessions in ``real`` run the real _run_loop instead; their step runs the
    blocker registered for them and counts in ``steps``. Every SessionLoop.run
    call, including an inbox wake, counts in ``runs``.
    """
    original_run, original_run_loop = SessionLoop.run, SessionLoop._run_loop
    state = SimpleNamespace(started=[], finish=set(), tasks=[], real={}, steps=[], runs=[])

    async def run(session_id, **kwargs):
        state.runs.append(session_id)
        return await original_run(session_id, **kwargs)

    async def run_loop(ctx, callbacks):
        if ctx.session.id in state.real:
            return await original_run_loop(ctx, callbacks)
        state.started.append(ctx.session.id)
        if ctx.session.id not in state.finish:
            await ctx.abort_event.wait()
        return LoopResult(action="stop")

    async def step(ctx, callbacks, messages, last_user):
        state.steps.append(ctx.session.id)
        await state.real[ctx.session.id](ctx)
        return StepResult(action="stop")

    def start(session_id):
        task = asyncio.create_task(SessionLoop.run(session_id, provider_id="p", model_id="m"))
        state.tasks.append(task)
        return task

    monkeypatch.setattr(SessionLoop, "run", run)
    monkeypatch.setattr(SessionLoop, "_run_loop", run_loop)
    monkeypatch.setattr(SessionLoop, "_process_step_with_failover", step)
    monkeypatch.setattr("flocks.session.session_loop.Provider.resolve_model_info", lambda *a: (0, 0, None))
    monkeypatch.setattr("flocks.session.lifecycle.title.SessionTitle.ensure_title", lambda *a, **k: None)
    monkeypatch.setattr(SessionLoop, "_prepare_auto_turn", AsyncMock(return_value=False))
    monkeypatch.setattr(SessionLoop, "_run_turn_after_hook", AsyncMock(return_value=False))
    state.start = start
    yield state
    for session_id in list(SessionLoop._active_loops):
        SessionLoop.abort(session_id)
    await asyncio.wait_for(asyncio.gather(*state.tasks, return_exceptions=True), 5)


async def make(tmp_path, *, parent=None, category="task", agent="explore"):
    return await Session.create(project_id="p", directory=str(tmp_path), agent=agent,
                                parent_id=parent.id if parent else None, category=category)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def settle(predicate):
    try:
        await until(predicate)
    except TimeoutError:
        pass


class _FakeProcessor:
    def __init__(self, **_: object):
        self.finish_reason = "stop"
        self.tool_calls = {}
        self._langfuse_generation = None

    async def process_event(self, event) -> None:
        return None

    def get_text_content(self) -> str:
        return ""

    def get_reasoning_content(self) -> str:
        return ""

    def get_finish_reason(self):
        return self.finish_reason

    async def drain_parallel_tool_calls(self, *, execute_deferred: bool = True) -> None:
        return None


def model_call_blocker(monkeypatch, entered):
    """A step whose model call blocks once its stream is open."""
    monkeypatch.setattr(runner_mod, "StreamProcessor", _FakeProcessor)
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))

    class Provider:
        async def chat_stream(self, **_kwargs):
            yield SimpleNamespace(delta="a", reasoning=None, tool_calls=None)
            entered.set()
            await asyncio.Event().wait()

    async def block(ctx):
        runner = SessionRunner(session=ctx.session, provider_id="p", model_id="m", abort_event=ctx.abort_event)
        assistant = await Message.create(ctx.session.id, MessageRole.ASSISTANT, "")
        await runner._call_llm(provider=Provider(), messages=[ChatMessage(role="user", content="work")],
                               tools=[], agent=SimpleNamespace(name=ctx.session.agent), assistant_msg=assistant)

    return block


def sleep_blocker(entered):
    """A step waiting in the sleep tool (deferred until after the stream)."""
    async def block(ctx):
        entered.set()
        await sleep(ToolContext(ctx.session.id, "msg-sleep", abort_event=ctx.abort_event), 60)

    return block


@pytest.mark.parametrize("phase", ["stream", "budget_wait", "sleep"])
async def test_cancelled_execution_stops_its_subagents_and_is_not_revived(tmp_path, monkeypatch, loops, phase):
    monkeypatch.setattr("flocks.tool.agent.delegate_task.is_delegatable", lambda _: True)
    parent = await make(tmp_path, agent="rex-junior")  # e.g. started by a workflow node
    await Message.create(parent.id, MessageRole.USER, "investigate")
    receipt_msg = await Message.create(parent.id, MessageRole.ASSISTANT, "")

    async def delegate(call_id, tool_finished):
        ctx = ToolContext(parent.id, receipt_msg.id, call_id=call_id, agent="rex-junior",
                          extra={"agent_execution_session": True, "tool_finished": tool_finished})
        receipt = await delegate_task_tool(ctx, subagent_type="explore", prompt=call_id, model="p/m")
        assert receipt.success
        return receipt.output["child_session_id"]

    finished = asyncio.Event()
    finished.set()
    running = await delegate("call-running", finished)
    submitted_call_finished = asyncio.Event()  # its delegate_task call is still storing the receipt
    submitted = await delegate("call-submitted", submitted_call_finished)
    idle = await make(tmp_path, parent=parent)  # finished, but its own child still runs
    loops.finish.add(idle.id)
    await loops.start(idle.id)
    grandchild = await make(tmp_path, parent=idle)

    async def run_grandchild():
        await SessionLoop.run(grandchild.id, provider_id="p", model_id="m")
        return ToolResult(success=True, output="done")

    start_child(ToolContext(idle.id, "msg-idle", call_id="call-grandchild"), grandchild.id, "explore",
                run_grandchild)
    user_child = await make(tmp_path, parent=parent, category="user", agent="rex")  # "new from parent"
    user_task = await make(tmp_path, parent=user_child)
    loops.start(user_child.id)
    loops.start(user_task.id)
    await until(lambda: all(SessionLoop.is_running(sid)
                            for sid in (running, grandchild.id, user_child.id, user_task.id)))

    entered = asyncio.Event()
    holder = None
    if phase == "sleep":
        loops.real[parent.id] = sleep_blocker(entered)
    else:
        loops.real[parent.id] = model_call_blocker(monkeypatch, entered)
    if phase == "budget_wait":
        set_loop_budget(asyncio.get_running_loop(), 1)
        holder = llm_slot()
        await holder.acquire()  # another workflow call holds the only slot
    try:
        job = loops.start(parent.id)
        if phase == "budget_wait":
            await until(lambda: loop_budget_snapshot()["waiting"] == 1)
        else:
            await asyncio.wait_for(entered.wait(), 2)
        job.cancel()  # its owner gives up on it; not an abort of the session
        await asyncio.wait_for(asyncio.gather(job, return_exceptions=True), 2)
    finally:
        if holder is not None:
            holder.release()
            set_loop_budget(asyncio.get_running_loop(), 0)
    submitted_call_finished.set()  # StreamProcessor does this once the tool call returns

    await settle(lambda: not any(sid in AgentInbox._children for sid in (running, submitted, grandchild.id)))
    await asyncio.sleep(0.05)  # room for a late child report to wake the parent
    assert not SessionLoop.is_running(running)
    assert not SessionLoop.is_running(grandchild.id)
    assert submitted not in AgentInbox._children and submitted not in loops.started
    assert AgentInbox._inboxes[idle.id].stopped
    assert loops.runs.count(parent.id) == 1 and loops.steps.count(parent.id) == 1
    assert not SessionLoop.is_running(parent.id) and not AgentInbox._inboxes[parent.id].messages
    for sid in (user_child.id, user_task.id):
        assert SessionLoop.is_running(sid)
        assert not SessionLoop._active_loops[sid].should_abort()
        assert not AgentInbox._inboxes[sid].stopped


async def test_cancel_in_stream_stops_subagents_before_draining_tool_calls(tmp_path, monkeypatch, loops):
    parent = await make(tmp_path, agent="rex-junior")
    await Message.create(parent.id, MessageRole.USER, "investigate")
    child = await make(tmp_path, parent=parent)

    async def run_child():
        await SessionLoop.run(child.id, provider_id="p", model_id="m")
        return ToolResult(success=True, output="done")

    start_child(ToolContext(parent.id, "msg-delegate", call_id="call-child"), child.id, "explore", run_child)
    await until(lambda: SessionLoop.is_running(child.id))
    entered = asyncio.Event()
    loops.real[parent.id] = model_call_blocker(monkeypatch, entered)
    draining, release_drain = asyncio.Event(), asyncio.Event()

    class SlowDrainProcessor(_FakeProcessor):
        async def drain_parallel_tool_calls(self, *, execute_deferred: bool = True) -> None:
            draining.set()
            await release_drain.wait()  # e.g. a delegate_task call waiting for a permission reply

    monkeypatch.setattr(runner_mod, "StreamProcessor", SlowDrainProcessor)
    job = loops.start(parent.id)
    await asyncio.wait_for(entered.wait(), 2)
    job.cancel()
    await asyncio.wait_for(draining.wait(), 2)

    await settle(lambda: not SessionLoop.is_running(child.id))
    assert not SessionLoop.is_running(child.id)  # while the drain still waits
    assert AgentInbox._inboxes[parent.id].stopped
    release_drain.set()
    await asyncio.wait_for(asyncio.gather(job, return_exceptions=True), 2)
    await asyncio.sleep(0.05)
    assert loops.runs.count(parent.id) == 1 and not SessionLoop.is_running(parent.id)


async def test_cancel_outside_a_step_also_stops_subagents(tmp_path, monkeypatch, loops):
    parent = await make(tmp_path, agent="rex-junior")
    await Message.create(parent.id, MessageRole.USER, "investigate")
    child = await make(tmp_path, parent=parent)  # started by an earlier execution, still running

    async def run_child():
        await SessionLoop.run(child.id, provider_id="p", model_id="m")
        return ToolResult(success=True, output="done")

    start_child(ToolContext(parent.id, "msg-earlier", call_id="call-earlier"), child.id, "explore", run_child)
    await until(lambda: SessionLoop.is_running(child.id))
    entered = asyncio.Event()

    async def orphan_cleanup(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("flocks.session.orphan_tools.abort_orphan_running_parts", orphan_cleanup)
    loops.real[parent.id] = sleep_blocker(asyncio.Event())
    job = loops.start(parent.id)
    await asyncio.wait_for(entered.wait(), 2)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    await settle(lambda: child.id not in AgentInbox._children)
    await asyncio.sleep(0.05)
    assert not SessionLoop.is_running(child.id)
    assert loops.runs.count(parent.id) == 1 and loops.steps == []
    assert not SessionLoop.is_running(parent.id) and not AgentInbox._inboxes[parent.id].messages


async def test_task_only_abort_keeps_user_branch_through_inbox_tree(tmp_path, loops):
    root = await make(tmp_path, category="user", agent="rex")
    task = await make(tmp_path, parent=root)
    user = await make(tmp_path, parent=task, category="user", agent="rex")
    nested_task = await make(tmp_path, parent=user)
    for session in (root, task, user, nested_task):
        loops.start(session.id)
    await until(lambda: all(SessionLoop.is_running(s.id) for s in (root, task, user, nested_task)))

    assert SessionLoop.abort_children(root.id, only_category="task") == 1

    await until(lambda: not SessionLoop.is_running(task.id))
    await asyncio.sleep(0.05)
    for session in (root, user, nested_task):
        assert SessionLoop.is_running(session.id)
        assert not SessionLoop._active_loops[session.id].should_abort()
        assert not AgentInbox._inboxes[session.id].stopped


async def test_task_only_abort_reaches_child_still_queued(tmp_path, monkeypatch, loops):
    monkeypatch.setattr("flocks.tool.agent.delegate_task.is_delegatable", lambda _: True)
    parent = await make(tmp_path, category="user", agent="rex")
    assistant = await Message.create(parent.id, MessageRole.ASSISTANT, "")
    call_finished = asyncio.Event()
    ctx = ToolContext(parent.id, assistant.id, call_id="call-queued", agent="rex",
                      extra={"agent_execution_session": True, "tool_finished": call_finished})
    receipt = await delegate_task_tool(ctx, subagent_type="explore", prompt="queued", model="p/m")
    child = receipt.output["child_session_id"]
    job = AgentInbox._children[child][1]

    # The parent's inbox stays open here, so only the abort itself can stop it.
    SessionLoop.abort_children(parent.id, only_category="task")
    call_finished.set()

    await settle(lambda: child not in AgentInbox._children)
    assert job.cancelled()
    assert child not in loops.started


async def test_task_only_abort_reaches_never_run_child_woken_by_message(tmp_path, monkeypatch):
    parent = await make(tmp_path, category="user", agent="rex")
    child = await make(tmp_path, parent=parent)  # a subagent session that has never run
    runs = []

    async def run(session_id, **kwargs):
        runs.append(session_id)
        return LoopResult(action="stop")

    monkeypatch.setattr(SessionLoop, "run", run)
    sent = await send_to_session(ToolContext(parent.id, "msg", agent="rex"), child.id, "please start")
    assert sent.success
    wake = AgentInbox._wakes[child.id]

    SessionLoop.abort_children(parent.id, only_category="task")

    await asyncio.gather(wake, return_exceptions=True)
    await asyncio.sleep(0.05)
    assert wake.cancelled()
    assert runs == []


async def test_delegation_finishing_after_its_parent_was_closed_does_not_start(tmp_path, monkeypatch, loops):
    monkeypatch.setattr("flocks.tool.agent.delegate_task.is_delegatable", lambda _: True)
    parent = await make(tmp_path, agent="rex-junior")
    assistant = await Message.create(parent.id, MessageRole.ASSISTANT, "")
    preparing, resume = asyncio.Event(), asyncio.Event()

    async def upsert_profile(*args, **kwargs):
        preparing.set()
        await resume.wait()

    monkeypatch.setattr("flocks.session.execution_profile.upsert_session_execution_profile", upsert_profile)
    finished = asyncio.Event()
    finished.set()
    ctx = ToolContext(parent.id, assistant.id, call_id="call-late", agent="rex-junior",
                      extra={"agent_execution_session": True, "tool_finished": finished})
    call = asyncio.create_task(delegate_task_tool(ctx, subagent_type="explore", prompt="late", model="p/m"))
    await asyncio.wait_for(preparing.wait(), 2)
    closed = AgentInbox._inboxes[parent.id]
    AgentInbox.close(parent.id)  # the execution that made the call ended meanwhile
    resume.set()

    with pytest.raises(asyncio.CancelledError):
        await call
    await asyncio.sleep(0.05)
    assert AgentInbox._inboxes[parent.id] is closed  # no next generation bound for the parent
    assert not AgentInbox._children
    assert loops.started == []


async def test_delegation_waiting_for_permission_does_not_start_once_its_parent_ended(
    tmp_path, monkeypatch, loops,
):
    monkeypatch.setattr("flocks.tool.agent.delegate_task.is_delegatable", lambda _: True)
    parent = await make(tmp_path, agent="rex-junior")
    assistant = await Message.create(parent.id, MessageRole.ASSISTANT, "")
    asking, approve = asyncio.Event(), asyncio.Event()

    async def permission(request):
        asking.set()
        await approve.wait()

    finished = asyncio.Event()
    finished.set()
    ctx = ToolContext(parent.id, assistant.id, call_id="call-asking", agent="rex-junior",
                      permission_callback=permission,
                      extra={"agent_execution_session": True, "tool_finished": finished})
    call = asyncio.create_task(delegate_task_tool(ctx, subagent_type="explore", prompt="asking", model="p/m"))
    await asyncio.wait_for(asking.wait(), 2)
    ended = AgentInbox._inboxes[parent.id]
    SessionLoop.end_cancelled_execution(parent.id)  # the execution that made the call was cancelled
    approve.set()  # the permission reply arrives later

    result = await asyncio.wait_for(call, 2)
    assert not result.success and result.error == "Execution stopped"
    await asyncio.sleep(0.05)
    assert AgentInbox._inboxes[parent.id] is ended  # no next generation bound for the parent
    assert not AgentInbox._children
    assert loops.started == [] and parent.id not in loops.runs


async def test_delegation_after_its_parent_ended_does_not_ask_for_permission(tmp_path, monkeypatch, loops):
    monkeypatch.setattr("flocks.tool.agent.delegate_task.is_delegatable", lambda _: True)
    parent = await make(tmp_path, agent="rex-junior")
    assistant = await Message.create(parent.id, MessageRole.ASSISTANT, "")
    ended = AgentInbox.bind(parent.id)  # pinned by the step, which was cancelled before this ran
    SessionLoop.end_cancelled_execution(parent.id)
    asked = []

    async def permission(request):
        asked.append(request.permission)

    ctx = ToolContext(parent.id, assistant.id, call_id="call-late", agent="rex-junior",
                      permission_callback=permission,
                      extra={"agent_execution_session": True, "agent_inbox": ended})
    result = await delegate_task_tool(ctx, subagent_type="explore", prompt="late", model="p/m")

    assert not result.success and result.error == "Execution stopped"
    assert asked == []  # no permission prompt nobody is waiting for
    assert AgentInbox._inboxes[parent.id] is ended
    assert not AgentInbox._children and loops.started == []


async def test_delegation_still_in_the_tool_pipeline_does_not_start_after_cancel(tmp_path, monkeypatch, loops):
    from flocks.session.streaming.stream_events import ToolCallEvent
    from flocks.session.streaming.stream_processor import StreamProcessor
    from flocks.tool.registry import ToolRegistry

    monkeypatch.setattr("flocks.tool.agent.delegate_task.is_delegatable", lambda _: True)
    parent = await make(tmp_path, agent="rex-junior")
    assistant = await Message.create(parent.id, MessageRole.ASSISTANT, "")
    ToolRegistry.init()
    AgentInbox.bind(parent.id)  # what SessionLoop.run does for the live execution
    processor = StreamProcessor(session_id=parent.id, assistant_message=assistant,
                                agent=SimpleNamespace(name="rex-junior"), allowed_tool_names=["delegate_task"])
    paused, resume = asyncio.Event(), asyncio.Event()

    async def sandbox_meta(*args, **kwargs):  # an await of the tool pipeline before the tool runs
        paused.set()
        await resume.wait()
        return {"blocked": False, "error": None, "extra": {}}

    monkeypatch.setattr(processor, "_resolve_sandbox_meta", sandbox_meta)
    await processor.process_event(ToolCallEvent(tool_call_id="call-delegate", tool_name="delegate_task",
                                                input={"subagent_type": "explore", "prompt": "work", "model": "p/m"}))
    await asyncio.wait_for(paused.wait(), 2)
    ended = AgentInbox._inboxes[parent.id]
    SessionLoop.end_cancelled_execution(parent.id)  # the step is cancelled meanwhile
    resume.set()
    await asyncio.wait_for(processor.drain_parallel_tool_calls(execute_deferred=False), 2)

    await asyncio.sleep(0.05)
    assert AgentInbox._inboxes[parent.id] is ended  # no next generation bound for the parent
    assert not AgentInbox._children
    assert loops.started == []
    receipt = next(p for p in await Message.parts(assistant.id, parent.id) if p.type == "tool")
    assert receipt.state.status in {"completed", "error"}


async def test_input_after_a_cancel_cleanup_is_left_to_the_next_execution(tmp_path, monkeypatch, loops):
    parent = await make(tmp_path, agent="rex-junior")
    await Message.create(parent.id, MessageRole.USER, "investigate")
    wakes = []
    monkeypatch.setattr(AgentInbox, "wake", lambda session_id, publisher=None: wakes.append(session_id))
    entered = asyncio.Event()
    loops.real[parent.id] = model_call_blocker(monkeypatch, entered)
    draining, release_drain = asyncio.Event(), asyncio.Event()

    class SlowDrainProcessor(_FakeProcessor):
        async def drain_parallel_tool_calls(self, *, execute_deferred: bool = True) -> None:
            draining.set()
            await release_drain.wait()

    monkeypatch.setattr(runner_mod, "StreamProcessor", SlowDrainProcessor)
    job = loops.start(parent.id)
    await asyncio.wait_for(entered.wait(), 2)
    job.cancel()
    await asyncio.wait_for(draining.wait(), 2)  # the runner has closed this execution's inbox
    AgentInbox.post(parent.id, AgentMessage("ses_sibling", "explore", "new work"))
    release_drain.set()
    await asyncio.wait_for(asyncio.gather(job, return_exceptions=True), 2)

    inbox = AgentInbox._inboxes[parent.id]
    assert not inbox.stopped
    assert [message.content for message in inbox.messages] == ["new work"]
    assert loops.steps.count(parent.id) == 1  # not continued inside the cancelled call
    assert wakes and wakes[-1] == parent.id  # the next execution picks it up


@pytest.mark.parametrize("phase", ["stream", "sleep"])
async def test_user_stop_keeps_input_sent_after_it(tmp_path, monkeypatch, loops, phase):
    session = await make(tmp_path, category="user", agent="rex")
    await Message.create(session.id, MessageRole.USER, "work")
    wakes = []
    monkeypatch.setattr(AgentInbox, "wake", lambda session_id, publisher=None: wakes.append(session_id))
    entered = asyncio.Event()
    if phase == "stream":
        loops.real[session.id] = model_call_blocker(monkeypatch, entered)
    else:
        loops.real[session.id] = sleep_blocker(entered)
    job = loops.start(session.id)
    await asyncio.wait_for(entered.wait(), 2)

    SessionLoop.abort(session.id)  # the user's Stop
    AgentInbox.post(session.id, AgentMessage("ses_sibling", "explore", "after the stop"))
    await asyncio.wait_for(job, 2)

    inbox = AgentInbox._inboxes[session.id]
    assert not inbox.stopped
    assert [message.content for message in inbox.messages] == ["after the stop"]
    assert wakes and wakes[-1] == session.id  # the next execution is started as before
