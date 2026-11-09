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
    mutate_many = AsyncMock(
        side_effect=RuntimeError("message storage unavailable")
    )
    monkeypatch.setattr(Storage, "mutate_many", mutate_many)

    with pytest.raises(RuntimeError, match="message storage unavailable"):
        await Message.delete(session_id, "msg_a")

    restored = await Message.get_with_parts_lazy(session_id, "msg_a")
    assert restored is not None
    assert restored.info.id == "msg_a"
    assert [part.text for part in restored.parts] == ["keep me"]
    stored_messages = await Storage.get(f"message:{session_id}")
    assert [message["id"] for message in stored_messages] == ["msg_a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["store_part", "add_part", "update_part", "remove_part"])
async def test_a_part_change_whose_write_failed_is_not_written_later(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    """The message also has a running tool's change waiting to be written: that one is, the failed one is not."""
    from flocks.session.message import ToolPart, ToolStateRunning

    session_id = f"ses_parts_failed_{change}"
    await Message.create(session_id, MessageRole.USER, "keep me", id="msg_a", part_id="part_a")
    await Message.store_part(session_id, "msg_a", ToolPart(
        id="part_tool", sessionID=session_id, messageID="msg_a", callID="call_a", tool="read",
        state=ToolStateRunning(input={}, time={"start": 1}),
    ))  # running: written a little later
    new_text = TextPart(id="part_new", sessionID=session_id, messageID="msg_a", text="never written")
    with monkeypatch.context() as failing, pytest.raises(RuntimeError, match="storage unavailable"):
        failing.setattr(Storage, "mutate_many", AsyncMock(side_effect=RuntimeError("storage unavailable")))
        if change == "store_part":
            await Message.store_part(session_id, "msg_a", new_text)
        elif change == "add_part":
            await Message.add_part(session_id, "msg_a", new_text)
        elif change == "update_part":
            await Message.update_part(session_id, "msg_a", "part_a", text="never written")
        else:
            await Message.remove_part(session_id, "msg_a", "part_a")

    def shape(message):
        return [(part.type, part.text if part.type == "text" else part.state.status) for part in message.parts]

    expected = [("text", "keep me"), ("tool", "running")]
    assert shape(await Message.get_with_parts_lazy(session_id, "msg_a")) == expected, "this process"
    await Message.quiesce_parts(session_id, persist=True)  # whatever is still pending is written now
    Message.invalidate_cache(session_id)
    assert shape(await Message.get_with_parts_lazy(session_id, "msg_a")) == expected, "storage"


@pytest.mark.asyncio
async def test_an_error_text_whose_write_failed_is_not_written_later(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner's visible error text, under the same failure: not changed in the cache before it is written."""
    from types import SimpleNamespace

    from flocks.session.message import ToolPart, ToolStateRunning
    from flocks.session.runner import SessionRunner

    session_id = "ses_parts_failed_error_text"
    await Message.create(session_id, MessageRole.USER, "hi", id="msg_u", part_id="part_u")
    reply = await Message.create(session_id, MessageRole.ASSISTANT, "", id="msg_a", part_id="part_a", parentID="msg_u")
    await Message.store_part(session_id, "msg_a", ToolPart(
        id="part_tool", sessionID=session_id, messageID="msg_a", callID="call_a", tool="read",
        state=ToolStateRunning(input={}, time={"start": 1}),
    ))  # running: written a little later
    runner = SimpleNamespace(session=SimpleNamespace(id=session_id))
    with monkeypatch.context() as failing, pytest.raises(RuntimeError, match="storage unavailable"):
        failing.setattr(Storage, "mutate_many", AsyncMock(side_effect=RuntimeError("storage unavailable")))
        await SessionRunner._ensure_visible_error_text(runner, reply, "the model returned nothing")

    def texts(message):
        return [part.text for part in message.parts if part.type == "text"]

    assert texts(await Message.get_with_parts_lazy(session_id, "msg_a")) == [""], "this process"
    await Message.quiesce_parts(session_id, persist=True)
    Message.invalidate_cache(session_id)
    assert texts(await Message.get_with_parts_lazy(session_id, "msg_a")) == [""], "storage"


@pytest.mark.asyncio
async def test_delete_removes_message_and_parts_atomically() -> None:
    session_id = "ses_parts_delete_parts_failure"
    await Message.create(
        session_id,
        MessageRole.USER,
        "delete me",
        id="msg_a",
        part_id="part_a",
    )
    assert await Message.delete(session_id, "msg_a") is True
    assert await Message.get(session_id, "msg_a") is None
    assert await Storage.get(f"message:{session_id}") == []
    assert await Storage.get(f"message_parts:{session_id}:msg_a") is None


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
async def test_clear_skips_full_parts_load_and_keeps_empty_cache(monkeypatch) -> None:
    """clear() only needs message metadata; afterwards the session stays cached as empty."""
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
        raise AssertionError("clear() must not deserialize every part just to discard it")

    monkeypatch.setattr(
        Message,
        "_load_all_parts_locked",
        classmethod(fake_load_all_parts_locked),
    )

    assert await Message.clear(session_id) == 0
    assert session_id in Message._lru
    assert Message._messages_cache[session_id] == []
    assert Message._parts_cache[session_id] == {}
    assert session_id in Message._parts_fully_loaded


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


@pytest.mark.asyncio
async def test_delete_keeps_owner_and_sibling_caches_warm() -> None:
    from flocks.session.message import _session_locks

    session_id = "ses_parts_delete_cache_scope"
    sibling_id = "ses_parts_delete_cache_scope_sibling"
    await Message.create(session_id, MessageRole.USER, "a", id="msg_a", part_id="part_a")
    await Message.create(session_id, MessageRole.USER, "b", id="msg_b", part_id="part_b")
    await Message.create(sibling_id, MessageRole.USER, "c", id="msg_c", part_id="part_c")
    epoch_before = Message._cache_epoch

    assert await Message.delete(session_id, "msg_a") is True

    # Message maintains its own cache; the storage write must not evict it.
    assert Message._cache_epoch == epoch_before
    assert [m.id for m in Message._messages_cache[session_id]] == ["msg_b"]
    assert session_id in Message._parts_fully_loaded
    assert [m.id for m in Message._messages_cache[sibling_id]] == ["msg_c"]
    assert sibling_id in _session_locks._locks


@pytest.mark.asyncio
async def test_clear_keeps_sibling_caches_warm() -> None:
    from flocks.session.message import _session_locks

    session_id = "ses_parts_clear_cache_scope"
    sibling_id = "ses_parts_clear_cache_scope_sibling"
    await Message.create(session_id, MessageRole.USER, "a", id="msg_a", part_id="part_a")
    await Message.create(sibling_id, MessageRole.USER, "c", id="msg_c", part_id="part_c")
    epoch_before = Message._cache_epoch

    assert await Message.clear(session_id) == 1

    assert Message._cache_epoch == epoch_before
    assert Message._messages_cache[session_id] == []
    assert [m.id for m in Message._messages_cache[sibling_id]] == ["msg_c"]
    assert sibling_id in _session_locks._locks


async def _touch_other_sessions(prefix: str) -> None:
    """A pass over enough other sessions to push anything out of the cache."""
    for index in range(Message._MAX_CACHED_SESSIONS + 5):
        await Message.list(f"{prefix}_{index}")


@pytest.mark.asyncio
async def test_pass_over_many_sessions_does_not_fail_a_load_in_progress(monkeypatch) -> None:
    """A slow load must survive a background pass, such as the startup orphan scan."""
    session_id = "ses_lru_load_in_progress"
    await _write_legacy_session(session_id, {"msg_a": "keeps loading"})

    original_load_all_parts_locked = Message._load_all_parts_locked
    loads = 0

    async def slow_load(cls, sid: str, *, message_times: dict) -> None:
        nonlocal loads
        loads += 1
        await _touch_other_sessions(f"ses_lru_load_other_{loads}")
        await original_load_all_parts_locked(sid, message_times=message_times)

    monkeypatch.setattr(Message, "_load_all_parts_locked", classmethod(slow_load))

    await Message._ensure_cache(session_id)

    assert loads == 1
    assert session_id in Message._parts_fully_loaded
    assert [message.id for message in await Message.list(session_id)] == ["msg_a"]
    assert len(Message._lru) <= Message._MAX_CACHED_SESSIONS


@pytest.mark.asyncio
async def test_writer_waiting_for_the_lock_keeps_the_history() -> None:
    """Evicted while it waits, ``create`` would store a history of only its own message."""
    from flocks.session.message import _session_locks

    session_id = "ses_lru_waiting_writer"
    await Message.create(session_id, MessageRole.USER, "first", id="msg_first", part_id="part_first")

    lock = _session_locks.get(session_id)
    await lock.acquire()
    writer = asyncio.create_task(
        Message.create(session_id, MessageRole.USER, "second", id="msg_second", part_id="part_second")
    )
    try:
        await asyncio.sleep(0)
        await _touch_other_sessions("ses_lru_waiting_other")
    finally:
        lock.release()
    await writer

    stored = await Storage.get(f"message:{session_id}")
    assert [message["id"] for message in stored] == ["msg_first", "msg_second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("discard_lock", [True, False])
async def test_writer_queued_behind_an_invalidation_keeps_the_history(discard_lock: bool) -> None:
    from flocks.session.message import _session_locks

    session_id = f"ses_invalidate_waiting_writer_{discard_lock}"
    await Message.create(session_id, MessageRole.USER, "first", id="msg_first", part_id="part_first")

    lock = _session_locks.get(session_id)
    await lock.acquire()
    writer = asyncio.create_task(
        Message.create(session_id, MessageRole.USER, "second", id="msg_second", part_id="part_second")
    )
    try:
        await asyncio.sleep(0)
        Message.invalidate_cache(session_id, discard_lock=discard_lock)
    finally:
        lock.release()
    await writer

    stored = await Storage.get(f"message:{session_id}")
    assert [message["id"] for message in stored] == ["msg_first", "msg_second"]


@pytest.mark.asyncio
async def test_writer_queued_behind_a_delete_keeps_what_was_not_deleted() -> None:
    session_id = "ses_delete_waiting_writer"
    await Message.create(session_id, MessageRole.USER, "old", id="msg_old", part_id="part_old")
    await Message.create(session_id, MessageRole.USER, "keep", id="msg_keep", part_id="part_keep")

    delete = asyncio.create_task(Message.delete_many(session_id, ["msg_old"]))
    await asyncio.sleep(0)
    writer = asyncio.create_task(
        Message.create(session_id, MessageRole.USER, "new", id="msg_new", part_id="part_new")
    )
    assert await delete == 1
    await writer

    stored = await Storage.get(f"message:{session_id}")
    assert [message["id"] for message in stored] == ["msg_keep", "msg_new"]
    assert [message.id for message in await Message.list(session_id)] == ["msg_keep", "msg_new"]


@pytest.mark.asyncio
async def test_part_writer_queued_behind_an_invalidation_keeps_the_other_parts() -> None:
    from flocks.session.message import _session_locks

    session_id = "ses_invalidate_waiting_part_writer"
    await Message.create(session_id, MessageRole.USER, "first", id="msg_a", part_id="part_first")

    lock = _session_locks.get(session_id)
    await lock.acquire()
    second = TextPart(id="part_second", sessionID=session_id, messageID="msg_a", text="second")
    writer = asyncio.create_task(Message.store_part(session_id, "msg_a", second))
    try:
        await asyncio.sleep(0)
        Message.invalidate_cache(session_id)
    finally:
        lock.release()
    await writer

    stored = await Storage.get(f"message_parts:{session_id}:msg_a")
    assert [part["id"] for part in stored] == ["part_first", "part_second"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "effect"),
    [
        (lambda sid: Message.update(sid, "msg_a", agent="reviewed"), "updated"),
        (lambda sid: Message.archive(sid, "msg_a"), "archived"),
        (lambda sid: Message.delete(sid, "msg_a"), "deleted"),
        (lambda sid: Message.update_part(sid, "msg_a", "part_a", text="edited"), "part_edited"),
        (lambda sid: Message.remove_part(sid, "msg_a", "part_a"), "part_removed"),
        (lambda sid: Message.add_part(sid, "msg_a", _text_part(sid, "msg_b", "more")), "part_added"),
        (lambda sid: Message.upsert_message_info(sid, _user_message(sid, "msg_b")), "message_upserted"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
async def test_queued_change_is_not_lost_to_an_invalidation(call, effect: str) -> None:
    """Without the reload these find nothing in the dropped cache and quietly do nothing."""
    from flocks.session.message import _session_locks

    session_id = f"ses_invalidate_waiting_change_{effect}"
    await Message.create(session_id, MessageRole.USER, "text", id="msg_a", part_id="part_a")

    lock = _session_locks.get(session_id)
    await lock.acquire()
    change = asyncio.create_task(call(session_id))
    try:
        await asyncio.sleep(0)
        Message.invalidate_cache(session_id)
    finally:
        lock.release()
    assert await change not in (None, False)

    stored = await Storage.get(f"message:{session_id}")
    stored_parts = await Storage.get(f"message_parts:{session_id}:msg_a")
    if effect == "updated":
        assert stored[0]["agent"] == "reviewed"
    elif effect == "archived":
        assert stored[0]["compacted"] is True
    elif effect == "deleted":
        assert stored == []
    elif effect == "part_edited":
        assert stored_parts[0]["text"] == "edited"
    elif effect == "part_removed":
        assert stored_parts == []
    elif effect == "part_added":
        assert [part["id"] for part in stored_parts] == ["part_a", "part_msg_b"]
    else:
        assert [message["id"] for message in stored] == ["msg_a", "msg_b"]


@pytest.mark.asyncio
async def test_queued_ensure_text_part_still_sees_the_existing_text() -> None:
    """Reading the dropped cache as empty would add a fallback next to the real text."""
    from flocks.session.message import _session_locks

    session_id = "ses_invalidate_waiting_ensure_text"
    await Message.create(session_id, MessageRole.USER, "text", id="msg_a", part_id="part_a")

    lock = _session_locks.get(session_id)
    await lock.acquire()
    change = asyncio.create_task(Message.ensure_text_part(session_id, "msg_a", "fallback"))
    try:
        await asyncio.sleep(0)
        Message.invalidate_cache(session_id)
    finally:
        lock.release()

    assert await change is False
    stored_parts = await Storage.get(f"message_parts:{session_id}:msg_a")
    assert [part["id"] for part in stored_parts] == ["part_a"]


@pytest.mark.asyncio
async def test_writer_gives_up_rather_than_write_when_the_cache_keeps_going(monkeypatch) -> None:
    session_id = "ses_invalidate_never_stable"
    await Message.create(session_id, MessageRole.USER, "first", id="msg_first", part_id="part_first")
    original_ensure_cache = Message._ensure_cache.__func__

    async def ensure_then_invalidate(cls, sid: str) -> None:
        await original_ensure_cache(cls, sid)
        Message.invalidate_cache(sid)

    monkeypatch.setattr(Message, "_ensure_cache", classmethod(ensure_then_invalidate))

    with pytest.raises(MessageCacheInvalidatedError):
        await Message.create(session_id, MessageRole.USER, "second", id="msg_second", part_id="part_second")

    monkeypatch.undo()
    stored = await Storage.get(f"message:{session_id}")
    assert [message["id"] for message in stored] == ["msg_first"]
    from flocks.session.message import _session_locks

    assert not _session_locks.in_use(session_id)


@pytest.mark.asyncio
async def test_lock_is_released_when_a_write_fails(monkeypatch) -> None:
    from flocks.session.message import _session_locks

    session_id = "ses_locked_cache_write_fails"
    await Message.create(session_id, MessageRole.USER, "first", id="msg_first", part_id="part_first")
    monkeypatch.setattr(Message, "_persist_indexed_state", AsyncMock(side_effect=RuntimeError("disk full")))

    with pytest.raises(RuntimeError):
        await Message.create(session_id, MessageRole.USER, "second", id="msg_second", part_id="part_second")

    assert not _session_locks.in_use(session_id)


@pytest.mark.asyncio
async def test_lru_goes_over_the_limit_only_while_sessions_are_in_use(monkeypatch) -> None:
    from flocks.session.message import _session_locks

    monkeypatch.setattr(Message, "_MAX_CACHED_SESSIONS", 3)
    Message.invalidate_cache()
    busy = [f"ses_lru_busy_{index}" for index in range(3)]
    locks = [_session_locks.get(session_id) for session_id in busy]
    for session_id in busy:
        await Message.list(session_id)
    for lock in locks:
        await lock.acquire()

    await Message.list("ses_lru_fourth")
    assert set(busy) <= set(Message._lru)
    assert len(Message._lru) == 4

    for lock in locks:
        lock.release()
    await Message.list("ses_lru_fifth")

    # Oldest first once nothing is in use, back within the limit.
    assert list(Message._lru) == [busy[2], "ses_lru_fourth", "ses_lru_fifth"]
    assert busy[0] not in Message._messages_cache


@pytest.mark.asyncio
async def test_lock_in_use_is_not_forgotten_at_the_cap() -> None:
    from flocks.session.message import _SessionLockManager

    manager = _SessionLockManager()
    manager._MAX_LOCKS = 2
    held = manager.get("held")
    idle = manager.get("idle")
    await held.acquire()

    manager.get("new")
    assert manager.get("held") is held
    assert manager.get("idle") is not idle

    # Every lock in use: the cap gives way rather than hand out a second lock.
    other = manager.get("other")
    await other.acquire()
    manager.get("third")
    assert manager.get("held") is held
    assert manager.get("other") is other

    held.release()
    other.release()


@pytest.mark.asyncio
async def test_discard_keeps_a_lock_in_use() -> None:
    from flocks.session.message import _SessionLockManager

    manager = _SessionLockManager()
    lock = manager.get("ses")
    await lock.acquire()

    manager.discard("ses")
    assert manager.get("ses") is lock

    lock.release()
    manager.discard("ses")
    assert manager.get("ses") is not lock


@pytest.mark.asyncio
async def test_session_lock_in_use_counts_holder_and_waiters() -> None:
    from flocks.session.message import _SessionLock

    lock = _SessionLock()
    assert not lock.in_use

    await lock.acquire()
    waiting = asyncio.create_task(lock.acquire())
    cancelled = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0)

    cancelled.cancel()
    await asyncio.gather(cancelled, return_exceptions=True)
    lock.release()
    # The waiter still counts between the release and its turn on the loop.
    assert lock.in_use

    await waiting
    assert lock.locked() and lock.in_use
    lock.release()
    assert not lock.in_use

    async with lock:
        assert lock.in_use
    assert not lock.in_use
