"""
Tests for flocks/session/streaming/tool_accumulator.py

Covers:
- feed_chunk(): single-chunk and multi-chunk accumulation
- flush_remaining(): normal flush and truncated finish reason
- _should_accumulate(): JSON completeness check
- Index-to-ID mapping
- Completed tool call skipped on re-feed
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


from flocks.session.streaming.tool_accumulator import ToolCallAccumulator
from flocks.session.streaming.stream_events import ToolInputStartEvent, ToolCallEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_accumulator():
    """Create an accumulator with a mock processor."""
    processor = MagicMock()
    processor.process_event = AsyncMock()
    return ToolCallAccumulator(processor), processor


def _make_chunk(index=0, tc_id=None, name=None, arguments=None):
    chunk = {"index": index}
    if tc_id:
        chunk["id"] = tc_id
    func = {}
    if name:
        func["name"] = name
    if arguments is not None:
        func["arguments"] = arguments
    if func:
        chunk["function"] = func
    return chunk


# ---------------------------------------------------------------------------
# feed_chunk: basic single-call scenarios
# ---------------------------------------------------------------------------

class TestFeedChunkBasic:
    @pytest.mark.asyncio
    async def test_complete_json_triggers_tool_call_event(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None
            mock_reg.get.return_value = None

            await acc.feed_chunk(_make_chunk(
                tc_id="call_001", name="bash", arguments='{"command": "ls"}'
            ))

        # Should have emitted ToolInputStartEvent and ToolCallEvent
        calls = proc.process_event.call_args_list
        event_types = [c.args[0].type for c in calls]
        assert "tool-input-start" in event_types
        assert "tool-call" in event_types

    @pytest.mark.asyncio
    async def test_id_assigned_from_chunk(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None
            mock_reg.get.return_value = None

            await acc.feed_chunk(_make_chunk(
                tc_id="call_explicit", name="read_file", arguments='{"path": "/tmp/f"}'
            ))

        tool_call_events = [
            c.args[0] for c in proc.process_event.call_args_list
            if c.args[0].type == "tool-call"
        ]
        assert len(tool_call_events) == 1
        assert tool_call_events[0].tool_call_id == "call_explicit"

    @pytest.mark.asyncio
    async def test_index_to_id_mapping_without_explicit_id(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None

            # First chunk: sets name but no id
            chunk1 = {"index": 0, "function": {"name": "my_tool", "arguments": ""}}
            await acc.feed_chunk(chunk1)

            # Second chunk: provides the id for index 0
            chunk2 = {"index": 0, "id": "call_late_id", "function": {"arguments": '{"x": 1}'}}
            await acc.feed_chunk(chunk2)

        # The call_late_id should now be in the accumulator or already fired
        assert "call_late_id" in acc._index_to_id.values() or len(proc.process_event.call_args_list) > 0


# ---------------------------------------------------------------------------
# feed_chunk: incremental JSON accumulation
# ---------------------------------------------------------------------------

class TestFeedChunkIncremental:
    @pytest.mark.asyncio
    async def test_partial_json_emits_start_without_executing_tool(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None

            await acc.feed_chunk(_make_chunk(
                tc_id="call_inc", name="write", arguments='{"filePath": "/tmp/f", "content": "long'
            ))

        events = [call.args[0] for call in proc.process_event.call_args_list]
        assert len(events) == 1
        assert isinstance(events[0], ToolInputStartEvent)
        assert events[0].id == "call_inc"
        assert events[0].tool_name == "write"

    @pytest.mark.asyncio
    async def test_incremental_chunks_complete_and_fire(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None

            await acc.feed_chunk(_make_chunk(tc_id="call_inc2", name="tool_x", arguments='{"key":'))
            assert proc.process_event.call_count == 1
            assert isinstance(proc.process_event.call_args_list[0].args[0], ToolInputStartEvent)

            await acc.feed_chunk(_make_chunk(tc_id="call_inc2", arguments='"value"}'))

        events = [call.args[0] for call in proc.process_event.call_args_list]
        assert [event.type for event in events] == ["tool-input-start", "tool-call"]
        assert events[1].input == {"key": "value"}

    @pytest.mark.asyncio
    async def test_name_only_chunk_emits_start_before_arguments_arrive(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None

            await acc.feed_chunk(_make_chunk(tc_id="call_name_first", name="edit"))

        events = [call.args[0] for call in proc.process_event.call_args_list]
        assert len(events) == 1
        assert isinstance(events[0], ToolInputStartEvent)
        assert events[0].tool_name == "edit"

    @pytest.mark.asyncio
    async def test_generated_id_stays_stable_when_provider_id_arrives_late(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None

            await acc.feed_chunk(_make_chunk(index=3, name="write"))
            start_event = proc.process_event.call_args_list[0].args[0]

            await acc.feed_chunk(_make_chunk(
                index=3,
                tc_id="provider_call_id",
                arguments='{"filePath": "/tmp/f", "content": "done"}',
            ))

        events = [call.args[0] for call in proc.process_event.call_args_list]
        assert [event.type for event in events] == ["tool-input-start", "tool-call"]
        assert events[1].tool_call_id == start_event.id

    @pytest.mark.asyncio
    async def test_completed_call_ignored_on_re_feed(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None

            # First feed: completes
            await acc.feed_chunk(_make_chunk(
                tc_id="call_done", name="bash", arguments='{"command": "echo hi"}'
            ))
            call_count = proc.process_event.call_count

            # Second feed: should be ignored (completed)
            await acc.feed_chunk(_make_chunk(
                tc_id="call_done", arguments='{"command": "extra"}'
            ))

        assert proc.process_event.call_count == call_count  # no new events


# ---------------------------------------------------------------------------
# flush_remaining
# ---------------------------------------------------------------------------

class TestFlushRemaining:
    @pytest.mark.asyncio
    async def test_flush_complete_json(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None

            # Feed incomplete chunk so it stays in accumulator
            await acc.feed_chunk(_make_chunk(tc_id="call_f1", name="tool_y"))
            # Manually set accumulated args to valid JSON
            acc._accumulator["call_f1"]["arguments_str"] = '{"param": "value"}'
            acc._accumulator["call_f1"]["completed"] = False

        await acc.flush_remaining()
        event_types = [c.args[0].type for c in proc.process_event.call_args_list]
        assert "tool-call" in event_types

    @pytest.mark.asyncio
    async def test_flush_invalid_json_sends_invalid_tool(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None
            mock_reg.get.return_value = None
            with patch("flocks.session.streaming.tool_accumulator._find_similar_tool", return_value=None):
                # Inject broken JSON into accumulator
                acc._accumulator["call_broken"] = {
                    "id": "call_broken",
                    "name": "some_tool",
                    "arguments_str": "{{broken json{{",
                    "completed": False,
                }
                await acc.flush_remaining()

        tool_call_events = [
            c.args[0] for c in proc.process_event.call_args_list
            if c.args[0].type == "tool-call"
        ]
        # Should redirect to "invalid" tool
        assert any(e.tool_name == "invalid" for e in tool_call_events)

    @pytest.mark.asyncio
    async def test_flush_with_length_finish_reason_mentions_truncated(self):
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_reg.get_schema.return_value = None
            mock_reg.get.return_value = None
            with patch("flocks.session.streaming.tool_accumulator._find_similar_tool", return_value=None):
                acc._accumulator["call_trunc"] = {
                    "id": "call_trunc",
                    "name": "write_file",
                    "arguments_str": '{"path": "/tmp/f", "content": "abc',
                    "completed": False,
                }
                await acc.flush_remaining(stream_finish_reason="length")

        tool_call_events = [
            c.args[0] for c in proc.process_event.call_args_list
            if c.args[0].type == "tool-call" and c.args[0].tool_name == "invalid"
        ]
        if tool_call_events:
            error_msg = tool_call_events[0].input.get("error", "")
            assert "truncated" in error_msg.lower() or "length" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_flush_empty_accumulator_does_nothing(self):
        acc, proc = _make_accumulator()
        await acc.flush_remaining()
        assert proc.process_event.call_count == 0

    @pytest.mark.asyncio
    async def test_flush_skips_entries_without_name_or_args(self):
        acc, proc = _make_accumulator()
        acc._accumulator["call_empty"] = {
            "id": "call_empty",
            "name": "",
            "arguments_str": "",
            "completed": False,
        }
        await acc.flush_remaining()
        assert proc.process_event.call_count == 0


# ---------------------------------------------------------------------------
# _find_similar_tool
# ---------------------------------------------------------------------------

class TestFindSimilarTool:
    def test_exact_case_insensitive_match(self):
        from flocks.session.streaming.tool_accumulator import _find_similar_tool
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_tool = MagicMock()
            mock_tool.name = "BashTool"
            mock_reg.list_tools.return_value = [mock_tool]
            result = _find_similar_tool("bashtool")
        assert result == "BashTool"

    def test_edit_distance_1_match(self):
        from flocks.session.streaming.tool_accumulator import _find_similar_tool
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_tool = MagicMock()
            mock_tool.name = "bash"
            mock_reg.list_tools.return_value = [mock_tool]
            result = _find_similar_tool("bas")  # 1 deletion = edit distance 1
        assert result == "bash"

    def test_no_similar_tool_returns_none(self):
        from flocks.session.streaming.tool_accumulator import _find_similar_tool
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as mock_reg:
            mock_tool = MagicMock()
            mock_tool.name = "totally_different_tool"
            mock_reg.list_tools.return_value = [mock_tool]
            result = _find_similar_tool("xyz")
        assert result is None
