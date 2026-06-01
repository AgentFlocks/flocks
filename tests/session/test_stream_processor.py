"""
Tests for flocks/session/streaming/stream_processor.py

Covers:
- process_event() routing for all event types
- Text accumulation (start → delta → end)
- Reasoning accumulation (start → delta → end)
- ToolInputStart: creates pending ToolPart
- ToolCall: executes tool, creates ToolCallState
- FinishEvent: sets finish_reason
- Callbacks: text_delta, reasoning_delta, event_publish
- doom-loop prevention via recent_tool_signatures
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flocks.tool.registry import ToolResult
from flocks.session.streaming.stream_processor import StreamProcessor, ToolCallState
from flocks.session.streaming.stream_events import (
    FinishEvent,
    ReasoningDeltaEvent,
    ReasoningEndEvent,
    ReasoningStartEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEvent,
    ToolInputStartEvent,
)
from flocks.session.message import MessageRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(name="rex"):
    agent = MagicMock()
    agent.name = name
    agent.model = None
    agent.provider = None
    return agent


def _make_assistant_msg(session_id="ses_sp_test"):
    msg = MagicMock()
    msg.id = "msg_sp_001"
    msg.sessionID = session_id
    msg.role = "assistant"
    return msg


def _make_processor(
    session_id="ses_sp_test",
    text_callback=None,
    reasoning_callback=None,
    event_callback=None,
):
    return StreamProcessor(
        session_id=session_id,
        assistant_message=_make_assistant_msg(session_id),
        agent=_make_agent(),
        text_delta_callback=text_callback,
        reasoning_delta_callback=reasoning_callback,
        event_publish_callback=event_callback,
    )


# ---------------------------------------------------------------------------
# StartEvent / FinishEvent
# ---------------------------------------------------------------------------

class TestStartFinishEvents:
    @pytest.mark.asyncio
    async def test_start_event_does_not_raise(self):
        proc = _make_processor()
        await proc.process_event(StartEvent())

    @pytest.mark.asyncio
    async def test_finish_event_sets_finish_reason(self):
        proc = _make_processor()
        await proc.process_event(FinishEvent(finish_reason="stop"))
        assert proc.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_finish_event_tool_calls(self):
        proc = _make_processor()
        await proc.process_event(FinishEvent(finish_reason="tool-calls"))
        assert proc.finish_reason == "tool-calls"

    def test_get_finish_reason_initially_none(self):
        proc = _make_processor()
        assert proc.finish_reason is None


# ---------------------------------------------------------------------------
# Text accumulation
# ---------------------------------------------------------------------------

class TestTextAccumulation:
    @pytest.mark.asyncio
    async def test_text_start_creates_text_part(self):
        proc = _make_processor()
        with (
            patch.object(proc, 'event_publish_callback', None),
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
        ):
            await proc.process_event(TextStartEvent())
        assert proc.current_text_part is not None
        assert proc.current_text_part.type == "text"

    @pytest.mark.asyncio
    async def test_text_start_persists_placeholder_immediately(self):
        proc = _make_processor()
        with patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as mock_store:
            await proc.process_event(TextStartEvent())

        mock_store.assert_awaited_once()
        stored_part = mock_store.call_args.args[2]
        assert stored_part.type == "text"
        assert stored_part.text == ""

    @pytest.mark.asyncio
    async def test_text_delta_accumulates(self):
        proc = _make_processor()
        await proc.process_event(TextStartEvent())
        await proc.process_event(TextDeltaEvent(text="Hello "))
        await proc.process_event(TextDeltaEvent(text="world"))
        assert proc.get_text_content() == "Hello world"

    @pytest.mark.asyncio
    async def test_text_delta_calls_callback(self):
        callback = AsyncMock()
        proc = _make_processor(text_callback=callback)
        await proc.process_event(TextStartEvent())
        await proc.process_event(TextDeltaEvent(text="test"))
        callback.assert_called_with("test")

    @pytest.mark.asyncio
    async def test_text_end_finalizes_part(self):
        proc = _make_processor()
        with patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()):
            await proc.process_event(TextStartEvent())
            await proc.process_event(TextDeltaEvent(text="done"))
            await proc.process_event(TextEndEvent())
        assert proc.current_text_part is None
        assert proc.get_text_content() == "done"

    @pytest.mark.asyncio
    async def test_get_text_content_no_start_returns_empty(self):
        proc = _make_processor()
        assert proc.get_text_content() == ""

    @pytest.mark.asyncio
    async def test_multiple_text_deltas(self):
        proc = _make_processor()
        await proc.process_event(TextStartEvent())
        for char in "ABCDE":
            await proc.process_event(TextDeltaEvent(text=char))
        assert proc.get_text_content() == "ABCDE"

    @pytest.mark.asyncio
    async def test_text_delta_hides_minimax_tool_call_from_published_text(self):
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)

        await proc.process_event(TextStartEvent())
        await proc.process_event(TextDeltaEvent(text="先查询一下"))
        await proc.process_event(TextDeltaEvent(text="""
<minimax:tool_call>
<invoke name="software_query_agent_list">
<parameter name="software_id">123</parameter>
</invoke>
"""))

        published_texts = [
            call.args[1]["part"]["text"]
            for call in event_callback.await_args_list
            if call.args[0] == "message.part.updated" and "part" in call.args[1]
        ]
        published_deltas = [
            call.args[1].get("delta", "")
            for call in event_callback.await_args_list
            if call.args[0] == "message.part.updated"
        ]

        assert published_texts[-1] == "先查询一下"
        assert all("<minimax:tool_call>" not in text for text in published_texts)
        assert all("<minimax:tool_call>" not in delta for delta in published_deltas)

    @pytest.mark.asyncio
    async def test_text_end_parses_minimax_tool_call_xml(self):
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch.object(proc, "_handle_tool_call", new=AsyncMock()) as mock_handle_tool_call,
        ):
            await proc.process_event(TextStartEvent())
            await proc.process_event(TextDeltaEvent(text="""
<minimax:tool_call>
<invoke name="onesec_ops">
<parameter name="action">ops_query_audit_log</parameter>
<parameter name="cur_page">1</parameter>
<parameter name="page_size">10</parameter>
</invoke>
</minimax:tool_call>
"""))
            await proc.process_event(TextEndEvent())

        mock_handle_tool_call.assert_awaited_once()
        tool_event = mock_handle_tool_call.await_args.args[0]
        assert tool_event.tool_name == "onesec_ops"
        assert tool_event.input == {"action": "ops_query_audit_log", "cur_page": 1, "page_size": 10}
        assert proc._text_tool_calls_executed is True
        assert proc.get_text_content() == ""

    @pytest.mark.asyncio
    async def test_text_placeholder_keeps_text_before_tool_in_stored_order(self):
        proc = _make_processor()
        stored_parts = []

        async def fake_store_part(_session_id, _message_id, part):
            for idx, existing in enumerate(stored_parts):
                if existing.id == part.id:
                    stored_parts[idx] = part
                    break
            else:
                stored_parts.append(part)
            return part

        with patch(
            "flocks.session.streaming.stream_processor.Message.store_part",
            new=AsyncMock(side_effect=fake_store_part),
        ):
            await proc.process_event(ReasoningStartEvent(id="r-order"))
            await proc.process_event(ReasoningDeltaEvent(id="r-order", text="plan"))
            await proc.process_event(ReasoningEndEvent(id="r-order"))
            await proc.process_event(TextStartEvent())
            await proc.process_event(TextDeltaEvent(text="answer"))
            await proc.process_event(ToolInputStartEvent(id="tc-order", tool_name="bash"))
            await proc.process_event(TextEndEvent())

        assert [part.type for part in stored_parts] == ["reasoning", "text", "tool"]


# ---------------------------------------------------------------------------
# Reasoning accumulation
# ---------------------------------------------------------------------------

class TestReasoningAccumulation:
    @pytest.mark.asyncio
    async def test_reasoning_start_creates_part(self):
        proc = _make_processor()
        await proc.process_event(ReasoningStartEvent(id="r1"))
        assert "r1" in proc.reasoning_parts

    @pytest.mark.asyncio
    async def test_reasoning_delta_accumulates(self):
        proc = _make_processor()
        await proc.process_event(ReasoningStartEvent(id="r2"))
        await proc.process_event(ReasoningDeltaEvent(id="r2", text="Let me think "))
        await proc.process_event(ReasoningDeltaEvent(id="r2", text="about this"))
        part = proc.reasoning_parts["r2"]
        assert part.text == "Let me think about this"

    @pytest.mark.asyncio
    async def test_reasoning_delta_accumulates_reasoning_content_metadata(self):
        proc = _make_processor()
        first_chunk = {
            "reasoningContent": "Need ",
            "reasoningSource": "reasoning_content",
            "reasoningField": "reasoning_content",
        }
        second_chunk = {
            "reasoningContent": "to think",
            "reasoningSource": "reasoning_content",
            "reasoningField": "reasoning_content",
        }

        await proc.process_event(ReasoningStartEvent(id="r2-meta", metadata=first_chunk))
        await proc.process_event(
            ReasoningDeltaEvent(id="r2-meta", text="Need ", metadata=first_chunk)
        )
        await proc.process_event(
            ReasoningDeltaEvent(id="r2-meta", text="to think", metadata=second_chunk)
        )

        part = proc.reasoning_parts["r2-meta"]
        assert part.text == "Need to think"
        assert part.metadata["reasoningContent"] == "Need to think"
        assert part.metadata["reasoningSource"] == "reasoning_content"
        assert part.metadata["reasoningField"] == "reasoning_content"

    @pytest.mark.asyncio
    async def test_reasoning_delta_callback_called(self):
        callback = AsyncMock()
        proc = _make_processor(reasoning_callback=callback)
        await proc.process_event(ReasoningStartEvent(id="r3"))
        await proc.process_event(ReasoningDeltaEvent(id="r3", text="thinking"))
        callback.assert_called_with("thinking")

    @pytest.mark.asyncio
    async def test_reasoning_end_removes_from_active(self):
        proc = _make_processor()
        with patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()):
            await proc.process_event(ReasoningStartEvent(id="r4"))
            await proc.process_event(ReasoningDeltaEvent(id="r4", text="done"))
            await proc.process_event(ReasoningEndEvent(id="r4"))
        assert "r4" not in proc.reasoning_parts

    @pytest.mark.asyncio
    async def test_reasoning_end_keeps_accumulated_reasoning_content_metadata(self):
        proc = _make_processor()
        first_chunk = {
            "reasoningContent": "Need ",
            "reasoningSource": "reasoning_content",
            "reasoningField": "reasoning_content",
        }
        second_chunk = {
            "reasoningContent": "to think",
            "reasoningSource": "reasoning_content",
            "reasoningField": "reasoning_content",
        }

        with patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as mock_store:
            await proc.process_event(ReasoningStartEvent(id="r4-meta", metadata=first_chunk))
            await proc.process_event(
                ReasoningDeltaEvent(id="r4-meta", text="Need ", metadata=first_chunk)
            )
            await proc.process_event(
                ReasoningDeltaEvent(id="r4-meta", text="to think", metadata=second_chunk)
            )
            await proc.process_event(ReasoningEndEvent(id="r4-meta", metadata=second_chunk))

        stored_part = mock_store.call_args.args[2]
        assert stored_part.text == "Need to think"
        assert stored_part.metadata["reasoningContent"] == "Need to think"
        assert stored_part.metadata["reasoningSource"] == "reasoning_content"

    @pytest.mark.asyncio
    async def test_reasoning_delta_unknown_id_ignored(self):
        proc = _make_processor()
        # Delta for unknown ID should not raise
        await proc.process_event(ReasoningDeltaEvent(id="unknown_id", text="text"))

    @pytest.mark.asyncio
    async def test_get_reasoning_content_initial_empty(self):
        proc = _make_processor()
        assert proc.get_reasoning_content() == ""

    @pytest.mark.asyncio
    async def test_reasoning_publish_callback_called(self):
        events_published = []

        async def capture_event(event_type, data):
            events_published.append((event_type, data))

        proc = _make_processor(event_callback=capture_event)
        await proc.process_event(ReasoningStartEvent(id="r5"))
        assert any(e[0] == "message.part.updated" for e in events_published)

    @pytest.mark.asyncio
    async def test_reasoning_end_extracts_tool_call_block(self):
        """MiniMax-M3 emits <tool_call> XML inside its reasoning text instead of
        using the native API tool-call channel. The stream processor must
        extract these so they actually execute, otherwise the model loops on
        the same plan in every turn (observed in trace ses_17c6e42a8… from
        msg_e83b3fc62 onwards)."""
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch.object(proc, "_handle_tool_call", new=AsyncMock()) as mock_handle_tool_call,
        ):
            await proc.process_event(ReasoningStartEvent(id="r6"))
            await proc.process_event(ReasoningDeltaEvent(id="r6", text=(
                "\n让我重新检测 + 启动。\n"
                "<tool_call>\n"
                "<invoke name=\"bash\">\n"
                "<parameter name=\"command\">pgrep -f \"Google Chrome\" 2>&1 | head -5; echo \"ports:\"; lsof -nP -iTCP:9222 -sTCP:LISTEN 2>&1 | head -3</parameter>\n"
                "</invoke>\n"
                "</tool_call>"
            )))
            await proc.process_event(ReasoningEndEvent(id="r6"))

        mock_handle_tool_call.assert_awaited_once()
        tool_event = mock_handle_tool_call.await_args.args[0]
        assert tool_event.tool_name == "bash"
        assert tool_event.input == {
            "command": "pgrep -f \"Google Chrome\" 2>&1 | head -5; echo \"ports:\"; lsof -nP -iTCP:9222 -sTCP:LISTEN 2>&1 | head -3",
        }
        assert proc._text_tool_calls_executed is True
        stored_part = proc.reasoning_parts  # already deleted; check via stored text below
        # part is gone from active tracking after _handle_reasoning_end
        assert "r6" not in proc.reasoning_parts

    @pytest.mark.asyncio
    async def test_reasoning_end_extracts_minimax_tool_call_block(self):
        """The prompt-taught <minimax:tool_call> format must also be recognized
        when it appears in reasoning (not just visible text)."""
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch.object(proc, "_handle_tool_call", new=AsyncMock()) as mock_handle_tool_call,
        ):
            await proc.process_event(ReasoningStartEvent(id="r7"))
            await proc.process_event(ReasoningDeltaEvent(id="r7", text=(
                "\n从 skill 文档中我看到 browser-use 这个 skill 提供了多种模式：\n"
                "<minimax:tool_call>\n"
                "<invoke name=\"onesec_ops\">\n"
                "<parameter name=\"action\">ops_query_audit_log</parameter>\n"
                "<parameter name=\"page_size\">5</parameter>\n"
                "</invoke>\n"
                "</minimax:tool_call>\n"
            )))
            await proc.process_event(ReasoningEndEvent(id="r7"))

        mock_handle_tool_call.assert_awaited_once()
        tool_event = mock_handle_tool_call.await_args.args[0]
        assert tool_event.tool_name == "onesec_ops"
        assert tool_event.input == {"action": "ops_query_audit_log", "page_size": 5}
        assert proc._text_tool_calls_executed is True

    @pytest.mark.asyncio
    async def test_reasoning_end_strips_tool_call_xml_from_stored_text(self):
        """The stored reasoning part must NOT contain the raw <tool_call> XML,
        otherwise prepare_reasoning_for_replay will re-feed it to the model on
        the next turn and the model will keep repeating the same plan."""
        proc = _make_processor()
        stored_parts: list = []

        async def fake_store_part(_session_id, _message_id, part):
            for idx, existing in enumerate(stored_parts):
                if existing.id == part.id:
                    stored_parts[idx] = part
                    break
            else:
                stored_parts.append(part)
            return part

        with (
            patch(
                "flocks.session.streaming.stream_processor.Message.store_part",
                new=AsyncMock(side_effect=fake_store_part),
            ),
            patch.object(proc, "_handle_tool_call", new=AsyncMock()),
        ):
            await proc.process_event(ReasoningStartEvent(id="r8"))
            await proc.process_event(ReasoningDeltaEvent(id="r8", text=(
                "\n让我重新检测 + 启动。\n"
                "<tool_call>\n"
                "<invoke name=\"bash\">\n"
                "<parameter name=\"command\">echo hi</parameter>\n"
                "</invoke>\n"
                "</tool_call>\n"
            )))
            await proc.process_event(ReasoningEndEvent(id="r8"))

        assert len(stored_parts) == 1
        stored = stored_parts[0]
        assert "<tool_call>" not in stored.text
        assert "</invoke>" not in stored.text
        assert "让我重新检测 + 启动" in stored.text
        assert "echo hi" not in stored.text

    @pytest.mark.asyncio
    async def test_reasoning_end_no_tool_call_block_unchanged(self):
        """Plain reasoning (no embedded tool call) must be stored verbatim."""
        proc = _make_processor()
        stored_parts: list = []

        async def fake_store_part(_session_id, _message_id, part):
            stored_parts.append(part)
            return part

        plain_reasoning = "\n让我看完 helpers.py 的剩余部分。\n"

        with patch(
            "flocks.session.streaming.stream_processor.Message.store_part",
            new=AsyncMock(side_effect=fake_store_part),
        ):
            await proc.process_event(ReasoningStartEvent(id="r9"))
            await proc.process_event(ReasoningDeltaEvent(id="r9", text=plain_reasoning))
            await proc.process_event(ReasoningEndEvent(id="r9"))

        assert len(stored_parts) == 1
        # The processor rstrip()s trailing whitespace before storage, so the
        # stored text equals plain_reasoning without the trailing newline.
        assert stored_parts[0].text == plain_reasoning.rstrip()
        assert proc._text_tool_calls_executed is False

    @pytest.mark.asyncio
    async def test_reasoning_end_multiple_tool_call_blocks_all_extracted(self):
        """When reasoning contains multiple <tool_call> blocks, all must be
        extracted (one tool dispatch per call) and stripped from the stored
        text."""
        proc = _make_processor()
        stored_parts: list = []

        async def fake_store_part(_session_id, _message_id, part):
            stored_parts.append(part)
            return part

        with (
            patch(
                "flocks.session.streaming.stream_processor.Message.store_part",
                new=AsyncMock(side_effect=fake_store_part),
            ),
            patch.object(proc, "_handle_tool_call", new=AsyncMock()) as mock_handle_tool_call,
        ):
            await proc.process_event(ReasoningStartEvent(id="r10"))
            await proc.process_event(ReasoningDeltaEvent(id="r10", text=(
                "\n先检测端口再启动。\n"
                "<tool_call>\n"
                "<invoke name=\"bash\">\n"
                "<parameter name=\"command\">lsof -nP -iTCP:9222 -sTCP:LISTEN 2>&1 | head -3</parameter>\n"
                "</invoke>\n"
                "</tool_call>\n"
                "<tool_call>\n"
                "<invoke name=\"read\">\n"
                "<parameter name=\"filePath\">/tmp/x</parameter>\n"
                "<parameter name=\"limit\">10</parameter>\n"
                "</invoke>\n"
                "</tool_call>\n"
            )))
            await proc.process_event(ReasoningEndEvent(id="r10"))

        assert mock_handle_tool_call.await_count == 2
        dispatched = [call.args[0] for call in mock_handle_tool_call.await_args_list]
        assert [d.tool_name for d in dispatched] == ["bash", "read"]
        assert stored_parts[0].text == "先检测端口再启动。"
        assert proc._text_tool_calls_executed is True

    @pytest.mark.asyncio
    async def test_reasoning_end_respects_doom_loop_guard(self):
        """If _stop_tool_processing is already True (doom loop from prior
        turns), the extracted tool calls must NOT be dispatched — otherwise we
        re-execute the same plan the model is already looping on."""
        proc = _make_processor()
        proc._stop_tool_processing = True

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch.object(proc, "_handle_tool_call", new=AsyncMock()) as mock_handle_tool_call,
        ):
            await proc.process_event(ReasoningStartEvent(id="r11"))
            await proc.process_event(ReasoningDeltaEvent(id="r11", text=(
                "\n<tool_call>\n"
                "<invoke name=\"bash\">\n"
                "<parameter name=\"command\">echo hi</parameter>\n"
                "</invoke>\n"
                "</tool_call>\n"
            )))
            await proc.process_event(ReasoningEndEvent(id="r11"))

        mock_handle_tool_call.assert_not_awaited()
        # _text_tool_calls_executed should NOT be set when dispatch was skipped,
        # otherwise the finish_reason override at process_event will incorrectly
        # turn "stop" into "tool-calls" for an empty execution plan.
        assert proc._text_tool_calls_executed is False

# ---------------------------------------------------------------------------
# ToolInputStart
# ---------------------------------------------------------------------------

class TestToolInputStart:
    @pytest.mark.asyncio
    async def test_creates_tool_part_in_db(self):
        proc = _make_processor()
        with patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as mock_store:
            await proc.process_event(ToolInputStartEvent(id="tc_001", tool_name="bash"))
        mock_store.assert_called_once()
        # The part passed to store_part should be a ToolPart
        stored_part = mock_store.call_args.args[2]
        assert stored_part.type == "tool"
        assert stored_part.tool == "bash"

    @pytest.mark.asyncio
    async def test_tool_call_state_created(self):
        # _handle_tool_input_start creates a ToolCallState (not added to self.parts)
        # self.parts only tracks text/reasoning parts
        proc = _make_processor()
        with patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()):
            await proc.process_event(ToolInputStartEvent(id="tc_002", tool_name="read_file"))
        # Tool call state should be tracked
        assert "tc_002" in proc.tool_calls
        assert proc.tool_calls["tc_002"].name == "read_file"


# ---------------------------------------------------------------------------
# ToolCall execution
# ---------------------------------------------------------------------------

class TestToolCallExecution:
    @pytest.mark.asyncio
    async def test_tool_call_executes_tool(self):
        proc = _make_processor()

        mock_result = MagicMock()
        mock_result.output = "ls output"
        mock_result.title = "bash"
        mock_result.metadata = {}
        mock_result.attachments = None

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(return_value=mock_result),
            ),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_exec", tool_name="bash"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_exec", tool_name="bash", input={"command": "ls"})
            )

        assert "tc_exec" in proc.tool_calls

    @pytest.mark.asyncio
    async def test_tool_call_skips_tool_span_without_langfuse_generation(self):
        proc = _make_processor()

        mock_result = MagicMock()
        mock_result.output = "ls output"
        mock_result.title = "bash"
        mock_result.metadata = {}
        mock_result.attachments = None

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(return_value=mock_result),
            ),
            patch("flocks.session.streaming.stream_processor.span_scope") as span_scope_mock,
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_no_span", tool_name="bash"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_no_span", tool_name="bash", input={"command": "ls"})
            )

        span_scope_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_tool_still_tracked(self):
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(side_effect=Exception("Tool not found")),
            ),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_unk", tool_name="nonexistent_tool"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_unk", tool_name="nonexistent_tool", input={})
            )

        assert "tc_unk" in proc.tool_calls
        state = proc.tool_calls["tc_unk"]
        assert state.status == "error"

    @pytest.mark.asyncio
    async def test_tool_start_callback_called(self):
        callback = AsyncMock()
        proc = _make_processor()
        proc.tool_start_callback = callback

        mock_result = MagicMock()
        mock_result.output = "output"
        mock_result.title = "bash"
        mock_result.metadata = {}
        mock_result.attachments = None

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(return_value=mock_result),
            ),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_cb", tool_name="bash"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_cb", tool_name="bash", input={"command": "pwd"})
            )

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_error_falls_back_to_metadata_output(self):
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)

        failed_result = ToolResult(
            success=False,
            output="Navigation failed: net::ERR_CERT_AUTHORITY_INVALID",
            metadata={
                "output": "Command failed with exit code 1\n\nNavigation failed: net::ERR_CERT_AUTHORITY_INVALID",
                "exit": 1,
            },
        )

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as mock_store,
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(return_value=failed_result),
            ),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_err", tool_name="bash"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_err", tool_name="bash", input={"command": "agent-browser open"})
            )

        completed_part = mock_store.await_args_list[-1].args[2]
        assert completed_part.state.status == "error"
        assert "Command failed with exit code 1" in completed_part.state.error
        assert "ERR_CERT_AUTHORITY_INVALID" in completed_part.state.error
        assert completed_part.state.metadata["exit"] == 1

        published_state = event_callback.await_args_list[-1].args[1]["part"]["state"]
        assert published_state["error"] == completed_part.state.error
        assert published_state["metadata"]["exit"] == 1


# ---------------------------------------------------------------------------
# ToolCallState dataclass
# ---------------------------------------------------------------------------

class TestToolCallStateDataclass:
    def test_default_status_pending(self):
        state = ToolCallState(id="c1", name="bash", input={}, part_id="p1")
        assert state.status == "pending"
        assert state.output is None
        assert state.error is None

    def test_completed_state(self):
        state = ToolCallState(id="c2", name="bash", input={}, part_id="p2", status="completed", output="ok")
        assert state.status == "completed"
        assert state.output == "ok"
