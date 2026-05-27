"""Persistence tests for message parts storage formats."""

import pytest

from flocks.session.message import (
    Message,
    MessageRole,
    TextPart,
    UserMessageInfo,
)
from flocks.storage.storage import Storage


def _user_message(session_id: str, message_id: str) -> UserMessageInfo:
    return UserMessageInfo(
        id=message_id,
        sessionID=session_id,
        role="user",
        time={"created": 1},
        agent="rex",
        model={"providerID": "test", "modelID": "test"},
    )


def _text_part(
    session_id: str,
    message_id: str,
    text: str,
    *,
    part_id: str | None = None,
) -> TextPart:
    return TextPart(
        id=part_id or f"part_{message_id}",
        sessionID=session_id,
        messageID=message_id,
        text=text,
    )


async def _write_legacy_session(session_id: str, messages: dict[str, str]) -> None:
    serialized_messages = []
    serialized_parts = {}
    for message_id, text in messages.items():
        serialized_messages.append(_user_message(session_id, message_id).model_dump())
        serialized_parts[message_id] = [
            _text_part(session_id, message_id, text).model_dump()
        ]

    await Storage.set(f"message:{session_id}", serialized_messages, "message")
    await Storage.set(f"message_parts:{session_id}", serialized_parts, "message_parts")
    Message.invalidate_cache(session_id)


@pytest.mark.asyncio
async def test_new_sessions_write_per_message_parts_keys() -> None:
    session_id = "ses_parts_per_message_new"

    await Message.create(session_id, MessageRole.USER, "hello", id="msg_a", part_id="part_a")
    await Message.create(session_id, MessageRole.USER, "world", id="msg_b", part_id="part_b")

    keys = sorted(await Storage.list_keys(prefix=f"message_parts:{session_id}:"))
    assert keys == [
        f"message_parts:{session_id}:msg_a",
        f"message_parts:{session_id}:msg_b",
    ]
    assert await Storage.get(f"message_parts:{session_id}") is None

    parts_a = await Storage.get(f"message_parts:{session_id}:msg_a")
    assert parts_a[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_legacy_blob_reads_without_migration() -> None:
    session_id = "ses_parts_legacy_read"
    await _write_legacy_session(session_id, {"msg_a": "legacy text"})

    messages = await Message.list_with_parts(session_id)

    assert len(messages) == 1
    assert messages[0].parts[0].text == "legacy text"
    assert await Storage.get(f"message_parts:{session_id}") is not None
    assert await Storage.list_keys(prefix=f"message_parts:{session_id}:") == []


@pytest.mark.asyncio
async def test_legacy_session_updates_continue_writing_legacy_blob() -> None:
    session_id = "ses_parts_legacy_update"
    await _write_legacy_session(session_id, {"msg_a": "old"})

    updated = await Message.update_part(
        session_id,
        "msg_a",
        "part_msg_a",
        text="new",
    )

    assert updated is not None
    legacy_parts = await Storage.get(f"message_parts:{session_id}")
    assert legacy_parts["msg_a"][0]["text"] == "new"
    assert await Storage.list_keys(prefix=f"message_parts:{session_id}:") == []


@pytest.mark.asyncio
async def test_mixed_session_reads_per_message_and_legacy_fallback() -> None:
    session_id = "ses_parts_mixed_read"
    serialized_messages = [
        _user_message(session_id, "msg_a").model_dump(),
        _user_message(session_id, "msg_b").model_dump(),
    ]
    legacy_parts = {
        "msg_a": [
            _text_part(session_id, "msg_a", "legacy-a1", part_id="part_a1").model_dump(),
            _text_part(session_id, "msg_a", "legacy-a2", part_id="part_a2").model_dump(),
        ],
        "msg_b": [
            _text_part(session_id, "msg_b", "legacy-b1", part_id="part_b1").model_dump(),
        ],
    }
    per_message_msg_a = [
        _text_part(session_id, "msg_a", "new-a2", part_id="part_a2").model_dump(),
    ]

    await Storage.set(f"message:{session_id}", serialized_messages, "message")
    await Storage.set(f"message_parts:{session_id}", legacy_parts, "message_parts")
    await Storage.set(
        f"message_parts:{session_id}:msg_a",
        per_message_msg_a,
        "message_part",
    )
    Message.invalidate_cache(session_id)

    messages = await Message.list_with_parts(session_id)
    by_id = {message.info.id: message for message in messages}

    assert [part.id for part in by_id["msg_a"].parts] == ["part_a1", "part_a2"]
    assert [part.text for part in by_id["msg_a"].parts] == ["legacy-a1", "new-a2"]
    assert [part.id for part in by_id["msg_b"].parts] == ["part_b1"]
    assert [part.text for part in by_id["msg_b"].parts] == ["legacy-b1"]

    assert await Storage.get(f"message_parts:{session_id}") == legacy_parts
    assert await Storage.get(f"message_parts:{session_id}:msg_a") == per_message_msg_a


@pytest.mark.asyncio
async def test_per_message_session_updates_only_target_message_key() -> None:
    session_id = "ses_parts_per_message_update"
    await Message.create(session_id, MessageRole.USER, "old", id="msg_a", part_id="part_a")

    updated = await Message.update_part(
        session_id,
        "msg_a",
        "part_a",
        text="new",
    )

    assert updated is not None
    assert await Storage.get(f"message_parts:{session_id}") is None
    parts_a = await Storage.get(f"message_parts:{session_id}:msg_a")
    assert parts_a[0]["text"] == "new"


@pytest.mark.asyncio
async def test_delete_removes_parts_using_session_storage_format() -> None:
    legacy_session_id = "ses_parts_delete_legacy"
    await _write_legacy_session(legacy_session_id, {"msg_a": "a", "msg_b": "b"})

    assert await Message.delete(legacy_session_id, "msg_a") is True

    legacy_parts = await Storage.get(f"message_parts:{legacy_session_id}")
    assert "msg_a" not in legacy_parts
    assert "msg_b" in legacy_parts
    assert await Storage.list_keys(prefix=f"message_parts:{legacy_session_id}:") == []

    per_message_session_id = "ses_parts_delete_per_message"
    await Message.create(per_message_session_id, MessageRole.USER, "a", id="msg_a", part_id="part_a")
    await Message.create(per_message_session_id, MessageRole.USER, "b", id="msg_b", part_id="part_b")

    assert await Message.delete(per_message_session_id, "msg_a") is True

    keys = await Storage.list_keys(prefix=f"message_parts:{per_message_session_id}:")
    assert keys == [f"message_parts:{per_message_session_id}:msg_b"]
    assert await Storage.get(f"message_parts:{per_message_session_id}") is None


@pytest.mark.asyncio
async def test_clear_removes_legacy_blob_and_per_message_keys() -> None:
    legacy_session_id = "ses_parts_clear_legacy"
    await _write_legacy_session(legacy_session_id, {"msg_a": "a"})

    assert await Message.clear(legacy_session_id) == 1

    assert await Storage.get(f"message_parts:{legacy_session_id}") is None
    assert await Storage.list_keys(prefix=f"message_parts:{legacy_session_id}:") == []

    per_message_session_id = "ses_parts_clear_per_message"
    await Message.create(per_message_session_id, MessageRole.USER, "a", id="msg_a", part_id="part_a")
    await Message.create(per_message_session_id, MessageRole.USER, "b", id="msg_b", part_id="part_b")

    assert await Message.clear(per_message_session_id) == 2

    assert await Storage.get(f"message_parts:{per_message_session_id}") is None
    assert await Storage.list_keys(prefix=f"message_parts:{per_message_session_id}:") == []
