"""Explicit, offline SQLite-to-PostgreSQL migration.

The application never imports a SQLite driver. This command snapshots sources
read-only, copies bounded batches in one PG transaction, and verifies every
copied value. Existing different values cause a rollback, never an overwrite.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import importlib
import os
import re
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from flocks.db.registry import load_settings

BATCH_SIZE = 300
ALERT_COLUMNS = {
    "record_id": "TEXT", "asset_date": "TEXT", "source_file": "TEXT", "line_number": "BIGINT",
    "event_time": "BIGINT", "source_type": "TEXT", "threat_name": "TEXT", "dedup_key": "TEXT",
    "is_duplicate": "INTEGER", "record_json": "JSONB",
}


class MigrationConflict(RuntimeError):
    """Source data differs from an existing target row."""


@dataclass
class TableSource:
    source: str
    snapshot: str
    name: str
    columns: list[dict]
    primary_key: list[str]
    count: int
    rowid: bool
    derived: bool = False
    unique_keys: list[list[str]] = field(default_factory=list)


@dataclass
class MigrationReport:
    applied: bool = False
    verified: int = 0
    inserted: int = 0
    unchanged: int = 0
    paused: int = 0
    adjusted: int = 0
    sources: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _ident(value: str) -> str:
    if not value or "\x00" in value or len(value.encode()) > 63:
        raise ValueError("Invalid or overlong database identifier")
    return '"' + value.replace('"', '""') + '"'


def _hash(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(path: Path) -> dict:
    return {suffix: _hash(Path(str(path) + suffix)) for suffix in ("", "-wal", "-journal")}


def discover_sources(paths: list[Path], *, exclude: tuple[Path, ...] = ()) -> list[Path]:
    excluded = tuple(path.expanduser().resolve() for path in exclude)

    def allowed(path):
        resolved = path.resolve()
        return not any(resolved.is_relative_to(root) for root in excluded)

    def candidates_in(path):
        if path.is_file():
            if allowed(path):
                yield path
            return
        for directory, children, files in os.walk(path, followlinks=False):
            root = Path(directory)
            if not allowed(root) or (root / ".flocks-migration-evidence").is_file():
                children[:] = []
                continue
            children[:] = [name for name in children if allowed(root / name)]
            for name in files:
                candidate = root / name
                if allowed(candidate):
                    yield candidate

    found = set()
    for path in paths:
        path = path.expanduser().resolve(strict=True)
        for candidate in candidates_in(path):
            if candidate.is_file() and candidate.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                with candidate.open("rb") as stream:
                    header = stream.read(16)
                    if header == b"SQLite format 3\x00":
                        found.add(candidate.resolve())
                    elif header:
                        raise ValueError(f"Nonempty source is not a SQLite database: {candidate.name}")
    return sorted(found)


def _snapshot(path: Path, directory: Path) -> tuple[dict, list[TableSource]]:
    before = _fingerprint(path)
    snapshot = directory / (hashlib.sha256(str(path).encode()).hexdigest()[:20] + ".sqlite")
    # Copy the bytes first: even SQLite's mode=ro may create WAL/SHM siblings.
    # SQLite only opens our private copy; the original is regular read-only I/O.
    capture = snapshot.with_name(snapshot.stem + "-captured.sqlite")
    for suffix, digest in before.items():
        if digest is not None:
            copied = Path(str(capture) + suffix)
            shutil.copyfile(Path(str(path) + suffix), copied)
            copied.chmod(0o600)
    if _fingerprint(path) != before:
        raise ValueError("Source changed during capture; stop its writer and retry")
    source = sqlite3.connect(capture.as_uri() + "?mode=ro", uri=True)
    try:
        source.execute("PRAGMA query_only=ON")
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    snapshot.chmod(0o600)
    after = _fingerprint(path)
    if before != after:
        raise ValueError("Source changed during snapshot; stop its writer and retry")
    connection = sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    tables = []
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError(f"SQLite integrity check failed: {path.name}")
        definitions = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        virtual_names = [r["name"] for r in definitions if "VIRTUAL TABLE" in (r["sql"] or "").upper()]
        for definition in definitions:
            name = definition["name"]
            derived = name.startswith("sqlite_") or any(
                name == virtual + suffix for virtual in virtual_names
                for suffix in ("_data", "_idx", "_content", "_docsize", "_config", "_segments", "_segdir", "_stat")
            )
            # table_info omits both stored and virtual generated columns.
            # Preserve their evaluated snapshot values as ordinary PG columns;
            # only virtual-table implementation columns (hidden=1) are private.
            columns = [dict(row) for row in connection.execute(f"PRAGMA table_xinfo({_ident(name)})")
                       if row["hidden"] != 1]
            primary = [row["name"] for row in sorted(columns, key=lambda r: r["pk"]) if row["pk"]]
            has_rowid = "WITHOUT ROWID" not in (definition["sql"] or "").upper()
            count = connection.execute(f"SELECT COUNT(*) FROM {_ident(name)}").fetchone()[0]
            unique_keys = []
            if not derived:
                for index in connection.execute(f"PRAGMA index_list({_ident(name)})"):
                    if index["unique"] and not index["partial"] and index["origin"] != "pk":
                        names = [row["name"] for row in connection.execute(f"PRAGMA index_info({_ident(index['name'])})")]
                        if all(names):
                            unique_keys.append(names)
            tables.append(TableSource(str(path), str(snapshot), name, columns, primary, count, has_rowid, derived, unique_keys))
    finally:
        connection.close()
    return {"path": str(path), "snapshot": str(snapshot), "fingerprint": before}, tables


def _sqlite_type(declaration: str, column: str, table: str) -> str:
    if table == "alert_records" and column == "record_json":
        return "JSONB"
    value = declaration.upper()
    if "INT" in value:
        return "BIGINT"
    if any(word in value for word in ("NUMERIC", "DECIMAL")):
        return "NUMERIC"
    if any(word in value for word in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE PRECISION"
    if "BLOB" in value:
        return "BYTEA"
    return "TEXT"


async def _bootstrap(connection) -> None:
    from flocks.db.dialect import PostgresDialect
    from flocks.runtime_state.postgres import SCHEMA_STATEMENTS
    from flocks.auth.service import _auth_schema
    from flocks.task.store import _tasks_ddl
    from flocks.workflow.store import _workflow_ddl
    from flocks.storage.vector import _vector_schema
    from flocks.db.search import PostgresMemoryIndex, PostgresSearchIndex
    from flocks.storage.session_search import _state_schema, _meta_schema
    from flocks.channel.inbound.session_binding import _binding_schema
    from flocks.session.message_pages import SCHEMA
    from flocks.db.backends.postgres import PostgresTransaction
    # Importing the module registers the application-owned device DDL.
    importlib.import_module("flocks.tool.device.models")
    from flocks.storage.storage import Storage

    dialect = PostgresDialect()
    statements = [*SCHEMA_STATEMENTS, *_auth_schema(dialect), *_tasks_ddl(dialect),
                  *_workflow_ddl(dialect), *_vector_schema(dialect), *_binding_schema(dialect),
                  *_state_schema(dialect), *_meta_schema(dialect),
                  *PostgresMemoryIndex().schema_statements(), *PostgresSearchIndex().schema_statements(), SCHEMA]
    await connection.execute(";\n".join(statements))
    # Device defaults, unique names and FKs must exist before copying old rows.
    # These are application-owned DDL, never code read from the source file.
    transaction = PostgresTransaction(connection)
    for ddl in Storage._extension_ddls:
        if isinstance(ddl, str):
            await connection.execute(ddl)
        else:
            await ddl(transaction)
    await connection.execute("""
        CREATE TABLE IF NOT EXISTS legacy_migration_adjustments (
            id TEXT PRIMARY KEY, source_path TEXT NOT NULL, table_name TEXT NOT NULL,
            original_row JSONB NOT NULL, reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS legacy_migration_runs (
            id TEXT PRIMARY KEY, completed_at TIMESTAMPTZ NOT NULL DEFAULT now(), report JSONB NOT NULL);
    """)


def _target_name(source: TableSource) -> str:
    return {"storage": "runtime_state", "session_transcript_fts": "session_transcript_search"}.get(source.name, source.name)


async def _prepare_table(connection, source: TableSource) -> tuple[str, dict, list[str]]:
    table = _target_name(source)
    special = source.name in {"storage", "session_transcript_fts"}
    columns = {row["name"]: _sqlite_type(row["type"], row["name"], table) for row in source.columns}
    primary = list(source.primary_key)
    if table == "alert_records":
        for name, kind in ALERT_COLUMNS.items():
            columns.setdefault(name, kind)
        columns["rowid"] = "BIGINT GENERATED BY DEFAULT AS IDENTITY UNIQUE"
        columns.setdefault("row_id", "TEXT")
        primary = ["row_id"]
    if not special:
        if not primary:
            columns["__legacy_key"] = "TEXT"
            primary = ["__legacy_key"]
        definitions = []
        metadata = {row["name"]: row for row in source.columns}
        for name, kind in columns.items():
            definition = f"{_ident(name)} {kind}"
            original = metadata.get(name, {})
            if original.get("notnull"):
                definition += " NOT NULL"
            default = _portable_default(original.get("dflt_value"), kind)
            if default is not None:
                definition += f" DEFAULT {default}"
            definitions.append(definition)
        definitions.append("PRIMARY KEY (" + ",".join(_ident(key) for key in primary) + ")")
        definitions.extend("UNIQUE (" + ",".join(_ident(key) for key in keys) + ")" for keys in source.unique_keys)
        await connection.execute(f"CREATE TABLE IF NOT EXISTS {_ident(table)} ({','.join(definitions)})")
    target_columns = await connection.fetch(
        "SELECT column_name, data_type, is_generated FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=$1 ORDER BY ordinal_position", table,
    )
    target = {r["column_name"]: r["data_type"] for r in target_columns if r["is_generated"] == "NEVER"}
    if not special:
        missing = [name for name in columns if name not in target]
        # Preserve old extension columns as well as current application data.
        for name in missing:
            await connection.execute(f"ALTER TABLE {_ident(table)} ADD COLUMN {_ident(name)} {columns[name]}")
            target[name] = columns[name].split(" GENERATED", 1)[0].lower()
    pk_rows = await connection.fetch(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid "
        "AND a.attnum=ANY(i.indkey) WHERE i.indrelid=to_regclass($1) AND i.indisprimary "
        "ORDER BY array_position(i.indkey, a.attnum)", _ident(table),
    )
    primary = [row["attname"] for row in pk_rows]
    if not primary:
        raise MigrationConflict(f"Target table {table} needs a primary key before migration")
    return table, target, primary


def _portable_default(value, kind):
    if value is None:
        return None
    value = str(value).strip()
    while value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    if re.fullmatch(r"(?:[-+]?\d+(?:\.\d+)?|NULL|TRUE|FALSE|'(?:[^']|'')*')", value, re.IGNORECASE):
        return value
    if value.upper() == "CURRENT_TIMESTAMP":
        if kind == "TEXT":
            from flocks.db.dialect import PostgresDialect

            # Preserve SQLite's UTC second format, including for audit filters.
            return PostgresDialect().now_text()
        return "CURRENT_TIMESTAMP"
    # Source trigger/default SQL is retained in the snapshot; extensions own
    # its PostgreSQL replacement. It is never run as arbitrary target SQL.
    return None


def _open_rows(source: TableSource):
    connection = sqlite3.connect(Path(source.snapshot).as_uri() + "?mode=ro&immutable=1", uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    fields = [row["name"] for row in source.columns]
    select = ",".join(_ident(name) for name in fields)
    if source.rowid and "rowid" not in fields:
        select += ',rowid AS "__source_rowid"'
    cursor = connection.execute(f"SELECT {select} FROM {_ident(source.name)}")
    return connection, cursor


def _json_value(value):
    if isinstance(value, bytes):
        return {"base64": __import__("base64").b64encode(value).decode()}
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_value)


def _transform(source: TableSource, raw: dict) -> tuple[dict, str | None]:
    row = dict(raw)
    rowid = row.pop("__source_rowid", None)
    reason = None
    if source.name == "storage":
        value = row["value"]
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            pass
        document = {"key": row["key"], "value": _encoded(value), "value_type": row.get("type") or "json"}
        for stamp in ("created_at", "updated_at"):
            if row.get(stamp) is not None:
                document[stamp] = row[stamp]
        return document, None
    if source.name == "session_transcript_fts":
        return {"row_id": row.get("row_id", rowid), "text": row.get("text", row.get("content", ""))}, None
    if source.name == "alert_records":
        if rowid is not None:
            row.setdefault("rowid", rowid)
        row["row_id"] = row.get("row_id") or "legacy-" + hashlib.sha256(f"{source.source}:{rowid}".encode()).hexdigest()[:32]
        payload = json.loads(row.get("record_json") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("Legacy alert record_json must contain a JSON object")
        defaults = {"record_id": payload.get("id"), "asset_date": payload.get("asset_date", ""),
                    "source_file": payload.get("source_file", Path(source.source).name),
                    "line_number": payload.get("line_number", rowid or 0), "event_time": payload.get("event_time"),
                    "source_type": payload.get("source_type"), "threat_name": payload.get("threat_name"),
                    "dedup_key": payload.get("dedup_key"), "is_duplicate": int(bool(payload.get("is_duplicate", False)))}
        missing = set(defaults) - row.keys()
        if missing:
            row.update({name: defaults[name] for name in missing})
            reason = "Added PostgreSQL alert columns missing from legacy schema; originals retained"
    if source.name in {"memory_files", "memory_chunks", "memory_fts"}:
        if not row.get("scope"):
            project = row.get("project_id")
            if row.get("scope_id"):
                raise ValueError("Legacy memory scope_id has no visibility scope")
            if project is not None and not isinstance(project, str):
                raise ValueError("Legacy memory project_id must be text")
            row["scope"] = "project" if project else "global"
            row["scope_id"] = project or ""
            reason = "Mapped legacy memory visibility without widening project scope"
        elif row["scope"] not in {"global", "project"}:
            raise ValueError("Unknown legacy memory visibility scope")
        elif row["scope"] == "project":
            if not isinstance(row.get("scope_id"), str) or not row["scope_id"].strip():
                raise ValueError("Project memory requires a nonempty scope_id")
        elif row.get("scope_id") not in (None, ""):
            raise ValueError("Global memory must have an empty scope_id")
        else:
            row["scope_id"] = ""
    if source.name == "task_schedulers" and row.get("status") == "active":
        row["status"] = "disabled"
        reason = "Schedule disabled for operator review after migration"
    elif source.name in {"task_executions", "task_execution_queue_refs"} and row.get("status") in {"pending", "queued", "running"}:
        row["status"] = "failed"
        reason = "Interrupted by offline migration; no automatic replay"
        if source.name == "task_executions":
            row["error"] = reason
            row["owner_node_id"] = None
            row["lease_expires_at"] = None
    elif source.name == "workflow_executions" and row.get("status") in {"running", "pending"}:
        row["status"] = "error"
        reason = "Interrupted by offline migration; no automatic replay"
        row["error_message"] = reason
        payload = json.loads(row.get("payload") or "{}")
        payload.update(status="error", errorMessage=reason)
        row["payload"] = _encoded(payload)
    elif source.name == "workflow_configs":
        config = json.loads(row["config"])
        if _disable_triggers(config):
            reason = "Workflow triggers disabled for operator review after migration"
            row["config"] = _encoded(config)
    if not source.primary_key and source.name != "alert_records":
        row["__legacy_key"] = hashlib.sha256(_encoded([source.source, source.name, rowid, raw]).encode()).hexdigest()
    return row, reason


def _disable_triggers(value) -> bool:
    changed = False
    if isinstance(value, dict):
        if value.get("enabled"):
            value["enabled"] = False
            changed = True
        for child in value.values():
            changed = _disable_triggers(child) or changed
    elif isinstance(value, list):
        for child in value:
            changed = _disable_triggers(child) or changed
    return changed


def _coerce(value, kind):
    if value is None:
        return None
    if kind in {"jsonb", "json"}:
        if isinstance(value, str):
            json.loads(value)  # Invalid JSON is an error, never silently lost.
            return value
        return _encoded(value)
    if kind in {"boolean"}:
        if value not in (0, 1, True, False):
            raise ValueError("Invalid boolean in source")
        return bool(value)
    if kind.startswith("timestamp"):
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    if kind in {"text", "character varying", "character"}:
        if isinstance(value, bytes):
            raise ValueError("A SQLite BLOB in a text column requires an explicit bytea mapping")
        return value if isinstance(value, str) else str(value)
    if kind == "bytea" and isinstance(value, str):
        # SQLite permits TEXT in a BLOB-declared embedding column; PG does
        # not. Its UTF-8 bytes preserve the JSON payload without numeric loss.
        return value.encode("utf-8")
    if kind in {"bigint", "integer", "smallint"}:
        # SQLite INTEGER affinity can still contain REAL/TEXT. Never truncate
        # a fraction and then compare the target against that truncated value.
        try:
            number = Decimal(str(int(value) if isinstance(value, bool) else value))
            if not number.is_finite() or number != number.to_integral_value():
                raise ValueError
            return int(number)
        except (ValueError, ArithmeticError):
            raise ValueError("Source value cannot be represented exactly as a PostgreSQL integer") from None
    if kind == "numeric":
        return Decimal(str(value))
    if kind in {"double precision", "real"}:
        return float(value)
    return value


async def _write_batch(connection, table, target, primary, rows, *, prefer_existing=False):
    # Optional source fields (notably nullable legacy timestamps) vary by row.
    # Group by the complete column set so a first row cannot silently decide
    # which values later rows insert and verify. Each group remains batched.
    groups = {}
    for row in rows:
        groups.setdefault(tuple(sorted(row)), []).append(row)
    inserted = 0
    for group in groups.values():
        inserted += await _write_column_batch(
            connection, table, target, primary, group, prefer_existing=prefer_existing,
        )
    return inserted


async def _write_column_batch(connection, table, target, primary, rows, *, prefer_existing=False):
    names = list(rows[0])
    if not all(key in names for key in primary):
        raise MigrationConflict(f"Missing primary key in source for {table}")
    # One statement per batch and one verification query. All values
    # remain bound parameters; a large table never means N+1 queries.
    values = [row[name] for row in rows for name in names]
    if len(values) > 30000:
        raise ValueError("Table exceeds bounded migration batch width")
    placeholders = ["(" + ",".join(f"${i * len(names) + j + 1}" for j in range(len(names))) + ")" for i in range(len(rows))]
    columns_sql = ",".join(_ident(name) for name in names)
    result = await connection.fetch(
        f"INSERT INTO {_ident(table)} ({columns_sql}) VALUES {','.join(placeholders)} "
        f"ON CONFLICT DO NOTHING RETURNING {','.join(_ident(key) for key in primary)}", *values,
    )
    inserted_keys = {tuple(row[key] for key in primary) for row in result}
    keys = [row[key] for row in rows for key in primary]
    conditions = ["(" + " AND ".join(f"{_ident(key)}=${i * len(primary) + j + 1}" for j, key in enumerate(primary)) + ")" for i in range(len(rows))]
    actual_rows = await connection.fetch(
        f"SELECT {columns_sql} FROM {_ident(table)} WHERE " + " OR ".join(conditions), *keys,
    )
    actual = {tuple(row[key] for key in primary): dict(row) for row in actual_rows}
    for expected in rows:
        if prefer_existing and tuple(expected[key] for key in primary) not in inserted_keys:
            continue
        got = actual.get(tuple(expected[key] for key in primary))
        if got is None:
            raise MigrationConflict(f"Unique key conflict in {table}; target was not overwritten")
        for name in names:
            left, right = expected[name], got[name]
            if target[name] in {"json", "jsonb"} and left is not None and right is not None:
                left, right = json.loads(left), json.loads(right)
            if left != right:
                raise MigrationConflict(f"Content conflict in {table}; target was not overwritten")
    return len(result)


async def _copy_table(connection, source: TableSource, report: MigrationReport) -> None:
    table, target, primary = await _prepare_table(connection, source)
    sqlite, cursor = await asyncio.to_thread(_open_rows, source)
    copied = 0
    inserted = 0
    try:
        while batch := await asyncio.to_thread(cursor.fetchmany, BATCH_SIZE):
            rows, adjustments = [], []
            chunk_scopes = {}
            if source.name == "memory_fts":
                chunks = await connection.fetch(
                    "SELECT id,scope,scope_id FROM memory_chunks WHERE id=ANY($1::text[])",
                    [str(item["chunk_id"]) for item in batch],
                )
                chunk_scopes = {item["id"]: (item["scope"], item["scope_id"]) for item in chunks}
            for item in batch:
                raw = dict(item)
                row, reason = _transform(source, raw)
                if source.name == "memory_fts":
                    scope = chunk_scopes.get(str(row["chunk_id"]))
                    if scope is None:
                        raise MigrationConflict("Legacy memory search row has no owning chunk")
                    if raw.get("scope") or raw.get("project_id"):
                        if (row["scope"], row["scope_id"]) != scope:
                            raise MigrationConflict("Legacy memory search scope conflicts with its owning chunk")
                    else:
                        row["scope"], row["scope_id"] = scope
                        reason = "Mapped legacy memory search visibility from its owning chunk"
                row = {key: _coerce(value, target[key]) for key, value in row.items()}
                rows.append(row)
                if reason:
                    adjustment_id = hashlib.sha256(_encoded([source.source, source.name, raw]).encode()).hexdigest()
                    adjustments.append((adjustment_id, source.source, source.name, _encoded(raw), reason))
            inserted += await _write_batch(connection, table, target, primary, rows)
            if adjustments:
                await connection.executemany(
                    "INSERT INTO legacy_migration_adjustments VALUES ($1,$2,$3,$4::jsonb,$5) ON CONFLICT DO NOTHING", adjustments,
                )
            copied += len(rows)
            report.adjusted += len(adjustments)
            report.paused += sum(item[4].startswith(("Schedule disabled", "Interrupted", "Workflow triggers disabled")) for item in adjustments)
    finally:
        await asyncio.to_thread(sqlite.close)
    if copied != source.count:
        raise MigrationConflict(f"Row count mismatch in {table}")
    if table == "alert_records":
        # The row identifier is a non-PK cursor. Advance its sequence only
        # after importing explicit legacy IDs; never move an existing one back.
        sequence = await connection.fetchval("SELECT pg_get_serial_sequence($1,'rowid')", _ident(table))
        if sequence:
            await connection.execute(
                f"SELECT setval($1, GREATEST(COALESCE((SELECT MAX(rowid) FROM {_ident(table)}), 1), nextval($1)), true)", sequence,
            )
    report.tables.append({"source": source.source, "table": source.name, "target": table,
                          "rows": copied, "inserted": inserted, "unchanged": copied - inserted, "verified": copied})
    report.verified += copied
    report.inserted += inserted
    report.unchanged += copied - inserted


def _workflow_projection(key: str, value: dict):
    from flocks.workflow.store import WorkflowStore, _WORKFLOW_KV_PREFIXES
    if key.startswith("workflow_execution_step/"):
        _, execution_id, step = key.split("/", 2)
        rows = WorkflowStore._step_rows(execution_id, [(int(step), value)])
        return "workflow_execution_steps", dict(zip(WorkflowStore._STEP_COLUMNS, rows[0]))
    if key.startswith("workflow_execution/"):
        payload = dict(value)
        # Stable fallback timestamps make a second import identical.
        payload["startedAt"] = payload.get("startedAt") or 1
        payload["updatedAt"] = payload.get("updatedAt") or payload["startedAt"]
        _, _, row = WorkflowStore._execution_row(payload)
        return "workflow_executions", dict(zip(WorkflowStore._EXECUTION_COLUMNS, row))
    if key.startswith("workflow/") and key.endswith("/stats"):
        def number(camel, snake, cast=int):
            return cast(value.get(camel) or value.get(snake) or 0)
        return "workflow_stats", {
            "workflow_id": key[len("workflow/"):-len("/stats")],
            "call_count": number("callCount", "call_count"), "success_count": number("successCount", "success_count"),
            "error_count": number("errorCount", "error_count"), "total_runtime": number("totalRuntime", "total_runtime", float),
            "avg_runtime": number("avgRuntime", "avg_runtime", float), "thumbs_up": number("thumbsUp", "thumbs_up"),
            "thumbs_down": number("thumbsDown", "thumbs_down"), "updated_at": number("updatedAt", "updated_at"),
        }
    if key.startswith(("workflow_integration_config/", "workflow_kafka_config/", "workflow_poller_config/", "workflow_syslog_config/")):
        prefix, workflow = key.split("/", 1)
        kind = (value.get("kind") or "workflow.integration-config") if prefix == "workflow_integration_config" else prefix
        return "workflow_configs", {"workflow_id": workflow, "kind": kind, "version": value.get("version"),
                                    "config": _encoded(value), "updated_at": value.get("updatedAt") or 0}
    if key.startswith(_WORKFLOW_KV_PREFIXES):
        return "workflow_kv", {"key": key, "value": _encoded(value), "type": "json",
                              "created_at": "1970-01-01T00:00:00+00:00", "updated_at": "1970-01-01T00:00:00+00:00"}
    return None


async def _project_legacy_workflows(connection, sources, report):
    """Build current workflow tables from old KV rows, retaining originals.

    A canonical relational row takes precedence over its older KV copy. Both
    original source forms have already been copied and verified separately.
    """
    primary_keys = {"workflow_executions": ["id"], "workflow_execution_steps": ["exec_id", "step_index"],
                    "workflow_stats": ["workflow_id"], "workflow_configs": ["workflow_id", "kind"], "workflow_kv": ["key"]}
    schemas = {}
    for source in sources:
        if source.name != "storage":
            continue
        sqlite, cursor = await asyncio.to_thread(_open_rows, source)
        projected = 0
        try:
            while batch := await asyncio.to_thread(cursor.fetchmany, BATCH_SIZE):
                groups = {}
                adjustments = []
                for item in batch:
                    raw = dict(item)
                    if not str(raw["key"]).startswith("workflow"):
                        continue
                    value = json.loads(raw["value"])
                    if not isinstance(value, dict):
                        continue
                    projection = _workflow_projection(raw["key"], value)
                    if projection is None:
                        continue
                    table, row = projection
                    descriptor = TableSource(source.source, source.snapshot, table, [], primary_keys[table], 0, False)
                    row, reason = _transform(descriptor, row)
                    groups.setdefault(table, []).append(row)
                    if reason:
                        adjustment_id = hashlib.sha256(_encoded([source.source, raw]).encode()).hexdigest()
                        adjustments.append((adjustment_id, source.source, table, _encoded(raw), reason))
                for table, rows in groups.items():
                    if table not in schemas:
                        schema = await connection.fetch(
                            "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=$1", table,
                        )
                        schemas[table] = {r["column_name"]: r["data_type"] for r in schema}
                    target = schemas[table]
                    rows = [{key: _coerce(value, target[key]) for key, value in row.items()} for row in rows]
                    projected += await _write_batch(connection, table, target, primary_keys[table], rows, prefer_existing=True)
                if adjustments:
                    await connection.executemany("INSERT INTO legacy_migration_adjustments VALUES ($1,$2,$3,$4::jsonb,$5) ON CONFLICT DO NOTHING", adjustments)
                    report.paused += len(adjustments)
                    report.adjusted += len(adjustments)
        finally:
            await asyncio.to_thread(sqlite.close)
        report.tables.append({"source": source.source, "table": "storage", "action": "workflow projection; canonical relational rows take precedence", "projected": projected})


async def migrate(paths: list[Path], *, report_dir: Path, dsn: str | None = None, apply: bool = False) -> MigrationReport:
    report = MigrationReport()
    # Each run retains its own evidence; never overwrite an older snapshot.
    output = report_dir / ("migration-" + uuid4().hex)
    output.mkdir(parents=True, mode=0o700)
    output.chmod(0o700)
    (output / ".flocks-migration-evidence").touch(mode=0o600, exist_ok=False)
    connection = None
    try:
        sources = await asyncio.to_thread(discover_sources, paths, exclude=(report_dir,))
        if not sources:
            raise ValueError("No SQLite databases found in the explicit source paths")
        tables = []
        for source in sources:
            metadata, definitions = await asyncio.to_thread(_snapshot, source, output)
            report.sources.append(metadata)
            tables.extend(definitions)
        derived = [table for table in tables if table.derived]
        report.tables.extend({"source": t.source, "table": t.name, "rows": t.count,
                              "action": "derived SQLite index internals; preserved in snapshot"} for t in derived)
        tables = [table for table in tables if not table.derived]
        if any(table.name in {"tasks", "task_execution_records", "task_queue_refs"} for table in tables):
            raise ValueError(
                "Legacy task v1 tables require explicit format conversion first. "
                "Run scripts/migrate_legacy_task_tables.py --db SOURCE --target-db NEW_FILE, "
                "then import NEW_FILE. The source and target PostgreSQL were not modified."
            )
        if not apply:
            report.tables.extend({"source": t.source, "table": t.name, "target": _target_name(t), "rows": t.count,
                                  "action": "planned"} for t in tables)
        else:
            import asyncpg
            connection = await asyncpg.connect(dsn or load_settings().dsn, timeout=30, command_timeout=120)
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(hashtext('flocks.offline.migration'))")
                await _bootstrap(connection)
                # Application FKs are parent-first. Generic extension tables
                # preserve data without executing SQLite triggers or code.
                priority = {"users": 0, "task_schedulers": 0, "device_groups": 0, "storage": 0, "memory_files": 0,
                            "device_integrations": 1, "task_executions": 1,
                            "device_tool_settings": 2, "task_execution_queue_refs": 2}
                tables.sort(key=lambda t: (priority.get(t.name, 1), t.name, t.source))
                for table in tables:
                    await _copy_table(connection, table, report)
                if any(table.name == "alert_records" for table in tables):
                    duplicate = await connection.fetchval(
                        "SELECT 1 FROM alert_records WHERE dedup_key IS NOT NULL AND dedup_key<>'' "
                        "GROUP BY dedup_key HAVING COUNT(*)>1 LIMIT 1"
                    )
                    if duplicate:
                        raise MigrationConflict("Legacy alert dedup_key conflict requires review; all source rows were retained")
                    await connection.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_records_first_seen_dedup_key "
                        "ON alert_records(dedup_key) WHERE dedup_key IS NOT NULL AND dedup_key<>''"
                    )
                await _project_legacy_workflows(connection, tables, report)
                for source in report.sources:
                    if await asyncio.to_thread(_fingerprint, Path(source["path"])) != source["fingerprint"]:
                        raise MigrationConflict("A source changed during migration; target transaction rolled back")
                report.applied = True
                await connection.execute("INSERT INTO legacy_migration_runs (id,report) VALUES ($1,$2::jsonb)",
                                         uuid4().hex, _encoded(asdict(report)))
    except Exception as exc:
        report.applied = False
        report.inserted = report.verified = report.unchanged = report.paused = report.adjusted = 0
        for table in report.tables:
            if "verified" in table:
                table["rolledBack"] = True
                table["inserted"] = table["unchanged"] = table["verified"] = 0
        # Driver errors can contain complete data values or credentials.
        report.errors.append(str(exc) if isinstance(exc, (MigrationConflict, ValueError)) else type(exc).__name__)
    finally:
        if connection is not None:
            await connection.close()
        report_path = output / "report.json"
        report_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.chmod(0o600)
    return report


def main() -> int:
    # Run on its own (python -m flocks.db.migrate): connection errors carry
    # DSNs, and the database drivers log through the standard library.
    from flocks.utils.log import install_log_record_scrubber

    install_log_record_scrubber()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True, help="Explicit legacy database or directory (repeatable)")
    parser.add_argument("--report-dir", type=Path, required=True, help="Private directory for retained snapshots and reports")
    parser.add_argument("--apply", action="store_true", help="Copy to configured PG; default only inventories and snapshots")
    args = parser.parse_args()
    report = asyncio.run(migrate(args.source, report_dir=args.report_dir, apply=args.apply))
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
