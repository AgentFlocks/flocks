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

from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolInfo,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.session.files import public_resource_id
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
    ToolInputErrorEvent,
    ToolInputStartEvent,
)
from flocks.session.message import MessageRole, ToolStateError


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
    abort_event=None,
):
    return StreamProcessor(
        session_id=session_id,
        assistant_message=_make_assistant_msg(session_id),
        agent=_make_agent(),
        allowed_tool_names=[
            "bash",
            "ip_query",
            "nonexistent_tool",
            "read_file",
            "read",
            "write",
            "run_workflow",
            "echo_tool",
            "ssh_host_cmd",
        ],
        abort_event=abort_event,
        text_delta_callback=text_callback,
        reasoning_delta_callback=reasoning_callback,
        event_publish_callback=event_callback,
    )


class _RegistrySandbox:
    """Register plain handlers as tools in an otherwise empty ToolRegistry."""

    def register(self, name, handler, *, params=("text",)):
        tool = Tool(
            info=ToolInfo(
                name=name,
                description=name,
                category=ToolCategory.TERMINAL,
                parameters=[ToolParameter(name=p, type=ParameterType.STRING) for p in params],
            ),
            handler=handler,
        )
        ToolRegistry._tools[name] = tool
        return tool


@pytest.fixture
def registry_sandbox(monkeypatch):
    """Real Tool.execute path (hooks included) without loading builtin/plugin tools."""
    from flocks.hooks.pipeline import HookPipeline

    monkeypatch.setattr(ToolRegistry, "_tools", {})
    monkeypatch.setattr(ToolRegistry, "_initialized", True)
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield _RegistrySandbox()
    HookPipeline.reset()


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
    async def test_text_delta_hides_dsml_tool_call_from_published_text(self):
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)

        await proc.process_event(TextStartEvent())
        await proc.process_event(TextDeltaEvent(text="先查询一下"))
        await proc.process_event(TextDeltaEvent(text="""
< | DSML | tool_use>
{"name":"bash","input":{"command":"ls /root/flocks/plugins/skills/ | head -50"}}
</ | DSML | tool_use>
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
        assert all("DSML" not in text for text in published_texts)
        assert all("DSML" not in delta for delta in published_deltas)

    @pytest.mark.asyncio
    async def test_text_end_parses_deepseek_dsml_json_tool_use(self):
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch.object(proc, "_handle_tool_call", new=AsyncMock()) as mock_handle_tool_call,
        ):
            await proc.process_event(TextStartEvent())
            await proc.process_event(TextDeltaEvent(text="""
< | DSML | tool_use>
{"name":"bash","input":{"command":"RUN_ROOT=/root/flocks/workspace/outputs/2026-08-03/risk-agent/ses_03991c1b0ffeb5KNPtPsetG6Jq && mkdir -p $RUN_ROOT/agent-notes"}}
</ | DSML | tool_use>
"""))
            await proc.process_event(TextEndEvent())

        mock_handle_tool_call.assert_awaited_once()
        tool_event = mock_handle_tool_call.await_args.args[0]
        assert tool_event.tool_name == "bash"
        assert tool_event.input == {
            "command": "RUN_ROOT=/root/flocks/workspace/outputs/2026-08-03/risk-agent/ses_03991c1b0ffeb5KNPtPsetG6Jq && mkdir -p $RUN_ROOT/agent-notes"
        }
        assert proc._text_tool_calls_executed is True
        assert proc.get_text_content() == ""

    @pytest.mark.asyncio
    async def test_text_end_parses_deepseek_dsml_invoke_tool_calls(self):
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch.object(proc, "_handle_tool_call", new=AsyncMock()) as mock_handle_tool_call,
        ):
            await proc.process_event(TextStartEvent())
            await proc.process_event(TextDeltaEvent(text="""
<｜DSML｜tool_calls>
<｜DSML｜invoke name="search">
<｜DSML｜parameter name="q" string="true">weather</｜DSML｜parameter>
<｜DSML｜parameter name="limit" string="false">3</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜tool_calls>
"""))
            await proc.process_event(TextEndEvent())

        mock_handle_tool_call.assert_awaited_once()
        tool_event = mock_handle_tool_call.await_args.args[0]
        assert tool_event.tool_name == "search"
        assert tool_event.input == {"q": "weather", "limit": 3}
        assert proc._text_tool_calls_executed is True
        assert proc.get_text_content() == ""

    @pytest.mark.asyncio
    async def test_text_end_parses_deepseek_dsml_ascii_pipe_tool_calls(self):
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch.object(proc, "_handle_tool_call", new=AsyncMock()) as mock_handle_tool_call,
        ):
            await proc.process_event(TextStartEvent())
            await proc.process_event(TextDeltaEvent(text="""
<||DSML||tool_calls>
[{"tool_name":"bash","parameters":{"command":"pwd"}}]
</||DSML||tool_calls>
"""))
            await proc.process_event(TextEndEvent())

        mock_handle_tool_call.assert_awaited_once()
        tool_event = mock_handle_tool_call.await_args.args[0]
        assert tool_event.tool_name == "bash"
        assert tool_event.input == {"command": "pwd"}
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

    @pytest.mark.asyncio
    async def test_tool_input_error_updates_pending_part_without_execution(self):
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)
        execute_mock = AsyncMock(return_value=ToolResult(success=True, output="should not run"))

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as mock_store,
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=execute_mock,
            ),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_trunc", tool_name="write"))
            await proc.process_event(
                ToolInputErrorEvent(
                    id="tc_trunc",
                    tool_name="write",
                    input={"arguments_preview": '{"path": "/tmp/f"', "finish_reason": "length"},
                    error="Output was truncated while generating tool arguments.",
                )
            )

        execute_mock.assert_not_awaited()
        state = proc.tool_calls["tc_trunc"]
        assert state.status == "error"
        assert state.name == "write"
        assert "truncated" in state.error

        completed_part = mock_store.await_args_list[-1].args[2]
        assert completed_part.tool == "write"
        assert completed_part.state.status == "error"
        assert completed_part.state.input["finish_reason"] == "length"
        assert "truncated" in completed_part.state.error

        published_part = event_callback.await_args_list[-1].args[1]["part"]
        assert published_part["tool"] == "write"
        assert published_part["state"]["status"] == "error"
        assert published_part["state"]["input"]["finish_reason"] == "length"


# ---------------------------------------------------------------------------
# ToolCall execution
# ---------------------------------------------------------------------------

class TestToolCallExecution:
    @pytest.mark.asyncio
    async def test_interrupted_tool_persists_and_publishes_metadata(self):
        events = []

        async def capture_event(event_type, data):
            events.append((event_type, data))

        proc = _make_processor(event_callback=capture_event)
        state = ToolCallState(
            id="tc_interrupted",
            name="bash",
            input={"command": "sleep 1"},
            part_id="part_interrupted",
            metadata={"title": "Long-running command"},
        )
        with patch(
            "flocks.session.streaming.stream_processor.Message.store_part",
            new=AsyncMock(),
        ) as store_part:
            await proc._finalize_interrupted_tool_call(
                tool_state=state,
                tool_name="bash",
                tool_input=state.input,
                tool_call_id=state.id,
                tool_start_time=1,
            )

        stored_state = store_part.await_args.args[2].state
        assert isinstance(stored_state, ToolStateError)
        assert stored_state.metadata["interrupted"] is True
        assert stored_state.metadata["title"] == "Long-running command"
        assert events[0][1]["part"]["state"]["metadata"]["interrupted"] is True

    @pytest.mark.asyncio
    async def test_pre_execution_cancellation_does_not_emit_unpaired_tool_after(self):
        """Cancelled before the registry ran: no before hook fired, so no after hook either."""
        events = []

        async def capture_event(event_type, data):
            events.append((event_type, data))

        proc = _make_processor(event_callback=capture_event)

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as store_part,
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
            patch.object(proc, "_resolve_sandbox_meta", new=AsyncMock(side_effect=asyncio.CancelledError())),
            patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=AsyncMock()) as tool_before,
            patch("flocks.hooks.pipeline.HookPipeline.run_tool_after", new=AsyncMock()) as tool_after,
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_cancelled", tool_name="bash"))
            with pytest.raises(asyncio.CancelledError):
                await proc.process_event(
                    ToolCallEvent(tool_call_id="tc_cancelled", tool_name="bash", input={"command": "pwd"})
                )

        tool_before.assert_not_awaited()
        tool_after.assert_not_awaited()
        assert proc.tool_calls["tc_cancelled"].status == "error"
        assert proc.tool_calls["tc_cancelled"].metadata["interrupted"] is True
        stored_state = store_part.await_args.args[2].state
        assert isinstance(stored_state, ToolStateError)
        assert stored_state.input == {"command": "pwd"}
        assert events[-1][1]["part"]["state"]["metadata"]["interrupted"] is True

    @pytest.mark.asyncio
    async def test_sandbox_block_skips_registry_execution(self):
        proc = _make_processor()
        execute = AsyncMock()

        with (
            patch(
                "flocks.session.streaming.stream_processor.Message.store_part",
                new=AsyncMock(),
            ),
            patch.object(
                proc,
                "_resolve_sandbox_meta",
                new=AsyncMock(return_value={
                    "blocked": True,
                    "error": "Sandbox denied tool",
                    "extra": {},
                }),
            ),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=execute,
            ),
        ):
            await proc.process_event(
                ToolInputStartEvent(id="tc_sandbox", tool_name="bash")
            )
            await proc.process_event(ToolCallEvent(
                tool_call_id="tc_sandbox",
                tool_name="bash",
                input={"command": "pwd"},
            ))

        execute.assert_not_awaited()
        assert proc.tool_calls["tc_sandbox"].status == "error"
        assert proc.tool_calls["tc_sandbox"].error == "Sandbox denied tool"

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
    async def test_tool_call_persists_and_publishes_attachments(self):
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)
        attachment = {
            "id": "file_output_1",
            "sessionID": proc.session_id,
            "messageID": proc.assistant_message.id,
            "type": "file",
            "mime": "text/markdown",
            "filename": "report.md",
            "origin": "agent_output",
            "source": {"root": "workspace-output", "path": "2026-09-14/report.md"},
        }
        store_part = AsyncMock()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=store_part),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(return_value=ToolResult(
                    success=True,
                    output="Wrote file successfully.",
                    title="report.md",
                    metadata={},
                    attachments=[attachment],
                )),
            ),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_attachment", tool_name="write"))
            await proc.process_event(ToolCallEvent(
                tool_call_id="tc_attachment",
                tool_name="write",
                input={"filePath": "report.md", "content": "# Report"},
            ))

        completed_part = store_part.await_args.args[2]
        assert completed_part.state.attachments == [attachment]
        completed_events = [
            call.args[1]["part"]
            for call in event_callback.await_args_list
            if (
                call.args[0] == "message.part.updated"
                and call.args[1].get("part", {}).get("state", {}).get("status") == "completed"
            )
        ]
        assert completed_events[-1]["state"]["attachments"] == [{
            **attachment,
            "resourceID": public_resource_id("msg_sp_001", "file_output_1"),
        }]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mime", ["application/pdf", "image/png"])
    async def test_read_large_attachments_do_not_enter_storage_or_sse(self, mime):
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)
        proc.tool_end_callback = AsyncMock()
        attachment = {
            "type": "file", "mime": mime, "filename": "large-document",
            "url": f"data:{mime};base64," + "A" * (2 * 1024 * 1024),
        }
        result = ToolResult(success=True, output="read preview", attachments=[attachment])
        original_attachments = result.attachments
        store_part = AsyncMock()
        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=store_part),
            patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=AsyncMock(return_value=result)),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_read_large", tool_name="read"))
            await proc.process_event(ToolCallEvent(
                tool_call_id="tc_read_large", tool_name="read", input={"filePath": "document"},
            ))
        completed_part = store_part.await_args.args[2]
        assert completed_part.state.status == "completed"
        assert completed_part.state.attachments is None
        states = [call.args[1]["part"].get("state", {}) for call in event_callback.await_args_list]
        assert states[-1]["status"] == "completed"
        assert all("attachments" not in state for state in states)
        proc.tool_end_callback.assert_awaited_once_with("read", result)
        assert result.attachments is original_attachments
        assert result.attachments == [attachment]
        assert len(result.attachments[0]["url"]) > 2 * 1024 * 1024

    @pytest.mark.asyncio
    @pytest.mark.parametrize("success", [False, True])
    async def test_write_storage_and_sse_share_only_successful_lightweight_descriptors(self, success):
        from flocks.session.files import output_file_attachments

        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)
        proc.tool_end_callback = AsyncMock()
        attachment = {
            "id": "prt_bounded", "sessionID": proc.session_id, "messageID": proc.assistant_message.id,
            "type": "file", "mime": "text/markdown", "filename": "report.md", "origin": "agent_output",
            "source": {"root": "workspace-output", "path": "report.md", "username": None, "unbounded": {"x": "A" * 10000}},
            "url": "data:application/pdf;base64," + "A" * 10000,
            "unknown": {"binary": b"data"},
        }
        result = ToolResult(
            success=success, output="ok" if success else None,
            error=None if success else "write failed", attachments=[attachment] * 40,
        )
        projected = output_file_attachments("write", result.attachments)
        store_part = AsyncMock()
        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=store_part),
            patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", new=AsyncMock(return_value=result)),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_bounded", tool_name="write"))
            await proc.process_event(ToolCallEvent(
                tool_call_id="tc_bounded", tool_name="write", input={"filePath": "report.md", "content": "report"},
            ))
        stored = store_part.await_args.args[2]
        final_state = event_callback.await_args.args[1]["part"]["state"]
        assert len(result.attachments) == 40
        assert "url" in result.attachments[0]
        proc.tool_end_callback.assert_awaited_once_with("write", result)
        if success:
            assert stored.state.attachments == projected
            assert len(projected) == 32
            assert final_state["attachments"] == [
                {**item, "resourceID": public_resource_id(proc.assistant_message.id, item["id"])}
                for item in projected
            ]
        else:
            assert stored.state.status == "error"
            assert not getattr(stored.state, "attachments", None)
            assert "attachments" not in final_state

    @pytest.mark.asyncio
    async def test_tool_call_passes_session_abort_event_to_tool_context(self):
        abort_event = asyncio.Event()
        proc = _make_processor(abort_event=abort_event)
        seen_abort = {}

        async def _fake_execute(*, tool_name, ctx, **kwargs):
            seen_abort["event"] = ctx.abort
            return ToolResult(success=True, output="ok", title=tool_name, metadata={})

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(side_effect=_fake_execute),
            ),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_abort", tool_name="run_workflow"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_abort", tool_name="run_workflow", input={"workflow": "wf.json"})
            )

        assert seen_abort["event"] is abort_event

    @pytest.mark.asyncio
    async def test_tool_before_hook_fires_once_per_tool_call(self, registry_sandbox):
        """One LLM tool call = one tool.execute.before + one tool.execute.after (registry only)."""
        from flocks.hooks.pipeline import HookBase, HookPipeline

        observed = {"before": [], "after": []}

        class Recorder(HookBase):
            async def tool_before(self, ctx):
                observed["before"].append(sorted(ctx.input.keys()))

            async def tool_after(self, ctx):
                observed["after"].append(sorted(ctx.input.keys()))

        HookPipeline.register("recorder", Recorder())
        handler_calls = []

        async def handler(_ctx, **kwargs):
            handler_calls.append(kwargs)
            return ToolResult(success=True, output="ok", title="echo")

        registry_sandbox.register("echo_tool", handler, params=("text",))
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_once", tool_name="echo_tool"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_once", tool_name="echo_tool", input={"text": "hi"})
            )

        assert handler_calls == [{"text": "hi"}]
        assert observed["before"] == [["operation", "tool_execution"]]
        assert observed["after"] == [["operation", "outcome", "tool_execution"]]
        assert proc.tool_calls["tc_once"].status == "completed"

    @pytest.mark.asyncio
    async def test_tool_input_rewrite_feeds_policy_hooks_and_execution(self, registry_sandbox):
        """Rewrite runs once on the raw input; policy hooks, the UI and the handler all see it.

        Placeholders inside secret-bearing keys are restored too (the rewrite
        hook sees the raw input), while tool.execute.before hooks still get the
        redacted view of those keys.
        """
        from flocks.hooks.pipeline import HookBase, HookPipeline

        rewrite_calls = []
        policy_seen = []

        class RestorePlaceholders(HookBase):
            async def tool_input_rewrite(self, ctx):
                tool_input = ctx.input["tool"]["input"]
                rewrite_calls.append(dict(tool_input))
                ctx.input["tool"]["input"] = {
                    key: {"[IP_1]": "10.1.2.3", "[PW_1]": "real-pw"}.get(value, value)
                    for key, value in tool_input.items()
                }

        class PolicyObserver(HookBase):
            async def tool_before(self, ctx):
                policy_seen.append(dict(ctx.input["tool_execution"]["tool"]["validated_input"]))

        HookPipeline.register("restore", RestorePlaceholders(), order=120)
        HookPipeline.register("policy", PolicyObserver(), order=200)
        handler_calls = []

        async def handler(_ctx, **kwargs):
            handler_calls.append(kwargs)
            return ToolResult(success=True, output="ok", title="ssh", metadata={})

        registry_sandbox.register("ssh_host_cmd", handler, params=("host", "command", "password"))
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)
        model_input = {"host": "[IP_1]", "command": "uname -a", "password": "[PW_1]"}

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as store_part,
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_rewrite", tool_name="ssh_host_cmd"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_rewrite", tool_name="ssh_host_cmd", input=dict(model_input))
            )

        restored = {"host": "10.1.2.3", "command": "uname -a", "password": "real-pw"}
        assert rewrite_calls == [model_input]
        assert handler_calls == [restored]
        # policy hooks evaluate the restored command but never see the secret
        assert policy_seen == [{"host": "10.1.2.3", "command": "uname -a", "password": "[REDACTED]"}]

        tool_states = [
            call.args[1]["part"]["state"]
            for call in event_callback.await_args_list
            if call.args[0] == "message.part.updated"
            and call.args[1].get("part", {}).get("type") == "tool"
        ]
        assert [state["input"] for state in tool_states if state["status"] == "running"] == [restored]
        assert tool_states[-1]["status"] == "completed" and tool_states[-1]["input"] == restored
        # nothing persisted for this call carries the placeholder version
        assert [call.args[2].state.input for call in store_part.await_args_list[1:]] == [restored, restored]
        state = proc.tool_calls["tc_rewrite"]
        assert state.input == restored
        assert state.raw_input == model_input

    @pytest.mark.asyncio
    async def test_tool_input_rewrite_isolated_hook_error_keeps_model_input(self, registry_sandbox):
        """Default (isolate) fail policy: a broken rewrite hook is best-effort, the tool still runs."""
        from flocks.hooks.pipeline import HookBase, HookPipeline

        class Broken(HookBase):
            async def tool_input_rewrite(self, ctx):
                raise RuntimeError("restore state corrupted")

        HookPipeline.register("broken", Broken())
        handler_calls = []

        async def handler(_ctx, **kwargs):
            handler_calls.append(kwargs)
            return ToolResult(success=True, output="ok", title="ip query")

        registry_sandbox.register("ip_query", handler, params=("ip",))
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_rw_isolated", tool_name="ip_query"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_rw_isolated", tool_name="ip_query", input={"ip": "[IP_1]"})
            )

        assert handler_calls == [{"ip": "[IP_1]"}]
        assert proc.tool_calls["tc_rw_isolated"].status == "completed"

    @pytest.mark.asyncio
    async def test_tool_input_rewrite_fail_closed_hook_error_blocks_execution(self, registry_sandbox):
        """A fail_closed rewrite hook that raises must stop the call, like tool.execute.before does."""
        from flocks.hooks.pipeline import HookBase, HookPipeline

        class Broken(HookBase):
            async def tool_input_rewrite(self, ctx):
                raise RuntimeError("redaction backend down")

        HookPipeline.register("broken", Broken(), fail_policy="fail_closed")
        handler_calls = []

        async def handler(_ctx, **kwargs):
            handler_calls.append(kwargs)
            return ToolResult(success=True, output="ok", title="ip query")

        registry_sandbox.register("ip_query", handler, params=("ip",))
        tool_end = AsyncMock()
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)
        proc.tool_end_callback = tool_end

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as store_part,
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
            patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=AsyncMock()) as tool_before,
            patch("flocks.hooks.pipeline.HookPipeline.run_tool_after", new=AsyncMock()) as tool_after,
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_rw_closed", tool_name="ip_query"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_rw_closed", tool_name="ip_query", input={"ip": "[IP_1]"})
            )

        assert handler_calls == []
        tool_before.assert_not_awaited()
        tool_after.assert_not_awaited()
        state = proc.tool_calls["tc_rw_closed"]
        assert state.status == "error"
        assert "redaction backend down" in state.error
        stored_state = store_part.await_args.args[2].state
        assert isinstance(stored_state, ToolStateError)
        assert stored_state.input == {"ip": "[IP_1]"}
        published = event_callback.await_args_list[-1].args[1]["part"]["state"]
        assert published["status"] == "error"
        tool_end.assert_awaited_once()
        assert tool_end.await_args.args[1].success is False

    @pytest.mark.asyncio
    async def test_tool_input_rewrite_hook_cannot_mutate_raw_input_or_decide(self, registry_sandbox):
        """The stage hands hooks a copy and carries no decision."""
        from flocks.hooks.pipeline import HookBase, HookPipeline

        class Sneaky(HookBase):
            async def tool_input_rewrite(self, ctx):
                ctx.input["tool"]["input"]["nested"]["ip"] = "10.1.2.3"
                return {"decision": {"action": "deny", "reason": "nope"}, "execution": {"stop": True}}

        HookPipeline.register("sneaky", Sneaky())
        handler_calls = []

        async def handler(_ctx, **kwargs):
            handler_calls.append(kwargs)
            return ToolResult(success=True, output="ok", title="ip query")

        registry_sandbox.register("ip_query", handler, params=("nested",))
        proc = _make_processor()
        model_input = {"nested": {"ip": "[IP_1]"}}

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_rw_copy", tool_name="ip_query"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_rw_copy", tool_name="ip_query", input=model_input)
            )

        state = proc.tool_calls["tc_rw_copy"]
        assert state.status == "completed"
        assert state.raw_input == {"nested": {"ip": "[IP_1]"}}
        assert model_input == {"nested": {"ip": "[IP_1]"}}
        assert state.input == {"nested": {"ip": "10.1.2.3"}}
        # a "nested" object param is coerced to a JSON string by the registry
        assert handler_calls == [{"nested": '{"ip": "10.1.2.3"}'}]

    @pytest.mark.asyncio
    async def test_tool_input_rewrite_cancellation_finalizes_interrupted_state(self, registry_sandbox):
        executed = []

        async def handler(_ctx, **kwargs):
            executed.append(kwargs)
            return ToolResult(success=True, output="ok", title="ip query")

        registry_sandbox.register("ip_query", handler, params=("ip",))
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as store_part,
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
            patch(
                "flocks.hooks.pipeline.HookPipeline.run_tool_input_rewrite",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ),
            patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=AsyncMock()) as tool_before,
            patch("flocks.hooks.pipeline.HookPipeline.run_tool_after", new=AsyncMock()) as tool_after,
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_rw_cancel", tool_name="ip_query"))
            with pytest.raises(asyncio.CancelledError):
                await proc.process_event(
                    ToolCallEvent(tool_call_id="tc_rw_cancel", tool_name="ip_query", input={"ip": "[IP_1]"})
                )

        assert executed == []
        tool_before.assert_not_awaited()
        tool_after.assert_not_awaited()
        state = proc.tool_calls["tc_rw_cancel"]
        assert state.status == "error" and state.metadata["interrupted"] is True
        assert isinstance(store_part.await_args.args[2].state, ToolStateError)

    @pytest.mark.asyncio
    async def test_doom_loop_detects_repeated_calls_when_rewrite_hook_changes_input(self, registry_sandbox):
        from flocks.hooks.pipeline import HookBase, HookPipeline

        class RestorePlaceholder(HookBase):
            async def tool_input_rewrite(self, ctx):
                ctx.input["tool"]["input"] = {"ip": "10.1.2.3"}

        HookPipeline.register("restore", RestorePlaceholder())
        handler_calls = []

        async def handler(_ctx, **kwargs):
            handler_calls.append(kwargs)
            return ToolResult(success=True, output="ok", title="ip query", metadata={})

        registry_sandbox.register("ip_query", handler, params=("ip",))
        proc = _make_processor()
        stored: dict = {}

        async def _store_part(_session_id, _message_id, part):
            stored[part.id] = part
            return part

        async def _parts(_message_id, _session_id=None):
            return list(stored.values())

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock(side_effect=_store_part)),
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(side_effect=_parts)),
        ):
            for index in range(3):
                call_id = f"tc_rw_loop_{index}"
                await proc.process_event(ToolInputStartEvent(id=call_id, tool_name="ip_query"))
                await proc.process_event(
                    ToolCallEvent(tool_call_id=call_id, tool_name="ip_query", input={"ip": "[IP_1]"})
                )

        assert proc._stop_tool_processing is True
        assert handler_calls == [{"ip": "10.1.2.3"}, {"ip": "10.1.2.3"}]

    @pytest.mark.asyncio
    async def test_tool_before_patch_republishes_running_state_and_executes_patched_input(
        self, registry_sandbox
    ):
        from flocks.hooks.pipeline import HookBase, HookPipeline

        before_calls = []

        class RestorePlaceholder(HookBase):
            async def tool_before(self, ctx):
                validated = ctx.input["tool_execution"]["tool"]["validated_input"]
                before_calls.append(dict(validated))
                if validated.get("ip") == "[IP_1]":
                    return {"decision": {"action": "allow", "validated_input_patch": {"ip": "10.1.2.3"}}}
                return None

        HookPipeline.register("restore", RestorePlaceholder())
        handler_calls = []

        async def handler(_ctx, **kwargs):
            handler_calls.append(kwargs)
            return ToolResult(success=True, output="ok", title="ip query", metadata={})

        registry_sandbox.register("ip_query", handler, params=("ip",))
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as store_part,
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_restore_running", tool_name="ip_query"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_restore_running", tool_name="ip_query", input={"ip": "[IP_1]"})
            )

        assert before_calls == [{"ip": "[IP_1]"}]
        assert handler_calls == [{"ip": "10.1.2.3"}]

        tool_updates = [
            call.args[1]["part"]["state"]
            for call in event_callback.await_args_list
            if call.args[0] == "message.part.updated"
            and call.args[1].get("part", {}).get("type") == "tool"
        ]
        running_inputs = [state["input"] for state in tool_updates if state["status"] == "running"]
        # First publish carries the model input, the hook rewrite is published
        # before the handler runs, and the terminal state keeps the rewrite.
        assert running_inputs == [{"ip": "[IP_1]"}, {"ip": "10.1.2.3"}]
        assert tool_updates[-1]["status"] == "completed"
        assert tool_updates[-1]["input"] == {"ip": "10.1.2.3"}
        stored_states = [call.args[2].state for call in store_part.await_args_list]
        assert stored_states[-1].input == {"ip": "10.1.2.3"}

        state = proc.tool_calls["tc_restore_running"]
        assert state.input == {"ip": "10.1.2.3"}
        assert state.raw_input == {"ip": "[IP_1]"}

    @pytest.mark.asyncio
    async def test_running_metadata_updates_keep_patched_input(self, registry_sandbox):
        """Live metadata updates during execution must not revert the input to the model's."""
        from flocks.hooks.pipeline import HookBase, HookPipeline

        class RestorePlaceholder(HookBase):
            async def tool_before(self, ctx):
                return {"decision": {"action": "allow", "validated_input_patch": {"ip": "10.1.2.3"}}}

        HookPipeline.register("restore", RestorePlaceholder())

        async def handler(ctx, **kwargs):
            ctx.metadata({"title": "probing", "metadata": {"progress": 1}})
            # let the scheduled running-state update tasks run
            for _ in range(3):
                await asyncio.sleep(0)
            return ToolResult(success=True, output="ok", title="ip query", metadata={})

        registry_sandbox.register("ip_query", handler, params=("ip",))
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as store_part,
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_meta", tool_name="ip_query"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_meta", tool_name="ip_query", input={"ip": "[IP_1]"})
            )

        running_states = [
            call.args[1]["part"]["state"]
            for call in event_callback.await_args_list
            if call.args[0] == "message.part.updated"
            and call.args[1].get("part", {}).get("type") == "tool"
            and call.args[1]["part"]["state"]["status"] == "running"
        ]
        with_metadata = [state for state in running_states if state.get("metadata")]
        assert with_metadata, "metadata update was not published"
        assert all(state["input"] == {"ip": "10.1.2.3"} for state in with_metadata)
        # Nothing published or persisted after the rewrite may carry the raw input.
        first_patched = next(i for i, s in enumerate(running_states) if s["input"] == {"ip": "10.1.2.3"})
        assert all(s["input"] == {"ip": "10.1.2.3"} for s in running_states[first_patched:])
        stored_inputs = [call.args[2].state.input for call in store_part.await_args_list]
        first_stored_patched = stored_inputs.index({"ip": "10.1.2.3"})
        assert all(i == {"ip": "10.1.2.3"} for i in stored_inputs[first_stored_patched:])

    @pytest.mark.asyncio
    async def test_doom_loop_detects_repeated_calls_when_hook_patches_input(self, registry_sandbox):
        """Doom-loop compares what the model emitted, even if a hook rewrote every call."""
        from flocks.hooks.pipeline import HookBase, HookPipeline

        class RestorePlaceholder(HookBase):
            async def tool_before(self, ctx):
                return {"decision": {"action": "allow", "validated_input_patch": {"ip": "10.1.2.3"}}}

        HookPipeline.register("restore", RestorePlaceholder())
        handler_calls = []

        async def handler(_ctx, **kwargs):
            handler_calls.append(kwargs)
            return ToolResult(success=True, output="ok", title="ip query", metadata={})

        registry_sandbox.register("ip_query", handler, params=("ip",))
        proc = _make_processor()

        # Minimal part store: keeps the latest state per part id, in creation order.
        stored: dict = {}

        async def _store_part(_session_id, _message_id, part):
            stored[part.id] = part
            return part

        async def _parts(_message_id, _session_id=None):
            return list(stored.values())

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock(side_effect=_store_part)),
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(side_effect=_parts)),
        ):
            for index in range(3):
                call_id = f"tc_loop_{index}"
                await proc.process_event(ToolInputStartEvent(id=call_id, tool_name="ip_query"))
                await proc.process_event(
                    ToolCallEvent(tool_call_id=call_id, tool_name="ip_query", input={"ip": "[IP_1]"})
                )

        assert proc._stop_tool_processing is True
        assert handler_calls == [{"ip": "10.1.2.3"}, {"ip": "10.1.2.3"}]

    @pytest.mark.asyncio
    async def test_tool_before_failure_allows_tool_execution(self, registry_sandbox):
        """An isolated (default fail policy) hook error must not block the tool."""
        from flocks.hooks.pipeline import HookBase, HookPipeline

        class Broken(HookBase):
            async def tool_before(self, ctx):
                raise RuntimeError("redaction restore failed")

        HookPipeline.register("broken", Broken())
        handler_calls = []

        async def handler(_ctx, **kwargs):
            handler_calls.append(kwargs)
            return ToolResult(success=True, output="executed", title="ip query")

        registry_sandbox.register("ip_query", handler, params=("ip",))
        proc = _make_processor()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_hook_fail", tool_name="ip_query"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_hook_fail", tool_name="ip_query", input={"ip": "[[V_IP_ADDRESS_1]]"})
            )

        assert handler_calls == [{"ip": "[[V_IP_ADDRESS_1]]"}]
        state = proc.tool_calls["tc_hook_fail"]
        assert state.status == "completed"
        assert state.output == "executed"

    @pytest.mark.asyncio
    async def test_cancelled_tool_blocks_late_running_metadata_updates(self):
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)
        saved_cb = {}

        async def _cancelled_execute(*, tool_name, ctx, **kwargs):
            saved_cb["cb"] = ctx._metadata_callback
            raise asyncio.CancelledError()

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(side_effect=_cancelled_execute),
            ),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_cancel", tool_name="run_workflow"))
            with pytest.raises(asyncio.CancelledError):
                await proc.process_event(
                    ToolCallEvent(tool_call_id="tc_cancel", tool_name="run_workflow", input={"workflow": "wf.json"})
                )

        baseline_calls = len(event_callback.await_args_list)
        saved_cb["cb"]({
            "title": "Running workflow: late-update",
            "metadata": {"phase": "running", "step_index": 99},
        })
        await asyncio.sleep(0)
        assert len(event_callback.await_args_list) == baseline_calls

    @pytest.mark.asyncio
    async def test_cancelled_tool_preserves_running_metadata_in_error_state(self):
        proc = _make_processor()
        store_part = AsyncMock()

        async def _cancelled_execute(*, tool_name, ctx, **kwargs):
            ctx.metadata({
                "title": "Inspect child",
                "metadata": {
                    "sessionId": "ses_child_cancelled",
                    "status": "running",
                },
            })
            await asyncio.sleep(0)
            raise asyncio.CancelledError()

        with (
            patch(
                "flocks.session.streaming.stream_processor.Message.store_part",
                new=store_part,
            ),
            patch(
                "flocks.session.streaming.stream_processor.Message.update_part",
                new=AsyncMock(),
            ),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(side_effect=_cancelled_execute),
            ),
        ):
            await proc.process_event(
                ToolInputStartEvent(id="tc_cancel_metadata", tool_name="run_workflow")
            )
            with pytest.raises(asyncio.CancelledError):
                await proc.process_event(
                    ToolCallEvent(
                        tool_call_id="tc_cancel_metadata",
                        tool_name="run_workflow",
                        input={"workflow": "wf.json"},
                    )
                )

        final_part = store_part.await_args_list[-1].args[2]
        assert isinstance(final_part.state, ToolStateError)
        assert final_part.state.metadata == {
            "title": "Inspect child",
            "sessionId": "ses_child_cancelled",
            "status": "interrupted",
            "interrupted": True,
        }

    @pytest.mark.asyncio
    async def test_completed_tool_cancels_pending_running_metadata_tasks(self):
        proc = _make_processor(event_callback=AsyncMock())
        created_tasks = []

        class _FakeTask:
            def __init__(self, coro):
                self._callbacks = []
                self.cancelled = False
                coro.close()

            def add_done_callback(self, callback):
                self._callbacks.append(callback)

            def cancel(self):
                if self.cancelled:
                    return
                self.cancelled = True
                for callback in list(self._callbacks):
                    callback(self)

            def result(self):
                raise asyncio.CancelledError()

        def _fake_create_task(coro):
            task = _FakeTask(coro)
            created_tasks.append(task)
            return task

        async def _successful_execute(*, tool_name, ctx, **kwargs):
            ctx.metadata({
                "title": "Running workflow: inflight-update",
                "metadata": {"phase": "running", "step_index": 1},
            })
            return ToolResult(success=True, output="ok", title=tool_name, metadata={})

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=_successful_execute,
            ),
            patch(
                "flocks.session.streaming.stream_processor.asyncio.create_task",
                side_effect=_fake_create_task,
            ),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_inflight", tool_name="run_workflow"))
            await proc.process_event(
                ToolCallEvent(
                    tool_call_id="tc_inflight",
                    tool_name="run_workflow",
                    input={"workflow": "wf.json"},
                )
            )

        assert len(created_tasks) == 2
        assert all(task.cancelled for task in created_tasks)

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
    async def test_invalid_tool_call_records_parse_error_without_registry_execution(self):
        event_callback = AsyncMock()
        proc = _make_processor(event_callback=event_callback)
        execute_mock = AsyncMock(return_value=ToolResult(success=True, output="should not run", title="invalid"))

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()) as mock_store,
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=execute_mock,
            ),
        ):
            await proc.process_event(
                ToolCallEvent(
                    tool_call_id="tc_invalid",
                    tool_name="invalid",
                    input={
                        "tool": "edit",
                        "error": "Failed to parse tool arguments (123 chars). Please ensure valid JSON.",
                        "arguments_preview": "{\"file_path\":",
                    },
                )
            )

        execute_mock.assert_not_awaited()
        state = proc.tool_calls["tc_invalid"]
        assert state.status == "error"
        assert "Failed to parse tool arguments for edit" in state.error
        assert "Failed to parse tool arguments (123 chars)" in state.error

        completed_part = mock_store.await_args_list[-1].args[2]
        assert completed_part.tool == "edit"
        assert completed_part.state.status == "error"
        assert completed_part.state.input["tool"] == "edit"
        assert completed_part.state.input["arguments_preview"] == "{\"file_path\":"
        assert completed_part.state.error == state.error

        published_part = event_callback.await_args_list[-1].args[1]["part"]
        assert published_part["tool"] == "edit"
        assert published_part["state"]["status"] == "error"
        assert published_part["state"]["error"] == state.error

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
    async def test_tool_span_records_full_output(self):
        proc = _make_processor()
        proc._langfuse_generation = object()
        long_output = "tool output " * 120
        span_ctx = MagicMock()

        successful_result = ToolResult(
            success=True,
            output=long_output,
            title="bash",
            metadata={},
        )

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(return_value=successful_result),
            ),
            patch("flocks.session.streaming.stream_processor.span_scope", return_value=span_ctx),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_span_output", tool_name="bash"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_span_output", tool_name="bash", input={"command": "ls -la"})
            )

        assert span_ctx.end.call_count == 1
        assert span_ctx.end.call_args.kwargs["output"] == long_output

    @pytest.mark.asyncio
    async def test_tool_span_records_full_error(self):
        proc = _make_processor()
        proc._langfuse_generation = object()
        long_error = "tool error " * 120
        span_ctx = MagicMock()

        failed_result = ToolResult(
            success=False,
            error=long_error,
            metadata={},
        )

        with (
            patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
            patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
            patch(
                "flocks.session.streaming.stream_processor.ToolRegistry.execute",
                new=AsyncMock(return_value=failed_result),
            ),
            patch("flocks.session.streaming.stream_processor.span_scope", return_value=span_ctx),
        ):
            await proc.process_event(ToolInputStartEvent(id="tc_span_error", tool_name="bash"))
            await proc.process_event(
                ToolCallEvent(tool_call_id="tc_span_error", tool_name="bash", input={"command": "false"})
            )

        assert span_ctx.end.call_count == 1
        assert span_ctx.end.call_args.kwargs["output"] == long_error
        assert span_ctx.end.call_args.kwargs["level"] == "ERROR"

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
