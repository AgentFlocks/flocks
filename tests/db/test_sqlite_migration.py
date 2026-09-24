"""Offline importer acceptance against real PostgreSQL and immutable sources."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import asyncpg
import pytest

from flocks.db.migrate import migrate

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="Requires isolated PostgreSQL")]


def legacy_file(directory: Path) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / "legacy.db"
    with sqlite3.connect(path) as source:
        source.executescript("""
            CREATE TABLE storage (key TEXT PRIMARY KEY, value TEXT NOT NULL, type TEXT NOT NULL);
            CREATE TABLE opaque_plugin (code TEXT PRIMARY KEY, binary_value BLOB, counter INTEGER);
            CREATE TABLE append_events (text TEXT, created REAL);
            CREATE TABLE alert_records (row_id TEXT PRIMARY KEY, record_json TEXT, asset_date TEXT, event_time INTEGER);
            CREATE TABLE task_schedulers (id TEXT PRIMARY KEY,title TEXT,trigger TEXT,status TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE task_executions (id TEXT PRIMARY KEY,scheduler_id TEXT,title TEXT,status TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE task_execution_queue_refs (id TEXT PRIMARY KEY,execution_id TEXT,status TEXT,created_at TEXT);
        """)
        source.executemany("INSERT INTO storage VALUES (?,?,?)", [
            ("session:p:s1", json.dumps({"id": "s1", "title": "中文会话"}), "session"),
            ("message:s1", json.dumps([{"id": "m1", "role": "user", "time": {"created": 1700000000000}}]), "message"),
            ("message_parts:s1:m1", '[{"type":"text","text":"保留内容"}]', "message_part"),
        ])
        source.execute("INSERT INTO opaque_plugin VALUES (?,?,?)", ("alpha", b"\x00\xff", 1700000000000))
        source.executemany("INSERT INTO append_events VALUES (?,?)", [("same", 1.5), ("same", 1.5)])
        source.execute("INSERT INTO alert_records VALUES (?,?,?,?)", ("a1", '{"id":"a1","priority":2}', "2026-09-23", 1700000000))
        source.execute("INSERT INTO task_schedulers VALUES (?,?,?,?,?,?)", ("sch", "schedule", "{}", "active", "2026-01-01", "2026-01-01"))
        source.execute("INSERT INTO task_executions VALUES (?,?,?,?,?,?)", ("exe", "sch", "execution", "running", "2026-01-01", "2026-01-01"))
        source.execute("INSERT INTO task_execution_queue_refs VALUES (?,?,?,?)", ("ref", "exe", "running", "2026-01-01"))
    return path


async def test_dry_run_is_read_only_and_needs_no_dsn(tmp_path):
    source = legacy_file(tmp_path / "source")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = await migrate([source], report_dir=tmp_path / "reports")
    assert report.ok and not report.applied
    assert report.sources and report.tables
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    snapshot = Path(report.sources[0]["snapshot"])
    assert snapshot.is_file() and snapshot.stat().st_mode & 0o077 == 0


async def test_complete_migration_verifies_every_row_and_reruns(tmp_path):
    source = legacy_file(tmp_path / "source")
    original = source.read_bytes()
    dsn = os.environ["FLOCKS_DB_DSN"]
    report = await migrate([source], report_dir=tmp_path / "reports", dsn=dsn, apply=True)
    assert report.ok, report.errors
    assert report.applied and report.verified == report.inserted == 10
    assert report.paused == 3
    assert report.adjusted == 4
    conn = await asyncpg.connect(dsn)
    try:
        assert json.loads(await conn.fetchval("SELECT value FROM runtime_state WHERE key='session:p:s1'"))["title"] == "中文会话"
        assert await conn.fetchval("SELECT binary_value FROM opaque_plugin WHERE code='alpha'") == b"\x00\xff"
        assert await conn.fetchval("SELECT COUNT(*) FROM append_events") == 2
        assert await conn.fetchval("SELECT status FROM task_schedulers WHERE id='sch'") == "disabled"
        assert await conn.fetchval("SELECT status FROM task_executions WHERE id='exe'") == "failed"
        assert await conn.fetchval("SELECT status FROM task_execution_queue_refs WHERE id='ref'") == "failed"
        assert await conn.fetchval("SELECT COUNT(*) FROM legacy_migration_adjustments") == 4
        assert await conn.fetchval("INSERT INTO alert_records(row_id,record_json) VALUES('a2','{}') RETURNING rowid") > 1
    finally:
        await conn.close()
    repeat = await migrate([source], report_dir=tmp_path / "reports", dsn=dsn, apply=True)
    assert repeat.ok, repeat.errors
    assert repeat.applied and repeat.inserted == 0 and repeat.unchanged == 10
    assert source.read_bytes() == original


async def test_conflict_rolls_back_all_new_rows(tmp_path):
    source = legacy_file(tmp_path / "source")
    dsn = os.environ["FLOCKS_DB_DSN"]
    first = await migrate([source], report_dir=tmp_path / "reports", dsn=dsn, apply=True)
    assert first.ok, first.errors
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE storage SET value=? WHERE key=?", ('{"new":true}', "session:p:s1"))
        connection.execute("INSERT INTO storage VALUES (?,?,?)", ("new-key", "{}", "json"))
    second = await migrate([source], report_dir=tmp_path / "reports", dsn=dsn, apply=True)
    assert not second.ok and not second.applied and second.inserted == 0
    assert "conflict" in second.errors[0].lower()
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT value FROM runtime_state WHERE key='new-key'") is None
        assert "中文会话" in await conn.fetchval("SELECT value FROM runtime_state WHERE key='session:p:s1'")
    finally:
        await conn.close()


async def test_missing_source_never_creates_a_sqlite_file(tmp_path):
    path = tmp_path / "not-present.db"
    report = await migrate([path], report_dir=tmp_path / "reports", apply=True)
    assert not report.ok and not path.exists()


async def test_same_keys_in_two_sources_are_not_silently_overwritten(tmp_path):
    first = legacy_file(tmp_path / "one")
    second = legacy_file(tmp_path / "two")
    with sqlite3.connect(second) as connection:
        connection.execute("UPDATE storage SET value=? WHERE key=?", ('{"title":"different"}', "session:p:s1"))
    report = await migrate([first, second], report_dir=tmp_path / "reports", apply=True)
    assert not report.ok and not report.applied


async def test_large_table_is_copied_across_batches(tmp_path):
    source = tmp_path / "batch.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE bulk_records (id TEXT PRIMARY KEY, value TEXT)")
        connection.executemany("INSERT INTO bulk_records VALUES (?,?)", [(str(i), f"record-{i}") for i in range(721)])
    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert report.ok, report.errors
    assert report.verified == report.inserted == 721


async def test_wal_snapshot_does_not_create_or_change_source_siblings(tmp_path):
    import shutil

    original = tmp_path / "writer.db"
    writer = sqlite3.connect(original)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE preserved(id TEXT PRIMARY KEY,value TEXT)")
        writer.execute("INSERT INTO preserved VALUES ('one','still in WAL')")
        writer.commit()
        source = tmp_path / "readonly-source.db"
        shutil.copyfile(original, source)
        shutil.copyfile(str(original) + "-wal", str(source) + "-wal")
        before = {suffix: Path(str(source) + suffix).read_bytes() for suffix in ("", "-wal")}
        report = await migrate([source], report_dir=tmp_path / "reports")
        assert report.ok, report.errors
        assert next(table for table in report.tables if table["table"] == "preserved")["rows"] == 1
        assert not Path(str(source) + "-shm").exists()
        assert before == {suffix: Path(str(source) + suffix).read_bytes() for suffix in before}
    finally:
        writer.close()


async def test_storage_timestamps_survive_import_and_repeat(tmp_path):
    source = tmp_path / "timestamp.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE storage(key TEXT PRIMARY KEY,value TEXT,type TEXT,created_at TEXT,updated_at TEXT)")
        connection.execute("INSERT INTO storage VALUES (?,?,?,?,?)", (
            "example", '{}', 'json', '2024-01-02T03:04:05+00:00', '2025-02-03T04:05:06+00:00',
        ))
    first = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert first.ok, first.errors
    repeat = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert repeat.ok and repeat.unchanged == 1, repeat.errors
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        row = await connection.fetchrow("SELECT created_at,updated_at FROM runtime_state WHERE key='example'")
        assert row[0].isoformat() == '2024-01-02T03:04:05+00:00'
        assert row[1].isoformat() == '2025-02-03T04:05:06+00:00'
    finally:
        await connection.close()


async def test_v1_task_tables_require_explicit_conversion_before_pg_write(tmp_path):
    source = tmp_path / "v1.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY,type TEXT)")
        connection.execute("INSERT INTO tasks VALUES ('legacy','once')")
    before = source.read_bytes()
    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert not report.ok and not report.applied
    assert "--target-db NEW_FILE" in report.errors[0]
    assert source.read_bytes() == before
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        assert not await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='tasks')"
        )
    finally:
        await connection.close()


async def test_corrupt_nonempty_database_in_directory_is_not_silently_skipped(tmp_path):
    legacy_file(tmp_path / "source")
    (tmp_path / "source" / "damaged.db").write_bytes(b'not a database')
    report = await migrate([tmp_path / "source"], report_dir=tmp_path / "reports")
    assert not report.ok and "Nonempty source is not" in report.errors[0]


async def test_blob_declared_columns_preserve_text_and_binary_json(tmp_path):
    source = tmp_path / "mixed-embeddings.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE legacy_vectors(id TEXT PRIMARY KEY,embedding BLOB)")
        connection.executemany("INSERT INTO legacy_vectors VALUES (?,?)", [
            ("text", '[1.0,0.0]'), ("bytes", b'[0.0,1.0]'),
        ])
    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert report.ok and report.verified == 2, report.errors
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        rows = await connection.fetch("SELECT id,embedding FROM legacy_vectors ORDER BY id")
        assert dict(rows) == {"bytes": b'[0.0,1.0]', "text": b'[1.0,0.0]'}
    finally:
        await connection.close()
