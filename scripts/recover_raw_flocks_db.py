"""One-click recovery for a damaged Flocks SQLite database."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import struct
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Sequence

WAL_MAGIC = 0x377F0682
WAL_VERSION = 3007000
COMMON_SQLITE_PAGE_SIZES = (4096, 8192, 2048, 1024, 16384, 32768, 65536)

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
    pagesize: int
    wal_frames: int
    wal_final_db_pages: int
    copied_rows: Dict[str, int]


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
    return [str(row[1]) for row in rows]


def _parse_wal_frames(wal_bytes: bytes) -> tuple[int, int, Dict[int, bytes], int]:
    """Return WAL page size, final db pages, latest page map, and frame count."""

    if len(wal_bytes) < 32:
        raise ValueError("WAL file is too small to contain a valid header.")

    magic, version, pagesize, _, _, _, _, _ = struct.unpack(">8I", wal_bytes[:32])
    if magic != WAL_MAGIC:
        raise ValueError(f"Unexpected WAL magic: 0x{magic:08x}")
    if version != WAL_VERSION:
        raise ValueError(f"Unexpected WAL version: {version}")
    if pagesize <= 0:
        raise ValueError(f"Invalid WAL pagesize: {pagesize}")

    frame_size = 24 + pagesize
    payload = len(wal_bytes) - 32
    if payload < 0 or payload % frame_size != 0:
        raise ValueError("WAL payload is not aligned to complete frames.")

    frame_count = payload // frame_size
    latest_pages: Dict[int, bytes] = {}
    final_db_pages = 0
    for frame_index in range(frame_count):
        offset = 32 + frame_index * frame_size
        page_no, db_page_count, *_ = struct.unpack(">6I", wal_bytes[offset : offset + 24])
        latest_pages[page_no] = wal_bytes[offset + 24 : offset + 24 + pagesize]
        if db_page_count:
            final_db_pages = db_page_count

    if final_db_pages <= 0:
        raise ValueError("WAL does not contain any committed frames.")

    return pagesize, final_db_pages, latest_pages, frame_count


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
    page1[96] = 0x0D
    page1[97] = 0x00
    page1[98:100] = (0).to_bytes(2, "big")
    cell_content_area = 0 if pagesize == 65536 else pagesize
    page1[100:102] = cell_content_area.to_bytes(2, "big")
    page1[102] = 0
    page1[103] = 0
    page1[104:108] = (0).to_bytes(4, "big")
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
        if len(raw_bytes) % pagesize != 0:
            raise ValueError(
                f"Raw file size {len(raw_bytes)} is not aligned to WAL pagesize {pagesize}."
            )
    else:
        pagesize = _guess_raw_pagesize(raw_bytes)
        final_db_pages = len(raw_bytes) // pagesize
        latest_pages = {}
        frame_count = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
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


def _run_sqlite_recover(candidate_db: Path, recover_sql_path: Path) -> None:
    """Write `sqlite3 .recover` output to a SQL file."""

    completed = subprocess.run(
        ["sqlite3", str(candidate_db), ".recover"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    recover_sql_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0 and not completed.stdout.strip():
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"sqlite3 .recover failed: {stderr or completed.returncode}")


def _materialize_recovered_sql(recover_sql_path: Path, extracted_db_path: Path) -> None:
    """Execute recovered SQL into a scratch SQLite database."""

    if extracted_db_path.exists():
        extracted_db_path.unlink()

    completed = subprocess.run(
        ["sqlite3", str(extracted_db_path)],
        input=recover_sql_path.read_text(encoding="utf-8"),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Failed to materialize recovered SQL: {stderr}")


def _ensure_recovered_schema(output_db_path: Path) -> None:
    """Create the normalized target schema."""

    if output_db_path.exists():
        output_db_path.unlink()

    output_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output_db_path)
    try:
        conn.executescript(STORAGE_DDL)
        conn.executescript(USAGE_RECORDS_DDL)
        conn.executescript(TASKS_DDL)
        conn.executescript(CHANNEL_BINDINGS_DDL)
        for stmt in (*USAGE_INDEX_STMTS, *TASK_INDEX_STMTS):
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def _insert_rows(
    target_conn: sqlite3.Connection,
    table_name: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
) -> int:
    """Insert rows into a table with INSERT OR REPLACE."""

    if not rows:
        return 0

    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    target_conn.executemany(
        (
            f"INSERT OR REPLACE INTO {_quote_identifier(table_name)} "
            f"({quoted_columns}) VALUES ({placeholders})"
        ),
        rows,
    )
    return len(rows)


def _copy_table_rows(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    table_name: str,
) -> int:
    """Copy shared columns from one table to another."""

    if not _table_exists(source_conn, table_name) or not _table_exists(target_conn, table_name):
        return 0

    source_columns = _table_columns(source_conn, table_name)
    target_columns = set(_table_columns(target_conn, table_name))
    copy_columns = [column for column in source_columns if column in target_columns]
    if not copy_columns:
        return 0

    quoted_columns = ", ".join(_quote_identifier(column) for column in copy_columns)
    rows = source_conn.execute(
        f"SELECT {quoted_columns} FROM {_quote_identifier(table_name)}"
    ).fetchall()
    if not rows:
        return 0

    return _insert_rows(target_conn, table_name, copy_columns, rows)


def _copy_lost_and_found_rows(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    table_name: str,
) -> int:
    """Recover business rows from `.recover` lost_and_found output."""

    if not _table_exists(source_conn, "lost_and_found"):
        return 0

    if table_name == "storage":
        rows = [
            tuple(row)
            for row in source_conn.execute(
                """
                SELECT c0, c1, c2, c3, c4
                FROM lost_and_found
                WHERE nfield = 5
                  AND c0 NOT IN ('table', 'index')
                  AND typeof(c0) = 'text'
                  AND typeof(c1) = 'text'
                  AND typeof(c2) = 'text'
                  AND typeof(c3) = 'text'
                  AND typeof(c4) = 'text'
                """
            ).fetchall()
            if ":" in str(row[0]) or "/" in str(row[0])
        ]
        return _insert_rows(
            target_conn,
            "storage",
            ("key", "value", "type", "created_at", "updated_at"),
            rows,
        )

    if table_name == "usage_records":
        rows = [
            tuple(row)
            for row in source_conn.execute(
                """
                SELECT c0, c1, c2, c3, c4, c5, c6, c7, c8, c9,
                       c10, c11, c12, c13, c14, c15, c16, c17, c18, c19
                FROM lost_and_found
                WHERE nfield = 20
                  AND typeof(c0) = 'text'
                  AND typeof(c1) = 'text'
                  AND typeof(c2) = 'text'
                """
            ).fetchall()
        ]
        return _insert_rows(target_conn, "usage_records", USAGE_RECORD_COLUMNS, rows)

    if table_name == "task_schedulers":
        rows = [
            tuple(row)
            for row in source_conn.execute(
                """
                SELECT c0, c1, c2, c3, c4, c5, c6, c7, c8, c9,
                       c10, c11, c12, c13, c14, c15, c16, c17, c18, c19, c20
                FROM lost_and_found
                WHERE nfield = 21
                  AND typeof(c0) = 'text'
                """
            ).fetchall()
            if str(row[0]).startswith("tsk_")
        ]
        return _insert_rows(target_conn, "task_schedulers", TASK_SCHEDULER_COLUMNS, rows)

    if table_name == "task_executions":
        rows = [
            tuple(row)
            for row in source_conn.execute(
                """
                SELECT c0, c1, c2, c3, c4, c5, c6, c7, c8, c9,
                       c10, c11, c12, c13, c14, c15, c16, c17, c18, c19,
                       c20, c21, c22, c23
                FROM lost_and_found
                WHERE nfield = 24
                  AND typeof(c0) = 'text'
                """
            ).fetchall()
            if str(row[0]).startswith("txe_")
        ]
        return _insert_rows(target_conn, "task_executions", TASK_EXECUTION_COLUMNS, rows)

    if table_name == "task_execution_queue_refs":
        rows = [
            tuple(row)
            for row in source_conn.execute(
                """
                SELECT c0, c1, c2, c3, c4
                FROM lost_and_found
                WHERE nfield = 5
                  AND typeof(c0) = 'text'
                  AND typeof(c1) = 'text'
                  AND typeof(c2) = 'text'
                  AND typeof(c3) = 'text'
                """
            ).fetchall()
            if str(row[0]).startswith("tqref_")
        ]
        return _insert_rows(target_conn, "task_execution_queue_refs", QUEUE_REF_COLUMNS, rows)

    if table_name == "channel_bindings":
        rows = [
            tuple(row)
            for row in source_conn.execute(
                """
                SELECT c0, c1, c2, c3, c4, c5, c6, c7, c8, c9
                FROM lost_and_found
                WHERE nfield = 10
                  AND typeof(c0) = 'text'
                """
            ).fetchall()
            if str(row[0]).startswith("chb_")
        ]
        return _insert_rows(target_conn, "channel_bindings", CHANNEL_BINDING_COLUMNS, rows)

    return 0


def build_normalized_recovery_db(
    extracted_db_path: Path,
    output_db_path: Path,
) -> Dict[str, int]:
    """Copy supported recovered tables into a clean output database."""

    _ensure_recovered_schema(output_db_path)

    copied_rows: Dict[str, int] = {}
    source_conn = sqlite3.connect(extracted_db_path)
    target_conn = sqlite3.connect(output_db_path)
    try:
        for table_name in SUPPORTED_COPY_TABLES:
            direct_rows = _copy_table_rows(source_conn, target_conn, table_name)
            if direct_rows == 0:
                _copy_lost_and_found_rows(source_conn, target_conn, table_name)
            target_conn.commit()
            copied_rows[table_name] = target_conn.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}"
            ).fetchone()[0]
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
        f"pagesize={artifacts.pagesize}",
        f"wal_frames={artifacts.wal_frames}",
        f"wal_final_db_pages={artifacts.wal_final_db_pages}",
    ]
    for table_name in SUPPORTED_COPY_TABLES:
        lines.append(f"copied_{table_name}={artifacts.copied_rows.get(table_name, 0)}")
    return "\n".join(lines) + "\n"


def recover_raw_storage_db(
    raw_path: Path,
    wal_path: Path | None,
    recovery_dir: Path,
    *,
    prefix: str,
) -> RecoveryArtifacts:
    """Recover a damaged raw SQLite file into a normalized database."""

    recovery_dir.mkdir(parents=True, exist_ok=True)
    candidate_db = recovery_dir / f"{prefix}.candidate.db"
    recover_sql = recovery_dir / f"{prefix}.recover.sql"
    extracted_db = recovery_dir / f"{prefix}.extracted.db"
    recovered_db = recovery_dir / f"{prefix}.db"
    summary_path = recovery_dir / f"{prefix}.summary.txt"

    candidate_stats = reconstruct_sqlite_candidate(raw_path, wal_path, candidate_db)
    _run_sqlite_recover(candidate_db, recover_sql)
    _materialize_recovered_sql(recover_sql, extracted_db)
    copied_rows = build_normalized_recovery_db(extracted_db, recovered_db)

    artifacts = RecoveryArtifacts(
        recovery_dir=recovery_dir,
        candidate_db=candidate_db,
        recover_sql=recover_sql,
        extracted_db=extracted_db,
        recovered_db=recovered_db,
        summary_path=summary_path,
        pagesize=candidate_stats["pagesize"],
        wal_frames=candidate_stats["wal_frames"],
        wal_final_db_pages=candidate_stats["wal_final_db_pages"],
        copied_rows=copied_rows,
    )
    summary_path.write_text(_render_summary(artifacts), encoding="utf-8")
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

    bases = {raw_path.name, raw_path.stem}
    suffixes = {"", raw_path.suffix}
    seen: set[Path] = set()

    for base in bases:
        for suffix in suffixes:
            for wal_suffix in ("-wal", ".wal"):
                candidate = raw_path.with_name(f"{base}{wal_suffix}{suffix}")
                if candidate in seen:
                    continue
                seen.add(candidate)
                if candidate.exists():
                    return candidate.resolve()

    return None


def _default_artifacts_dir(raw_path: Path) -> Path:
    """Create the default workspace output directory for recovery artifacts."""

    workspace_dir = Path(os.getenv("FLOCKS_WORKSPACE_DIR", Path.home() / ".flocks" / "workspace"))
    today = dt.date.today().isoformat()
    run_name = _sanitize_name(raw_path.name)
    output_dir = workspace_dir / "outputs" / today / f"db-recovery-{run_name}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _cleanup_live_sqlite_sidecars() -> list[Path]:
    """Delete stale live WAL/SHM sidecar files under `~/.flocks/data/`."""

    data_dir = Path(os.getenv("FLOCKS_DATA_DIR", Path.home() / ".flocks" / "data"))
    removed: list[Path] = []
    for name in ("flocks.db-shm", "flocks.db-wal"):
        candidate = data_dir / name
        if candidate.exists():
            candidate.unlink()
            removed.append(candidate)
    return removed


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

    prefix = args.prefix or output_db.stem
    return artifacts_dir, output_db, prefix


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Recover a damaged Flocks SQLite DB in one command. The script can "
            "auto-detect a sibling WAL file and writes the repaired DB plus "
            "intermediate artifacts."
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
        help="Optional final output DB file path.",
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

    args = build_parser().parse_args(argv)
    removed_sidecars = _cleanup_live_sqlite_sidecars()
    raw_path = _resolve_raw_path(args)
    if not raw_path.exists():
        raise FileNotFoundError(f"Damaged DB file does not exist: {raw_path}")

    wal_path = args.wal.expanduser().resolve() if args.wal is not None else _detect_wal_path(raw_path)
    artifacts_dir, output_db, prefix = _resolve_output_paths(raw_path, args)

    artifacts = recover_raw_storage_db(
        raw_path=raw_path,
        wal_path=wal_path,
        recovery_dir=artifacts_dir,
        prefix=prefix,
    )

    if artifacts.recovered_db != output_db:
        output_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifacts.recovered_db, output_db)

    print(f"input_db={raw_path}")
    print(f"wal_path={wal_path if wal_path is not None else 'none'}")
    print(
        "removed_sidecars="
        + (
            ",".join(str(path) for path in removed_sidecars)
            if removed_sidecars
            else "none"
        )
    )
    print(f"artifacts_dir={artifacts_dir}")
    print(f"recovered_db={output_db}")
    print(f"summary_path={artifacts.summary_path}")
    print(artifacts.summary_path.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

