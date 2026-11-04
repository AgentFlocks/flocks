"""A message change whose write failed is not in the cache that the next write is made from."""

from unittest.mock import AsyncMock

import pytest

from flocks.session.message import Message, MessageRole, UserMessageInfo
from flocks.storage.storage import Storage

CHANGES = ["create", "upsert_new", "upsert_existing", "archive", "clear", "update"]


def _user_message(session_id: str, message_id: str, agent: str) -> UserMessageInfo:
    return UserMessageInfo(
        id=message_id,
        sessionID=session_id,
        role="user",
        time={"created": 1},
        agent=agent,
        model={"providerID": "test", "modelID": "test"},
    )


async def _make_the_change(session_id: str, change: str) -> None:
    if change == "create":
        await Message.create(session_id, MessageRole.USER, "never written", id="msg_x", part_id="part_x")
    elif change == "upsert_new":
        await Message.upsert_message_info(session_id, _user_message(session_id, "msg_x", "rex"))
    elif change == "upsert_existing":
        await Message.upsert_message_info(session_id, _user_message(session_id, "msg_a", "never-written"))
    elif change == "archive":
        await Message.archive(session_id, "msg_a")
    elif change == "clear":
        await Message.clear(session_id)
    else:
        await Message.update(session_id, "msg_a", agent="never-written")


def _shape(messages) -> list[tuple]:
    return [(message.id, message.agent, bool(getattr(message, "compacted", False))) for message in messages]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", CHANGES)
async def test_the_next_write_neither_carries_the_failed_change_nor_loses_history(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    """A failed clear left an empty cache: the next message overwrote the whole stored history."""
    session_id = f"ses_failed_{change}"
    await Message.create(session_id, MessageRole.USER, "first", id="msg_a", part_id="part_a")

    with monkeypatch.context() as failing, pytest.raises(RuntimeError, match="storage unavailable"):
        failing.setattr(Storage, "mutate_many", AsyncMock(side_effect=RuntimeError("storage unavailable")))
        failing.setattr(Storage, "set", AsyncMock(side_effect=RuntimeError("storage unavailable")))
        await _make_the_change(session_id, change)

    await Message.create(session_id, MessageRole.USER, "second", id="msg_b", part_id="part_b")

    expected = [("msg_a", "rex", False), ("msg_b", "rex", False)]
    assert _shape(await Message.list(session_id, include_archived=True)) == expected, "this process"
    Message.invalidate_cache(session_id)
    assert _shape(await Message.list(session_id, include_archived=True)) == expected, "storage"
    first = await Message.get_with_parts_lazy(session_id, "msg_a")
    assert [part.text for part in first.parts] == ["first"]
