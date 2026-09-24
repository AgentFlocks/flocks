"""Message search indexing uses PostgreSQL RETURNING and application-generated IDs."""

from __future__ import annotations


import pytest

from flocks.storage import session_search






@pytest.mark.asyncio
async def test_a_message_is_indexed_with_postgres_returning(tmp_path, monkeypatch):
    """The whole point: the insert path works when RETURNING is unavailable."""
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    from flocks.storage.storage import Storage

    db_path = tmp_path / "flocks.db"
    await Storage.init(db_path)
    try:
        message = {
            "id": "msg_1",
            "sessionID": "ses_1",
            "role": "user",
            "time": {"created": 1, "updated": 1},
        }
        parts = [{"type": "text", "text": "现在当前运行环境是"}]

        async with Storage.transaction(db_path) as real:
            db = real
            assert db.dialect.supports_returning is True

            written = await session_search.upsert_session_document(
                db, project_id="proj_1", message=message, parts=parts
            )
            assert written is True

            row = await db.fetch_one(
                "SELECT id, message_id FROM session_transcript_index_state "
                "WHERE message_id = ?",
                ("msg_1",),
            )
            assert row is not None
            rowid = row[0]
            # A real id, not the 0/None a missing lastrowid would leave behind.
            assert isinstance(rowid, int) and rowid > 0

            # And the text actually reached the search index under that id.
            from flocks.db.search import search_index_for

            index = search_index_for(db.dialect.name)
            assert await index.read_text(db, rowid) == "现在当前运行环境是"
    finally:
        await Storage.shutdown()


@pytest.mark.asyncio
async def test_postgres_message_update_keeps_one_search_document(
    tmp_path, monkeypatch
):
    """The second write takes the UPDATE branch; it must not regress either."""
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    from flocks.storage.storage import Storage

    db_path = tmp_path / "flocks.db"
    await Storage.init(db_path)
    try:
        message = {
            "id": "msg_2",
            "sessionID": "ses_1",
            "role": "assistant",
            "time": {"created": 1, "updated": 1},
        }

        async with Storage.transaction(db_path) as real:
            db = real
            await session_search.upsert_session_document(
                db, project_id="p", message=message, parts=[{"type": "text", "text": "第一版"}]
            )
            message["time"]["updated"] = 2
            await session_search.upsert_session_document(
                db, project_id="p", message=message, parts=[{"type": "text", "text": "第二版"}]
            )

            rows = await db.fetch_all(
                "SELECT id FROM session_transcript_index_state WHERE message_id = ?",
                ("msg_2",),
            )
            assert len(rows) == 1, "an update must not insert a second row"

            from flocks.db.search import search_index_for

            index = search_index_for(db.dialect.name)
            assert await index.read_text(db, rows[0][0]) == "第二版"
    finally:
        await Storage.shutdown()
