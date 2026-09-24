"""All visible source columns are preserved and verified on real PostgreSQL."""

from datetime import datetime
import os
import sqlite3

import asyncpg
import pytest

from flocks.db.migrate import migrate

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="requires isolated PostgreSQL")]


@pytest.mark.parametrize("reverse", [False, True])
async def test_optional_timestamps_do_not_depend_on_first_row_or_batch(tmp_path, reverse):
    source = tmp_path / "timestamps.db"
    stamps = ("2024-01-02T03:04:05+00:00", "2025-02-03T04:05:06+00:00")
    inputs = [(f"row-{i}", "{}", "json", stamps[0] if i % 2 else None, stamps[1] if i % 3 else None)
              for i in range(605)]
    if reverse:
        inputs.reverse()
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE storage(key TEXT PRIMARY KEY,value TEXT,type TEXT,created_at TEXT,updated_at TEXT)")
        connection.executemany("INSERT INTO storage VALUES (?,?,?,?,?)", inputs)
    original = source.read_bytes()
    first = await migrate([source], report_dir=tmp_path / "first", apply=True)
    second = await migrate([source], report_dir=tmp_path / "second", apply=True)
    assert first.ok and second.ok, (first.errors, second.errors)
    assert first.inserted == first.verified == 605
    assert second.inserted == 0 and second.verified == second.unchanged == 605
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        actual = {row["key"]: row for row in await connection.fetch("SELECT key,created_at,updated_at FROM runtime_state WHERE key LIKE 'row-%'")}
        for key, _, _, created, updated in inputs:
            for name, stamp in (("created_at", created), ("updated_at", updated)):
                assert actual[key][name] is not None
                if stamp is not None:
                    assert actual[key][name] == datetime.fromisoformat(stamp)
    finally:
        await connection.close()
    assert source.read_bytes() == original


async def test_stored_and_virtual_generated_columns_are_preserved_as_snapshot_values(tmp_path):
    source = tmp_path / "generated.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE plugin_generated(id TEXT PRIMARY KEY,value INTEGER,"
                           "doubled INTEGER GENERATED ALWAYS AS(value*2) STORED,"
                           "label TEXT GENERATED ALWAYS AS(id || ':' || value) VIRTUAL)")
        connection.executemany("INSERT INTO plugin_generated(id,value) VALUES (?,?)", [("one", 21), ("two", None)])
    original = source.read_bytes()
    first = await migrate([source], report_dir=tmp_path / "first", apply=True)
    second = await migrate([source], report_dir=tmp_path / "second", apply=True)
    assert first.ok and second.ok, (first.errors, second.errors)
    assert first.verified == 2 and second.unchanged == 2
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        assert [tuple(row) for row in await connection.fetch("SELECT id,value,doubled,label FROM plugin_generated ORDER BY id")] == [
            ("one", 21, 42, "one:21"), ("two", None, None, None),
        ]
        # A mismatch in a formerly omitted generated column must abort a repeat.
        await connection.execute("UPDATE plugin_generated SET doubled=99 WHERE id='one'")
    finally:
        await connection.close()
    conflict = await migrate([source], report_dir=tmp_path / "conflict", apply=True)
    assert not conflict.ok and not conflict.applied
    assert conflict.inserted == conflict.verified == 0
    assert source.read_bytes() == original


async def test_preexisting_target_generated_column_fails_explicitly_without_overwrite(tmp_path):
    source = tmp_path / "generated.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE plugin_generated(id TEXT PRIMARY KEY,value INTEGER,doubled INTEGER GENERATED ALWAYS AS(value*2) STORED)")
        connection.execute("INSERT INTO plugin_generated(id,value) VALUES ('one',21)")
    original = source.read_bytes()
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        await connection.execute("CREATE TABLE plugin_generated(id TEXT PRIMARY KEY,value BIGINT,doubled BIGINT GENERATED ALWAYS AS(value*2) STORED)")
    finally:
        await connection.close()
    report = await migrate([source], report_dir=tmp_path / "report", apply=True)
    assert not report.ok and not report.applied and report.verified == 0
    assert source.read_bytes() == original
