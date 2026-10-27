import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.session.agent_inbox import AgentInbox
from flocks.session.session_loop import SessionLoop

from flocks.session.message import (
    Message,
    MessageRole,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStatePending,
    ToolStateRunning,
)
from flocks.session.orphan_tools import (
    INTERRUPTED_MESSAGE_ERROR,
    INTERRUPTED_TOOL_ERROR,
    _is_unfinished_assistant,
    abort_all_orphan_running_parts,
    abort_orphan_running_parts,
    abort_orphan_running_parts_in_messages,
)
from flocks.session.core.status import SessionStatus, SessionStatusBusy, SessionStatusQueued, SessionStatusRetry
from flocks.session.runner import SessionRunner
from flocks.session.session import Session
from flocks.storage.storage import Storage


@pytest.mark.asyncio
async def test_abort_orphan_running_parts_marks_running_tools_error():
    session = await Session.create(project_id="proj_orphan", directory="/tmp")
    # 助手消息已收尾（finish=stop），这里只看 running 工具 part 的修复计数
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "", finish="stop")
    part = ToolPart(
        id="part_orphan_running",
        sessionID=session.id,
        messageID=msg.id,
        callID="call_orphan_running",
        tool="delegate_task",
        state=ToolStateRunning(
            input={"prompt": "keep going"},
            title="delegate_task",
            metadata={"sessionId": "ses_child"},
            time={"start": 1234},
        ),
    )

    await Message.store_part(session.id, msg.id, part)

    repaired = await abort_orphan_running_parts(session.id)
    parts = await Message.parts(msg.id, session.id)
    repaired_part = next(p for p in parts if p.id == "part_orphan_running")

    assert repaired == 1
    assert isinstance(repaired_part.state, ToolStateError)
    assert repaired_part.state.status == "error"
    assert repaired_part.state.error == INTERRUPTED_TOOL_ERROR
    assert repaired_part.state.input == {"prompt": "keep going"}
    assert repaired_part.state.metadata == {"sessionId": "ses_child"}
    assert repaired_part.state.time["start"] == 1234
    assert repaired_part.state.time["end"] >= 1234


@pytest.mark.asyncio
async def test_abort_orphan_running_parts_leaves_terminal_tools_unchanged():
    session = await Session.create(project_id="proj_orphan_terminal", directory="/tmp")
    # 助手消息已收尾（finish=stop），这里只看 running 工具 part 的修复计数
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "", finish="stop")
    completed = ToolPart(
        id="part_orphan_completed",
        sessionID=session.id,
        messageID=msg.id,
        callID="call_orphan_completed",
        tool="bash",
        state=ToolStateCompleted(
            input={"cmd": "pwd"},
            output="/tmp",
            title="bash",
            metadata={},
            time={"start": 1000, "end": 2000},
        ),
    )

    await Message.store_part(session.id, msg.id, completed)

    repaired = await abort_orphan_running_parts(session.id)
    parts = await Message.parts(msg.id, session.id)
    completed_part = next(p for p in parts if p.id == "part_orphan_completed")

    assert repaired == 0
    assert completed_part.state.status == "completed"
    assert completed_part.state.time == {"start": 1000, "end": 2000}


@pytest.mark.asyncio
async def test_abort_orphan_running_parts_in_messages_reuses_loaded_parts():
    session = await Session.create(project_id="proj_orphan_loaded", directory="/tmp")
    # 助手消息已收尾（finish=stop），这里只看 running 工具 part 的修复计数
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "", finish="stop")
    running = ToolPart(
        id="part_orphan_loaded_running",
        sessionID=session.id,
        messageID=msg.id,
        callID="call_orphan_loaded_running",
        tool="bash",
        state=ToolStateRunning(
            input={"cmd": "sleep 60"},
            metadata={"sessionId": "ses_child"},
            time={"start": 4321},
        ),
    )
    completed = ToolPart(
        id="part_orphan_loaded_completed",
        sessionID=session.id,
        messageID=msg.id,
        callID="call_orphan_loaded_completed",
        tool="bash",
        state=ToolStateCompleted(
            input={"cmd": "pwd"},
            output="/tmp",
            title="bash",
            metadata={},
            time={"start": 1000, "end": 2000},
        ),
    )

    await Message.store_part(session.id, msg.id, running)
    await Message.store_part(session.id, msg.id, completed)

    messages = await Message.list_with_parts(session.id)
    repaired = await abort_orphan_running_parts_in_messages(session.id, messages)

    assert repaired == 1
    repaired_running = next(p for p in messages[0].parts if p.id == "part_orphan_loaded_running")
    untouched_completed = next(p for p in messages[0].parts if p.id == "part_orphan_loaded_completed")
    assert isinstance(repaired_running.state, ToolStateError)
    assert repaired_running.state.error == INTERRUPTED_TOOL_ERROR
    assert repaired_running.state.metadata == {"sessionId": "ses_child"}
    assert repaired_running.state.time["start"] == 4321
    assert repaired_running.state.time["end"] >= 4321
    assert untouched_completed.state.status == "completed"


@pytest.mark.asyncio
async def test_abort_all_orphan_running_parts_scans_persisted_sessions():
    session = await Session.create(project_id="proj_orphan_all", directory="/tmp")
    # 助手消息已收尾（finish=stop），这里只看 running 工具 part 的修复计数
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "", finish="stop")
    part = ToolPart(
        id="part_orphan_all",
        sessionID=session.id,
        messageID=msg.id,
        callID="call_orphan_all",
        tool="bash",
        state=ToolStateRunning(
            input={"cmd": "sleep 60"},
            time={"start": 5000},
        ),
    )

    await Message.store_part(session.id, msg.id, part)

    repaired = await abort_all_orphan_running_parts()
    parts = await Message.parts(msg.id, session.id)
    repaired_part = next(p for p in parts if p.id == "part_orphan_all")

    assert repaired == 1
    assert repaired_part.state.status == "error"
    assert repaired_part.state.error == INTERRUPTED_TOOL_ERROR


@pytest.mark.asyncio
async def test_abort_all_orphan_running_parts_skips_busy_sessions():
    session = await Session.create(project_id="proj_orphan_busy", directory="/tmp")
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "")
    part = ToolPart(
        id="part_orphan_busy",
        sessionID=session.id,
        messageID=msg.id,
        callID="call_orphan_busy",
        tool="bash",
        state=ToolStateRunning(
            input={"cmd": "sleep 60"},
            time={"start": 5000},
        ),
    )

    await Message.store_part(session.id, msg.id, part)
    SessionStatus.set(session.id, SessionStatusBusy())
    try:
        repaired = await abort_all_orphan_running_parts()
    finally:
        SessionStatus.clear(session.id)
    parts = await Message.parts(msg.id, session.id)
    running_part = next(p for p in parts if p.id == "part_orphan_busy")

    assert repaired == 0
    assert running_part.state.status == "running"


@pytest.mark.asyncio
async def test_abort_orphan_running_parts_marks_unfinished_assistant_reply_interrupted():
    """进程重启留下的没有 finish/error 的助手回复也要收尾，否则前端一直显示生成中。"""
    session = await Session.create(project_id="proj_orphan_reply", directory="/tmp")
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "partial answer")
    part = ToolPart(
        id="part_orphan_reply_running",
        sessionID=session.id,
        messageID=msg.id,
        callID="call_orphan_reply_running",
        tool="bash",
        state=ToolStateRunning(input={"cmd": "sleep 60"}, time={"start": 7000}),
    )
    await Message.store_part(session.id, msg.id, part)

    repaired = await abort_orphan_running_parts(session.id)
    info = next(m for m in await Message.list(session.id) if m.id == msg.id)

    assert repaired == 2  # 1 个 running 工具 part + 1 条未收尾助手回复
    assert info.finish == "error"
    assert info.error["name"] == "MessageAbortedError"
    assert info.error["message"] == INTERRUPTED_MESSAGE_ERROR

    # 再跑一次是幂等的
    assert await abort_orphan_running_parts(session.id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_key", ["completed", "end"])
@pytest.mark.parametrize("recovery", ["preloaded", "startup"])
async def test_orphan_recovery_preserves_legacy_completed_reply(completion_key, recovery):
    session = await Session.create(project_id="proj_legacy_completed", directory="/tmp")
    stored_messages = [{
        "id": "msg_legacy_completed",
        "role": "assistant",
        "time": {"created": 1000, completion_key: 2000},
    }]
    stored_parts = {"msg_legacy_completed": [{
        "id": "part_legacy_completed",
        "type": "text",
        "text": "The completed historical answer.",
    }]}
    await Storage.set(f"message:{session.id}", stored_messages, "message")
    await Storage.set(f"message_parts:{session.id}", stored_parts, "message_parts")
    Message.invalidate_cache(session.id)

    if recovery == "preloaded":
        messages = await Message.list_with_parts(session.id)
        repaired = await abort_orphan_running_parts_in_messages(session.id, messages)
    else:
        repaired = await abort_all_orphan_running_parts()

    assert repaired == 0
    Message.invalidate_cache(session.id)
    recovered = await Message.get_with_parts(session.id, "msg_legacy_completed")
    assert recovered.info.time["completed"] == 2000
    assert recovered.info.finish is None
    assert recovered.info.error is None
    assert await Message.get_text_content(recovered.info, parts=recovered.parts) == stored_parts["msg_legacy_completed"][0]["text"]
    # Recovery must not rewrite the old record just to populate a finish reason.
    assert await Storage.get(f"message:{session.id}") == stored_messages
    assert await Storage.get(f"message_parts:{session.id}") == stored_parts


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_key", ["completed", "end"])
@pytest.mark.parametrize("completion_time", [0, -1])
async def test_orphan_recovery_still_closes_reply_with_unset_completion_time(
    completion_key, completion_time,
):
    session = await Session.create(project_id="proj_unset_completed", directory="/tmp")
    message = await Message.create(
        session.id, MessageRole.ASSISTANT, "Partial answer.",
        time={"created": 1000, completion_key: completion_time},
    )
    Message.invalidate_cache(session.id)

    assert await abort_orphan_running_parts(session.id) == 1
    Message.invalidate_cache(session.id)
    recovered = await Message.get_with_parts(session.id, message.id)
    assert recovered.info.finish == "error"
    assert recovered.info.error["name"] == "MessageAbortedError"
    assert await Message.get_text_content(recovered.info, parts=recovered.parts) == "Partial answer."


@pytest.mark.parametrize("raw_time", [
    None,
    {},
    {"completed": None},
    {"completed": ""},
    {"completed": "not-a-timestamp"},
    {"completed": True},
    {"completed": False},
    {"completed": []},
    {"completed": {}},
    {"end": "not-a-timestamp"},
])
def test_invalid_completion_timestamp_does_not_hide_unfinished_reply(raw_time):
    info = SimpleNamespace(role="assistant", finish=None, error=None, time=raw_time)
    assert _is_unfinished_assistant(info)


@pytest.mark.asyncio
async def test_idle_pending_tool_recovery_preserves_identity_input_and_is_idempotent():
    session = await Session.create(project_id="proj_orphan_pending", directory="/tmp")
    # 助手消息已收尾（finish=stop），这里只看 pending 工具 part 的修复计数
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "", finish="stop")
    part = ToolPart(
        id="part_pending_orphan", sessionID=session.id, messageID=msg.id,
        callID="call_pending_orphan", tool="sleep",
        state=ToolStatePending(input={"timeout_s": 123}, raw='{"timeout_s":123}'),
    )
    await Message.store_part(session.id, msg.id, part)

    assert await abort_orphan_running_parts(session.id) == 1
    recovered = next(p for p in await Message.parts(msg.id, session.id) if p.id == part.id)
    assert recovered.id == part.id
    assert recovered.callID == part.callID
    assert isinstance(recovered.state, ToolStateError)
    assert recovered.state.input == {"timeout_s": 123}
    assert recovered.state.error == INTERRUPTED_TOOL_ERROR
    assert recovered.state.time["end"] >= recovered.state.time["start"]
    assert await abort_orphan_running_parts(session.id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["busy", "queued", "retry", "loop", "runner"])
async def test_pending_recovery_does_not_touch_active_or_queued_session(guard, monkeypatch):
    session = await Session.create(project_id="proj_orphan_pending_safe", directory="/tmp")
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "")
    part = ToolPart(
        id="part_pending_safe", sessionID=session.id, messageID=msg.id,
        callID="call_pending_safe", tool="sleep",
        state=ToolStatePending(input={}, raw=""),
    )
    await Message.store_part(session.id, msg.id, part)
    if guard in {"busy", "queued", "retry"}:
        status = {
            "busy": SessionStatusBusy(),
            "queued": SessionStatusQueued(),
            "retry": SessionStatusRetry(attempt=1, message="retrying", next=1234),
        }[guard]
        SessionStatus.set(session.id, status)
    elif guard == "loop":
        monkeypatch.setitem(SessionLoop._active_loops, session.id, object())
    else:
        monkeypatch.setitem(SessionRunner._active_sessions, session.id, object())
    try:
        assert await abort_orphan_running_parts(session.id) == 0
        unchanged = next(p for p in await Message.parts(msg.id, session.id) if p.id == part.id)
        assert isinstance(unchanged.state, ToolStatePending)
    finally:
        SessionStatus.clear(session.id)


@pytest.fixture
async def submitted_receipt(monkeypatch):
    monkeypatch.setattr(AgentInbox, "_inboxes", {})
    monkeypatch.setattr(AgentInbox, "_children", {})
    monkeypatch.setattr(AgentInbox, "_wakes", {})
    monkeypatch.setattr(SessionLoop, "_active_loops", {})
    session = await Session.create(project_id="proj_orphan_receipt", directory="/tmp")
    child = await Session.create(project_id=session.project_id, directory="/tmp", parent_id=session.id)
    # The step that submitted the delegation already finished its reply.
    message = await Message.create(session.id, MessageRole.ASSISTANT, "", finish="tool-calls")
    part = ToolPart(
        sessionID=session.id,
        messageID=message.id,
        callID="call_submitted",
        tool="delegate_task",
        state=ToolStateCompleted(
            input={"prompt": "Finish the task"},
            output={"status": "submitted", "child_session_id": child.id},
            title="Delegated task",
            metadata={"sessionId": child.id, "submitted": True, "status": "running", "custom": "preserved"},
            time={"start": 1000, "end": 2000},
        ),
    )
    await Message.store_part(session.id, message.id, part)
    return session, message, part


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["delegate_task", "task"])
@pytest.mark.parametrize("recovery", ["preloaded", "startup"])
async def test_submitted_receipt_recovery_after_restart_preserves_completed_state(
    submitted_receipt, monkeypatch, tool_name, recovery,
):
    session, message, part = submitted_receipt
    part.tool = tool_name
    await Message.store_part(session.id, message.id, part)
    original_state = part.state.model_dump(exclude={"metadata"})
    original_metadata = dict(part.state.metadata)
    monkeypatch.setattr("flocks.session.orphan_tools.time.time", lambda: 12.345)
    run = AsyncMock()
    monkeypatch.setattr(SessionLoop, "run", run)

    if recovery == "preloaded":
        messages = await Message.list_with_parts(session.id)
        repaired = await abort_orphan_running_parts_in_messages(session.id, messages)
        assert next(p for p in messages[0].parts if p.id == part.id).state.metadata["status"] == "error"
    else:
        repaired = await abort_all_orphan_running_parts()

    recovered = next(p for p in await Message.parts(message.id, session.id) if p.id == part.id)
    assert repaired == 1
    assert isinstance(recovered.state, ToolStateCompleted)
    assert recovered.state.model_dump(exclude={"metadata"}) == original_state
    assert recovered.state.metadata == {
        **original_metadata,
        "status": "error",
        "result": INTERRUPTED_TOOL_ERROR,
        "finished_at": 12345,
    }
    assert await abort_orphan_running_parts(session.id) == 0
    run.assert_not_awaited()
    assert not AgentInbox._children
    assert not AgentInbox._wakes


@pytest.mark.asyncio
@pytest.mark.parametrize("registry_name", ["_children", "_wakes"])
@pytest.mark.parametrize("done", [False, True], ids=["live-finalizer", "done-callback-handoff"])
async def test_submitted_receipt_recovery_skips_registered_execution_and_finalizers(
    submitted_receipt, registry_name, done,
):
    session, message, part = submitted_receipt
    original = part.model_dump()
    child_id = part.state.metadata["sessionId"]
    task = asyncio.create_task(asyncio.sleep(0) if done else asyncio.Event().wait())
    if done:
        await task
    registry = getattr(AgentInbox, registry_name)
    registry[child_id] = (session.id, task) if registry_name == "_children" else task
    try:
        assert await abort_orphan_running_parts(session.id) == 0
        unchanged = next(p for p in await Message.parts(message.id, session.id) if p.id == part.id)
        assert unchanged.model_dump() == original
    finally:
        registry.pop(child_id)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_submitted_receipt_recovery_skips_live_session_loop(submitted_receipt, monkeypatch):
    session, message, part = submitted_receipt
    original = part.model_dump()
    monkeypatch.setitem(SessionLoop._active_loops, part.state.metadata["sessionId"], object())

    assert await abort_orphan_running_parts(session.id) == 0
    unchanged = next(p for p in await Message.parts(message.id, session.id) if p.id == part.id)
    assert unchanged.model_dump() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,metadata", [
    ("bash", {"submitted": True, "status": "running"}),
    ("delegate_task", {"submitted": True, "status": "completed", "result": "done", "finished_at": 3000}),
    ("task", {"submitted": True, "status": "error", "result": "failed", "finished_at": 3000}),
    ("delegate_task", {"status": "running"}),
    ("task", {"submitted": False, "status": "running"}),
])
async def test_submitted_receipt_recovery_leaves_other_completed_tools_unchanged(
    submitted_receipt, tool_name, metadata,
):
    session, message, part = submitted_receipt
    part.tool = tool_name
    part.state.metadata = {"sessionId": part.state.metadata["sessionId"], **metadata}
    await Message.store_part(session.id, message.id, part)
    original = part.model_dump()

    assert await abort_orphan_running_parts(session.id) == 0
    unchanged = next(p for p in await Message.parts(message.id, session.id) if p.id == part.id)
    assert unchanged.model_dump() == original
