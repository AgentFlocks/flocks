"""Real PostgreSQL pagination, migration and document/index atomicity."""
import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from flocks.db import registry
from flocks.session import message_pages
from flocks.storage.storage import Storage

pytestmark = pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="requires isolated PostgreSQL")


@pytest.fixture
async def database(monkeypatch):
    import asyncpg
    dsn = os.environ["FLOCKS_TEST_PG_DSN"]
    schema = "message_pages_" + uuid.uuid4().hex
    connection = await asyncpg.connect(dsn)
    await connection.execute(f'CREATE SCHEMA "{schema}"')
    await connection.close()
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query))
    query["search_path"] = schema + ",public"
    isolated = urlunsplit(parts._replace(query=urlencode(query)))
    monkeypatch.setattr(registry, "_settings", registry.DatabaseSettings("postgres", isolated))
    await Storage.init()
    return schema


def info(index, *, compacted=False):
    return {"id": f"msg_{index:04d}", "sessionID": "session", "role": "user", "time": {"created": index // 3}, "compacted": compacted}


async def seed(count):
    # A migrated session: only canonical documents exist, no projection.
    await Storage.set("message:session", [info(i, compacted=i % 7 == 0) for i in range(count)])
    await Storage.set("message_parts:session", {f"msg_{i:04d}": [{"type": "text", "text": str(i)}] for i in range(count)})


async def page(**kwargs):
    async with Storage.transaction() as db:
        return await message_pages.read_page(db, "session", **kwargs)


@pytest.mark.asyncio
async def test_latest_and_bidirectional_pages_have_no_gaps_with_tied_timestamps(database):
    await seed(123)
    items, older, before, newer, after = await page(limit=50)
    assert [row["info"]["id"] for row in items] == [f"msg_{i:04d}" for i in range(73, 123)]
    assert older and before == "msg_0073" and not newer and after is None
    previous = await page(limit=50, before=before)
    assert len(previous[0]) == 50 and previous[0][0]["parts"][0]["text"] == "23"
    first = await page(limit=50, before=previous[2])
    assert len(first[0]) == 23 and first[1] is False
    forward = await page(limit=50, after=first[4])
    assert [row["info"]["id"] for row in forward[0]] == [row["info"]["id"] for row in previous[0]]


@pytest.mark.asyncio
async def test_focus_archived_and_invalid_cursors(database):
    await seed(123)
    focused = await page(limit=20, around="msg_0050")
    assert "msg_0050" in [row["info"]["id"] for row in focused[0]]
    assert focused[1] and focused[3] and len(focused[0]) == 20
    visible = await page(limit=100, include_archived=False)
    assert not any(row["info"]["compacted"] for row in visible[0])
    with pytest.raises(LookupError):
        await page(before="other-session-message")
    with pytest.raises(ValueError):
        await page(before="msg_0001", after="msg_0002")


@pytest.mark.asyncio
async def test_backfill_once_parts_precedence_and_atomic_metadata_update(database):
    await seed(2)
    assert len((await page())[0]) == 2
    await Storage.set("message_parts:session:msg_0001", [{"type": "text", "text": "new-format"}])
    assert (await page())[0][-1]["parts"][0]["text"] == "new-format"
    changed = info(2)
    async def update(db):
        await message_pages.upsert(db, "session", changed)
    await Storage.mutate_many(set_entries=[("message:session", [info(0), info(1), changed], "json")], transaction_hook=update)
    assert len((await page())[0]) == 3
    async def failure(db):
        await message_pages.remove(db, "session", "msg_0002")
        raise RuntimeError("rollback")
    with pytest.raises(RuntimeError):
        await Storage.mutate_many(set_entries=[("message:session", [], "json")], transaction_hook=failure)
    assert len((await page())[0]) == 3
    assert len(await Storage.get("message:session")) == 3
    async with Storage.transaction() as db:
        await message_pages.replace_metadata(db, "session", [])
    # A ready empty directory is not rebuilt from a stale legacy blob.
    assert (await page())[0] == []


@pytest.mark.asyncio
async def test_message_api_reads_only_requested_models_and_does_not_fill_full_cache(database):
    from flocks.session.message import Message
    await seed(123)
    Message.invalidate_cache("session")
    messages, *_ = await Message.list_page_with_parts("session", limit=10)
    assert len(messages) == 10
    assert "session" not in Message._messages_cache
    assert messages[-1].info.id == "msg_0122"


@pytest.mark.asyncio
@pytest.mark.parametrize("state_name", ["pending", "running"])
async def test_live_parts_are_visible_before_flush_and_pg_is_authoritative_afterward(
    database, monkeypatch, tmp_path, state_name,
):
    from flocks.session.message import Message, ToolPart, ToolStatePending, ToolStateRunning

    monkeypatch.setenv("FLOCKS_RECORD_DIR", str(tmp_path / "records"))
    monkeypatch.setattr(Message, "_PARTS_PERSIST_DEBOUNCE_MS", 60_000)
    await seed(3)
    state = (
        ToolStatePending(input={"name": "live-skill"}, raw="{}")
        if state_name == "pending"
        else ToolStateRunning(input={"name": "live-skill"}, time={"start": 1})
    )
    part = ToolPart(
        id="prt_live", sessionID="session", messageID="msg_0002",
        callID="call_live", tool="skill_load", state=state,
    )
    try:
        await Message.store_part("session", "msg_0002", part)
        # The PG document still predates the delayed write.
        disk_parts = (await page(limit=1))[0][0]["parts"]
        assert not any(item.get("id") == part.id for item in disk_parts)
        messages, *_ = await Message.list_page_with_parts("session", limit=1)
        assert [item.id for item in messages[0].parts if item.type == "tool"] == [part.id]
        assert messages[0].parts[-1].state.status == state_name

        await Message.quiesce_parts("session", persist=True)
        assert "session" not in Message._parts_flush_tasks
        # Simulate another worker writing a newer value without invalidating
        # this process's now-clean cache. A page must use the committed row.
        stored = await Storage.get("message_parts:session")
        stored["msg_0002"][-1]["state"]["input"] = {"name": "remote-skill"}
        await Storage.mutate_many(
            set_entries=[("message_parts:session", stored, "message_parts")],
            invalidate_caches=False,
        )
        assert Message._parts_cache["session"]["msg_0002"][-1].state.input["name"] == "live-skill"
        messages, *_ = await Message.list_page_with_parts("session", limit=1)
        assert messages[0].parts[-1].state.input["name"] == "remote-skill"
        Message.invalidate_cache("session")
        cold, *_ = await Message.list_page_with_parts("session", limit=1)
        assert cold[0].parts[-1].state.input["name"] == "remote-skill"
        assert "session" not in Message._messages_cache
    finally:
        await Message.quiesce_parts("session", persist=False)
        Message.invalidate_cache("session")


@pytest.mark.asyncio
async def test_live_parts_lookup_is_limited_to_the_selected_page(database, monkeypatch, tmp_path):
    from flocks.session.message import Message, ToolPart, ToolStatePending

    monkeypatch.setenv("FLOCKS_RECORD_DIR", str(tmp_path / "records"))
    monkeypatch.setattr(Message, "_PARTS_PERSIST_DEBOUNCE_MS", 60_000)
    await seed(123)
    part = ToolPart(
        id="prt_live", sessionID="session", messageID="msg_0122",
        callID="call_live", tool="skill_load",
        state=ToolStatePending(input={"name": "live-skill"}, raw="{}"),
    )
    class SelectedPageCache(dict):
        def __iter__(self):
            raise AssertionError("A page must not traverse all cached messages")

        def items(self):
            raise AssertionError("A page must not traverse all cached messages")

        def values(self):
            raise AssertionError("A page must not traverse all cached messages")

        def get(self, key, default=None):
            assert key in {"msg_0119", "msg_0120", "msg_0121"}
            return super().get(key, default)

    try:
        await Message.store_part("session", "msg_0122", part)
        with monkeypatch.context() as page_patch:
            page_patch.setitem(
                Message._parts_cache, "session", SelectedPageCache(Message._parts_cache["session"]),
            )
            messages, *_ = await Message.list_page_with_parts("session", limit=3, before="msg_0122")
        assert [item.info.id for item in messages] == ["msg_0119", "msg_0120", "msg_0121"]
        assert all(item.type != "tool" for message in messages for item in message.parts)
    finally:
        await Message.quiesce_parts("session", persist=False)
        Message.invalidate_cache("session")


@pytest.mark.asyncio
async def test_deleted_message_is_not_revived_from_live_parts_cache(database, monkeypatch, tmp_path):
    from flocks.session.message import Message, ToolPart, ToolStatePending

    monkeypatch.setenv("FLOCKS_RECORD_DIR", str(tmp_path / "records"))
    monkeypatch.setattr(Message, "_PARTS_PERSIST_DEBOUNCE_MS", 60_000)
    await seed(3)
    part = ToolPart(
        id="prt_deleted", sessionID="session", messageID="msg_0002",
        callID="call_deleted", tool="skill_load",
        state=ToolStatePending(input={"name": "deleted-skill"}, raw="{}"),
    )
    try:
        await Message.store_part("session", "msg_0002", part)
        async def remove_message(db):
            await message_pages.remove(db, "session", "msg_0002")

        await Storage.mutate_many(
            set_entries=[("message:session", [info(0), info(1)], "json")],
            transaction_hook=remove_message,
            invalidate_caches=False,
        )
        assert "msg_0002" in Message._parts_cache["session"]
        messages, *_ = await Message.list_page_with_parts("session", limit=10)
        assert [item.info.id for item in messages] == ["msg_0000", "msg_0001"]
        assert all(item.type != "tool" for message in messages for item in message.parts)
    finally:
        await Message.quiesce_parts("session", persist=False)
        Message.invalidate_cache("session")
