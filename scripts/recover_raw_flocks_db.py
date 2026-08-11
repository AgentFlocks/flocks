"""Offline recovery for a damaged Flocks SQLite database.

The helper writes recovery artifacts only. It never deletes SQLite sidecars or
installs a recovered database over the live Flocks store.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import struct
import subprocess
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Sequence

from dotenv import load_dotenv

WAL_MAGIC = 0x377F0682
WAL_MAGIC_BIG_ENDIAN_CHECKSUM = 0x377F0683
WAL_MAGIC_VALUES = {WAL_MAGIC, WAL_MAGIC_BIG_ENDIAN_CHECKSUM}
WAL_VERSION = 3007000
COMMON_SQLITE_PAGE_SIZES = (4096, 8192, 2048, 1024, 512, 16384, 32768, 65536)

STORAGE_DDL = """
CREATE TABLE IF NOT EXISTS storage (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

USAGE_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS usage_records (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    credential_id TEXT,
    session_id TEXT,
    message_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    input_cost REAL NOT NULL DEFAULT 0,
    output_cost REAL NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    latency_ms INTEGER,
    source TEXT NOT NULL DEFAULT 'live',
    created_at TEXT NOT NULL,
    backfilled_at TEXT
);
"""

TASKS_DDL = """
CREATE TABLE IF NOT EXISTS task_schedulers (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    mode                TEXT NOT NULL DEFAULT 'once',
    status              TEXT NOT NULL DEFAULT 'active',
    priority            TEXT NOT NULL DEFAULT 'normal',
    source              TEXT,
    trigger             TEXT NOT NULL,
    execution_mode      TEXT NOT NULL DEFAULT 'agent',
    agent_name          TEXT NOT NULL DEFAULT 'rex',
    workflow_id         TEXT,
    skills              TEXT DEFAULT '[]',
    category            TEXT,
    context             TEXT DEFAULT '{}',
    workspace_directory TEXT,
    retry               TEXT,
    tags                TEXT DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL DEFAULT 'rex',
    dedup_key           TEXT
);

CREATE TABLE IF NOT EXISTS task_executions (
    id                       TEXT PRIMARY KEY,
    scheduler_id             TEXT NOT NULL,
    title                    TEXT NOT NULL,
    description              TEXT NOT NULL DEFAULT '',
    priority                 TEXT NOT NULL DEFAULT 'normal',
    source                   TEXT,
    trigger_type             TEXT NOT NULL DEFAULT 'run_once',
    status                   TEXT NOT NULL DEFAULT 'pending',
    delivery_status          TEXT NOT NULL DEFAULT 'unread',
    queued_at                TEXT,
    started_at               TEXT,
    completed_at             TEXT,
    duration_ms              INTEGER,
    session_id               TEXT,
    result_summary           TEXT,
    error                    TEXT,
    execution_input_snapshot TEXT NOT NULL DEFAULT '{}',
    workspace_directory      TEXT,
    retry                    TEXT,
    execution_mode           TEXT NOT NULL DEFAULT 'agent',
    agent_name               TEXT NOT NULL DEFAULT 'rex',
    workflow_id              TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    FOREIGN KEY (scheduler_id) REFERENCES task_schedulers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_execution_queue_refs (
    id           TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    FOREIGN KEY (execution_id) REFERENCES task_executions(id) ON DELETE CASCADE
);
"""

CHANNEL_BINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS channel_bindings (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT 'default',
    chat_id TEXT NOT NULL,
    chat_type TEXT NOT NULL DEFAULT 'direct',
    thread_id TEXT,
    session_id TEXT NOT NULL,
    agent_id TEXT,
    created_at REAL NOT NULL,
    last_message_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_bindings_unique
    ON channel_bindings(channel_id, account_id, chat_id, COALESCE(thread_id, ''));

CREATE INDEX IF NOT EXISTS idx_channel_bindings_session
    ON channel_bindings(session_id);
"""

USAGE_INDEX_STMTS = (
    "CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_records(provider_id, model_id)",
    "CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_records(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_records(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_usage_message ON usage_records(session_id, message_id)",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_unique_message "
        "ON usage_records(session_id, message_id) WHERE message_id IS NOT NULL"
    ),
)

TASK_INDEX_STMTS = (
    "CREATE INDEX IF NOT EXISTS idx_task_schedulers_status ON task_schedulers(status)",
    "CREATE INDEX IF NOT EXISTS idx_task_schedulers_priority ON task_schedulers(priority)",
    "CREATE INDEX IF NOT EXISTS idx_task_schedulers_dedup ON task_schedulers(dedup_key)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_scheduler ON task_executions(scheduler_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_status ON task_executions(status)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_delivery ON task_executions(delivery_status)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_priority ON task_executions(priority)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_queued ON task_executions(queued_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_started ON task_executions(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_completed ON task_executions(completed_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_queue_refs_status_created ON task_execution_queue_refs(status, created_at)",
)

SUPPORTED_COPY_TABLES = (
    "storage",
    "usage_records",
    "task_schedulers",
    "task_executions",
    "task_execution_queue_refs",
    "channel_bindings",
)

TASK_SCHEDULER_COLUMNS = (
    "id",
    "title",
    "description",
    "mode",
    "status",
    "priority",
    "source",
    "trigger",
    "execution_mode",
    "agent_name",
    "workflow_id",
    "skills",
    "category",
    "context",
    "workspace_directory",
    "retry",
    "tags",
    "created_at",
    "updated_at",
    "created_by",
    "dedup_key",
)

TASK_EXECUTION_COLUMNS = (
    "id",
    "scheduler_id",
    "title",
    "description",
    "priority",
    "source",
    "trigger_type",
    "status",
    "delivery_status",
    "queued_at",
    "started_at",
    "completed_at",
    "duration_ms",
    "session_id",
    "result_summary",
    "error",
    "execution_input_snapshot",
    "workspace_directory",
    "retry",
    "execution_mode",
    "agent_name",
    "workflow_id",
    "created_at",
    "updated_at",
)

USAGE_RECORD_COLUMNS = (
    "id",
    "provider_id",
    "model_id",
    "credential_id",
    "session_id",
    "message_id",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
    "input_cost",
    "output_cost",
    "total_cost",
    "currency",
    "latency_ms",
    "source",
    "created_at",
    "backfilled_at",
)

CHANNEL_BINDING_COLUMNS = (
    "id",
    "channel_id",
    "account_id",
    "chat_id",
    "chat_type",
    "thread_id",
    "session_id",
    "agent_id",
    "created_at",
    "last_message_at",
)

QUEUE_REF_COLUMNS = (
    "id",
    "execution_id",
    "status",
    "created_at",
    "started_at",
)


@dataclass(frozen=True)
class RecoveryArtifacts:
    """Artifacts and row counts for one recovery run."""

    recovery_dir: Path
    candidate_db: Path
    recover_sql: Path
    extracted_db: Path
    recovered_db: Path
    summary_path: Path
    lost_and_found_table: str
    pagesize: int
    wal_frames: int
    wal_final_db_pages: int
    copied_rows: Dict[str, int]


def _cleanup_temporary_sqlite_files(path: Path) -> None:
    """Remove only temporary files created by the current recovery operation."""

    for candidate in (
        path,
        path.with_name(f"{path.name}-journal"),
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    ):
        candidate.unlink(missing_ok=True)


def _publish_file_exclusive(temporary_path: Path, destination: Path) -> None:
    """Atomically publish a sibling temporary file without replacing a destination."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        try:
            os.rename(temporary_path, destination)
        except FileExistsError:
            raise FileExistsError(f"Refusing to overwrite existing recovery file: {destination}")
        return
    try:
        os.link(temporary_path, destination, follow_symlinks=False)
    except FileExistsError:
        raise FileExistsError(f"Refusing to overwrite existing recovery file: {destination}")
    temporary_path.unlink()


@contextmanager
def _atomic_output_path(destination: Path):
    """Yield a sibling temporary path and publish it only after successful completion."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
    try:
        yield temporary_path
        if not temporary_path.is_file():
            raise RuntimeError(f"Recovery output was not created: {temporary_path}")
        unexpected_sidecars = [
            candidate
            for candidate in (
                temporary_path.with_name(f"{temporary_path.name}-journal"),
                temporary_path.with_name(f"{temporary_path.name}-wal"),
                temporary_path.with_name(f"{temporary_path.name}-shm"),
            )
            if candidate.exists()
        ]
        if unexpected_sidecars:
            raise RuntimeError(
                "Recovery staging file still has SQLite sidecars: "
                + ", ".join(str(path) for path in unexpected_sidecars)
            )
        # Windows maps fsync() to _commit(), which requires a writable file
        # descriptor. ``r+b`` works on every supported platform and keeps the
        # durability step consistent before the no-replace publish.
        with temporary_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        _publish_file_exclusive(temporary_path, destination)
    finally:
        _cleanup_temporary_sqlite_files(temporary_path)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _wal_checksum(
    data: bytes,
    *,
    byte_order: str,
    state: tuple[int, int] = (0, 0),
) -> tuple[int, int]:
    """Return SQLite's rolling WAL checksum for an 8-byte-aligned payload."""

    if len(data) % 8 != 0:
        raise ValueError("WAL checksum input must be aligned to 8 bytes.")
    words = struct.unpack(f"{byte_order}{len(data) // 4}I", data)
    checksum_1, checksum_2 = state
    for index in range(0, len(words), 2):
        checksum_1 = (checksum_1 + words[index] + checksum_2) & 0xFFFFFFFF
        checksum_2 = (checksum_2 + words[index + 1] + checksum_1) & 0xFFFFFFFF
    return checksum_1, checksum_2


def _parse_wal_frames(wal_bytes: bytes) -> tuple[int, int, Dict[int, bytes], int]:
    """Return committed, checksum-valid WAL pages through the last transaction."""

    if len(wal_bytes) < 32:
        raise ValueError("WAL file is too small to contain a valid header.")

    magic, version, pagesize, _, salt_1, salt_2, stored_1, stored_2 = struct.unpack(
        ">8I", wal_bytes[:32]
    )
    if magic not in WAL_MAGIC_VALUES:
        raise ValueError(f"Unexpected WAL magic: 0x{magic:08x}")
    if version != WAL_VERSION:
        raise ValueError(f"Unexpected WAL version: {version}")
    if pagesize < 512 or pagesize > 65536 or pagesize & (pagesize - 1):
        raise ValueError(f"Invalid WAL pagesize: {pagesize}")

    byte_order = "<" if magic == WAL_MAGIC else ">"
    checksum = _wal_checksum(wal_bytes[:24], byte_order=byte_order)
    if checksum != (stored_1, stored_2):
        raise ValueError("WAL header checksum is invalid.")

    frame_size = 24 + pagesize
    payload = len(wal_bytes) - 32
    available_frames = payload // frame_size
    valid_frames: list[tuple[int, bytes]] = []
    last_commit_frame_count = 0
    final_db_pages = 0
    for frame_index in range(available_frames):
        offset = 32 + frame_index * frame_size
        frame_header = wal_bytes[offset : offset + 24]
        page = wal_bytes[offset + 24 : offset + 24 + pagesize]
        page_no, db_page_count, frame_salt_1, frame_salt_2, frame_sum_1, frame_sum_2 = (
            struct.unpack(">6I", frame_header)
        )
        if page_no == 0 or (frame_salt_1, frame_salt_2) != (salt_1, salt_2):
            break

        next_checksum = _wal_checksum(
            frame_header[:8],
            byte_order=byte_order,
            state=checksum,
        )
        next_checksum = _wal_checksum(
            page,
            byte_order=byte_order,
            state=next_checksum,
        )
        if next_checksum != (frame_sum_1, frame_sum_2):
            break

        checksum = next_checksum
        valid_frames.append((page_no, page))
        if db_page_count:
            final_db_pages = db_page_count
            last_commit_frame_count = len(valid_frames)

    if final_db_pages <= 0 or last_commit_frame_count == 0:
        raise ValueError("WAL does not contain any committed frames.")

    latest_pages: Dict[int, bytes] = {}
    for page_no, page in valid_frames[:last_commit_frame_count]:
        latest_pages[page_no] = page

    return pagesize, final_db_pages, latest_pages, last_commit_frame_count


def _read_header_pagesize(raw_bytes: bytes) -> int | None:
    """Return a valid SQLite page size from the database header, if present."""

    if len(raw_bytes) < 100 or not raw_bytes.startswith(b"SQLite format 3\x00"):
        return None
    encoded = int.from_bytes(raw_bytes[16:18], "big")
    pagesize = 65536 if encoded == 1 else encoded
    if pagesize < 512 or pagesize > 65536 or pagesize & (pagesize - 1):
        return None
    return pagesize


def _guess_raw_pagesize(raw_bytes: bytes) -> int:
    """Infer the most likely SQLite page size from a damaged raw file."""

    for pagesize in COMMON_SQLITE_PAGE_SIZES:
        if len(raw_bytes) < pagesize * 2 or len(raw_bytes) % pagesize != 0:
            continue
        second_page = raw_bytes[pagesize : pagesize + 1]
        if second_page and second_page[0] in {0x00, 0x02, 0x05, 0x0A, 0x0D}:
            return pagesize
    raise ValueError("Could not infer SQLite page size from the raw file.")


def _build_synthetic_page1(pagesize: int, total_pages: int) -> bytes:
    """Create a minimal SQLite page 1 so `.recover` can scan later pages."""

    page1 = bytearray(pagesize)
    page1[:16] = b"SQLite format 3\x00"
    page1[16:18] = pagesize.to_bytes(2, "big") if pagesize != 65536 else b"\x00\x01"
    page1[18] = 0x01
    page1[19] = 0x01
    page1[20] = 0x00
    page1[21] = 0x40
    page1[22] = 0x20
    page1[23] = 0x20
    page1[24:28] = (1).to_bytes(4, "big")
    page1[28:32] = total_pages.to_bytes(4, "big")
    page1[40:44] = (1).to_bytes(4, "big")
    page1[44:48] = (4).to_bytes(4, "big")
    page1[56:60] = (1).to_bytes(4, "big")
    page1[92:96] = (1).to_bytes(4, "big")
    # Page 1's b-tree header begins after the 100-byte database header.
    page1[100] = 0x0D
    page1[101:103] = (0).to_bytes(2, "big")
    page1[103:105] = (0).to_bytes(2, "big")
    cell_content_area = 0 if pagesize == 65536 else pagesize
    page1[105:107] = cell_content_area.to_bytes(2, "big")
    page1[107] = 0
    return bytes(page1)


def reconstruct_sqlite_candidate(
    raw_path: Path,
    wal_path: Path | None,
    output_path: Path,
) -> dict[str, int]:
    """Build a recoverable SQLite candidate from raw bytes and an optional WAL."""

    raw_bytes = raw_path.read_bytes()
    raw_has_header = raw_bytes.startswith(b"SQLite format 3\x00")

    if wal_path is not None:
        wal_bytes = wal_path.read_bytes()
        pagesize, final_db_pages, latest_pages, frame_count = _parse_wal_frames(wal_bytes)
        header_pagesize = _read_header_pagesize(raw_bytes)
        if header_pagesize is not None and header_pagesize != pagesize:
            raise ValueError(
                "WAL page size does not match the SQLite database header: "
                f"{pagesize} != {header_pagesize}."
            )
        if len(raw_bytes) % pagesize != 0:
            raise ValueError(
                f"Raw file size {len(raw_bytes)} is not aligned to WAL pagesize {pagesize}."
            )
    else:
        pagesize = _read_header_pagesize(raw_bytes) or _guess_raw_pagesize(raw_bytes)
        final_db_pages = len(raw_bytes) // pagesize
        latest_pages = {}
        frame_count = 0

    with _atomic_output_path(output_path) as temporary_path:
        with temporary_path.open("xb") as handle:
            if wal_path is None and not raw_has_header:
                handle.write(_build_synthetic_page1(pagesize, final_db_pages))
                start_page = 2
            else:
                start_page = 1

            if wal_path is None and raw_has_header:
                handle.write(raw_bytes)
                return {
                    "pagesize": pagesize,
                    "wal_frames": 0,
                    "wal_final_db_pages": final_db_pages,
                    "wal_pages_used": 0,
                }

            for page_no in range(1, final_db_pages + 1):
                if wal_path is None and page_no < start_page:
                    continue
                if page_no in latest_pages:
                    handle.write(latest_pages[page_no])
                    continue

                offset = (page_no - 1) * pagesize
                page = raw_bytes[offset : offset + pagesize]
                handle.write(page if len(page) == pagesize else (b"\x00" * pagesize))

    return {
        "pagesize": pagesize,
        "wal_frames": frame_count,
        "wal_final_db_pages": final_db_pages,
        "wal_pages_used": sum(1 for page_no in latest_pages if page_no <= final_db_pages),
    }


def _sqlite_recover_capability(sqlite_bin: str = "sqlite3") -> tuple[bool, str]:
    """Return whether *sqlite_bin* was built with support for ``.recover``."""

    try:
        completed = subprocess.run(
            [
                sqlite_bin,
                ":memory:",
                "SELECT sqlite_compileoption_used('ENABLE_DBPAGE_VTAB');",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        return False, str(exc)

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        return False, detail or f"capability probe exited with {completed.returncode}"
    if completed.stdout.strip() != "1":
        return False, "SQLite CLI was built without SQLITE_ENABLE_DBPAGE_VTAB"
    return True, ""


def _run_sqlite_recover(
    candidate_db: Path,
    recover_sql_path: Path,
    *,
    lost_and_found_table: str,
) -> None:
    """Write `sqlite3 .recover` output to a SQL file."""

    supported, reason = _sqlite_recover_capability()
    if not supported:
        raise RuntimeError(f"sqlite3 .recover is unavailable: {reason}")

    completed = subprocess.run(
        [
            "sqlite3",
            str(candidate_db),
            f".recover --lost-and-found {lost_and_found_table}",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"sqlite3 .recover failed: {stderr or completed.returncode}")
    with _atomic_output_path(recover_sql_path) as temporary_path:
        temporary_path.write_text(completed.stdout or "", encoding="utf-8")


def _materialize_recovered_sql(recover_sql_path: Path, extracted_db_path: Path) -> None:
    """Execute recovered SQL into a scratch SQLite database."""

    with _atomic_output_path(extracted_db_path) as temporary_path:
        completed = subprocess.run(
            ["sqlite3", str(temporary_path)],
            input=recover_sql_path.read_text(encoding="utf-8"),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Failed to materialize recovered SQL: {stderr}")


def _schema_manifest(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str | None]]:
    """Return logical schema objects, excluding SQLite-managed internal objects."""

    rows = conn.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return {
        (str(row[0]), str(row[1])): (str(row[2]), None if row[3] is None else str(row[3]))
        for row in rows
    }


def _validate_recovered_db(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
) -> None:
    """Fail closed if the published backup changed recovered business data."""

    source_manifest = _schema_manifest(source_conn)
    target_manifest = _schema_manifest(target_conn)
    if target_manifest != source_manifest:
        changed_keys = sorted(
            key
            for key in source_manifest.keys() | target_manifest.keys()
            if source_manifest.get(key) != target_manifest.get(key)
        )
        changed = changed_keys[0] if changed_keys else ("schema", "unknown")
        raise sqlite3.DatabaseError(
            f"Recovered DB schema object was not preserved: {changed[0]} {changed[1]}"
        )

    source_tables = {
        name for object_type, name in source_manifest if object_type == "table"
    }
    virtual_tables = {
        name
        for (object_type, name), (_, sql) in source_manifest.items()
        if object_type == "table"
        and sql is not None
        and sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")
    }
    missing_row = object()
    for table_name in sorted(source_tables - virtual_tables):
        quoted_table = _quote_identifier(table_name)
        table_sql = source_manifest[("table", table_name)][1] or ""
        column_names = {
            str(row[1]).casefold()
            for row in source_conn.execute(f"PRAGMA table_info({quoted_table})").fetchall()
        }
        rowid_alias = next(
            (
                alias
                for alias in ("rowid", "_rowid_", "oid")
                if alias.casefold() not in column_names
            ),
            None,
        )
        include_rowid = rowid_alias is not None and "WITHOUT ROWID" not in table_sql.upper()
        projection = f"{_quote_identifier(rowid_alias)}, *" if include_rowid else "*"
        try:
            source_rows = source_conn.execute(f"SELECT {projection} FROM {quoted_table}")
            target_rows = target_conn.execute(f"SELECT {projection} FROM {quoted_table}")
            row_number = 0
            while True:
                source_row = source_rows.fetchone()
                target_row = target_rows.fetchone()
                if source_row is None and target_row is None:
                    break
                row_number += 1
                source_value = missing_row if source_row is None else tuple(source_row)
                target_value = missing_row if target_row is None else tuple(target_row)
                if source_value != target_value:
                    raise sqlite3.DatabaseError(
                        f"Recovered DB row content changed for {table_name} at row {row_number}"
                    )
        except sqlite3.DatabaseError:
            raise
        except sqlite3.Error as exc:
            raise sqlite3.DatabaseError(
                f"Recovered DB table could not be compared: {table_name}: {exc}"
            ) from exc

    for pragma_name in ("application_id", "user_version"):
        source_value = source_conn.execute(f"PRAGMA {pragma_name}").fetchone()[0]
        target_value = target_conn.execute(f"PRAGMA {pragma_name}").fetchone()[0]
        if target_value != source_value:
            raise sqlite3.DatabaseError(
                f"Recovered DB PRAGMA {pragma_name} changed: {source_value} -> {target_value}"
            )

    integrity_rows = target_conn.execute("PRAGMA integrity_check").fetchall()
    if integrity_rows != [("ok",)]:
        detail = "; ".join(str(row[0]) for row in integrity_rows)
        raise sqlite3.DatabaseError(f"Recovered DB integrity check failed: {detail}")
    foreign_key_rows = target_conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_rows:
        raise sqlite3.IntegrityError(
            f"Recovered DB foreign key check failed: {foreign_key_rows[0]}"
        )


def build_normalized_recovery_db(
    extracted_db_path: Path,
    output_db_path: Path,
    *,
    lost_and_found_table: str = "lost_and_found",
) -> Dict[str, int]:
    """Publish an exact recovered backup and retain unresolved rows for inspection.

    ``sqlite3 .recover`` stores rows it cannot attribute confidently in its
    lost-and-found table. Guessing their destination from field count or value
    prefixes can silently inject another table's data into Flocks, so this
    function deliberately leaves those rows untouched.
    """

    copied_rows: Dict[str, int] = {}
    with _atomic_output_path(output_db_path) as temporary_path:
        source_conn = sqlite3.connect(extracted_db_path)
        target_conn = sqlite3.connect(temporary_path)
        try:
            source_conn.backup(target_conn)
        finally:
            source_conn.close()
            target_conn.close()

        source_conn = sqlite3.connect(extracted_db_path)
        target_conn = sqlite3.connect(temporary_path)
        try:
            journal_mode = target_conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if str(journal_mode).lower() != "delete":
                raise sqlite3.DatabaseError(
                    f"Could not switch recovered DB to DELETE journal mode: {journal_mode}"
                )
            for table_name in SUPPORTED_COPY_TABLES:
                copied_rows[table_name] = (
                    target_conn.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}"
                    ).fetchone()[0]
                    if _table_exists(target_conn, table_name)
                    else 0
                )
            _validate_recovered_db(source_conn, target_conn)
        finally:
            source_conn.close()
            target_conn.close()

    return copied_rows


def _render_summary(artifacts: RecoveryArtifacts) -> str:
    """Return a readable summary for the recovery run."""

    lines = [
        f"recovery_dir={artifacts.recovery_dir}",
        f"candidate_db={artifacts.candidate_db}",
        f"recover_sql={artifacts.recover_sql}",
        f"extracted_db={artifacts.extracted_db}",
        f"recovered_db={artifacts.recovered_db}",
        f"summary_path={artifacts.summary_path}",
        f"lost_and_found_table={artifacts.lost_and_found_table}",
        f"pagesize={artifacts.pagesize}",
        f"wal_frames={artifacts.wal_frames}",
        f"wal_final_db_pages={artifacts.wal_final_db_pages}",
    ]
    for table_name in SUPPORTED_COPY_TABLES:
        lines.append(f"copied_{table_name}={artifacts.copied_rows.get(table_name, 0)}")
    return "\n".join(lines) + "\n"


def _recovery_artifact_paths(
    recovery_dir: Path,
    prefix: str,
) -> tuple[Path, Path, Path, Path, Path]:
    """Return every file path written by one recovery run."""

    return (
        recovery_dir / f"{prefix}.candidate.db",
        recovery_dir / f"{prefix}.recover.sql",
        recovery_dir / f"{prefix}.extracted.db",
        recovery_dir / f"{prefix}.db",
        recovery_dir / f"{prefix}.summary.txt",
    )


def _recovery_sqlite_sidecar_paths(
    artifact_paths: tuple[Path, Path, Path, Path, Path],
) -> tuple[Path, ...]:
    """Return sidecar paths SQLite may create while processing DB artifacts."""

    database_paths = (artifact_paths[0], artifact_paths[2], artifact_paths[3])
    return tuple(
        database_path.with_name(f"{database_path.name}{suffix}")
        for database_path in database_paths
        for suffix in ("-journal", "-wal", "-shm")
    )


def recover_raw_storage_db(
    raw_path: Path,
    wal_path: Path | None,
    recovery_dir: Path,
    *,
    prefix: str,
) -> RecoveryArtifacts:
    """Recover a damaged raw SQLite file into a normalized database."""

    candidate_db, recover_sql, extracted_db, recovered_db, summary_path = _recovery_artifact_paths(
        recovery_dir,
        prefix,
    )
    _validate_recovery_plan(
        raw_path=raw_path,
        wal_path=wal_path,
        recovery_dir=recovery_dir,
        output_db=recovered_db,
        prefix=prefix,
    )
    recovery_dir.mkdir(parents=True, exist_ok=True)

    lost_and_found_table = f"flocks_recovery_lost_{uuid.uuid4().hex}"
    candidate_stats = reconstruct_sqlite_candidate(raw_path, wal_path, candidate_db)
    _run_sqlite_recover(
        candidate_db,
        recover_sql,
        lost_and_found_table=lost_and_found_table,
    )
    _materialize_recovered_sql(recover_sql, extracted_db)
    copied_rows = build_normalized_recovery_db(
        extracted_db,
        recovered_db,
        lost_and_found_table=lost_and_found_table,
    )

    artifacts = RecoveryArtifacts(
        recovery_dir=recovery_dir,
        candidate_db=candidate_db,
        recover_sql=recover_sql,
        extracted_db=extracted_db,
        recovered_db=recovered_db,
        summary_path=summary_path,
        lost_and_found_table=lost_and_found_table,
        pagesize=candidate_stats["pagesize"],
        wal_frames=candidate_stats["wal_frames"],
        wal_final_db_pages=candidate_stats["wal_final_db_pages"],
        copied_rows=copied_rows,
    )
    with _atomic_output_path(summary_path) as temporary_path:
        temporary_path.write_text(_render_summary(artifacts), encoding="utf-8")
    return artifacts


def _sanitize_name(value: str) -> str:
    """Return a filesystem-safe name while keeping it readable."""

    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
    return safe.strip("-") or "flocks-db-recovery"


def _resolve_raw_path(args: argparse.Namespace) -> Path:
    """Resolve the damaged DB path from new or legacy CLI flags."""

    raw_path = args.raw or args.damaged_db
    if raw_path is None:
        raise ValueError("A damaged DB path is required.")
    return raw_path.expanduser().resolve()


def _detect_wal_path(raw_path: Path) -> Path | None:
    """Try to find a matching WAL file next to the damaged DB."""

    candidates: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(name: str) -> None:
        candidate = raw_path.with_name(name)
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    add_candidate(f"{raw_path.name}-wal")
    add_candidate(f"{raw_path.name}.wal")

    marker = ".corrupt."
    if marker in raw_path.name:
        original_name, quarantine_suffix = raw_path.name.split(marker, 1)
        add_candidate(f"{original_name}-wal{marker}{quarantine_suffix}")
        suffix_base, separator, counter = quarantine_suffix.rpartition(".")
        if separator and counter.isdigit():
            add_candidate(f"{original_name}-wal{marker}{suffix_base}")

    for base in (raw_path.stem, raw_path.name):
        for suffix in ("", raw_path.suffix):
            for wal_suffix in ("-wal", ".wal"):
                add_candidate(f"{base}{wal_suffix}{suffix}")

    for candidate in candidates:
        # A clean ``wal_checkpoint(TRUNCATE)`` commonly leaves a zero-length
        # WAL beside a complete main database. Auto-detection should treat it
        # as no pending WAL; an explicitly supplied ``--wal`` remains strict.
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.resolve()

    return None


def _default_artifacts_dir(raw_path: Path) -> Path:
    """Return the default workspace output directory for recovery artifacts."""

    workspace_value = _getenv_case_insensitive("FLOCKS_WORKSPACE_DIR")
    workspace_dir = Path(
        workspace_value
        if workspace_value is not None
        else Path.home() / ".flocks" / "workspace"
    )
    today = dt.date.today().isoformat()
    run_name = _sanitize_name(raw_path.name)
    run_id = uuid.uuid4().hex
    return workspace_dir / "outputs" / today / f"db-recovery-{run_name}-{run_id}"


def _getenv_case_insensitive(name: str) -> str | None:
    """Read a Flocks setting with the same case-insensitive key semantics as BaseSettings."""

    result: str | None = None
    for key, value in os.environ.items():
        if key.lower() == name.lower():
            result = value
    return result


def _resolve_live_data_dir() -> Path:
    """Resolve the live Flocks data directory without importing the application."""

    data_dir = _getenv_case_insensitive("FLOCKS_DATA_DIR")
    if data_dir is not None:
        return Path(data_dir).resolve()

    xdg_data = _getenv_case_insensitive("XDG_DATA_HOME")
    if xdg_data:
        return (Path(xdg_data) / "flocks").resolve()

    flocks_root = _getenv_case_insensitive("FLOCKS_ROOT")
    if flocks_root:
        return (Path(flocks_root) / "data").resolve()

    return (Path.home() / ".flocks" / "data").resolve()


def _same_file_or_path(first: Path, second: Path) -> bool:
    """Return whether two paths resolve to the same path or existing inode."""

    if first.resolve() == second.resolve():
        return True
    try:
        return first.samefile(second)
    except OSError:
        return False


def _path_collision_key(path: Path) -> str:
    """Return a conservative path key for case-insensitive filesystems."""

    return os.path.normcase(str(path.resolve())).casefold()


def _is_within(path: Path, directory: Path) -> bool:
    """Return whether a resolved path is inside a resolved directory."""

    path_key = _path_collision_key(path)
    directory_key = _path_collision_key(directory).rstrip(os.sep)
    return path_key == directory_key or path_key.startswith(f"{directory_key}{os.sep}")


def _validate_recovery_plan(
    *,
    raw_path: Path,
    wal_path: Path | None,
    recovery_dir: Path,
    output_db: Path,
    prefix: str,
) -> None:
    """Reject recovery plans that could mutate live data or source evidence."""

    live_data_dir = _resolve_live_data_dir()
    live_db = live_data_dir / "flocks.db"
    live_sidecars = (live_data_dir / "flocks.db-wal", live_data_dir / "flocks.db-shm")
    artifact_paths = _recovery_artifact_paths(recovery_dir, prefix)
    sqlite_sidecar_paths = _recovery_sqlite_sidecar_paths(artifact_paths)
    recovered_db = artifact_paths[3]

    if _same_file_or_path(raw_path, live_db):
        raise ValueError(
            "Refusing to recover the live Flocks database directly. "
            "Stop Flocks, copy the database and matching WAL to a separate path, "
            "then recover that copy."
        )

    if wal_path is not None and any(
        _same_file_or_path(wal_path, live_sidecar) for live_sidecar in live_sidecars
    ):
        raise ValueError(
            "Refusing to read a live Flocks SQLite sidecar. "
            "Stop Flocks and copy the database with its matching WAL before recovery."
        )

    output_key = _path_collision_key(output_db)
    recovered_key = _path_collision_key(recovered_db)
    if output_db.resolve() != recovered_db.resolve() and (
        output_key == recovered_key or _same_file_or_path(output_db, recovered_db)
    ):
        raise ValueError(
            "Recovery output has an ambiguous case-insensitive collision with the recovered DB: "
            f"{output_db}"
        )

    intermediate_paths = (
        *(path for path in artifact_paths if path != recovered_db),
        *sqlite_sidecar_paths,
    )
    colliding_artifact = next(
        (
            artifact_path
            for artifact_path in intermediate_paths
            if (
                output_key == _path_collision_key(artifact_path)
                or _same_file_or_path(output_db, artifact_path)
            )
        ),
        None,
    )
    if colliding_artifact is not None:
        raise ValueError(
            f"Recovery output collides with an intermediate artifact: {colliding_artifact}"
        )

    write_paths = [*artifact_paths, *sqlite_sidecar_paths, output_db]

    source_paths = [raw_path]
    if wal_path is not None:
        source_paths.append(wal_path)
    for write_path in write_paths:
        if any(_same_file_or_path(write_path, source_path) for source_path in source_paths):
            raise ValueError(
                f"Refusing to overwrite recovery source evidence: {write_path}"
            )

    if any(_same_file_or_path(write_path, live_db) for write_path in write_paths):
        raise ValueError(
            "Refusing to write recovery output to the live Flocks database. "
            "Generate a recovery artifact, stop Flocks, then install it explicitly."
        )

    for write_path in write_paths:
        if _is_within(write_path, live_data_dir):
            raise ValueError(
                "Refusing to write recovery files inside the live Flocks data directory: "
                f"{write_path}"
            )
        if write_path.exists() or write_path.is_symlink():
            raise FileExistsError(f"Refusing to overwrite existing recovery file: {write_path}")


def _resolve_output_paths(raw_path: Path, args: argparse.Namespace) -> tuple[Path, Path, str]:
    """Choose the artifact directory, final DB path, and working prefix."""

    if args.output is not None:
        output_db = args.output.expanduser().resolve()
        artifacts_dir = (
            args.artifacts_dir.expanduser().resolve()
            if args.artifacts_dir is not None
            else output_db.parent / f"{_sanitize_name(output_db.stem)}.artifacts"
        )
    else:
        artifacts_dir = (
            args.artifacts_dir.expanduser().resolve()
            if args.artifacts_dir is not None
            else _default_artifacts_dir(raw_path)
        )
        output_db = artifacts_dir / f"{_sanitize_name(raw_path.name)}.recovered.db"

    prefix = _sanitize_name(args.prefix or output_db.stem)
    return artifacts_dir, output_db, prefix


def _copy_file_exclusive(source: Path, destination: Path) -> None:
    """Copy a file without ever replacing a destination created after validation."""

    with _atomic_output_path(destination) as temporary_path:
        with source.open("rb") as source_handle, temporary_path.open("xb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Recover an offline copy of a damaged Flocks SQLite DB. The script can "
            "auto-detect a sibling WAL file and writes the repaired DB plus "
            "intermediate artifacts without modifying the live store."
        )
    )
    parser.add_argument(
        "damaged_db",
        nargs="?",
        type=Path,
        help="Path to the damaged SQLite DB file.",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=None,
        help="Legacy alias for the damaged DB path.",
    )
    parser.add_argument(
        "--wal",
        type=Path,
        default=None,
        help="Optional path to the matching WAL file. If omitted, the script auto-detects one.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional new output DB path. Existing files and the live Flocks DB are rejected.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="Optional directory for intermediate recovery artifacts.",
    )
    parser.add_argument(
        "--out-dir",
        dest="artifacts_dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Optional filename prefix for generated artifacts.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run recovery and print artifact locations."""

    # Match the normal ``flocks`` CLI: environment variables already exported
    # by the operator win, and missing values are filled from the current
    # project's .env before resolving the protected live data directory.
    current_env = Path.cwd() / ".env"
    if current_env.is_file():
        load_dotenv(current_env)
    project_env = Path(__file__).resolve().parent.parent / ".env"
    if project_env.is_file() and project_env != current_env:
        load_dotenv(project_env)
    args = build_parser().parse_args(argv)
    raw_path = _resolve_raw_path(args)
    if not raw_path.exists():
        raise FileNotFoundError(f"Damaged DB file does not exist: {raw_path}")

    wal_path = args.wal.expanduser().resolve() if args.wal is not None else _detect_wal_path(raw_path)
    if wal_path is not None and not wal_path.is_file():
        raise FileNotFoundError(f"WAL file does not exist or is not a file: {wal_path}")
    artifacts_dir, output_db, prefix = _resolve_output_paths(raw_path, args)
    _validate_recovery_plan(
        raw_path=raw_path,
        wal_path=wal_path,
        recovery_dir=artifacts_dir,
        output_db=output_db,
        prefix=prefix,
    )

    artifacts = recover_raw_storage_db(
        raw_path=raw_path,
        wal_path=wal_path,
        recovery_dir=artifacts_dir,
        prefix=prefix,
    )

    if not _same_file_or_path(artifacts.recovered_db, output_db):
        _copy_file_exclusive(artifacts.recovered_db, output_db)

    print(f"input_db={raw_path}")
    print(f"wal_path={wal_path if wal_path is not None else 'none'}")
    # Preserve the original machine-readable field for callers while making
    # the non-destructive behavior explicit.
    print("removed_sidecars=none")
    print(f"artifacts_dir={artifacts_dir}")
    print(f"recovered_db={output_db}")
    print(f"summary_path={artifacts.summary_path}")
    print(artifacts.summary_path.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
