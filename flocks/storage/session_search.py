"""Derived FTS5 index for persisted session transcripts."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import aiosqlite

from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="storage.session_search")


SESSION_SEARCH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_transcript_index_state (
    id INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    project_id TEXT,
    role TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    source_updated_at INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    indexed_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_transcript_state_session
    ON session_transcript_index_state(session_id);

CREATE INDEX IF NOT EXISTS idx_session_transcript_state_project
    ON session_transcript_index_state(project_id);

CREATE VIRTUAL TABLE IF NOT EXISTS session_transcript_fts USING fts5(
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _message_timestamp(message: Any, name: str) -> int:
    time_value = _value(message, "time", {})
    raw = _value(time_value, name, 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def build_session_document(
    message: Any,
    parts: Iterable[Any],
) -> Optional[dict[str, Any]]:
    """Build a searchable user/assistant document from authoritative parts."""
    role_value = _value(message, "role", "")
    role = getattr(role_value, "value", role_value)
    if role not in {"user", "assistant"}:
        return None

    text_parts: list[str] = []
    for part in parts:
        if _value(part, "type") != "text":
            continue
        if bool(_value(part, "synthetic", False)) or bool(
            _value(part, "ignored", False)
        ):
            continue
        text = str(_value(part, "text", "") or "").strip()
        if text:
            text_parts.append(text)

    text = "\n".join(text_parts).strip()
    if not text:
        return None

    created_at = _message_timestamp(message, "created")
    updated_at = (
        _message_timestamp(message, "updated")
        or _message_timestamp(message, "completed")
        or created_at
    )
    return {
        "message_id": str(_value(message, "id")),
        "session_id": str(_value(message, "sessionID")),
        "role": role,
        "created_at": created_at,
        "source_updated_at": updated_at,
        "text": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


async def ensure_session_search_tables(db_path: Path) -> None:
    """Create the derived transcript search tables."""
    async with Storage.connect(db_path) as db:
        await db.executescript(SESSION_SEARCH_SCHEMA_SQL)
        await db.commit()


async def upsert_session_document(
    db: aiosqlite.Connection,
    *,
    project_id: str,
    message: Any,
    parts: Sequence[Any],
) -> bool:
    """Synchronize one message into the transcript FTS index."""
    message_id = str(_value(message, "id"))
    document = build_session_document(message, parts)
    cursor = await db.execute(
        """
        SELECT id, project_id, role, created_at, source_updated_at, content_hash
        FROM session_transcript_index_state
        WHERE message_id = ?
        """,
        (message_id,),
    )
    existing = await cursor.fetchone()

    if document is None:
        if existing is None:
            return False
        await db.execute(
            "DELETE FROM session_transcript_fts WHERE rowid = ?",
            (existing[0],),
        )
        await db.execute(
            "DELETE FROM session_transcript_index_state WHERE id = ?",
            (existing[0],),
        )
        return True

    unchanged = existing is not None and (
        existing[1],
        existing[2],
        existing[3],
        existing[4],
        existing[5],
    ) == (
        project_id,
        document["role"],
        document["created_at"],
        document["source_updated_at"],
        document["content_hash"],
    )
    if unchanged:
        return False

    indexed_at = int(datetime.now(UTC).timestamp() * 1000)
    if existing is None:
        cursor = await db.execute(
            """
            INSERT INTO session_transcript_index_state (
                message_id, session_id, project_id, role, created_at,
                source_updated_at, content_hash, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document["message_id"],
                document["session_id"],
                project_id,
                document["role"],
                document["created_at"],
                document["source_updated_at"],
                document["content_hash"],
                indexed_at,
            ),
        )
        rowid = cursor.lastrowid
    else:
        rowid = existing[0]
        await db.execute(
            """
            UPDATE session_transcript_index_state
            SET session_id = ?, project_id = ?, role = ?, created_at = ?,
                source_updated_at = ?, content_hash = ?, indexed_at = ?
            WHERE id = ?
            """,
            (
                document["session_id"],
                project_id,
                document["role"],
                document["created_at"],
                document["source_updated_at"],
                document["content_hash"],
                indexed_at,
                rowid,
            ),
        )
        await db.execute(
            "DELETE FROM session_transcript_fts WHERE rowid = ?",
            (rowid,),
        )

    await db.execute(
        "INSERT INTO session_transcript_fts(rowid, text) VALUES (?, ?)",
        (rowid, document["text"]),
    )
    return True


async def delete_session_documents(
    db: aiosqlite.Connection,
    session_ids: Sequence[str],
) -> int:
    """Delete all derived transcript rows for the supplied sessions."""
    if not session_ids:
        return 0
    placeholders = ",".join("?" for _ in session_ids)
    cursor = await db.execute(
        f"""
        SELECT id FROM session_transcript_index_state
        WHERE session_id IN ({placeholders})
        """,
        tuple(session_ids),
    )
    rowids = [row[0] for row in await cursor.fetchall()]
    if rowids:
        rowid_placeholders = ",".join("?" for _ in rowids)
        await db.execute(
            f"DELETE FROM session_transcript_fts WHERE rowid IN ({rowid_placeholders})",
            tuple(rowids),
        )
    cursor = await db.execute(
        f"""
        DELETE FROM session_transcript_index_state
        WHERE session_id IN ({placeholders})
        """,
        tuple(session_ids),
    )
    return max(cursor.rowcount, 0)


async def delete_message_document(
    db: aiosqlite.Connection,
    message_id: str,
) -> bool:
    """Delete one derived transcript row."""
    cursor = await db.execute(
        "SELECT id FROM session_transcript_index_state WHERE message_id = ?",
        (message_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False
    await db.execute(
        "DELETE FROM session_transcript_fts WHERE rowid = ?",
        (row[0],),
    )
    await db.execute(
        "DELETE FROM session_transcript_index_state WHERE id = ?",
        (row[0],),
    )
    return True


async def reconcile_session_index(
    *,
    project_id: str,
    batch_size: int = 50,
) -> dict[str, int]:
    """Rebuild missing/stale rows and remove orphaned derived rows."""
    from flocks.session.message import Message
    from flocks.session.session import Session

    sessions = [
        session
        for session in await Session.list_all_unfiltered()
        if session.project_id == project_id and session.status != "deleted"
    ]
    documents: list[tuple[Any, Sequence[Any]]] = []
    desired_ids: set[str] = set()
    for session in sessions:
        for item in await Message.list_with_parts(
            session.id,
            include_archived=True,
        ):
            document = build_session_document(item.info, item.parts)
            if document is not None:
                desired_ids.add(document["message_id"])
            documents.append((item.info, item.parts))

    stats = {"scanned": len(documents), "updated": 0, "deleted": 0}
    effective_batch_size = max(1, batch_size)
    for offset in range(0, len(documents), effective_batch_size):
        batch = documents[offset : offset + effective_batch_size]
        async with Storage.connect(Storage.get_db_path()) as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                for message, parts in batch:
                    if await upsert_session_document(
                        db,
                        project_id=project_id,
                        message=message,
                        parts=parts,
                    ):
                        stats["updated"] += 1
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async with Storage.connect(Storage.get_db_path()) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                SELECT id, message_id
                FROM session_transcript_index_state
                WHERE project_id = ?
                """,
                (project_id,),
            )
            stale_rowids = [
                rowid
                for rowid, message_id in await cursor.fetchall()
                if message_id not in desired_ids
            ]
            if stale_rowids:
                placeholders = ",".join("?" for _ in stale_rowids)
                await db.execute(
                    f"""
                    DELETE FROM session_transcript_fts
                    WHERE rowid IN ({placeholders})
                    """,
                    tuple(stale_rowids),
                )
                await db.execute(
                    f"""
                    DELETE FROM session_transcript_index_state
                    WHERE id IN ({placeholders})
                    """,
                    tuple(stale_rowids),
                )
                stats["deleted"] += len(stale_rowids)
            cursor = await db.execute(
                """
                DELETE FROM session_transcript_fts
                WHERE rowid NOT IN (
                    SELECT id FROM session_transcript_index_state
                )
                """
            )
            stats["deleted"] += max(cursor.rowcount, 0)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    log.info(
        "session_search.reconciled",
        {"project_id": project_id, **stats},
    )
    return stats


async def session_fts_search(
    *,
    db_path: Path,
    project_id: str,
    query: str,
    max_results: int,
) -> list[dict[str, Any]]:
    """Search indexed session messages using FTS5 BM25 ranking."""
    from flocks.storage.vector import build_fts_query

    fts_query = build_fts_query(query)
    if not fts_query:
        return []

    async with Storage.connect(db_path) as db:
        cursor = await db.execute(
            """
            SELECT
                s.message_id,
                s.session_id,
                s.role,
                s.created_at,
                snippet(session_transcript_fts, 0, '', '', ' … ', 24),
                bm25(session_transcript_fts)
            FROM session_transcript_fts
            JOIN session_transcript_index_state s
                ON s.id = session_transcript_fts.rowid
            WHERE session_transcript_fts MATCH ?
                AND s.project_id = ?
            ORDER BY bm25(session_transcript_fts)
            LIMIT ?
            """,
            (fts_query, project_id, max_results),
        )
        rows = await cursor.fetchall()

    count = len(rows)
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        message_id, session_id, role, created_at, snippet, _rank = row
        score = 1.0 if count == 1 else 1.0 - (index / (2 * count))
        results.append(
            {
                "path": f"sessions/{session_id}/messages/{message_id}",
                "source": "session",
                "start_line": 1,
                "end_line": 1,
                "text": snippet,
                "score": score,
                "citation": (
                    f"session:{session_id} message:{message_id} "
                    f"role:{role} created_at:{created_at}"
                ),
            }
        )
    return results
