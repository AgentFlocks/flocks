"""Persistence tests for message parts storage formats."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from flocks.session.message import (
    Message,
    MessageCacheInvalidatedError,
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
async def test_recent_legacy_page_reads_legacy_blob_once(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = "ses_parts_legacy_recent_page"
    await _write_legacy_session(
        session_id,
        {
            "msg_a": "legacy a",
            "msg_b": "legacy b",
            "msg_c": "legacy c",
        },
    )

    original_get = Storage.get
    legacy_blob_reads = 0

    async def counting_get(key: str):
        nonlocal legacy_blob_reads
        if key == f"message_parts:{session_id}":
            legacy_blob_reads += 1
        return await original_get(key)

    monkeypatch.setattr(Storage, "get", counting_get)

    messages, has_more, next_before = await Message.list_recent_with_parts(session_id, limit=3)

    assert [message.info.id for message in messages] == ["msg_a", "msg_b", "msg_c"]
    assert [message.parts[0].text for message in messages] == ["legacy a", "legacy b", "legacy c"]
    assert has_more is False
    assert next_before is None
    assert legacy_blob_reads == 1


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
async def test_delete_restores_caches_when_message_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "ses_parts_delete_message_failure"
    await Message.create(
        session_id,
        MessageRole.USER,
        "keep me",
        id="msg_a",
        part_id="part_a",
    )
    persist_messages = AsyncMock(
        side_effect=RuntimeError("message storage unavailable")
    )
    monkeypatch.setattr(Message, "_persist_messages", persist_messages)

    with pytest.raises(RuntimeError, match="message storage unavailable"):
        await Message.delete(session_id, "msg_a")

    restored = await Message.get_with_parts_lazy(session_id, "msg_a")
    assert restored is not None
    assert restored.info.id == "msg_a"
    assert [part.text for part in restored.parts] == ["keep me"]
    stored_messages = await Storage.get(f"message:{session_id}")
    assert [message["id"] for message in stored_messages] == ["msg_a"]


@pytest.mark.asyncio
async def test_delete_commits_when_parts_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "ses_parts_delete_parts_failure"
    await Message.create(
        session_id,
        MessageRole.USER,
        "delete me",
        id="msg_a",
        part_id="part_a",
    )
    original_delete = Storage.delete

    async def fail_parts_delete(key: str) -> None:
        if key == f"message_parts:{session_id}:msg_a":
            raise RuntimeError("parts storage unavailable")
        await original_delete(key)

    monkeypatch.setattr(Storage, "delete", fail_parts_delete)

    assert await Message.delete(session_id, "msg_a") is True
    assert await Message.get(session_id, "msg_a") is None
    assert await Storage.get(f"message:{session_id}") == []
    assert await Storage.get(f"message_parts:{session_id}:msg_a") is not None


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


@pytest.mark.asyncio
async def test_clear_tolerates_cache_invalidation_during_full_parts_load(monkeypatch) -> None:
    session_id = "ses_parts_clear_lru_race"
    Message.invalidate_cache()
    Message._lru[session_id] = True
    Message._messages_cache[session_id] = []
    Message._parts_cache[session_id] = {}
    Message._parts_revision_cache[session_id] = {}
    Message._parts_serialized_cache[session_id] = {}
    Message._parts_storage_format[session_id] = "per_message"
    Message._parts_persisted_mids[session_id] = set()
    Message._parts_fully_loaded.discard(session_id)

    async def fake_load_all_parts_locked(cls, sid: str, *, message_times: dict) -> None:
        assert sid == session_id
        Message.invalidate_cache(sid)
        await asyncio.sleep(0)
        Message._parts_cache[sid] = {}
        Message._parts_revision_cache[sid] = {}
        Message._parts_serialized_cache[sid] = {}
        Message._parts_storage_format[sid] = "per_message"
        Message._parts_persisted_mids[sid] = set()

    monkeypatch.setattr(
        Message,
        "_load_all_parts_locked",
        classmethod(fake_load_all_parts_locked),
    )

    assert await Message.clear(session_id) == 0
    assert session_id not in Message._lru
    assert session_id not in Message._parts_fully_loaded


@pytest.mark.asyncio
async def test_ensure_cache_raises_after_repeated_invalidation(monkeypatch) -> None:
    session_id = "ses_parts_full_load_repeated_invalidate"
    await _write_legacy_session(session_id, {"msg_a": "never stabilizes"})

    async def fake_load_all_parts_locked(cls, sid: str, *, message_times: dict) -> None:
        assert sid == session_id
        Message.invalidate_cache(sid)
        for index in range(Message._MAX_CACHE_GENERATIONS + 5):
            Message.invalidate_cache(f"ses_parts_full_load_churn_{index}")
        Message._parts_cache[sid] = {
            "msg_a": [_text_part(sid, "msg_a", "partial")]
        }
        await asyncio.sleep(0)

    monkeypatch.setattr(
        Message,
        "_load_all_parts_locked",
        classmethod(fake_load_all_parts_locked),
    )

    with pytest.raises(MessageCacheInvalidatedError):
        await Message._ensure_cache(session_id)

    assert session_id not in Message._lru
    assert session_id not in Message._messages_cache
    assert session_id not in Message._parts_cache
    assert session_id not in Message._parts_fully_loaded


@pytest.mark.asyncio
async def test_ensure_cache_retries_when_invalidated_during_full_parts_load(monkeypatch) -> None:
    session_id = "ses_parts_full_load_invalidate_retry"
    await _write_legacy_session(session_id, {"msg_a": "survives reload"})

    original_load_all_parts_locked = Message._load_all_parts_locked
    invalidated = False

    async def fake_load_all_parts_locked(cls, sid: str, *, message_times: dict) -> None:
        nonlocal invalidated
        assert sid == session_id
        if not invalidated:
            invalidated = True
            Message.invalidate_cache(sid)
            await asyncio.sleep(0)
            return
        await original_load_all_parts_locked(sid, message_times=message_times)

    monkeypatch.setattr(
        Message,
        "_load_all_parts_locked",
        classmethod(fake_load_all_parts_locked),
    )

    await Message._ensure_cache(session_id)

    assert invalidated is True
    assert session_id in Message._lru
    assert session_id in Message._messages_cache
    assert session_id in Message._parts_fully_loaded
    messages = await Message.list(session_id)
    assert [message.id for message in messages] == ["msg_a"]


@pytest.mark.asyncio
async def test_ensure_cache_ignores_unrelated_session_invalidation(monkeypatch) -> None:
    session_id = "ses_parts_full_load_target"
    unrelated_id = "ses_parts_full_load_unrelated"
    await _write_legacy_session(session_id, {"msg_a": "target survives"})
    await _write_legacy_session(unrelated_id, {"msg_b": "unrelated"})

    original_load_all_parts_locked = Message._load_all_parts_locked
    load_calls = 0

    async def fake_load_all_parts_locked(cls, sid: str, *, message_times: dict) -> None:
        nonlocal load_calls
        assert sid == session_id
        load_calls += 1
        Message.invalidate_cache(unrelated_id)
        await original_load_all_parts_locked(sid, message_times=message_times)

    monkeypatch.setattr(
        Message,
        "_load_all_parts_locked",
        classmethod(fake_load_all_parts_locked),
    )

    await Message._ensure_cache(session_id)

    assert load_calls == 1
    assert session_id in Message._parts_fully_loaded
    messages = await Message.list(session_id)
    assert [message.id for message in messages] == ["msg_a"]


def test_session_cache_generation_map_is_bounded() -> None:
    Message.invalidate_cache()

    for index in range(Message._MAX_CACHE_GENERATIONS + 25):
        Message.invalidate_cache(f"ses_generation_bound_{index}")

    assert len(Message._session_cache_generations) == Message._MAX_CACHE_GENERATIONS


@pytest.mark.asyncio
async def test_message_list_recovers_when_lru_outlives_message_cache() -> None:
    session_id = "ses_parts_stale_lru_without_messages"
    await _write_legacy_session(session_id, {"msg_a": "restored from disk"})
    Message._lru[session_id] = True
    Message._messages_cache.pop(session_id, None)
    Message._parts_fully_loaded.add(session_id)

    messages = await Message.list(session_id)

    assert [message.id for message in messages] == ["msg_a"]
    assert session_id in Message._lru
    assert session_id in Message._messages_cache
    assert session_id not in Message._parts_fully_loaded


@pytest.mark.asyncio
async def test_parts_without_session_uses_cache_snapshot(monkeypatch) -> None:
    session_id = "ses_parts_snapshot_search"
    Message.invalidate_cache()
    Message._parts_cache[session_id] = {}

    async def fake_ensure_cache(cls, sid: str) -> None:
        assert sid == session_id
        Message._parts_cache["ses_parts_snapshot_added"] = {}
        await asyncio.sleep(0)

    monkeypatch.setattr(Message, "_ensure_cache", classmethod(fake_ensure_cache))

    assert await Message.parts("missing_message_id") == []


@pytest.mark.asyncio
async def test_persist_parts_uses_snapshot_when_cache_changes(monkeypatch) -> None:
    session_id = "ses_parts_persist_snapshot"
    Message.invalidate_cache()
    Message._parts_cache[session_id] = {
        "msg_a": [_text_part(session_id, "msg_a", "a")],
    }
    Message._parts_serialized_cache[session_id] = {}
    Message._parts_storage_format[session_id] = "per_message"
    Message._parts_persisted_mids[session_id] = set()

    async def fake_storage_set(key: str, value, value_type: str = "json") -> None:
        Message._parts_cache[session_id]["msg_b"] = [
            _text_part(session_id, "msg_b", "b")
        ]
        await asyncio.sleep(0)

    monkeypatch.setattr(Storage, "set", fake_storage_set)

    await Message._persist_parts(session_id)


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
