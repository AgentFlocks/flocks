"""Derived PostgreSQL full-text index for persisted session transcripts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from uuid import uuid4
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence


from flocks.db.params import Params, in_clause
from flocks.db.search import search_index_for
from flocks.storage.storage import Storage
from flocks.utils.log import Log

if TYPE_CHECKING:
    from flocks.db.store import Transaction
    from flocks.memory.types import MemoryTimeRange

log = Log.create(service="storage.session_search")

_SESSION_BACKFILL_KEY = "history-v1"
_reconcile_locks: dict[str, asyncio.Lock] = {}

_SESSION_SEARCH_UNAVAILABLE_MESSAGE = (
    "The PostgreSQL session search index is not initialized. "
    "Session messages will continue to be stored normally."
)


class SessionSearchUnavailableError(RuntimeError):
    """Raised when the PostgreSQL search index is not ready."""




def require_session_search_available() -> None:
    """Raise a stable, user-facing error when Session FTS is disabled."""
    if not Storage.session_search_available():
        raise SessionSearchUnavailableError(_SESSION_SEARCH_UNAVAILABLE_MESSAGE)


def _state_schema(dialect) -> list[str]:
    """The message-state table, typed by the active backend.

    The full-text table is not here: its shape is the one thing the backends
    genuinely do not share, so each search index brings its own (see
    ``flocks.db.search``). What both need is a row per indexed message with a
    stable integer id, because that id is what the index is keyed by.
    """
    text = dialect.text_type()
    num = dialect.integer_type()
    return [
        f"""
        CREATE TABLE IF NOT EXISTS session_transcript_index_state (
            id                BIGINT PRIMARY KEY,
            message_id        {text} NOT NULL UNIQUE,
            session_id        {text} NOT NULL,
            project_id        {text},
            role              {text} NOT NULL,
            created_at        {num} NOT NULL,
            source_updated_at {num} NOT NULL,
            content_hash      {text} NOT NULL,
            indexed_at        {num} NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_session_transcript_state_session
            ON session_transcript_index_state(session_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_session_transcript_state_project
            ON session_transcript_index_state(project_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_session_transcript_state_project_created
            ON session_transcript_index_state(project_id, created_at)
        """,
    ]


def _meta_schema(dialect) -> list[str]:
    text = dialect.text_type()
    return [
        f"""
        CREATE TABLE IF NOT EXISTS session_transcript_meta (
            key        {text} PRIMARY KEY,
            value      {text} NOT NULL,
            updated_at BIGINT NOT NULL
        )
        """
    ]


async def _table_exists(db, table: str) -> bool:
    """Whether *table* has any columns, which is how both backends say it exists.

    PRAGMA returns no rows for a table SQLite does not have, and
    information_schema returns none for one PostgreSQL does not have, so the
    dialect's column query answers this without a second per-backend branch.
    """
    d = db.dialect
    rows = await db.fetch_all(d.columns_query().format(table=table), d.columns_query_params(table))
    return bool(rows)


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








def _reconcile_lock(db_path: Path) -> asyncio.Lock:
    """Return the process-local reconcile owner; PostgreSQL also locks across workers."""
    key = str(db_path.resolve())
    lock = _reconcile_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _reconcile_locks[key] = lock
    return lock


async def _session_index_is_ready(db_path: Path) -> bool:
    async with Storage.transaction(db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM session_transcript_meta WHERE key = ?",
            (_SESSION_BACKFILL_KEY,),
        )
        return await cursor.fetchone() is not None


async def _mark_session_index_ready(db_path: Path) -> None:
    now = int(datetime.now(UTC).timestamp() * 1000)
    async with Storage.transaction(db_path) as db:
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
    transaction: "Transaction",
    *,
    project_id: str,
    message: Any,
    parts: Sequence[Any],
) -> bool:
    """Synchronize one message into the transcript search index."""
    mark = transaction.dialect.placeholder
    index = search_index_for(transaction.dialect.name)
    message_id = str(_value(message, "id"))
    document = build_session_document(message, parts)
    existing = await transaction.fetch_one(
        f"""
        SELECT id, project_id, role, created_at, source_updated_at, content_hash
        FROM session_transcript_index_state
        WHERE message_id = {mark(1)}
        """,
        (message_id,),
    )

    if document is None:
        if existing is None:
            return False
        if index is not None:
            await index.delete_rows(transaction, [existing[0]])
        await transaction.execute(
            f"DELETE FROM session_transcript_index_state WHERE id = {mark(1)}",
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
    if unchanged and index is not None:
        indexed = await index.read_text(transaction, existing[0])
        if indexed is not None and indexed == document["text"]:
            return False

    indexed_at = int(datetime.now(UTC).timestamp() * 1000)
    if existing is None:
        markers = ", ".join(mark(position) for position in range(1, 10))
        insert = f"""
            INSERT INTO session_transcript_index_state (
                id, message_id, session_id, project_id, role, created_at,
                source_updated_at, content_hash, indexed_at
            ) VALUES ({markers})
        """
        values = (
            uuid4().int >> 65,
            document["message_id"],
            document["session_id"],
            project_id,
            document["role"],
            document["created_at"],
            document["source_updated_at"],
            document["content_hash"],
            indexed_at,
        )
        inserted = await transaction.fetch_one(f"{insert} RETURNING id", values)
        rowid = inserted[0]
    else:
        rowid = existing[0]
        await transaction.execute(
            f"""
            UPDATE session_transcript_index_state
            SET session_id = {mark(1)}, project_id = {mark(2)}, role = {mark(3)},
                created_at = {mark(4)}, source_updated_at = {mark(5)},
                content_hash = {mark(6)}, indexed_at = {mark(7)}
            WHERE id = {mark(8)}
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

    if index is not None:
        # The index implementation decides how to replace a row's text; FTS5
        # needs delete-then-insert, PostgreSQL can upsert.
        await index.upsert_text(transaction, rowid, document["text"])
    return True


async def delete_session_documents(
    transaction: "Transaction",
    session_ids: Sequence[str],
) -> int:
    """Delete all derived transcript rows for the supplied sessions."""
    if not session_ids:
        return 0
    mark = transaction.dialect.placeholder
    markers = ", ".join(mark(index) for index in range(1, len(session_ids) + 1))
    rows = await transaction.fetch_all(
        f"SELECT id FROM session_transcript_index_state WHERE session_id IN ({markers})",
        tuple(session_ids),
    )
    rowids = [row[0] for row in rows]
    if rowids:
        index = search_index_for(transaction.dialect.name)
        if index is not None:
            await index.delete_rows(transaction, rowids)
    await transaction.execute(
        f"DELETE FROM session_transcript_index_state WHERE session_id IN ({markers})",
        tuple(session_ids),
    )
    # Counting the rows we selected is portable; cursor.rowcount is not
    # available through the transaction handle on every backend.
    return len(rowids)


async def delete_message_document(
    transaction: "Transaction",
    message_id: str,
) -> bool:
    """Delete one derived transcript row."""
    mark = transaction.dialect.placeholder
    row = await transaction.fetch_one(
        f"SELECT id FROM session_transcript_index_state WHERE message_id = {mark(1)}",
        (message_id,),
    )
    if row is None:
        return False
    index = search_index_for(transaction.dialect.name)
    if index is not None:
        await index.delete_rows(transaction, [row[0]])
    await transaction.execute(
        f"DELETE FROM session_transcript_index_state WHERE id = {mark(1)}",
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


async def _reconcile_batch(db, project_id: str, batch: Sequence[Any], run_id: str) -> int:
    """Repair a bounded page with batch reads/writes, never a query per message."""
    documents = {item.info.id: build_session_document(item.info, item.parts) for item in batch}
    existing = {
        row[0]: row[1:]
        for row in await db.fetch_all(
            "SELECT s.message_id,s.id,s.project_id,s.role,s.created_at,s.source_updated_at,s.content_hash,f.text "
            "FROM session_transcript_index_state s LEFT JOIN session_transcript_search f ON f.row_id=s.id "
            "WHERE s.message_id=ANY(?::text[])", (list(documents),),
        )
    }
    seen = [(run_id, key) for key, document in documents.items() if document is not None]
    await db.execute_many(
        "INSERT INTO session_reconcile_seen_v2(run_id,message_id) VALUES (?,?) ON CONFLICT DO NOTHING", seen,
    )
    removed = [existing[key][0] for key, document in documents.items() if document is None and key in existing]
    if removed:
        await db.execute("DELETE FROM session_transcript_search WHERE row_id=ANY(?::bigint[])", (removed,))
        await db.execute("DELETE FROM session_transcript_index_state WHERE id=ANY(?::bigint[])", (removed,))
    pending = []
    indexed_at = int(datetime.now(UTC).timestamp() * 1000)
    for key, document in documents.items():
        if document is None:
            continue
        fields = (project_id, document["role"], document["created_at"],
                  document["source_updated_at"], document["content_hash"], document["text"])
        if key in existing and existing[key][1:] == fields:
            continue
        pending.append((existing[key][0] if key in existing else uuid4().int >> 65,
                        key, document["session_id"], *fields[:5], indexed_at))
    if pending:
        await db.execute_many(
            "INSERT INTO session_transcript_index_state "
            "(id,message_id,session_id,project_id,role,created_at,source_updated_at,content_hash,indexed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET "
            "session_id=EXCLUDED.session_id,project_id=EXCLUDED.project_id,role=EXCLUDED.role,"
            "created_at=EXCLUDED.created_at,source_updated_at=EXCLUDED.source_updated_at,"
            "content_hash=EXCLUDED.content_hash,indexed_at=EXCLUDED.indexed_at", pending,
        )
        ids = await db.fetch_all(
            "SELECT id,message_id FROM session_transcript_index_state WHERE message_id=ANY(?::text[])",
            ([row[1] for row in pending],),
        )
        await db.execute_many(
            "INSERT INTO session_transcript_search(row_id,text) VALUES (?,?) "
            "ON CONFLICT(row_id) DO UPDATE SET text=EXCLUDED.text",
            [(row_id, documents[message_id]["text"]) for row_id, message_id in ids],
        )
    return len(removed) + len(pending)


async def _reconcile_session_index_unlocked(
    *, project_id: Optional[str] = None, batch_size: int = 50,
) -> dict[str, int]:
    """Repair derived search data using bounded pages and reusable PG scratch tables."""
    from flocks.session.message import Message
    from flocks.session.session import Session

    sessions = [session for session in await Session.list_all_unfiltered()
                if session.status != "deleted" and (project_id is None or session.project_id == project_id)]
    stats = {"scanned": 0, "updated": 0, "deleted": 0}
    effective_batch_size = max(1, min(batch_size, 200))
    run_id = uuid4().hex
    async with Storage.transaction(Storage.get_db_path()) as db:
        # Serialize rebuilds across workers. Scratch rows belong to this run;
        # pooled connections keep only three empty tables after completion.
        await db.execute("SELECT pg_advisory_xact_lock(hashtext('flocks.session-search.reconcile'))")
        await db.executescript("""
            CREATE TEMP TABLE IF NOT EXISTS session_reconcile_seen_v2 (
                run_id TEXT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(run_id,message_id));
            CREATE TEMP TABLE IF NOT EXISTS session_reconcile_candidates_v2 (
                run_id TEXT NOT NULL,id BIGINT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(run_id,id));
            CREATE TEMP TABLE IF NOT EXISTS session_reconcile_fts_v2 (
                run_id TEXT NOT NULL,indexed_id BIGINT NOT NULL,PRIMARY KEY(run_id,indexed_id));
        """)
        await db.execute(
            "INSERT INTO session_reconcile_candidates_v2 SELECT ?,id,message_id "
            "FROM session_transcript_index_state" + (" WHERE project_id=?" if project_id is not None else ""),
            (run_id, project_id) if project_id is not None else (run_id,),
        )
        await db.execute(
            "INSERT INTO session_reconcile_fts_v2 SELECT ?,row_id FROM session_transcript_search", (run_id,),
        )
        for session in sessions:
            async with Session.lifecycle_lock(session.id):
                current = await Session.get_by_id_unfiltered(session.id)
                if current is None or current.status == "deleted" or Session.is_lifecycle_transitioning(session.id):
                    continue
                before = None
                while True:
                    batch, has_more, before = await Message.list_recent_with_parts(
                        session.id, limit=effective_batch_size, before=before, include_archived=True,
                    )
                    stats["scanned"] += len(batch)
                    if batch:
                        stats["updated"] += await _reconcile_batch(db, session.project_id, batch, run_id)
                    if not has_more:
                        break
        stale = """SELECT c.id FROM session_reconcile_candidates_v2 c
                   WHERE c.run_id=? AND NOT EXISTS (
                     SELECT 1 FROM session_reconcile_seen_v2 s WHERE s.run_id=c.run_id AND s.message_id=c.message_id)"""
        await db.execute("DELETE FROM session_transcript_search WHERE row_id IN (" + stale + ")", (run_id,))
        removed = await db.execute("DELETE FROM session_transcript_index_state WHERE id IN (" + stale + ")", (run_id,))
        stats["deleted"] += max(removed.rowcount, 0)
        orphans = await db.execute(
            "DELETE FROM session_transcript_search WHERE row_id IN ("
            "SELECT c.indexed_id FROM session_reconcile_fts_v2 c WHERE c.run_id=? "
            "AND NOT EXISTS (SELECT 1 FROM session_transcript_index_state s WHERE s.id=c.indexed_id))", (run_id,),
        )
        stats["deleted"] += max(orphans.rowcount, 0)
        await db.execute("DELETE FROM session_reconcile_seen_v2 WHERE run_id=?", (run_id,))
        await db.execute("DELETE FROM session_reconcile_candidates_v2 WHERE run_id=?", (run_id,))
        await db.execute("DELETE FROM session_reconcile_fts_v2 WHERE run_id=?", (run_id,))
    log.info("session_search.reconciled", {"project_id": project_id or "*", **stats})
    return stats


async def session_fts_search(
    *,
    db_path: Path,
    project_id: str,
    query: str,
    max_results: int,
    readable_session_ids: Optional[set[str]] = None,
    time_range: Optional[MemoryTimeRange] = None,
) -> list[dict[str, Any]]:
    """Search readable Session messages in the current project."""
    require_session_search_available()
    if readable_session_ids is not None and not readable_session_ids:
        return []

    async with Storage.transaction(db_path) as db:
        index = search_index_for(db.dialect.name)
        if index is None:
            # No full-text support on this backend. Returning nothing is the
            # honest answer; the alternative is a query that always matches.
            return []

        # The query language differs per backend -- FTS5 syntax on SQLite,
        # ordinary text for plainto_tsquery -- so the index prepares it.
        fts_query = index.prepare_query(query)

        params = Params(db.dialect)
        # Built in the order the markers appear, because a backend that numbers
        # its parameters cannot tolerate them being assembled out of order.
        snippet_expression = index.snippet_expression(params, fts_query)
        sql = f"""
            SELECT
                s.message_id,
                s.session_id,
                s.role,
                s.created_at,
                {snippet_expression}
            FROM {index.table}
            JOIN session_transcript_index_state s
                ON s.id = {index.table}.{index.join_column}
            WHERE s.project_id = {params.add(project_id)}
        """
        if fts_query:
            sql += f" AND {index.match_clause(params, fts_query)}"
        if readable_session_ids is not None:
            ordered_ids = sorted(readable_session_ids)
            sql += f" AND {in_clause(params, 's.session_id', ordered_ids)}"
        if time_range is not None:
            if time_range.start_ms is not None:
                sql += f" AND s.created_at >= {params.add(time_range.start_ms)}"
            if time_range.end_ms is not None:
                sql += f" AND s.created_at < {params.add(time_range.end_ms)}"
        if fts_query:
            sql += f" ORDER BY {index.rank_clause(params, fts_query)}"
        else:
            sql += " ORDER BY s.created_at DESC"
        sql += f" LIMIT {params.add(max_results)}"

        rows = await db.fetch_all(sql, params.values)

    count = len(rows)
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        message_id, session_id, role, created_at, snippet = row
        score = (
            1.0
            if not fts_query or count == 1
            else 1.0 - (index / (2 * count))
        )
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


async def ensure_session_search_tables(db_path: Path) -> bool:
    """Initialize PostgreSQL transcript metadata and its full-text index."""
    async with Storage.transaction(db_path, ensure_init=False) as db:
        index = search_index_for("postgres")
        existing = await _table_exists(db, index.table)
        await db.executescript(";\n".join(_meta_schema(db.dialect) + _state_schema(db.dialect) + index.schema_statements()))
        await db.execute("ALTER TABLE session_transcript_meta ALTER COLUMN updated_at TYPE BIGINT")
        if not existing:
            await db.execute("DELETE FROM session_transcript_meta WHERE key = ?", (_SESSION_BACKFILL_KEY,))
    return True
