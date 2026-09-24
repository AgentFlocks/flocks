"""PostgreSQL message directory for bounded conversation reads.

The canonical documents remain intact. Existing sessions are indexed once,
under a database lock, and mutations update this directory in the same
transaction as their documents. Parts are joined only after limiting the page.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_message_pages (
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    compacted BOOLEAN NOT NULL DEFAULT FALSE,
    info JSONB NOT NULL,
    PRIMARY KEY (session_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_message_pages_cursor
    ON session_message_pages (session_id, created_at, message_id);
CREATE TABLE IF NOT EXISTS session_message_page_state (
    session_id TEXT PRIMARY KEY,
    ready BOOLEAN NOT NULL DEFAULT FALSE,
    revision BIGINT NOT NULL DEFAULT 0,
    deleted BOOLEAN NOT NULL DEFAULT FALSE
);
ALTER TABLE session_message_page_state ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE session_message_page_state ADD COLUMN IF NOT EXISTS deleted BOOLEAN NOT NULL DEFAULT FALSE;
"""


class SessionMessagesDeletedError(LookupError):
    """A delayed writer cannot recreate a permanently deleted session."""


async def ensure_schema(db: Any) -> None:
    await db.executescript(SCHEMA)


async def lock_session(db: Any, session_id: str) -> int:
    """Serialize a session's short document/index transactions across workers."""
    await ensure_session(db, session_id)
    row = await db.fetch_one(
        "SELECT revision, deleted FROM session_message_page_state WHERE session_id = ? FOR UPDATE",
        (session_id,),
    )
    if row[1]:
        raise SessionMessagesDeletedError(f"Session was deleted: {session_id}")
    return int(row[0])


async def revision(db: Any, session_id: str) -> int:
    row = await db.fetch_one(
        "SELECT revision FROM session_message_page_state WHERE session_id = ?", (session_id,),
    )
    return int(row[0]) if row else 0


async def bump_revision(db: Any, session_id: str) -> int:
    row = await db.fetch_one(
        "UPDATE session_message_page_state SET revision = revision + 1 "
        "WHERE session_id = ? RETURNING revision", (session_id,),
    )
    return int(row[0])


async def sync_document(db: Any, session_id: str) -> None:
    """Maintain the legacy export document from committed rows, never a cache.

    The compatibility JSON aggregate stays in PostgreSQL; only the changed
    message crosses the application connection. Caller holds lock_session.
    """
    await db.execute(
        """INSERT INTO runtime_state(key, value, value_type)
        SELECT ?, COALESCE(jsonb_agg(info ORDER BY created_at, message_id), '[]'::jsonb), 'json'
        FROM session_message_pages WHERE session_id = ?
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = clock_timestamp()""",
        (f"message:{session_id}", session_id),
    )


async def read_info(db: Any, session_id: str, message_id: str) -> dict | None:
    row = await db.fetch_one(
        "SELECT info FROM session_message_pages WHERE session_id = ? AND message_id = ?",
        (session_id, message_id),
    )
    return _decode(row[0]) if row else None


def with_part_ids(parts: list[dict], session_id: str, message_id: str) -> list[dict]:
    """Give exported legacy parts stable identities before concurrent merges."""
    return [dict(item, id=item.get("id") or "part_" + uuid.uuid5(
        uuid.NAMESPACE_URL, f"flocks:{session_id}:{message_id}:{index}").hex)
        for index, item in enumerate(parts) if isinstance(item, dict)]


async def merge_parts(
    db: Any, session_id: str, message_id: str, *, previous: list[dict],
    incoming: list[dict], legacy: bool, delete: bool = False,
) -> list[dict]:
    """Apply this writer's part changes to the latest row under lock_session."""
    key = f"message_parts:{session_id}"
    row = await db.fetch_one(
        "SELECT COALESCE(item.value, legacy.value -> ?) FROM (SELECT 1) seed "
        "LEFT JOIN runtime_state item ON item.key = ? "
        "LEFT JOIN runtime_state legacy ON legacy.key = ?",
        (message_id, f"{key}:{message_id}", key),
    )
    current = with_part_ids(_decode(row[0]) if row and row[0] is not None else [], session_id, message_id)
    old = {item['id']: item for item in with_part_ids(previous, session_id, message_id)}
    new = {item['id']: item for item in incoming}
    removed = old.keys() - new.keys()
    changes = {pid: item for pid, item in new.items() if old.get(pid) != item}
    merged = []
    seen = set()
    for item in current:
        pid = item.get('id')
        if pid in removed or delete:
            continue
        replacement = changes.get(pid, item)
        # An old stream delta must never reopen a tool another worker closed.
        if (item.get('type') == 'tool'
                and item.get('state', {}).get('status') in {'completed', 'error'}
                and replacement.get('state', {}).get('status') in {'pending', 'running'}):
            replacement = item
        merged.append(replacement)
        seen.add(pid)
    if not delete:
        merged.extend(item for pid, item in changes.items() if pid not in seen)
    if legacy:
        if delete:
            await db.execute("UPDATE runtime_state SET value = value - ?, updated_at = clock_timestamp() WHERE key = ?",
                             (message_id, key))
        else:
            await db.execute(
                """INSERT INTO runtime_state(key, value, value_type) VALUES (?, jsonb_build_object(?::text, ?::jsonb), 'message_parts')
                ON CONFLICT (key) DO UPDATE SET value = runtime_state.value || EXCLUDED.value,
                    updated_at = clock_timestamp()""",
                (key, message_id, json.dumps(merged, ensure_ascii=False)),
            )
    elif not delete:
        await db.execute(
            """INSERT INTO runtime_state(key, value, value_type) VALUES (?, ?::jsonb, 'message_part')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = clock_timestamp()""",
            (f"{key}:{message_id}", json.dumps(merged, ensure_ascii=False)),
        )
    if delete:
        await db.execute("DELETE FROM runtime_state WHERE key = ?", (f"{key}:{message_id}",))
    return merged


async def ensure_session(db: Any, session_id: str) -> None:
    row = await db.fetch_one(
        "SELECT ready FROM session_message_page_state WHERE session_id = ?", (session_id,)
    )
    if row is not None and row[0]:
        return
    await db.execute(
        "INSERT INTO session_message_page_state(session_id, ready) VALUES (?, FALSE) "
        "ON CONFLICT (session_id) DO NOTHING", (session_id,),
    )
    row = await db.fetch_one(
        "SELECT ready FROM session_message_page_state WHERE session_id = ? FOR UPDATE", (session_id,)
    )
    if row[0]:
        return
    # Executed only once per imported session; no full JSON array is transferred
    # to the application or parsed into Message objects during pagination.
    await db.execute(
        """INSERT INTO session_message_pages(session_id, message_id, created_at, compacted, info)
        SELECT ?, item->>'id',
               CASE WHEN (item->'time'->>'created') ~ '^[0-9]+$'
                    THEN (item->'time'->>'created')::bigint ELSE 0 END,
               COALESCE(item->>'compacted', 'false') = 'true', item
        FROM runtime_state,
             LATERAL jsonb_array_elements(
                 CASE WHEN jsonb_typeof(value) = 'array' THEN value ELSE '[]'::jsonb END
             ) AS item
        WHERE key = ? AND jsonb_typeof(item) = 'object' AND item->>'id' IS NOT NULL
        ON CONFLICT (session_id, message_id) DO NOTHING""",
        (session_id, f"message:{session_id}"),
    )
    await db.execute(
        "UPDATE session_message_page_state SET ready = TRUE WHERE session_id = ?", (session_id,)
    )


async def upsert(db: Any, session_id: str, message: Any) -> None:
    await ensure_session(db, session_id)
    info = message.model_dump() if hasattr(message, "model_dump") else message
    await db.execute(
        """INSERT INTO session_message_pages(session_id, message_id, created_at, compacted, info)
        VALUES (?, ?, ?, ?, ?::jsonb)
        ON CONFLICT (session_id, message_id) DO UPDATE SET
            created_at = EXCLUDED.created_at, compacted = EXCLUDED.compacted, info = EXCLUDED.info""",
        (session_id, info["id"], int(info.get("time", {}).get("created", 0)),
         bool(info.get("compacted")), json.dumps(info, ensure_ascii=False)),
    )


async def replace_metadata(db: Any, session_id: str, messages: list[dict]) -> None:
    await ensure_session(db, session_id)
    # This path serves explicit archive/clear operations, not streaming deltas.
    await db.execute(
        "DELETE FROM session_message_pages WHERE session_id = ? AND NOT (message_id = ANY(?::text[]))",
        (session_id, [message["id"] for message in messages]),
    )
    if messages:
        await db.execute_many(
            """INSERT INTO session_message_pages(session_id, message_id, created_at, compacted, info)
            VALUES (?, ?, ?, ?, ?::jsonb)
            ON CONFLICT (session_id, message_id) DO UPDATE SET
                created_at = EXCLUDED.created_at, compacted = EXCLUDED.compacted, info = EXCLUDED.info""",
            [(session_id, item["id"], int(item.get("time", {}).get("created", 0)),
              bool(item.get("compacted")), json.dumps(item, ensure_ascii=False)) for item in messages],
        )


async def remove(db: Any, session_id: str, message_id: str | None = None) -> None:
    await ensure_session(db, session_id)
    if message_id is None:
        await db.execute("DELETE FROM session_message_pages WHERE session_id = ?", (session_id,))
    else:
        await db.execute(
            "DELETE FROM session_message_pages WHERE session_id = ? AND message_id = ?",
            (session_id, message_id),
        )


async def remove_sessions(db: Any, session_ids: list[str]) -> None:
    """Remove derived rows and retain a fence against delayed peer writes."""
    if not session_ids:
        return
    await db.execute(
        "DELETE FROM session_message_pages WHERE session_id = ANY(?::text[])",
        (session_ids,),
    )
    await db.execute(
        "UPDATE session_message_page_state SET ready = TRUE, deleted = TRUE, revision = revision + 1 "
        "WHERE session_id = ANY(?::text[])",
        (session_ids,),
    )


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def read_page(
    db: Any, session_id: str, *, limit: int = 50, before: str | None = None,
    after: str | None = None, around: str | None = None, include_archived: bool = True,
) -> tuple[list[dict], bool, str | None, bool, str | None]:
    await ensure_session(db, session_id)
    limit = max(1, min(int(limit), 200))
    if sum(bool(cursor) for cursor in (before, after, around)) > 1:
        raise ValueError("Only one message cursor may be supplied")
    where = "session_id = ?" + ("" if include_archived else " AND compacted = FALSE")
    params: list[Any] = [session_id]
    cursor_id = before or after or around
    cursor = None
    if cursor_id:
        cursor = await db.fetch_one(
            "SELECT created_at, message_id FROM session_message_pages WHERE session_id = ? AND message_id = ?",
            (session_id, cursor_id),
        )
        if cursor is None:
            raise LookupError("Message cursor not found")
    if around:
        # One bounded query on each side of the target; deterministic tie break
        # allows messages sharing a millisecond to paginate without loss.
        left = await db.fetch_all(
            f"SELECT created_at, message_id FROM session_message_pages WHERE {where} "
            "AND (created_at, message_id) <= (?, ?) ORDER BY created_at DESC, message_id DESC LIMIT ?",
            (*params, *cursor, max(1, limit // 2)),
        )
        start = left[-1] if left else cursor
        where += " AND (created_at, message_id) >= (?, ?)"
        params.extend(start)
        order = "ASC"
    elif cursor:
        where += f" AND (created_at, message_id) {'<' if before else '>'} (?, ?)"
        params.extend(cursor)
        order = "DESC" if before else "ASC"
    else:
        order = "DESC"
    rows = await db.fetch_all_mappings(
        f"""WITH selected AS MATERIALIZED (
          SELECT session_id, message_id, created_at, info FROM session_message_pages
          WHERE {where} ORDER BY created_at {order}, message_id {order} LIMIT ?
        ) SELECT selected.info, selected.created_at, selected.message_id,
                 COALESCE(parts.value, legacy.value -> selected.message_id, '[]'::jsonb) AS parts
          FROM selected
          LEFT JOIN runtime_state parts ON parts.key = 'message_parts:' || selected.session_id || ':' || selected.message_id
          LEFT JOIN runtime_state legacy ON legacy.key = 'message_parts:' || selected.session_id
          ORDER BY selected.created_at, selected.message_id""",
        (*params, limit),
    )
    if not rows:
        return [], False, None, False, None
    first, last = rows[0], rows[-1]
    exists = await db.fetch_one(
        "SELECT EXISTS(SELECT 1 FROM session_message_pages WHERE " +
        ("session_id = ?" if include_archived else "session_id = ? AND compacted = FALSE") +
        " AND (created_at, message_id) < (?, ?)), " +
        "EXISTS(SELECT 1 FROM session_message_pages WHERE " +
        ("session_id = ?" if include_archived else "session_id = ? AND compacted = FALSE") +
        " AND (created_at, message_id) > (?, ?))",
        (session_id, first["created_at"], first["message_id"], session_id, last["created_at"], last["message_id"]),
    )
    return (
        [{"info": _decode(row["info"]), "parts": _decode(row["parts"])} for row in rows],
        bool(exists[0]), first["message_id"] if exists[0] else None,
        bool(exists[1]), last["message_id"] if exists[1] else None,
    )
