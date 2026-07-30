"""Derived FTS5 index for persisted session transcripts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Optional, Sequence

import aiosqlite

from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="storage.session_search")

_SESSION_BACKFILL_KEY = "history-v1"
_reconcile_locks: dict[str, asyncio.Lock] = {}

_SESSION_SEARCH_UNAVAILABLE_MESSAGE = (
    "Session search is unavailable because this SQLite runtime does not "
    "support FTS5. Session messages will continue to be stored normally."
)


class SessionSearchUnavailableError(RuntimeError):
    """Raised when the active SQLite runtime cannot provide Session FTS."""


def _is_fts5_unavailable_error(error: BaseException) -> bool:
    """Return whether an SQLite failure specifically means FTS5 is absent."""
    current: Optional[BaseException] = error
    while current is not None:
        if isinstance(current, sqlite3.OperationalError):
            message = str(current).casefold()
            if "fts5" in message and (
                "no such module" in message or "unknown module" in message
            ):
                return True
        current = current.__cause__ or current.__context__
    return False


def require_session_search_available() -> None:
    """Raise a stable, user-facing error when Session FTS is disabled."""
    if not Storage.session_search_available():
        raise SessionSearchUnavailableError(_SESSION_SEARCH_UNAVAILABLE_MESSAGE)


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


SESSION_SEARCH_META_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_transcript_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
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


async def ensure_session_search_tables(db_path: Path) -> bool:
    """Create Session search tables if the SQLite runtime supports FTS5.

    Returns:
        ``True`` when Session FTS is available and the schema is ready.
        ``False`` only when SQLite explicitly reports that the FTS5 module is
        unavailable. All other database errors are propagated.
    """
    async with Storage.connect(db_path) as db:
        await db.executescript(SESSION_SEARCH_META_SCHEMA_SQL)
        try:
            await db.execute(
                """
                CREATE VIRTUAL TABLE temp._flocks_session_fts5_probe
                USING fts5(text)
                """
            )
            await db.execute(
                "DROP TABLE temp._flocks_session_fts5_probe"
            )
        except sqlite3.OperationalError as exc:
            if _is_fts5_unavailable_error(exc):
                # Messages may be created, updated, or deleted while Session
                # indexing is disabled. Force a complete reconciliation if a
                # future runtime restores FTS5 support.
                await db.execute(
                    "DELETE FROM session_transcript_meta WHERE key = ?",
                    (_SESSION_BACKFILL_KEY,),
                )
                await db.commit()
                return False
            raise

        cursor = await db.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE name IN (
                'session_transcript_index_state',
                'session_transcript_fts'
            )
            """
        )
        existing_tables = {row[0] for row in await cursor.fetchall()}
        await db.executescript(SESSION_SEARCH_SCHEMA_SQL)
        if existing_tables != {
            "session_transcript_index_state",
            "session_transcript_fts",
        }:
            await db.execute(
                "DELETE FROM session_transcript_meta WHERE key = ?",
                (_SESSION_BACKFILL_KEY,),
            )
        await db.commit()
    return True


def _reconcile_lock(db_path: Path) -> asyncio.Lock:
    """Return the process-local reconcile owner for one SQLite database."""
    key = str(db_path.resolve())
    lock = _reconcile_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _reconcile_locks[key] = lock
    return lock


async def _session_index_is_ready(db_path: Path) -> bool:
    async with Storage.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM session_transcript_meta WHERE key = ?",
            (_SESSION_BACKFILL_KEY,),
        )
        return await cursor.fetchone() is not None


async def _mark_session_index_ready(db_path: Path) -> None:
    now = int(datetime.now(UTC).timestamp() * 1000)
    async with Storage.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO session_transcript_meta (key, value, updated_at)
            VALUES (?, 'complete', ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (_SESSION_BACKFILL_KEY, now),
        )
        await db.commit()


async def ensure_session_index_ready(*, batch_size: int = 50) -> bool:
    """Backfill legacy transcripts once, then use realtime message indexing.

    Returns ``True`` when this call performed the historical backfill and
    ``False`` when a previous successful pass already made the index ready.
    """
    require_session_search_available()
    db_path = Storage.get_db_path()
    if await _session_index_is_ready(db_path):
        return False

    async with _reconcile_lock(db_path):
        if await _session_index_is_ready(db_path):
            return False
        stats = await _reconcile_session_index_unlocked(batch_size=batch_size)
        await _mark_session_index_ready(db_path)
        log.info("session_search.backfill.complete", stats)
        return True


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
        cursor = await db.execute(
            "SELECT text FROM session_transcript_fts WHERE rowid = ?",
            (existing[0],),
        )
        indexed = await cursor.fetchone()
        if indexed is not None and indexed[0] == document["text"]:
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
    project_id: Optional[str] = None,
    batch_size: int = 50,
) -> dict[str, int]:
    """Rebuild missing/stale rows and remove orphaned derived rows."""
    require_session_search_available()
    db_path = Storage.get_db_path()
    async with _reconcile_lock(db_path):
        stats = await _reconcile_session_index_unlocked(
            project_id=project_id,
            batch_size=batch_size,
        )
        if project_id is None:
            await _mark_session_index_ready(db_path)
        return stats


async def _reconcile_session_index_unlocked(
    *,
    project_id: Optional[str] = None,
    batch_size: int = 50,
) -> dict[str, int]:
    """Repair Session FTS while bounding loaded TextParts to one batch."""
    from flocks.session.message import Message
    from flocks.session.session import Session

    sessions = [
        session
        for session in await Session.list_all_unfiltered()
        if session.status != "deleted"
        and (project_id is None or session.project_id == project_id)
    ]
    stats = {"scanned": 0, "updated": 0, "deleted": 0}
    effective_batch_size = max(1, batch_size)

    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            """
            CREATE TEMP TABLE session_transcript_reconcile_seen (
                message_id TEXT PRIMARY KEY
            )
            """
        )
        if project_id is None:
            await db.execute(
                """
                CREATE TEMP TABLE session_transcript_reconcile_candidates AS
                SELECT id, message_id
                FROM session_transcript_index_state
                """
            )
        else:
            await db.execute(
                """
                CREATE TEMP TABLE session_transcript_reconcile_candidates AS
                SELECT id, message_id
                FROM session_transcript_index_state
                WHERE project_id = ?
                """,
                (project_id,),
            )
        await db.execute(
            """
            CREATE TEMP TABLE session_transcript_reconcile_fts_candidates AS
            SELECT rowid
            FROM session_transcript_fts
            """
        )

        for session in sessions:
            # Session deletion owns this same lifecycle lock. Holding it while
            # rebuilding prevents a deleted transcript from being reinserted
            # after its transactional FTS cleanup.
            async with Session.lifecycle_lock(session.id):
                current = await Session.get_by_id_unfiltered(session.id)
                if (
                    current is None
                    or current.status == "deleted"
                    or Session.is_lifecycle_transitioning(session.id)
                ):
                    continue

                messages = await Message.list(
                    session.id,
                    include_archived=True,
                )
                for offset in range(0, len(messages), effective_batch_size):
                    batch_messages = messages[
                        offset : offset + effective_batch_size
                    ]
                    batch = []
                    for message in batch_messages:
                        item = await Message.get_with_parts_lazy(
                            session.id,
                            message.id,
                        )
                        if item is not None:
                            batch.append(item)

                    stats["scanned"] += len(batch)
                    try:
                        await db.execute("BEGIN IMMEDIATE")
                        for item in batch:
                            document = build_session_document(
                                item.info,
                                item.parts,
                            )
                            if document is not None:
                                await db.execute(
                                    """
                                    INSERT OR IGNORE INTO
                                        session_transcript_reconcile_seen (
                                            message_id
                                        )
                                    VALUES (?)
                                    """,
                                    (document["message_id"],),
                                )
                            if await upsert_session_document(
                                db,
                                project_id=session.project_id,
                                message=item.info,
                                parts=item.parts,
                            ):
                                stats["updated"] += 1
                        await db.commit()
                    except BaseException:
                        await db.rollback()
                        raise

        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                SELECT count(*)
                FROM session_transcript_reconcile_candidates AS candidate
                LEFT JOIN session_transcript_reconcile_seen AS seen
                    ON seen.message_id = candidate.message_id
                WHERE seen.message_id IS NULL
                """
            )
            stale_count = int((await cursor.fetchone())[0])
            if stale_count:
                await db.execute(
                    """
                    DELETE FROM session_transcript_fts
                    WHERE rowid IN (
                        SELECT candidate.id
                        FROM session_transcript_reconcile_candidates AS candidate
                        LEFT JOIN session_transcript_reconcile_seen AS seen
                            ON seen.message_id = candidate.message_id
                        WHERE seen.message_id IS NULL
                    )
                    """
                )
                await db.execute(
                    """
                    DELETE FROM session_transcript_index_state
                    WHERE id IN (
                        SELECT candidate.id
                        FROM session_transcript_reconcile_candidates AS candidate
                        LEFT JOIN session_transcript_reconcile_seen AS seen
                            ON seen.message_id = candidate.message_id
                        WHERE seen.message_id IS NULL
                    )
                    """
                )
                stats["deleted"] += stale_count

            cursor = await db.execute(
                """
                DELETE FROM session_transcript_fts
                WHERE rowid IN (
                    SELECT candidate.rowid
                    FROM session_transcript_reconcile_fts_candidates AS candidate
                    WHERE candidate.rowid NOT IN (
                        SELECT id FROM session_transcript_index_state
                    )
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
        {"project_id": project_id or "*", **stats},
    )
    return stats


async def session_fts_search(
    *,
    db_path: Path,
    project_id: str,
    query: str,
    max_results: int,
) -> list[dict[str, Any]]:
    """Search all indexed session messages using FTS5 BM25 ranking."""
    from flocks.storage.vector import build_fts_query

    require_session_search_available()
    del project_id  # Retained for API compatibility; Session search is global.
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
            ORDER BY bm25(session_transcript_fts)
            LIMIT ?
            """,
            (fts_query, max_results),
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
