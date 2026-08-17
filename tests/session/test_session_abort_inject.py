"""
Tests for session abort and inject functionality.

Tests cover:
- StepEngine external abort_event propagation
- SessionLoop abort mechanism
- Inject endpoint logic (message creation without starting new loop)
- _should_exit behavior with injected messages
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.session.runtime.continuation_policy import DEFAULT_CONTINUATION_POLICY
from flocks.session.runtime.session_turn import LoopContext as RuntimeLoopContext
from flocks.session.message import ToolPart, ToolStateCompleted
from flocks.session.goal import GoalDecision
from flocks.session.session_loop import (
    LoopCallbacks,
    LoopContext,
    SessionLoop,
)
from flocks.session.runtime.step_engine import StepEngine, StepResult
from flocks.session.session import SessionInfo
from flocks.server.routes import session as session_routes
from tests.session_runtime_testkit import run_logical_turns


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
    """Test that abort_event propagates from SessionLoop to StepEngine."""

    def test_runner_uses_supplied_abort_event(self):
        """StepEngine should share the supplied turn abort event."""
        abort_event = asyncio.Event()
        session_info = _make_session_info()

        runner = StepEngine(
            session=session_info,
            abort_event=abort_event,
        )

        assert runner._abort is abort_event
        assert runner.is_aborted is False

        runner.abort()
        assert abort_event.is_set()
        assert runner.is_aborted is True

    def test_runner_creates_abort_event_when_omitted(self):
        """Standalone engines should create their own abort event."""
        session_info = _make_session_info()
        runner = StepEngine(session=session_info)

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
            "flocks.session.runtime.session_turn.Message.list",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.orphan_tools.abort_orphan_running_parts",
            AsyncMock(return_value=0),
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
        SessionLoop._active_turns["test_loop_abort"] = ctx

        try:
            assert ctx.should_abort() is False
            result = SessionLoop.abort("test_loop_abort")
            assert result is True
            assert ctx.should_abort() is True
        finally:
            # Clean up
            SessionLoop._active_turns.pop("test_loop_abort", None)

    def test_is_running(self):
        """is_running should reflect the runtime lease registry."""
        assert SessionLoop.is_running("not_there") is False

        session_info = _make_session_info("running_test")
        ctx = LoopContext(
            session=session_info,
            provider_id="test",
            model_id="test",
            agent_name="test",
        )
        SessionLoop._active_turns["running_test"] = ctx

        try:
            assert SessionLoop.is_running("running_test") is True
        finally:
            SessionLoop._active_turns.pop("running_test", None)

# ---------------------------------------------------------------------------
# _should_exit logic with injected messages
# ---------------------------------------------------------------------------

class TestShouldExitWithInject:
    """Test that _should_exit correctly handles injected user messages."""

    @staticmethod
    def _make_msg(
        msg_id: str,
        role: str,
        finish: str = None,
        *,
        parent_id: str | None = None,
    ):
        """Create a minimal message-like object for testing."""
        msg = type("Msg", (), {})()
        msg.id = msg_id
        msg.role = role
        msg.finish = finish
        msg.parentID = parent_id
        return msg

    def test_exit_when_assistant_replies_to_user_with_non_monotonic_ids(self):
        """Should use parent linkage instead of ordering generated IDs."""
        last_user = self._make_msg("msg_002", "user")
        last_assistant = self._make_msg(
            "msg_001",
            "assistant",
            finish="stop",
            parent_id=last_user.id,
        )

        assert RuntimeLoopContext._should_exit(last_user, last_assistant) is True

    def test_no_exit_when_user_injected_after_assistant(self):
        """Should NOT exit when a new user message appears after the assistant.

        This is the core inject scenario: the injected user message has a
        higher ID than the last assistant message, so the loop should continue.
        """
        last_user = self._make_msg("msg_003", "user")  # injected message
        last_assistant = self._make_msg(
            "msg_002",
            "assistant",
            finish="stop",
            parent_id="msg_001",
        )

        assert RuntimeLoopContext._should_exit(last_user, last_assistant) is False

    def test_no_exit_when_assistant_has_tool_calls(self):
        """Should NOT exit when assistant finish is 'tool-calls'."""
        last_user = self._make_msg("msg_001", "user")
        last_assistant = self._make_msg("msg_002", "assistant", finish="tool-calls")

        assert RuntimeLoopContext._should_exit(last_user, last_assistant) is False

    def test_no_exit_when_no_assistant(self):
        """Should NOT exit when there is no assistant message yet."""
        last_user = self._make_msg("msg_001", "user")

        assert RuntimeLoopContext._should_exit(last_user, None) is False

    def test_no_exit_when_assistant_finish_is_unknown(self):
        """Should NOT exit when finish reason is 'unknown'."""
        last_user = self._make_msg("msg_001", "user")
        last_assistant = self._make_msg("msg_002", "assistant", finish="unknown")

        assert RuntimeLoopContext._should_exit(last_user, last_assistant) is False

    def test_no_exit_when_assistant_not_finished(self):
        """Should NOT exit when assistant has no finish status."""
        last_user = self._make_msg("msg_001", "user")
        last_assistant = self._make_msg("msg_002", "assistant", finish=None)

        assert RuntimeLoopContext._should_exit(last_user, last_assistant) is False

    def test_no_exit_when_assistant_has_completed_tool_parts(self):
        """Should continue so completed tool results can be fed back to the model."""
        last_user = self._make_msg("msg_001", "user")
        last_assistant = self._make_msg("msg_002", "assistant", finish="stop")
        last_assistant_parts = [_make_completed_tool_part(last_assistant.id)]

        assert RuntimeLoopContext._should_exit(
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

        queued = await DEFAULT_CONTINUATION_POLICY.detect_queued_user_message(
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

        queued = await DEFAULT_CONTINUATION_POLICY.detect_queued_user_message(
            "session-1",
            [current_user, newer_user],
            current_user.id,
            None,
        )

        assert queued is newer_user


class TestTurnLifecycle:
    @staticmethod
    def _make_msg(
        msg_id: str,
        role: str,
        finish: str = None,
        *,
        tokens=None,
        summary: bool = False,
        parent_id: str | None = None,
    ):
        msg = type("Msg", (), {})()
        msg.id = msg_id
        msg.role = role
        msg.finish = finish
        msg.tokens = tokens
        msg.summary = summary
        msg.parentID = parent_id
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
        ctx.session_store = SimpleNamespace(get_messages=AsyncMock(return_value=[]))
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        result = await run_logical_turns(ctx, callbacks)

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
        ctx.session_store = SimpleNamespace(get_messages=AsyncMock(return_value=[assistant]))
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        result = await run_logical_turns(ctx, callbacks)

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
        assistant = self._make_msg(
            "msg_002",
            "assistant",
            finish="stop",
            parent_id=user.id,
        )
        goal_user = self._make_msg("msg_003", "user")
        assistant_after_goal = self._make_msg(
            "msg_004",
            "assistant",
            finish="stop",
            parent_id=goal_user.id,
        )
        ctx.session_store = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[
                [user],
                [user, assistant],
                [user, assistant],
                [user, assistant],
                [user, assistant, goal_user],
                [user, assistant, goal_user, assistant_after_goal],
                [user, assistant, goal_user, assistant_after_goal],
                [user, assistant, goal_user, assistant_after_goal],
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
            "flocks.session.runtime.session_turn.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.runtime.session_turn.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.runtime.session_turn.Message.get_text_content",
            MagicMock(return_value="still working"),
        ), patch(
            "flocks.session.runtime.session_turn.Message.create",
            AsyncMock(return_value=goal_user),
        ) as create_message, patch(
            "flocks.session.runtime.continuation_policy.GoalManager.evaluate_after_turn",
            AsyncMock(side_effect=goal_decisions),
        ), patch(
            "flocks.session.runtime.step_engine.StepEngine._process_step",
            AsyncMock(side_effect=[StepResult(action="stop"), StepResult(action="stop")]),
        ):
            result = await run_logical_turns(ctx, callbacks)

        assert result.action == "stop"
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
        assistant = self._make_msg(
            "msg_002",
            "assistant",
            finish="stop",
            parent_id=user.id,
        )
        ctx.session_store = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[
                [user],
                [user, assistant],
                [user, assistant],
                [user, assistant],
                [user, assistant],
            ])
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        with patch(
            "flocks.session.runtime.session_turn.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.runtime.session_turn.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.runtime.session_turn.Message.get_text_content",
            MagicMock(return_value="Please clarify what tests to write."),
        ), patch(
            "flocks.session.runtime.session_turn.Message.create",
            AsyncMock(),
        ) as create_message, patch(
            "flocks.session.runtime.continuation_policy.GoalManager.evaluate_after_turn",
            AsyncMock(return_value=GoalDecision(
                status="active",
                verdict="waiting",
                should_continue=False,
                reason="waiting for user clarification",
            )),
        ), patch(
            "flocks.session.runtime.step_engine.StepEngine._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ):
            result = await run_logical_turns(ctx, callbacks)

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
        assistant = self._make_msg(
            "msg_002",
            "assistant",
            finish="stop",
            parent_id=user.id,
        )
        ctx.session_store = SimpleNamespace(
            get_messages=AsyncMock(side_effect=[
                [user],
                [user, assistant],
                [user, assistant],
                [user, assistant],
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
            "flocks.session.runtime.session_turn.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.runtime.session_turn.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.runtime.session_turn.Message.get_text_content",
            MagicMock(return_value="Please provide the input."),
        ), patch(
            "flocks.server.routes.question.has_pending_questions",
            MagicMock(return_value=True),
        ), patch(
            "flocks.session.runtime.session_turn.Message.create",
            AsyncMock(),
        ) as create_message, patch(
            "flocks.session.runtime.continuation_policy.GoalManager.evaluate_after_turn",
            evaluate_goal,
        ), patch(
            "flocks.session.runtime.step_engine.StepEngine._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ):
            result = await run_logical_turns(ctx, callbacks)

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
            self._make_msg(
                "msg_002",
                "assistant",
                finish="stop",
                parent_id="msg_001",
            ),
        ]
        ctx.session_store = SimpleNamespace(
            get_messages=AsyncMock(
                side_effect=[
                    [messages[0]],
                    messages,
                    messages,
                    messages,
                    messages,
                ]
            )
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        with patch(
            "flocks.session.runtime.session_turn.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.runtime.session_turn.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.runtime.session_turn.Message.get_text_content",
            MagicMock(return_value="Goal complete: done"),
        ), patch(
            "flocks.session.runtime.continuation_policy.GoalManager.evaluate_after_turn",
            AsyncMock(return_value=GoalDecision(
                status="completed",
                verdict="complete",
                reason="Goal complete: done",
                objective="finish work",
            )),
        ), patch(
            "flocks.session.runtime.step_engine.StepEngine._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ):
            result = await run_logical_turns(ctx, callbacks)

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
        ctx.session_store = SimpleNamespace(
            get_messages=AsyncMock(
                side_effect=[
                    overflow_messages,
                    normal_messages,
                    normal_messages,
                    normal_messages,
                    normal_messages,
                    normal_messages,
                ]
            )
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)

        with patch(
            "flocks.session.runtime.session_turn.Provider.resolve_model_info",
            return_value=(20000, 1024, None),
        ), patch(
            "flocks.session.runtime.session_turn.SessionCompaction.truncate_oversized_tool_outputs",
            AsyncMock(return_value=1),
        ), patch(
            "flocks.session.runtime.session_turn.SessionPrompt.estimate_full_context_tokens",
            AsyncMock(return_value=0),
        ), patch(
            "flocks.session.runtime.step_engine.StepEngine._process_step",
            AsyncMock(return_value=StepResult(action="stop")),
        ):
            result = await run_logical_turns(ctx, callbacks)

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
        assistant = self._make_msg(
            "stale_usage_assistant",
            "assistant",
            finish="tool-calls",
            tokens={
                "input": 95_000,
                "output": 2_000,
                "reasoning": 3_000,
                "cache": {"read": 0, "write": 0},
            },
        )
        later_user = self._make_msg("stale_usage_later_user", "user")
        messages = [assistant, later_user]
        cleanup = AsyncMock(return_value=object())

        with patch(
            "flocks.session.runtime.session_turn.Provider.resolve_model_info",
            return_value=(128_000, 8_192, None),
        ), patch(
            "flocks.session.runtime.session_turn.SessionPrompt.invalidate_message_cache",
            MagicMock(),
        ), patch(
            "flocks.session.runtime.session_turn.SessionPrompt.estimate_tool_result_tokens",
            AsyncMock(return_value=20_000),
        ), patch(
            "flocks.session.runtime.session_turn.SessionPrompt.estimate_full_context_tokens",
            AsyncMock(return_value=5_000),
        ), patch.object(
            ctx,
            "_prepare_tool_result_cleanup",
            cleanup,
        ):
            result = await ctx._prepare_context_window(
                messages,
                later_user,
                assistant,
            )

        assert result is cleanup.return_value
        assert cleanup.await_args.args[2] == 125_000
        assert ctx.last_observed_prompt_tokens == 100_000

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
            self._make_msg(
                "msg_002",
                "assistant",
                finish="stop",
                parent_id="msg_001",
            ),
        ]
        ctx.session_store = SimpleNamespace(
            get_messages=AsyncMock(
                side_effect=[messages, messages, messages, messages, messages]
            )
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)
        process_step = AsyncMock(return_value=StepResult(action="stop"))
        log_info = MagicMock()

        with patch(
            "flocks.session.runtime.session_turn.Message.parts",
            AsyncMock(return_value=[_make_completed_tool_part("msg_002")]),
        ), patch(
            "flocks.session.runtime.session_turn.Provider.resolve_model_info",
            return_value=(0, 0, None),
        ), patch(
            "flocks.session.lifecycle.title.SessionTitle.ensure_title",
            MagicMock(return_value=None),
        ), patch(
            "flocks.session.runtime.session_turn.fire_and_forget",
            MagicMock(),
        ), patch(
            "flocks.session.runtime.step_engine.StepEngine._process_step",
            process_step,
        ), patch(
            "flocks.session.runtime.session_turn.log.info",
            log_info,
        ):
            result = await run_logical_turns(ctx, callbacks)

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
            self._make_msg(
                "msg_002",
                "assistant",
                finish="stop",
                parent_id="msg_001",
            ),
        ]
        ctx.session_store = SimpleNamespace(
            get_messages=AsyncMock(return_value=messages)
        )
        event_callback = AsyncMock()
        callbacks = LoopCallbacks(event_publish_callback=event_callback)
        process_step = AsyncMock(return_value=StepResult(action="stop"))
        log_info = MagicMock()

        with patch(
            "flocks.session.runtime.session_turn.Message.parts",
            AsyncMock(return_value=[]),
        ), patch(
            "flocks.session.runtime.session_turn.log.info",
            log_info,
        ), patch(
            "flocks.session.runtime.step_engine.StepEngine._process_step",
            process_step,
        ):
            result = await run_logical_turns(ctx, callbacks)

        assert result.action == "stop"
        assert result.last_message is messages[1]
        assert process_step.await_count == 0
        assert any(call.args and call.args[0] == "loop.exit_condition" for call in log_info.call_args_list)
        event_names = [call.args[0] for call in event_callback.await_args_list]
        assert event_names == ["turn.started"]

    @pytest.mark.asyncio
    async def test_commit_step_does_not_reuse_previous_assistant_reply(self):
        """A failed current step must not surface an older assistant reply."""
        session = SimpleNamespace(id="stale_reply_session")
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
            parent_id=previous_user.id,
        )
        current_user = self._make_msg("msg_current_user", "user")
        ctx.prepared_user_id = current_user.id
        ctx.session_store = SimpleNamespace(
            get_messages=AsyncMock(
                return_value=[
                    previous_user,
                    previous_assistant,
                    current_user,
                ]
            )
        )

        boundary = await ctx.commit_step(StepResult(action="stop"))

        assert boundary.last_message is None


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
