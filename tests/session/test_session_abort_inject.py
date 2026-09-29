"""
Tests for session abort and inject functionality.

Tests cover:
- SessionRunner external abort_event propagation
- SessionLoop abort mechanism
- Inject endpoint logic (message creation without starting new loop)
- _should_exit behavior with injected messages
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.session.message import ToolPart, ToolStateCompleted
from flocks.session.goal import GoalDecision
from flocks.session.prompt import SessionPrompt
from flocks.session.session_loop import SessionLoop, LoopCallbacks, LoopContext, LoopResult
from flocks.session.runner import SessionRunner, StepResult
from flocks.session.session import SessionInfo
from flocks.server.routes import session as session_routes


def _make_session_info(session_id: str = "test_session") -> SessionInfo:
    """Create a minimal SessionInfo via model_construct (skips validation)."""
    return SessionInfo.model_construct(
        id=session_id,
        slug="test",
        project_id="test_project",
        directory="/tmp",
        title="Test Session",
    )


def _make_completed_tool_part(message_id: str) -> ToolPart:
    """Create a completed tool part that should force one more loop iteration."""
    return ToolPart(
        sessionID="test_session",
        messageID=message_id,
        callID="call_001",
        tool="bash",
        state=ToolStateCompleted(
            input={"command": "echo hi"},
            output="hi",
            title="bash",
            metadata={},
            time={"start": 1, "end": 2},
        ),
    )


# ---------------------------------------------------------------------------
# Abort propagation tests
# ---------------------------------------------------------------------------

class TestAbortPropagation:
    """Test that abort_event propagates from SessionLoop to SessionRunner."""

    def test_runner_accepts_external_abort_event(self):
        """SessionRunner should accept an optional external abort_event."""
        external_event = asyncio.Event()
        session_info = _make_session_info()

        runner = SessionRunner(
            session=session_info,
            abort_event=external_event,
        )

        # Initially not aborted
        assert runner.is_aborted is False

        # Set external event → runner should report aborted
        external_event.set()
        assert runner.is_aborted is True

    def test_runner_internal_abort_still_works(self):
        """SessionRunner's own abort() method should still work."""
        session_info = _make_session_info()
        runner = SessionRunner(session=session_info)

        assert runner.is_aborted is False
        runner.abort()
        assert runner.is_aborted is True

    def test_runner_either_abort_triggers(self):
        """Either internal or external abort should trigger is_aborted."""
        external_event = asyncio.Event()
        session_info = _make_session_info()

        runner = SessionRunner(
            session=session_info,
            abort_event=external_event,
        )

        # Neither set → not aborted
        assert runner.is_aborted is False

        # Only external set
        external_event.set()
        assert runner.is_aborted is True

        # Clear external, set internal
        external_event.clear()
        runner._abort.clear()
        assert runner.is_aborted is False

        runner.abort()
        assert runner.is_aborted is True

    def test_runner_without_external_event(self):
        """Runner created without abort_event should still work normally."""
        session_info = _make_session_info()
        runner = SessionRunner(session=session_info)

        assert runner._external_abort is None
        assert runner.is_aborted is False
        runner.abort()
        assert runner.is_aborted is True

    @pytest.mark.asyncio
    async def test_session_loop_run_publishes_busy_and_idle_status_events(self):
        session_info = _make_session_info("status_event_session")
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        with patch(
            "flocks.session.session_loop.Session.get_by_id",
            AsyncMock(return_value=session_info),
        ), patch(
            "flocks.session.session_loop.Message.list",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.orphan_tools.abort_orphan_running_parts",
            AsyncMock(return_value=0),
        ), patch(
            "flocks.session.session_loop.SessionLoop._run_loop",
            AsyncMock(return_value=LoopResult(action="stop")),
        ), patch(
            "flocks.session.session_loop.Session.touch",
            AsyncMock(),
        ), patch(
            "flocks.bus.bus.Bus.publish",
            AsyncMock(),
        ):
            result = await SessionLoop.run(
                session_id=session_info.id,
                provider_id="test-provider",
                model_id="test-model",
                agent_name="rex",
                callbacks=callbacks,
            )

        assert result.action == "stop"
        status_events = [
            call.args
            for call in event_callback.await_args_list
            if call.args and call.args[0] == "session.status"
        ]
        assert status_events == [
            ("session.status", {"sessionID": session_info.id, "status": {"type": "busy"}}),
            ("session.status", {"sessionID": session_info.id, "status": {"type": "idle"}}),
        ]


# ---------------------------------------------------------------------------
# SessionLoop abort tests
# ---------------------------------------------------------------------------

class TestSessionLoopAbort:
    """Test SessionLoop.abort() class method."""

    def test_abort_nonexistent_session(self):
        """Aborting a session that isn't running should return False."""
        result = SessionLoop.abort("nonexistent_session_id")
        assert result is False

    def test_abort_running_session(self):
        """Aborting a running session should set the abort_event and return True."""
        session_info = _make_session_info("test_loop_abort")
        ctx = LoopContext(
            session=session_info,
            provider_id="test",
            model_id="test",
            agent_name="test",
        )

        # Register the context
        SessionLoop._active_loops["test_loop_abort"] = ctx

        try:
            assert ctx.should_abort() is False
            result = SessionLoop.abort("test_loop_abort")
            assert result is True
            assert ctx.should_abort() is True
        finally:
            # Clean up
            SessionLoop._active_loops.pop("test_loop_abort", None)

    def test_is_running(self):
        """is_running should reflect _active_loops state."""
        assert SessionLoop.is_running("not_there") is False

        session_info = _make_session_info("running_test")
        ctx = LoopContext(
            session=session_info,
            provider_id="test",
            model_id="test",
            agent_name="test",
        )
        SessionLoop._active_loops["running_test"] = ctx

        try:
            assert SessionLoop.is_running("running_test") is True
        finally:
            SessionLoop._active_loops.pop("running_test", None)

    def test_get_context(self):
        """get_context should return the LoopContext for a running session."""
        session_info = _make_session_info("ctx_get_test")
        ctx = LoopContext(
            session=session_info,
            provider_id="test",
            model_id="test",
            agent_name="test",
        )
        SessionLoop._active_loops["ctx_get_test"] = ctx

        try:
            retrieved = SessionLoop.get_context("ctx_get_test")
            assert retrieved is ctx
            assert SessionLoop.get_context("nonexistent") is None
        finally:
            SessionLoop._active_loops.pop("ctx_get_test", None)


# ---------------------------------------------------------------------------
# _should_exit logic with injected messages
# ---------------------------------------------------------------------------

class TestShouldExitWithInject:
    """Test that _should_exit correctly handles injected user messages."""

    @staticmethod
    def _make_msg(msg_id: str, role: str, finish: str = None):
        """Create a minimal message-like object for testing."""
        msg = type("Msg", (), {})()
        msg.id = msg_id
        msg.role = role
        msg.finish = finish
        msg.parentID = None
        return msg

    def test_exit_when_assistant_replies_to_user_and_finished(self):
        """Should exit if last assistant is a finished reply to last user."""
        last_user = self._make_msg("msg_002", "user")
        last_assistant = self._make_msg("msg_001", "assistant", finish="stop")
        last_assistant.parentID = last_user.id

        assert SessionLoop._should_exit(last_user, last_assistant) is True

    def test_no_exit_when_assistant_belongs_to_previous_user(self):
        """Should NOT exit when a new user message follows the assistant.

        This is the core inject scenario: the last assistant belongs to the
        preceding user turn, so the loop should continue regardless of IDs.
        """
        last_user = self._make_msg("msg_001", "user")
        last_assistant = self._make_msg("msg_002", "assistant", finish="stop")
        last_assistant.parentID = "msg_previous_user"

        assert SessionLoop._should_exit(last_user, last_assistant) is False

    def test_no_exit_when_assistant_has_tool_calls(self):
        """Should NOT exit when assistant finish is 'tool-calls'."""
        last_user = self._make_msg("msg_001", "user")
        last_assistant = self._make_msg("msg_002", "assistant", finish="tool-calls")

        assert SessionLoop._should_exit(last_user, last_assistant) is False

    def test_no_exit_when_no_assistant(self):
        """Should NOT exit when there is no assistant message yet."""
        last_user = self._make_msg("msg_001", "user")

        assert SessionLoop._should_exit(last_user, None) is False

    def test_no_exit_when_assistant_finish_is_unknown(self):
        """Should NOT exit when finish reason is 'unknown'."""
        last_user = self._make_msg("msg_001", "user")
        last_assistant = self._make_msg("msg_002", "assistant", finish="unknown")

        assert SessionLoop._should_exit(last_user, last_assistant) is False

    def test_no_exit_when_assistant_not_finished(self):
        """Should NOT exit when assistant has no finish status."""
        last_user = self._make_msg("msg_001", "user")
        last_assistant = self._make_msg("msg_002", "assistant", finish=None)

        assert SessionLoop._should_exit(last_user, last_assistant) is False

    def test_no_exit_when_assistant_has_completed_tool_parts(self):
        """Should continue so completed tool results can be fed back to the model."""
        last_user = self._make_msg("msg_001", "user")
        last_assistant = self._make_msg("msg_002", "assistant", finish="stop")
        last_assistant_parts = [_make_completed_tool_part(last_assistant.id)]

        assert SessionLoop._should_exit(
            last_user,
            last_assistant,
            last_assistant_parts,
        ) is False


class TestQueuedUserDetection:
    @staticmethod
    def _make_msg(msg_id: str, role: str):
        msg = type("Msg", (), {})()
        msg.id = msg_id
        msg.role = role
        return msg

    @pytest.mark.asyncio
    async def test_does_not_treat_current_user_as_queued_when_no_assistant_exists(self):
        current_user = self._make_msg("msg_001", "user")

        queued = await SessionLoop._detect_queued_user_message(
            "session-1",
            [current_user],
            current_user.id,
            None,
        )

        assert queued is None

    @pytest.mark.asyncio
    async def test_detects_newer_user_when_step_failed_before_assistant_created(self):
        current_user = self._make_msg("msg_001", "user")
        newer_user = self._make_msg("msg_002", "user")

        queued = await SessionLoop._detect_queued_user_message(
            "session-1",
            [current_user, newer_user],
            current_user.id,
            None,
        )

        assert queued is newer_user


class TestTurnLifecycle:
    @staticmethod
    def _make_msg(msg_id: str, role: str, finish: str = None, *, tokens=None, summary: bool = False):
        msg = type("Msg", (), {})()
        msg.id = msg_id
        msg.role = role
        msg.finish = finish
        msg.parentID = None
        msg.tokens = tokens
        msg.summary = summary
        return msg

    @pytest.mark.asyncio
    async def test_run_loop_stops_turn_when_messages_are_empty(self):
        session = SimpleNamespace(
            id="turn_no_messages_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        ctx.session_ctx = SimpleNamespace(get_messages=AsyncMock(return_value=[]))
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        result = await SessionLoop._run_loop(ctx, callbacks)

        assert result.action == "stop"
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == ["turn.started", "turn.stopped"]
        stopped_payload = event_callback.await_args_list[1].args[1]
        assert stopped_payload["stop_reason"] == "no_messages"

    @pytest.mark.asyncio
    async def test_run_loop_stops_turn_when_no_user_message_exists(self):
        session = SimpleNamespace(
            id="turn_no_user_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        assistant = self._make_msg("msg_001", "assistant", finish="stop")
        ctx.session_ctx = SimpleNamespace(get_messages=AsyncMock(return_value=[assistant]))
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        result = await SessionLoop._run_loop(ctx, callbacks)

        assert result.action == "stop"
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == ["turn.started", "turn.stopped"]
        stopped_payload = event_callback.await_args_list[1].args[1]
        assert stopped_payload["stop_reason"] == "no_user_message"

    @pytest.mark.asyncio
    async def test_run_loop_continues_for_active_goal_after_stop(self):
        session = SimpleNamespace(
            id="turn_goal_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        user = self._make_msg("msg_001", "user")
        assistant = self._make_msg("msg_002", "assistant", finish="stop")
        assistant.parentID = user.id
        goal_user = self._make_msg("msg_003", "user")
        assistant_after_goal = self._make_msg("msg_004", "assistant", finish="stop")
        assistant_after_goal.parentID = goal_user.id
        ctx.session_ctx = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[
                [user],
                [user, assistant],
                [user, assistant, goal_user],
                [user, assistant, goal_user, assistant_after_goal],
            ])
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)
        goal_decisions = [
            GoalDecision(
                status="active",
                verdict="continue",
                should_continue=True,
                continuation_prompt="continue toward goal",
                reason="not done",
            ),
            GoalDecision(status="completed", verdict="inactive"),
        ]

        with patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.session_loop.Message.get_text_content",
            MagicMock(return_value="still working"),
        ), patch(
            "flocks.session.session_loop.Message.create",
            AsyncMock(return_value=goal_user),
        ) as create_message, patch(
            "flocks.session.session_loop.GoalManager.evaluate_after_turn",
            AsyncMock(side_effect=goal_decisions),
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(side_effect=[StepResult(action="stop"), StepResult(action="stop")]),
        ):
            result = await SessionLoop._run_loop(ctx, callbacks)

        assert result.action == "stop"
        assert result.last_message is assistant_after_goal
        create_message.assert_awaited_once()
        assert create_message.await_args.kwargs["content"] == "continue toward goal"
        assert create_message.await_args.kwargs["synthetic"] is True
        assert create_message.await_args.kwargs["part_metadata"]["goalContinuation"] is True
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == [
            "turn.started",
            "turn.continued",
            "turn.started",
            "turn.stopped",
        ]
        continued_payload = event_callback.await_args_list[1].args[1]
        assert continued_payload["continue_reason"] == "goal"
        assert continued_payload["goalMessageID"] == goal_user.id

    @pytest.mark.asyncio
    async def test_run_loop_waits_for_user_input_after_goal_clarification(self):
        session = SimpleNamespace(
            id="turn_goal_waiting_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        user = self._make_msg("msg_001", "user")
        assistant = self._make_msg("msg_002", "assistant", finish="stop")
        assistant.parentID = user.id
        ctx.session_ctx = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[
                [user],
                [user, assistant],
            ])
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        with patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.session_loop.Message.get_text_content",
            MagicMock(return_value="Please clarify what tests to write."),
        ), patch(
            "flocks.session.session_loop.Message.create",
            AsyncMock(),
        ) as create_message, patch(
            "flocks.session.session_loop.GoalManager.evaluate_after_turn",
            AsyncMock(return_value=GoalDecision(
                status="active",
                verdict="waiting",
                should_continue=False,
                reason="waiting for user clarification",
            )),
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ):
            result = await SessionLoop._run_loop(ctx, callbacks)

        assert result.action == "stop"
        create_message.assert_not_awaited()
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == ["turn.started", "turn.stopped"]

    @pytest.mark.asyncio
    async def test_run_loop_passes_pending_question_to_goal_judge(self):
        session = SimpleNamespace(
            id="turn_goal_pending_question_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        user = self._make_msg("msg_001", "user")
        assistant = self._make_msg("msg_002", "assistant", finish="stop")
        assistant.parentID = user.id
        ctx.session_ctx = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[
                [user],
                [user, assistant],
            ])
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)
        evaluate_goal = AsyncMock(return_value=GoalDecision(
            status="active",
            verdict="waiting",
            should_continue=False,
            reason="session has a pending user question",
        ))

        with patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.session_loop.Message.get_text_content",
            MagicMock(return_value="Please provide the input."),
        ), patch(
            "flocks.server.routes.question.has_pending_questions",
            MagicMock(return_value=True),
        ), patch(
            "flocks.session.session_loop.Message.create",
            AsyncMock(),
        ) as create_message, patch(
            "flocks.session.session_loop.GoalManager.evaluate_after_turn",
            evaluate_goal,
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ):
            result = await SessionLoop._run_loop(ctx, callbacks)

        assert result.action == "stop"
        create_message.assert_not_awaited()
        assert evaluate_goal.await_args.kwargs["pending_user_input"] is True
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == ["turn.started", "turn.stopped"]

    @pytest.mark.asyncio
    async def test_run_loop_publishes_goal_terminal_status(self):
        session = SimpleNamespace(
            id="turn_goal_done_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        messages = [
            self._make_msg("msg_001", "user"),
            self._make_msg("msg_002", "assistant", finish="stop"),
        ]
        messages[1].parentID = messages[0].id
        ctx.session_ctx = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[[messages[0]], messages])
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        with patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.session_loop.Message.get_text_content",
            MagicMock(return_value="Goal complete: done"),
        ), patch(
            "flocks.session.session_loop.GoalManager.evaluate_after_turn",
            AsyncMock(return_value=GoalDecision(
                status="completed",
                verdict="complete",
                reason="Goal complete: done",
                objective="finish work",
            )),
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ):
            result = await SessionLoop._run_loop(ctx, callbacks)

        assert result.action == "stop"
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == ["turn.started", "session.goal.updated", "turn.stopped"]
        goal_payload = event_callback.await_args_list[1].args[1]
        assert goal_payload == {
            "sessionID": session.id,
            "status": "completed",
            "objective": "finish work",
            "reason": "Goal complete: done",
        }

    @pytest.mark.asyncio
    async def test_pre_compact_cleanup_emits_turn_continued_before_next_iteration(self):
        session = SimpleNamespace(
            id="turn_cleanup_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        overflow_messages = [
            self._make_msg("msg_001", "user"),
            self._make_msg(
                "msg_002",
                "assistant",
                finish="tool-calls",
                tokens={"input": 50000, "output": 0, "cache": {"read": 0, "write": 0}},
            ),
        ]
        normal_messages = [
            self._make_msg("msg_001", "user"),
            self._make_msg(
                "msg_002",
                "assistant",
                finish="tool-calls",
                tokens={"input": 0, "output": 0, "cache": {"read": 0, "write": 0}},
            ),
        ]
        ctx.session_ctx = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[overflow_messages, normal_messages, normal_messages])
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)
        truncated = False

        async def truncate_tool_outputs(*_args, **_kwargs):
            nonlocal truncated
            truncated = True
            return 1

        async def estimate_messages(_session_id, messages, **_kwargs):
            # The whole overflowing conversation is large until its tool
            # output is truncated; any smaller slice is negligible.
            return 50_000 if messages is overflow_messages and not truncated else 0

        with patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(20000, 1024, None),
        ), patch(
            "flocks.session.session_loop.SessionCompaction.truncate_oversized_tool_outputs",
            truncate_tool_outputs,
        ), patch(
            "flocks.session.session_loop.SessionPrompt.estimate_full_context_tokens",
            estimate_messages,
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ):
            result = await SessionLoop._run_loop(ctx, callbacks)

        assert result.action == "stop"
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == [
            "turn.started",
            "context.compacted",
            "turn.continued",
            "turn.started",
            "turn.stopped",
        ]
        cleanup_turn = event_callback.await_args_list[2].args[1]
        assert cleanup_turn["continue_reason"] == "pre_compact_cleanup"
        assert cleanup_turn["status"] == "continued"

    @pytest.mark.asyncio
    async def test_post_observation_tool_delta_combines_with_observed_prompt(self):
        session = SimpleNamespace(
            id="turn_stale_usage_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        messages = [
            self._make_msg("stale_usage_user", "user"),
            self._make_msg(
                "stale_usage_assistant",
                "assistant",
                finish="tool-calls",
                tokens={"input": 95_000, "output": 0, "cache": {"read": 0, "write": 0}},
            ),
        ]
        messages[0].content = "h" * 260_000
        ctx.session_ctx = SimpleNamespace(get_messages=AsyncMock(return_value=messages))
        tool_parts = [
            ToolPart(
                sessionID=session.id,
                messageID="stale_usage_assistant",
                callID=f"call_delta_{index}",
                tool="bash",
                state=ToolStateCompleted(
                    input={"command": f"produce output {index}"},
                    output="x" * 80_000,
                    title="bash",
                    metadata={},
                    time={"start": index, "end": index + 1},
                ),
            )
            for index in range(2)
        ]
        run_compaction = AsyncMock(return_value="stop")
        parts_by_message = {"stale_usage_assistant": []}
        truncation_calls = 0

        async def truncate_one_tool_result(*args, **kwargs):  # noqa: ARG001
            nonlocal truncation_calls
            truncation_calls += 1
            if truncation_calls == 1:
                tool_parts[0].state.time["compacted"] = 1
                return 1
            return 0

        with patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(128_000, 8_192, None),
        ), patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(
                side_effect=lambda message_id, _session_id: (
                    list(parts_by_message.get(message_id, []))
                ),
            ),
        ), patch(
            "flocks.session.session_loop.SessionCompaction.truncate_oversized_tool_outputs",
            AsyncMock(side_effect=truncate_one_tool_result),
        ), patch(
            "flocks.session.session_loop.SessionCompaction.prune",
            AsyncMock(),
        ), patch(
            "flocks.session.session_loop.run_compaction",
            run_compaction,
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ):
            estimated_tokens = await SessionPrompt.estimate_full_context_tokens(
                session.id,
                messages,
            )
            parts_by_message["stale_usage_assistant"] = tool_parts
            result = await SessionLoop._run_loop(ctx, LoopCallbacks())
            current_message_tokens = await SessionPrompt.estimate_full_context_tokens(
                session.id,
                messages,
            )

        assert estimated_tokens < int(128_000 * 0.85)
        assert current_message_tokens < int(128_000 * 0.85)
        assert result.action == "stop"
        run_compaction.assert_awaited_once()
        assert truncation_calls == 2
        assert ctx.last_observed_prompt_tokens == 95_000

    async def _run_overflow_cleanup(
        self,
        *,
        session_id,
        old_output_chars,
        run_compaction,
        cleanup="prune",
    ):
        """Observed 95K + ~20K of new tool output overflows 108.8K (128K
        window).  ``cleanup`` ("prune" or "truncate", overflow recovery step
        2 or 1) drops an earlier turn's ``read`` output."""
        session = SimpleNamespace(
            id=session_id,
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        messages = [
            self._make_msg(f"{session_id}_user_a", "user"),
            self._make_msg(f"{session_id}_assistant_a", "assistant", finish="stop"),
            self._make_msg(f"{session_id}_user_b", "user"),
            self._make_msg(
                f"{session_id}_assistant_b",
                "assistant",
                finish="tool-calls",
                tokens={"input": 95_000, "output": 0, "cache": {"read": 0, "write": 0}},
            ),
        ]
        messages[1].parentID = messages[0].id
        messages[3].parentID = messages[2].id
        ctx.session_ctx = SimpleNamespace(get_messages=AsyncMock(return_value=messages))
        old_output = ToolPart(
            sessionID=session.id,
            messageID=f"{session_id}_assistant_a",
            callID="call_old_read",
            tool="read",
            state=ToolStateCompleted(
                input={"filePath": "/tmp/big.md"},
                output="o" * old_output_chars,  # an earlier turn's read
                title="read",
                metadata={},
                time={"start": 1, "end": 2},
            ),
        )
        new_output = ToolPart(
            sessionID=session.id,
            messageID=f"{session_id}_assistant_b",
            callID="call_new_bash",
            tool="bash",
            state=ToolStateCompleted(
                input={"command": "ls"},
                output="n" * 80_000,  # ~20K tokens produced after the call
                title="bash",
                metadata={},
                time={"start": 3, "end": 4},
            ),
        )
        parts_by_message = {
            f"{session_id}_assistant_a": [old_output],
            f"{session_id}_assistant_b": [new_output],
        }

        def drop_old_output():
            old_output.state.time["compacted"] = 5
            SessionPrompt.invalidate_message_cache(f"{session_id}_assistant_a")

        order = []

        async def prune(*_args, **_kwargs):
            order.append("prune")
            if cleanup == "prune":
                drop_old_output()

        async def push_before_cleanup(*_args, **_kwargs):
            order.append("usage_push")

        forced_pushes = []

        def schedule_push(_cls, _ctx, _callbacks, *, force=False):
            forced_pushes.append(force)

        truncate_calls = []

        async def truncate(*_args, **_kwargs):
            # The first call is the preemptive cleanup before the overflow
            # check; the second is overflow recovery step 1.
            truncate_calls.append(1)
            if cleanup == "truncate" and len(truncate_calls) == 2:
                drop_old_output()
                return 1
            return 0

        prune_mock = AsyncMock(side_effect=prune)
        process_step = AsyncMock(return_value=StepResult(action="stop"))
        event_callback = AsyncMock()

        with patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(128_000, 8_192, None),
        ), patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(
                side_effect=lambda message_id, _session_id: (
                    list(parts_by_message.get(message_id, []))
                ),
            ),
        ), patch.object(
            SessionLoop,
            "_push_context_usage_before_cleanup",
            AsyncMock(side_effect=push_before_cleanup),
        ), patch.object(
            SessionLoop,
            "_schedule_context_usage_push",
            classmethod(schedule_push),
        ), patch(
            "flocks.session.session_loop.SessionCompaction.truncate_oversized_tool_outputs",
            AsyncMock(side_effect=truncate),
        ), patch(
            "flocks.session.session_loop.SessionCompaction.prune",
            prune_mock,
        ), patch(
            "flocks.session.session_loop.run_compaction",
            run_compaction,
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            process_step,
        ):
            result = await SessionLoop._run_loop(
                ctx,
                LoopCallbacks(event_publish_callback=event_callback),
            )

        compacted = [
            call.args[1] for call in event_callback.await_args_list
            if call.args[0] == "context.compacted"
        ]
        return SimpleNamespace(
            result=result,
            ctx=ctx,
            process_step=process_step,
            prune=prune_mock,
            truncate_calls=len(truncate_calls),
            compacted=compacted,
            order=order,
            forced_pushes=[force for force in forced_pushes if force],
        )

    @pytest.mark.asyncio
    async def test_overflow_prunes_then_always_summarizes(self):
        """Pruning is only the first half of overflow recovery: a summary
        follows even when pruning alone got the prompt back under the
        threshold, so the session does not sit near the limit."""
        run_compaction = AsyncMock(return_value="stop")

        # ~50K tokens pruned: 95K - 50K + 20K = 65K, well under 108.8K.
        run = await self._run_overflow_cleanup(
            session_id="turn_prune_then_summary_session",
            old_output_chars=200_000,
            run_compaction=run_compaction,
        )

        assert run.result.action == "stop"
        assert run.order == ["usage_push", "prune"]
        run_compaction.assert_awaited_once()
        run.process_step.assert_not_awaited()
        assert run.compacted == []
        assert run.ctx.overflow_compaction_attempts == 1

    @pytest.mark.asyncio
    async def test_truncation_that_resolves_overflow_skips_prune_and_summary(self):
        run_compaction = AsyncMock(return_value="stop")

        run = await self._run_overflow_cleanup(
            session_id="turn_truncate_resolves_session",
            old_output_chars=120_000,
            run_compaction=run_compaction,
            cleanup="truncate",
        )

        assert run.truncate_calls == 2
        run.prune.assert_not_awaited()
        run_compaction.assert_not_awaited()
        run.process_step.assert_awaited_once()
        # The meter still showed the pre-cleanup size; the next step's push
        # would fall inside the throttle window.
        assert run.forced_pushes == [True]

    @pytest.mark.asyncio
    async def test_step_without_usage_is_judged_by_the_previous_observation(self):
        """A stream cut mid tool call reports no usage.  The overflow check
        must not fall back to the message-only estimate, which leaves out the
        system prompt and tool definitions the provider counted."""
        from flocks.session.context_usage import estimate_observed_context_tokens

        session = SimpleNamespace(
            id="turn_no_usage_step_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        messages = [
            self._make_msg("no_usage_user", "user"),
            self._make_msg(
                "no_usage_observed",
                "assistant",
                finish="tool-calls",
                tokens={"input": 105_000, "output": 0, "cache": {"read": 0, "write": 0}},
            ),
            self._make_msg(
                "no_usage_cut_step",
                "assistant",
                finish="tool-calls",
                tokens={"input": 0, "output": 0, "cache": {"read": 0, "write": 0}},
            ),
        ]
        messages[2].content = "n" * 20_000  # ~5K tokens produced by the cut step
        ctx.session_ctx = SimpleNamespace(get_messages=AsyncMock(return_value=messages))
        run_compaction = AsyncMock(return_value="stop")
        process_step = AsyncMock(return_value=StepResult(action="stop"))

        with patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(128_000, 8_192, None),
        ), patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.session_loop.SessionCompaction.truncate_oversized_tool_outputs",
            AsyncMock(return_value=0),
        ), patch(
            "flocks.session.session_loop.SessionCompaction.prune",
            AsyncMock(),
        ), patch(
            "flocks.session.session_loop.run_compaction",
            run_compaction,
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            process_step,
        ):
            # The loop examined the observed step one iteration earlier.
            await estimate_observed_context_tokens(session.id, messages, 1, 105_000)
            result = await SessionLoop._run_loop(ctx, LoopCallbacks())

        assert result.action == "stop"
        run_compaction.assert_awaited_once()  # 105K observed + 5K > 108.8K
        process_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cache_writes_count_toward_overflow(self):
        """A cold-cache Anthropic call reports most of its prompt as cache
        writes; leaving them out hid an overflowing prompt from the trigger."""
        session = SimpleNamespace(
            id="turn_cache_write_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        messages = [
            self._make_msg("cache_write_user", "user"),
            self._make_msg(
                "cache_write_assistant",
                "assistant",
                finish="tool-calls",
                tokens={"input": 50, "output": 0, "cache": {"read": 0, "write": 110_000}},
            ),
        ]
        ctx.session_ctx = SimpleNamespace(get_messages=AsyncMock(return_value=messages))
        run_compaction = AsyncMock(return_value="stop")
        process_step = AsyncMock(return_value=StepResult(action="stop"))

        with patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(128_000, 8_192, None),
        ), patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.session_loop.SessionCompaction.truncate_oversized_tool_outputs",
            AsyncMock(return_value=0),
        ), patch(
            "flocks.session.session_loop.SessionCompaction.prune",
            AsyncMock(),
        ), patch(
            "flocks.session.session_loop.run_compaction",
            run_compaction,
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            process_step,
        ):
            result = await SessionLoop._run_loop(ctx, LoopCallbacks())

        assert result.action == "stop"
        run_compaction.assert_awaited_once()  # 110,050 > 108,800
        process_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_loop_skips_exit_condition_when_assistant_has_tool_parts(self):
        session = SimpleNamespace(
            id="loop_tool_part_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        messages = [
            self._make_msg("msg_001", "user"),
            self._make_msg("msg_002", "assistant", finish="stop"),
        ]
        messages[1].parentID = messages[0].id
        ctx.session_ctx = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[messages, messages])
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)
        process_step = AsyncMock(return_value=StepResult(action="stop"))
        log_info = MagicMock()

        with patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[_make_completed_tool_part("msg_002")]),
        ), patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.lifecycle.title.SessionTitle.ensure_title",
            MagicMock(return_value=None),
        ), patch(
            "flocks.session.session_loop.fire_and_forget",
            MagicMock(),
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            process_step,
        ), patch(
            "flocks.session.session_loop.log.info",
            log_info,
        ):
            result = await SessionLoop._run_loop(ctx, callbacks)

        assert result.action == "stop"
        assert result.last_message is messages[1]
        assert process_step.await_count == 1
        assert not any(call.args and call.args[0] == "loop.exit_condition" for call in log_info.call_args_list)
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == ["turn.started", "turn.stopped"]

    @pytest.mark.asyncio
    async def test_run_loop_breaks_on_exit_condition_without_tool_parts(self):
        session = SimpleNamespace(
            id="loop_exit_condition_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        messages = [
            self._make_msg("msg_001", "user"),
            self._make_msg("msg_002", "assistant", finish="stop"),
        ]
        messages[1].parentID = messages[0].id
        ctx.session_ctx = SimpleNamespace(
            get_messages=AsyncMock(return_value=messages)
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)
        process_step = AsyncMock(return_value=StepResult(action="stop"))
        log_info = MagicMock()

        with patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.session_loop.log.info",
            log_info,
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            process_step,
        ):
            result = await SessionLoop._run_loop(ctx, callbacks)

        assert result.action == "stop"
        assert result.last_message is messages[1]
        assert process_step.await_count == 0
        assert any(call.args and call.args[0] == "loop.exit_condition" for call in log_info.call_args_list)
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == ["turn.started"]

    @pytest.mark.asyncio
    async def test_run_loop_processes_new_user_when_ids_are_not_monotonic(
        self,
    ) -> None:
        session = SimpleNamespace(
            id="loop_non_monotonic_id_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        previous_user = self._make_msg("msg_previous_user", "user")
        previous_assistant = self._make_msg(
            "msg_ffedcf5c6001TWU0fGZXuDeY00", "assistant", finish="stop"
        )
        previous_assistant.parentID = previous_user.id
        current_user = self._make_msg(
            "msg_ffed09fd1001S0plX81SJ55NUz",
            "user",
        )
        current_assistant = self._make_msg(
            "msg_current_assistant", "assistant", finish="stop"
        )
        current_assistant.parentID = current_user.id
        messages_before_step = [previous_user, previous_assistant, current_user]
        messages_after_step = [*messages_before_step, current_assistant]
        ctx.session_ctx = SimpleNamespace(
            get_messages=AsyncMock(
                side_effect=[messages_before_step, messages_after_step]
            )
        )
        process_step = AsyncMock(return_value=StepResult(action="stop"))

        with patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.lifecycle.title.SessionTitle.ensure_title",
            MagicMock(return_value=None),
        ), patch(
            "flocks.session.session_loop.fire_and_forget",
            MagicMock(),
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            process_step,
        ):
            result = await SessionLoop._run_loop(ctx, LoopCallbacks())

        assert result.last_message is current_assistant
        process_step.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_loop_does_not_return_previous_reply_when_current_step_fails(
        self,
    ) -> None:
        session = SimpleNamespace(
            id="loop_failed_current_turn_session",
            agent="rex",
            directory="/tmp",
            memory_enabled=False,
        )
        ctx = LoopContext(
            session=session,
            provider_id="test-provider",
            model_id="test-model",
            agent_name="rex",
        )
        previous_user = self._make_msg("msg_previous_user", "user")
        previous_assistant = self._make_msg(
            "msg_previous_assistant",
            "assistant",
            finish="stop",
        )
        previous_assistant.parentID = previous_user.id
        current_user = self._make_msg("msg_current_user", "user")
        messages = [previous_user, previous_assistant, current_user]
        ctx.session_ctx = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[messages, messages])
        )
        on_error = AsyncMock()
        process_step = AsyncMock(
            return_value=StepResult(action="stop", error="provider failed")
        )

        with patch(
            "flocks.session.session_loop.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.session_loop.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.lifecycle.title.SessionTitle.ensure_title",
            MagicMock(return_value=None),
        ), patch(
            "flocks.session.session_loop.fire_and_forget",
            MagicMock(),
        ), patch(
            "flocks.session.runner.SessionRunner._process_step",
            process_step,
        ):
            result = await SessionLoop._run_loop(
                ctx,
                LoopCallbacks(on_error=on_error),
            )

        assert result.last_message is None
        on_error.assert_awaited_once_with("provider failed")
        process_step.assert_awaited_once()



# ---------------------------------------------------------------------------
# LoopContext tests
# ---------------------------------------------------------------------------

class TestLoopContext:
    """Test LoopContext abort event lifecycle."""

    def test_signal_and_check_abort(self):
        """signal_abort should cause should_abort to return True."""
        session_info = _make_session_info("ctx_abort_test")
        ctx = LoopContext(
            session=session_info,
            provider_id="test",
            model_id="test",
            agent_name="test",
        )

        assert ctx.should_abort() is False
        ctx.signal_abort()
        assert ctx.should_abort() is True

    def test_abort_event_is_asyncio_event(self):
        """abort_event should be a proper asyncio.Event."""
        session_info = _make_session_info("event_type_test")
        ctx = LoopContext(
            session=session_info,
            provider_id="test",
            model_id="test",
            agent_name="test",
        )

        assert isinstance(ctx.abort_event, asyncio.Event)

    def test_step_counter_default(self):
        """Step counter should default to 0."""
        session_info = _make_session_info("step_test")
        ctx = LoopContext(
            session=session_info,
            provider_id="test",
            model_id="test",
            agent_name="test",
        )
        assert ctx.step == 0


@pytest.mark.real_context_usage_push
class TestContextUsagePush:
    """Mid-turn composer meter updates (the route only pushes at turn end)."""

    @pytest.mark.asyncio
    async def test_push_publishes_snapshot_and_is_throttled(self):
        ctx = LoopContext(
            session=_make_session_info("ctx_usage_push"),
            provider_id="gateway",
            model_id="gpt-test",
            agent_name="rex",
        )
        published = []

        async def publish(event_name, payload):
            published.append((event_name, payload))

        snapshot = MagicMock()
        snapshot.model_dump.return_value = {"sessionID": "ctx_usage_push", "usedTokens": 42}
        build = AsyncMock(return_value=snapshot)
        callbacks = LoopCallbacks(event_publish_callback=publish)

        with patch("flocks.session.session_loop.build_context_usage_snapshot", build):
            SessionLoop._schedule_context_usage_push(ctx, callbacks)
            SessionLoop._schedule_context_usage_push(ctx, callbacks)  # throttled
            await ctx.context_usage_push_task

        assert published == [(
            "context.usage.updated",
            {"sessionID": "ctx_usage_push", "usedTokens": 42},
        )]
        build.assert_awaited_once()
        assert build.await_args.kwargs["provider_id"] == "gateway"
        assert build.await_args.kwargs["model_id"] == "gpt-test"

    @pytest.mark.asyncio
    async def test_push_failure_never_raises(self):
        ctx = LoopContext(
            session=_make_session_info("ctx_usage_push_failure"),
            provider_id="gateway",
            model_id="gpt-test",
            agent_name="rex",
        )
        publish = AsyncMock()
        with patch(
            "flocks.session.session_loop.build_context_usage_snapshot",
            AsyncMock(side_effect=RuntimeError("storage gone")),
        ):
            SessionLoop._schedule_context_usage_push(
                ctx,
                LoopCallbacks(event_publish_callback=publish),
            )
            await ctx.context_usage_push_task

        publish.assert_not_awaited()

    def test_push_needs_a_publish_callback(self):
        ctx = LoopContext(
            session=_make_session_info("ctx_usage_push_no_callback"),
            provider_id="gateway",
            model_id="gpt-test",
            agent_name="rex",
        )
        SessionLoop._schedule_context_usage_push(ctx, LoopCallbacks())
        assert ctx.context_usage_push_task is None

    @pytest.mark.asyncio
    async def test_forced_push_after_cleanup_lands_after_a_slower_earlier_push(self):
        """A snapshot taken before pruning must not be the last one the
        composer sees."""
        ctx = LoopContext(
            session=_make_session_info("ctx_usage_push_forced"),
            provider_id="gateway",
            model_id="gpt-test",
            agent_name="rex",
        )
        published = []

        async def publish(event_name, payload):
            published.append(payload["label"])

        release_first = asyncio.Event()
        labels = iter(["before_prune", "after_prune"])

        async def build(*_args, **_kwargs):
            label = next(labels)
            if label == "before_prune":
                await release_first.wait()
            snapshot = MagicMock()
            snapshot.model_dump.return_value = {"label": label}
            return snapshot

        callbacks = LoopCallbacks(event_publish_callback=publish)
        with patch("flocks.session.session_loop.build_context_usage_snapshot", build):
            SessionLoop._schedule_context_usage_push(ctx, callbacks)
            first = ctx.context_usage_push_task
            await asyncio.sleep(0)  # first push is now building its snapshot
            SessionLoop._schedule_context_usage_push(ctx, callbacks, force=True)
            forced = ctx.context_usage_push_task
            assert forced is not first
            await asyncio.sleep(0)
            release_first.set()
            await forced

        assert published == ["before_prune", "after_prune"]

    @pytest.mark.asyncio
    async def test_pre_cleanup_push_is_published_before_returning(self):
        """Overflow recovery prunes right after this; the meter must already
        show the size that triggered it, not a snapshot read mid-prune."""
        ctx = LoopContext(
            session=_make_session_info("ctx_usage_push_pre_cleanup"),
            provider_id="gateway",
            model_id="gpt-test",
            agent_name="rex",
        )
        published = []

        async def publish(event_name, payload):
            published.append(payload["usedTokens"])

        async def build(*_args, **_kwargs):
            await asyncio.sleep(0.01)
            snapshot = MagicMock()
            snapshot.model_dump.return_value = {"usedTokens": 97_000}
            return snapshot

        # A throttled period would normally skip the push.
        ctx.last_context_usage_push_at = time.monotonic()
        with patch("flocks.session.session_loop.build_context_usage_snapshot", build):
            await SessionLoop._push_context_usage_before_cleanup(
                ctx,
                LoopCallbacks(event_publish_callback=publish),
            )

        assert published == [97_000]

    @pytest.mark.asyncio
    async def test_pre_cleanup_push_does_not_block_or_break_recovery(self, monkeypatch):
        ctx = LoopContext(
            session=_make_session_info("ctx_usage_push_pre_cleanup_slow"),
            provider_id="gateway",
            model_id="gpt-test",
            agent_name="rex",
        )
        monkeypatch.setattr(
            "flocks.session.session_loop.CONTEXT_USAGE_PUSH_WAIT_SECONDS",
            0.05,
        )
        never = asyncio.Event()

        async def build(*_args, **_kwargs):
            await never.wait()

        callbacks = LoopCallbacks(event_publish_callback=AsyncMock())
        with patch("flocks.session.session_loop.build_context_usage_snapshot", build):
            started = time.monotonic()
            await SessionLoop._push_context_usage_before_cleanup(ctx, callbacks)
            assert time.monotonic() - started < 1.0

            # A push cancelled elsewhere (e.g. at shutdown) must not cancel
            # the loop that waits for it.
            SessionLoop._schedule_context_usage_push(ctx, callbacks, force=True)
            ctx.context_usage_push_task.cancel()
            await SessionLoop._push_context_usage_before_cleanup(ctx, callbacks)
            never.set()
