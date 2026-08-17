"""
Tests for message Part types in flocks/session/message.py

Covers:
- Pydantic model validation for all Part types
- ToolStateCompleted.get_output_str()
- MessagePart.from_typed_part() legacy conversion
- TokenUsage.total property
- Message.deserialize_part() round-trip
- Message store_part / parts async CRUD
"""

import json
import asyncio
import pytest
from unittest.mock import AsyncMock

from flocks.session.message import (
    AgentPart,
    CompactionPart,
    FilePart,
    MessagePart,
    PartTime,
    PatchPart,
    ReasoningPart,
    RetryPart,
    SnapshotPart,
    StepFinishPart,
    StepStartPart,
    SubtaskPart,
    TextPart,
    TokenCache,
    TokenUsage,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStatePending,
    ToolStateRunning,
    Message,
    MessageRole,
)


SID = "ses_parts_test"
MID = "msg_parts_test"


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_total_sums_input_output_reasoning(self):
        usage = TokenUsage(input=100, output=200, reasoning=50)
        assert usage.total == 350

    def test_total_with_defaults(self):
        usage = TokenUsage()
        assert usage.total == 0

    def test_cache_defaults(self):
        usage = TokenUsage()
        assert usage.cache.read == 0
        assert usage.cache.write == 0

    def test_total_ignores_cache(self):
        usage = TokenUsage(input=10, output=20, reasoning=5, cache=TokenCache(read=100, write=200))
        assert usage.total == 35  # cache not counted


# ---------------------------------------------------------------------------
# PartTime
# ---------------------------------------------------------------------------

class TestPartTime:
    def test_required_start(self):
        pt = PartTime(start=1000)
        assert pt.start == 1000
        assert pt.end is None

    def test_with_end(self):
        pt = PartTime(start=1000, end=2000)
        assert pt.end == 2000

    def test_with_compacted(self):
        pt = PartTime(start=1000, compacted=3000)
        assert pt.compacted == 3000


# ---------------------------------------------------------------------------
# TextPart
# ---------------------------------------------------------------------------

class TestTextPart:
    def test_basic_creation(self):
        part = TextPart(sessionID=SID, messageID=MID, text="hello")
        assert part.type == "text"
        assert part.text == "hello"
        # ID format is "prt_..." (Identifier.ascending uses "prt" prefix)
        assert len(part.id) > 3

    def test_synthetic_flag(self):
        part = TextPart(sessionID=SID, messageID=MID, text="", synthetic=True)
        assert part.synthetic is True

    def test_default_empty_text(self):
        part = TextPart(sessionID=SID, messageID=MID)
        assert part.text == ""


# ---------------------------------------------------------------------------
# FilePart
# ---------------------------------------------------------------------------

class TestFilePart:
    def test_basic_creation(self):
        part = FilePart(sessionID=SID, messageID=MID, mime="text/plain", url="file:///tmp/test.txt")
        assert part.type == "file"
        assert part.mime == "text/plain"

    def test_optional_filename(self):
        part = FilePart(sessionID=SID, messageID=MID, mime="image/png", url="data:image/png;base64,abc")
        assert part.filename is None


# ---------------------------------------------------------------------------
# ToolState variants
# ---------------------------------------------------------------------------

class TestToolStatePending:
    def test_creation(self):
        state = ToolStatePending(input={"cmd": "ls"}, raw='{"cmd": "ls"}')
        assert state.status == "pending"
        assert state.input == {"cmd": "ls"}


class TestToolStateRunning:
    def test_creation(self):
        state = ToolStateRunning(input={"cmd": "ls"}, time={"start": 1000})
        assert state.status == "running"


class TestToolStateCompleted:
    def test_get_output_str_string(self):
        state = ToolStateCompleted(
            input={"cmd": "ls"},
            output="file1.txt\nfile2.txt",
            title="bash",
            metadata={},
            time={"start": 1000, "end": 2000},
        )
        assert state.get_output_str() == "file1.txt\nfile2.txt"

    def test_get_output_str_dict(self):
        output_dict = {"result": "ok", "count": 3}
        state = ToolStateCompleted(
            input={},
            output=output_dict,
            title="tool",
            metadata={},
            time={"start": 1000, "end": 2000},
        )
        result = state.get_output_str()
        parsed = json.loads(result)
        assert parsed == output_dict

    def test_get_output_str_list(self):
        state = ToolStateCompleted(
            input={},
            output=["a", "b", "c"],
            title="tool",
            metadata={},
            time={"start": 1000, "end": 2000},
        )
        result = state.get_output_str()
        assert "a" in result

    def test_with_attachments(self):
        state = ToolStateCompleted(
            input={},
            output="done",
            title="tool",
            metadata={},
            time={"start": 1000, "end": 2000},
            attachments=[{"filename": "out.txt", "url": "file:///tmp/out.txt"}],
        )
        assert len(state.attachments) == 1


class TestToolStateError:
    def test_creation(self):
        state = ToolStateError(
            input={"cmd": "fail"},
            error="Permission denied",
            time={"start": 1000, "end": 2000},
        )
        assert state.status == "error"
        assert "Permission denied" in state.error


# ---------------------------------------------------------------------------
# ToolPart
# ---------------------------------------------------------------------------

class TestToolPart:
    def test_with_pending_state(self):
        part = ToolPart(
            sessionID=SID,
            messageID=MID,
            callID="call_001",
            tool="bash",
            state=ToolStatePending(input={"cmd": "ls"}, raw='{"cmd": "ls"}'),
        )
        assert part.type == "tool"
        assert part.tool == "bash"
        assert part.state.status == "pending"


# ---------------------------------------------------------------------------
# ReasoningPart
# ---------------------------------------------------------------------------

class TestReasoningPart:
    def test_creation(self):
        part = ReasoningPart(
            sessionID=SID,
            messageID=MID,
            text="I think this is a good approach",
            time=PartTime(start=1000),
        )
        assert part.type == "reasoning"
        assert "good approach" in part.text

    def test_metadata_optional(self):
        part = ReasoningPart(
            sessionID=SID, messageID=MID, text="thinking", time=PartTime(start=0)
        )
        assert part.metadata is None


# ---------------------------------------------------------------------------
# StepFinishPart
# ---------------------------------------------------------------------------

class TestStepFinishPart:
    def test_creation(self):
        part = StepFinishPart(
            sessionID=SID,
            messageID=MID,
            reason="stop",
            cost=0.001,
            tokens=TokenUsage(input=100, output=50),
        )
        assert part.type == "step-finish"
        assert part.reason == "stop"
        assert part.cost == 0.001

    def test_snapshot_optional(self):
        part = StepFinishPart(
            sessionID=SID,
            messageID=MID,
            reason="tool-calls",
            cost=0.0,
            tokens=TokenUsage(),
        )
        assert part.snapshot is None


# ---------------------------------------------------------------------------
# CompactionPart / SnapshotPart / PatchPart
# ---------------------------------------------------------------------------

class TestCompactionPart:
    def test_auto_true(self):
        part = CompactionPart(sessionID=SID, messageID=MID, auto=True)
        assert part.type == "compaction"
        assert part.auto is True

    def test_auto_false(self):
        part = CompactionPart(sessionID=SID, messageID=MID, auto=False)
        assert part.auto is False


class TestSnapshotPart:
    def test_creation(self):
        part = SnapshotPart(sessionID=SID, messageID=MID, snapshot="snap_abc")
        assert part.type == "snapshot"
        assert part.snapshot == "snap_abc"


class TestPatchPart:
    def test_creation(self):
        part = PatchPart(sessionID=SID, messageID=MID, hash="abc123", files=["a.py", "b.py"])
        assert part.type == "patch"
        assert "a.py" in part.files


# ---------------------------------------------------------------------------
# RetryPart
# ---------------------------------------------------------------------------

class TestRetryPart:
    def test_creation(self):
        part = RetryPart(
            sessionID=SID,
            messageID=MID,
            attempt=2,
            error={"message": "Rate limited"},
            time={"start": 1000},
        )
        assert part.type == "retry"
        assert part.attempt == 2


# ---------------------------------------------------------------------------
# SubtaskPart / AgentPart
# ---------------------------------------------------------------------------

class TestSubtaskPart:
    def test_creation(self):
        part = SubtaskPart(
            sessionID=SID,
            messageID=MID,
            prompt="Summarize findings",
            description="Summarize",
            agent="rex",
        )
        assert part.type == "subtask"
        assert part.agent == "rex"


class TestAgentPart:
    def test_creation(self):
        part = AgentPart(sessionID=SID, messageID=MID, name="explore")
        assert part.type == "agent"
        assert part.name == "explore"


# ---------------------------------------------------------------------------
# MessagePart.from_typed_part() legacy conversion
# ---------------------------------------------------------------------------

class TestMessagePartFromTypedPart:
    def test_from_text_part(self):
        typed = TextPart(sessionID=SID, messageID=MID, text="hello", synthetic=True)
        legacy = MessagePart.from_typed_part(typed)
        assert legacy.type == "text"
        assert legacy.content == "hello"
        assert legacy.metadata.get("synthetic") is True

    def test_from_file_part(self):
        typed = FilePart(sessionID=SID, messageID=MID, mime="image/png", url="file:///img.png", filename="img.png")
        legacy = MessagePart.from_typed_part(typed)
        assert legacy.type == "file"
        assert legacy.content == "file:///img.png"
        assert legacy.metadata.get("filename") == "img.png"
        assert legacy.metadata.get("mime") == "image/png"

    def test_from_tool_part(self):
        typed = ToolPart(
            sessionID=SID, messageID=MID, callID="call_x", tool="bash",
            state=ToolStatePending(input={}, raw="{}"),
        )
        legacy = MessagePart.from_typed_part(typed)
        assert legacy.type == "tool"
        assert legacy.metadata.get("tool") == "bash"

    def test_from_reasoning_part(self):
        typed = ReasoningPart(
            sessionID=SID, messageID=MID, text="reasoning text", time=PartTime(start=0)
        )
        legacy = MessagePart.from_typed_part(typed)
        assert legacy.type == "reasoning"
        assert legacy.content == "reasoning text"

    def test_from_patch_part(self):
        typed = PatchPart(sessionID=SID, messageID=MID, hash="h1", files=["foo.py"])
        legacy = MessagePart.from_typed_part(typed)
        assert legacy.type == "patch"
        assert "foo.py" in legacy.metadata.get("files", [])

    def test_from_compaction_part(self):
        typed = CompactionPart(sessionID=SID, messageID=MID, auto=True)
        legacy = MessagePart.from_typed_part(typed)
        assert legacy.type == "compaction"


# ---------------------------------------------------------------------------
# Message.deserialize_part() round-trip
# ---------------------------------------------------------------------------

class TestDeserializePart:
    def test_deserialize_text_part(self):
        part = TextPart(sessionID=SID, messageID=MID, text="test")
        serialized = part.model_dump()
        deserialized = Message.deserialize_part(serialized)
        assert deserialized is not None
        assert deserialized.type == "text"

    def test_deserialize_tool_part(self):
        part = ToolPart(
            sessionID=SID, messageID=MID, callID="c1", tool="bash",
            state=ToolStatePending(input={"x": 1}, raw='{"x":1}'),
        )
        serialized = part.model_dump()
        deserialized = Message.deserialize_part(serialized)
        assert deserialized is not None
        assert deserialized.type == "tool"

    def test_deserialize_reasoning_part(self):
        part = ReasoningPart(
            sessionID=SID, messageID=MID, text="think", time=PartTime(start=123)
        )
        serialized = part.model_dump()
        deserialized = Message.deserialize_part(serialized)
        assert deserialized is not None
        assert deserialized.type == "reasoning"

    def test_deserialize_unknown_type_falls_back_to_text(self):
        # Unknown type falls back to TextPart; missing required fields raise exception
        with pytest.raises(Exception):
            Message.deserialize_part({"type": "nonexistent_type", "id": "part_x"})

    def test_deserialize_empty_dict_raises(self):
        # Empty dict missing required fields should raise Pydantic validation error
        with pytest.raises(Exception):
            Message.deserialize_part({})


# ---------------------------------------------------------------------------
# Message.store_part / parts async integration
# ---------------------------------------------------------------------------

class TestMessageStoreAndParts:
    @pytest.mark.asyncio
    async def test_store_and_retrieve_text_part(self):
        msg = await Message.create(SID + "_sp", MessageRole.ASSISTANT, "")
        part = TextPart(sessionID=SID + "_sp", messageID=msg.id, text="stored text")
        await Message.store_part(SID + "_sp", msg.id, part)
        parts = await Message.parts(msg.id, SID + "_sp")
        text_parts = [p for p in parts if p.type == "text"]
        assert any(getattr(p, "text", "") == "stored text" for p in text_parts)

    @pytest.mark.asyncio
    async def test_store_and_retrieve_tool_part(self):
        sid = SID + "_tool"
        msg = await Message.create(sid, MessageRole.ASSISTANT, "")
        tool_part = ToolPart(
            sessionID=sid,
            messageID=msg.id,
            callID="call_test",
            tool="bash",
            state=ToolStatePending(input={"cmd": "echo"}, raw='{"cmd":"echo"}'),
        )
        await Message.store_part(sid, msg.id, tool_part)
        parts = await Message.parts(msg.id, sid)
        tool_parts = [p for p in parts if p.type == "tool"]
        assert len(tool_parts) >= 1
        assert tool_parts[0].tool == "bash"

    @pytest.mark.asyncio
    async def test_store_part_debounces_non_terminal_tool_state(self, monkeypatch):
        sid = SID + "_debounce"
        msg = await Message.create(sid, MessageRole.ASSISTANT, "")
        tool_part = ToolPart(
            sessionID=sid,
            messageID=msg.id,
            callID="call_pending",
            tool="bash",
            state=ToolStatePending(input={"cmd": "echo"}, raw='{"cmd":"echo"}'),
        )

        persist_mock = AsyncMock()
        monkeypatch.setattr(Message, "_PARTS_PERSIST_DEBOUNCE_MS", 1)
        monkeypatch.setattr(Message, "_persist_parts", persist_mock)
        monkeypatch.setattr("flocks.session.message.Recorder.record_tool_state", AsyncMock())

        await Message.store_part(sid, msg.id, tool_part)
        persist_mock.assert_not_awaited()

        await asyncio.sleep(0.02)
        persist_mock.assert_awaited_once_with(sid, message_id=msg.id)

    @pytest.mark.asyncio
    async def test_store_completed_tool_part_caches_prompt_output_and_revision(self):
        sid = SID + "_completed_cache"
        msg = await Message.create(sid, MessageRole.ASSISTANT, "")
        tool_part = ToolPart(
            sessionID=sid,
            messageID=msg.id,
            callID="call_completed",
            tool="bash",
            state=ToolStateCompleted(
                input={"cmd": "echo hi"},
                output={"stdout": "hello", "exit_code": 0},
                title="bash",
                metadata={},
                time={"start": 1, "end": 2},
            ),
        )

        assert Message.get_parts_revision(sid, msg.id) == 0
        stored_part = await Message.store_part(sid, msg.id, tool_part)

        assert Message.get_parts_revision(sid, msg.id) == 1
        assert isinstance(stored_part.state.output, str)
        assert stored_part.state.metadata["llm_output_text"] == stored_part.state.output
        assert stored_part.state.metadata["llm_output_len"] == len(stored_part.state.output)

        updated = await Message.update_part(
            sid,
            msg.id,
            stored_part.id,
            state={
                "status": "completed",
                "input": {"cmd": "echo hi"},
                "output": {"stdout": "hello again"},
                "title": "bash",
                "metadata": {},
                "time": {"start": 1, "end": 3},
            },
        )
        assert updated is not None
        assert Message.get_parts_revision(sid, msg.id) == 2
        assert updated.state.metadata["llm_output_text"].startswith("{")

    @pytest.mark.asyncio
    async def test_store_part_does_not_downgrade_terminal_tool_state(self, monkeypatch):
        sid = SID + "_terminal_guard"
        msg = await Message.create(sid, MessageRole.ASSISTANT, "")
        part_id = "part_terminal_guard"

        monkeypatch.setattr("flocks.session.message.Recorder.record_tool_state", AsyncMock())

        completed_part = ToolPart(
            id=part_id,
            sessionID=sid,
            messageID=msg.id,
            callID="call_terminal_guard",
            tool="task",
            state=ToolStateCompleted(
                input={"prompt": "run"},
                output="done",
                title="task",
                metadata={"sessionId": "ses_child_done"},
                time={"start": 1000, "end": 2000},
            ),
        )
        stale_running_part = ToolPart(
            id=part_id,
            sessionID=sid,
            messageID=msg.id,
            callID="call_terminal_guard",
            tool="task",
            state=ToolStateRunning(
                input={"prompt": "run"},
                title="task",
                metadata={"sessionId": "ses_child_done", "status": "running"},
                time={"start": 1000},
            ),
        )

        await Message.store_part(sid, msg.id, completed_part)
        stored = await Message.store_part(sid, msg.id, stale_running_part)
        parts = await Message.parts(msg.id, sid)
        tool_parts = [p for p in parts if p.type == "tool" and p.id == part_id]

        assert stored.state.status == "completed"
        assert len(tool_parts) == 1
        assert tool_parts[0].state.status == "completed"
        assert getattr(tool_parts[0].state, "time", {}).get("end") == 2000


@pytest.mark.asyncio
async def test_list_with_parts_keeps_message_info_separate():
    sid = SID + "_list_with_parts"
    msg = await Message.create(sid, MessageRole.ASSISTANT, "base text")
    tool_part = ToolPart(
        sessionID=sid,
        messageID=msg.id,
        callID="call_list_with_parts",
        tool="bash",
        state=ToolStatePending(input={"cmd": "echo hi"}, raw='{"cmd":"echo hi"}'),
    )

    await Message.store_part(sid, msg.id, tool_part)

    messages = await Message.list(sid)
    assert len(messages) == 1
    assert not hasattr(messages[0], "parts")

    messages_with_parts = await Message.list_with_parts(sid)
    assert len(messages_with_parts) == 1
    assert messages_with_parts[0].info.id == msg.id
    assert any(part.type == "text" for part in messages_with_parts[0].parts)
    assert any(part.type == "tool" for part in messages_with_parts[0].parts)
