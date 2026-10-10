"""The todo list once an agent run has ended.

Agents often end their turn on the last step without marking it completed,
and the progress panel then showed that step as in progress for good.  When a
run ends nothing stays ``in_progress``: the final steps of a finished run are
completed (and the closed list cleared, as the todo tool does), anything else
goes back to pending so the plan carries over to the next turn.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.session.agent_inbox import AgentInbox, Inbox
from flocks.session.features.todo import Todo, TodoInfo
from flocks.session.session import Session
from flocks.session.session_loop import LoopContext, LoopResult, SessionLoop


def _todos(*statuses: str) -> list[TodoInfo]:
    return [
        TodoInfo(id=f"t{index}", content=f"step {index}", status=status)
        for index, status in enumerate(statuses, 1)
    ]


async def _session(name: str):
    return await Session.create(project_id=f"todo_settle_{name}", directory="/tmp")


async def _statuses(session_id: str):
    snapshot = await Todo.get_snapshot(session_id)
    return None if snapshot is None else [todo.status for todo in snapshot]


def _finished(finish: str = "stop", error=None) -> LoopResult:
    return LoopResult(action="stop", last_message=SimpleNamespace(id="msg_last", finish=finish, error=error))


async def _run(session_id: str, body) -> None:
    """Run SessionLoop.run with ``body(ctx)`` standing in for the loop body."""

    async def run_loop(ctx, _callbacks):
        return await body(ctx)

    with patch.object(SessionLoop, "_run_loop", AsyncMock(side_effect=run_loop)):
        await SessionLoop.run(
            session_id=session_id,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )


def _ends_with(result: LoopResult, *, abort: bool = False, goal_verdict=None):
    async def body(ctx):
        if abort:
            ctx.signal_abort()
        ctx.goal_verdict = goal_verdict
        return result

    return body


def _ctx(session) -> LoopContext:
    return LoopContext(session=session, provider_id="p", model_id="m", agent_name="rex")


class TestSettleRules:
    @pytest.mark.parametrize(
        ("before", "finished", "after"),
        [
            # The reported case: everything done except the last step.
            (("completed", "completed", "in_progress"), True, []),
            (("cancelled", "in_progress"), True, []),
            # Stopped mid-plan (e.g. asking the user): the plan is kept.
            (("completed", "in_progress", "pending"), True, ["completed", "pending", "pending"]),
            # Stopped short of finishing: the step was not done.
            (("completed", "in_progress"), False, ["completed", "pending"]),
        ],
    )
    def test_no_item_stays_in_progress(self, before, finished, after):
        settled = Todo.settle(_todos(*before), finished=finished)
        assert [todo.status for todo in settled] == after

    @pytest.mark.parametrize("todos", [None, [], _todos("completed", "pending")])
    def test_list_without_in_progress_items_is_left_alone(self, todos):
        assert Todo.settle(todos, finished=True) is None


class TestSessionLoopRunEnd:
    @pytest.mark.asyncio
    async def test_finished_run_closes_the_last_step(self):
        session = await _session("finished")
        await Todo.update(session.id, _todos("completed", "completed", "in_progress"))

        with patch("flocks.server.routes.event.publish_event", AsyncMock()) as publish:
            await _run(session.id, _ends_with(_finished()))

        assert await _statuses(session.id) == []
        # The progress panel refetches on this event.
        publish.assert_any_await("todo.updated", {"sessionID": session.id, "todos": []})

    @pytest.mark.asyncio
    async def test_run_without_in_progress_items_writes_nothing(self):
        session = await _session("untouched")
        await Todo.update(session.id, _todos("completed", "pending"))

        with patch("flocks.server.routes.event.publish_event", AsyncMock()) as publish:
            await _run(session.id, _ends_with(_finished()))

        assert await _statuses(session.id) == ["completed", "pending"]
        assert not [call for call in publish.await_args_list if call.args[0] == "todo.updated"]

    @pytest.mark.asyncio
    async def test_failed_run_reopens_the_step(self):
        session = await _session("failed")
        await Todo.update(session.id, _todos("completed", "in_progress"))

        await _run(session.id, _ends_with(_finished(finish="error", error={"name": "APIError"})))

        assert await _statuses(session.id) == ["completed", "pending"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("verdict", "after"),
        [
            ("complete", []),
            ("continue", ["completed", "pending"]),  # paused: turn budget used up
            ("blocked", ["completed", "pending"]),
            ("waiting", ["completed", "pending"]),
        ],
    )
    async def test_unfinished_goal_keeps_the_plan(self, verdict, after):
        session = await _session(f"goal_{verdict}")
        await Todo.update(session.id, _todos("completed", "in_progress"))

        await _run(session.id, _ends_with(_finished(), goal_verdict=verdict))

        assert await _statuses(session.id) == after

    @pytest.mark.asyncio
    async def test_aborted_run_reopens_the_step_even_with_children(self, monkeypatch):
        session = await _session("aborted")
        await Todo.update(session.id, _todos("completed", "in_progress"))
        # Stopped children cannot report back to a stopped session.
        monkeypatch.setitem(AgentInbox._children, "ses_child_stopped", (session.id, None))

        await _run(session.id, _ends_with(_finished(), abort=True))

        assert await _statuses(session.id) == ["completed", "pending"]

    @pytest.mark.asyncio
    async def test_loop_exception_reopens_the_step(self):
        session = await _session("exception")
        await Todo.update(session.id, _todos("completed", "in_progress"))

        async def body(_ctx):
            raise RuntimeError("headless budget exhausted")

        await _run(session.id, body)

        assert await _statuses(session.id) == ["completed", "pending"]

    @pytest.mark.asyncio
    async def test_cancelled_run_reopens_the_step_and_still_cancels(self):
        session = await _session("cancelled")
        await Todo.update(session.id, _todos("completed", "in_progress"))

        async def body(_ctx):
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await _run(session.id, body)

        for _ in range(100):
            if await _statuses(session.id) == ["completed", "pending"]:
                break
            await asyncio.sleep(0.01)
        assert await _statuses(session.id) == ["completed", "pending"]

    @pytest.mark.asyncio
    async def test_pending_question_keeps_the_list(self, monkeypatch):
        from flocks.server.routes import question

        session = await _session("question")
        await Todo.update(session.id, _todos("completed", "in_progress"))
        monkeypatch.setitem(question._question_requests, "que_waiting", {"sessionID": session.id})

        await _run(session.id, _ends_with(_finished()))

        assert await _statuses(session.id) == ["completed", "in_progress"]

    @pytest.mark.asyncio
    async def test_running_subagent_keeps_the_list(self, monkeypatch):
        session = await _session("child")
        await Todo.update(session.id, _todos("completed", "in_progress"))
        monkeypatch.setitem(AgentInbox._children, "ses_child_running", (session.id, None))

        await _run(session.id, _ends_with(_finished()))

        assert await _statuses(session.id) == ["completed", "in_progress"]


class TestSettleRaces:
    @pytest.mark.asyncio
    async def test_newer_run_owns_the_list(self, monkeypatch):
        session = await _session("newer_run")
        await Todo.update(session.id, _todos("completed", "in_progress"))
        monkeypatch.setitem(SessionLoop._active_loops, session.id, _ctx(session))

        await SessionLoop._settle_todos(_ctx(session), _finished())

        assert await _statuses(session.id) == ["completed", "in_progress"]

    @pytest.mark.asyncio
    async def test_todo_write_that_lands_first_is_kept(self):
        session = await _session("late_write")
        await Todo.update(session.id, _todos("completed", "in_progress"))

        async with Session.lifecycle_lock(session.id):
            settle = asyncio.create_task(SessionLoop._settle_todos(_ctx(session), _finished()))
            await asyncio.sleep(0.05)  # settle saw the old list and waits for the lock
            await Todo.update(session.id, _todos("pending", "pending"))
        await settle

        assert await _statuses(session.id) == ["pending", "pending"]

    @pytest.mark.asyncio
    async def test_stop_that_lands_first_reopens_instead_of_clearing(self):
        session = await _session("late_stop")
        await Todo.update(session.id, _todos("completed", "in_progress"))
        ctx = _ctx(session)

        async with Session.lifecycle_lock(session.id):
            settle = asyncio.create_task(SessionLoop._settle_todos(ctx, _finished()))
            await asyncio.sleep(0.05)
            ctx.signal_abort()
        await settle

        assert await _statuses(session.id) == ["completed", "pending"]

    @pytest.mark.asyncio
    async def test_stop_during_the_locked_read_reopens_instead_of_clearing(self, monkeypatch):
        session = await _session("stop_while_reading")
        await Todo.update(session.id, _todos("completed", "in_progress"))
        ctx = _ctx(session)
        read = Todo.get_snapshot
        reads = 0

        async def snapshot(session_id):
            nonlocal reads
            reads += 1
            todos = await read(session_id)
            if reads == 2:  # the read under the session write lock
                ctx.signal_abort()
            return todos

        monkeypatch.setattr(Todo, "get_snapshot", snapshot)
        await SessionLoop._settle_todos(ctx, _finished())
        monkeypatch.undo()

        assert reads == 2
        assert await _statuses(session.id) == ["completed", "pending"]


class TestPendingReports:
    def test_submitted_or_running_child(self, monkeypatch):
        monkeypatch.setitem(AgentInbox._children, "ses_job", ("ses_root", None))
        assert AgentInbox.has_pending_reports("ses_root")
        assert not AgentInbox.has_pending_reports("ses_other")

    def test_idle_child_with_running_grandchild(self, monkeypatch):
        # The child already reported once; its own subagent will wake it and
        # it then reports to the root again.
        child = Inbox(parent_id="ses_root_2", result_callback_factory=lambda: None)
        monkeypatch.setitem(AgentInbox._inboxes, "ses_child_2", child)
        assert not AgentInbox.has_pending_reports("ses_root_2")

        monkeypatch.setitem(AgentInbox._children, "ses_grandchild_2", ("ses_child_2", None))
        assert AgentInbox.has_pending_reports("ses_root_2")

    def test_finished_child_counts_again_once_it_has_input(self, monkeypatch):
        child = Inbox(parent_id="ses_root_3", result_callback_factory=lambda: None)
        monkeypatch.setitem(AgentInbox._inboxes, "ses_child_3", child)
        assert not AgentInbox.has_pending_reports("ses_root_3")

        monkeypatch.setitem(AgentInbox._wakes, "ses_child_3", object())
        assert AgentInbox.has_pending_reports("ses_root_3")

    def test_jobs_of_a_stopped_run_do_not_count(self, monkeypatch):
        # After Stop the old result delivery still runs, into a closed inbox.
        monkeypatch.setitem(AgentInbox._children, "ses_old_job", ("ses_root_5", None))
        monkeypatch.setitem(AgentInbox._report_to, "ses_old_job", Inbox(stopped=True))
        assert not AgentInbox.has_pending_reports("ses_root_5")

        monkeypatch.setitem(AgentInbox._report_to, "ses_old_job", Inbox())
        assert AgentInbox.has_pending_reports("ses_root_5")

    def test_stopped_child_does_not_count(self, monkeypatch):
        child = Inbox(parent_id="ses_root_6", result_callback_factory=lambda: None, stopped=True)
        monkeypatch.setitem(AgentInbox._inboxes, "ses_child_6", child)
        monkeypatch.setitem(AgentInbox._wakes, "ses_child_6", object())
        assert not AgentInbox.has_pending_reports("ses_root_6")

    def test_fork_filed_under_the_session_never_reports(self, monkeypatch):
        fork = Inbox(parent_id="ses_root_4")  # WebUI "new from parent": no report callback
        monkeypatch.setitem(AgentInbox._inboxes, "ses_fork_4", fork)
        monkeypatch.setitem(AgentInbox._wakes, "ses_fork_4", object())
        assert not AgentInbox.has_pending_reports("ses_root_4")
