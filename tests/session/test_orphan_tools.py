import pytest

from flocks.session.message import (
    Message,
    MessageRole,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStateRunning,
)
from flocks.session.orphan_tools import (
    INTERRUPTED_TOOL_ERROR,
    abort_all_orphan_running_parts,
    abort_orphan_running_parts,
)
from flocks.session.core.status import SessionStatus, SessionStatusBusy
from flocks.session.session import Session


@pytest.mark.asyncio
async def test_abort_orphan_running_parts_marks_running_tools_error():
    session = await Session.create(project_id="proj_orphan", directory="/tmp")
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "")
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
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "")
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
async def test_abort_all_orphan_running_parts_scans_persisted_sessions():
    session = await Session.create(project_id="proj_orphan_all", directory="/tmp")
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "")
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
