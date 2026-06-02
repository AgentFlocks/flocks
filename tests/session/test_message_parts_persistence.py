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


def _text_part(session_id: str, message_id: str, text: str) -> TextPart:
    return TextPart(
        id=f"part_{message_id}",
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


async def _write_raw_legacy_payload(
    session_id: str,
    messages: list[dict],
    parts: dict[str, list],
) -> None:
    await Storage.set(f"message:{session_id}", messages, "message")
    await Storage.set(f"message_parts:{session_id}", parts, "message_parts")
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


def test_deserialize_legacy_text_part_normalizes_content_and_time() -> None:
    part = Message.deserialize_part(
        {
            "id": "part_legacy_text",
            "sessionID": "ses_legacy_text",
            "messageID": "msg_legacy_text",
            "type": "text",
            "content": "hello legacy",
            "time": {"created": 7},
        }
    )

    assert part.text == "hello legacy"
    assert part.time is not None
    assert part.time.start == 7
    assert part.time.end == 7


@pytest.mark.asyncio
async def test_ensure_cache_loads_legacy_assistant_missing_fields() -> None:
    session_id = "ses_legacy_assistant_missing_fields"
    await _write_raw_legacy_payload(
        session_id,
        messages=[
            {
                "id": "msg_assistant_legacy",
                "role": "assistant",
                "time": {"created": 2},
                "path": [],
                "content": "",
            }
        ],
        parts={
            "msg_assistant_legacy": [
                {
                    "id": "part_assistant_legacy",
                    "type": "text",
                    "content": "restored assistant text",
                    "time": {"created": 2},
                }
            ]
        },
    )

    messages = await Message.list_with_parts(session_id)

    assert len(messages) == 1
    info = messages[0].info
    assert info.sessionID == session_id
    assert info.agent == "rex"
    assert info.parentID == ""
    assert info.modelID == ""
    assert info.providerID == ""
    assert info.path.cwd == "./"
    assert info.tokens.input == 0
    assert messages[0].parts[0].text == "restored assistant text"
    assert messages[0].parts[0].time is not None
    assert messages[0].parts[0].time.start == 2


@pytest.mark.asyncio
async def test_ensure_cache_preserves_zero_created_timestamp() -> None:
    session_id = "ses_legacy_zero_created"
    await _write_raw_legacy_payload(
        session_id,
        messages=[
            {
                "id": "msg_assistant_zero",
                "role": "assistant",
                "time": {"created": 0},
                "path": [],
                "content": "",
            }
        ],
        parts={
            "msg_assistant_zero": [
                {
                    "id": "part_assistant_zero",
                    "type": "text",
                    "content": "zero timestamp text",
                    "time": {"created": 0},
                }
            ]
        },
    )

    messages = await Message.list_with_parts(session_id)

    assert len(messages) == 1
    assert messages[0].info.time["created"] == 0
    assert messages[0].parts[0].time is not None
    assert messages[0].parts[0].time.start == 0
    assert messages[0].parts[0].time.end == 0


@pytest.mark.asyncio
async def test_ensure_cache_loads_legacy_tool_part_without_time() -> None:
    session_id = "ses_legacy_tool_missing_time"
    await _write_raw_legacy_payload(
        session_id,
        messages=[
            {
                "id": "msg_assistant_tool",
                "role": "assistant",
                "time": {"created": 0},
                "path": [],
                "content": "",
            }
        ],
        parts={
            "msg_assistant_tool": [
                {
                    "id": "part_tool_legacy",
                    "type": "tool",
                    "tool": "bash",
                    "callID": "call_legacy",
                    "state": {
                        "status": "completed",
                        "output": "legacy output",
                    },
                }
            ]
        },
    )

    messages = await Message.list_with_parts(session_id)

    assert len(messages) == 1
    assert len(messages[0].parts) == 1
    tool_part = messages[0].parts[0]
    assert tool_part.type == "tool"
    assert tool_part.state.status == "completed"
    assert tool_part.state.time == {"start": 0, "end": 0}


@pytest.mark.asyncio
async def test_ensure_cache_skips_invalid_part_keeps_siblings() -> None:
    session_id = "ses_legacy_bad_part_skip"
    await _write_raw_legacy_payload(
        session_id,
        messages=[_user_message(session_id, "msg_a").model_dump()],
        parts={
            "msg_a": [
                "not-a-dict-part",
                {
                    "id": "part_good",
                    "sessionID": session_id,
                    "messageID": "msg_a",
                    "type": "text",
                    "text": "still here",
                },
            ]
        },
    )

    messages = await Message.list_with_parts(session_id)

    assert len(messages) == 1
    assert [part.text for part in messages[0].parts] == ["still here"]
