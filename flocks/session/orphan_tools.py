"""Recovery helpers for tool calls left running by interrupted processes."""

import time
from typing import Iterable, Optional

from flocks.session.message import Message, MessageWithParts, ToolPart, ToolStateError
from flocks.session.session import SessionInfo
from flocks.storage.storage import Storage
from flocks.utils.log import Log


log = Log.create(service="session.orphan_tools")


INTERRUPTED_TOOL_ERROR = "Interrupted by server restart"


def _build_interrupted_error_state(state: object, now_ms: int) -> ToolStateError:
    """Create the terminal error state used for recovered orphaned tools."""
    time_info = getattr(state, "time", {}) or {}
    start_ms = time_info.get("start", now_ms)

    return ToolStateError(
        status="error",
        input=getattr(state, "input", {}),
        error=INTERRUPTED_TOOL_ERROR,
        metadata=getattr(state, "metadata", None),
        time={"start": start_ms, "end": now_ms},
    )


async def abort_orphan_running_parts_in_messages(
    session_id: str,
    messages_with_parts: Iterable[MessageWithParts],
) -> int:
    """Mark running tool parts as interrupted using preloaded message parts."""
    now_ms = int(time.time() * 1000)
    repaired = 0

    for msg_with_parts in messages_with_parts:
        message_id = msg_with_parts.info.id
        for part in msg_with_parts.parts:
            if not isinstance(part, ToolPart):
                continue
            state = part.state
            if getattr(state, "status", None) != "running":
                continue

            part.state = _build_interrupted_error_state(state, now_ms)
            await Message.store_part(session_id, message_id, part)
            repaired += 1

    if repaired:
        log.info("session.orphan_tools.aborted", {
            "session_id": session_id,
            "count": repaired,
        })
    return repaired


async def abort_orphan_running_parts(session_id: str) -> int:
    """Mark persisted running tool parts as interrupted errors."""
    messages_with_parts = await Message.list_with_parts(session_id)
    return await abort_orphan_running_parts_in_messages(session_id, messages_with_parts)


async def abort_orphan_running_parts_for_sessions(
    session_ids: Iterable[str],
    *,
    skip_busy: bool = False,
) -> int:
    """Best-effort recovery for a known set of sessions."""
    total = 0
    for session_id in dict.fromkeys(session_ids):
        try:
            if skip_busy:
                from flocks.session.core.status import SessionStatus

                if session_id in SessionStatus.get_busy_session_ids():
                    continue
            total += await abort_orphan_running_parts(session_id)
        except Exception as exc:
            log.warn("session.orphan_tools.session_failed", {
                "session_id": session_id,
                "error": str(exc),
            })
    return total


async def abort_all_orphan_running_parts(*, limit: Optional[int] = None) -> int:
    """Best-effort startup recovery for all persisted sessions."""
    entries = await Storage.list_entries(prefix="session:", model=SessionInfo)
    session_ids = [
        session.id
        for _, session in entries
        if getattr(session, "status", None) != "deleted"
    ]
    if limit is not None:
        session_ids = session_ids[:limit]
    return await abort_orphan_running_parts_for_sessions(session_ids, skip_busy=True)
