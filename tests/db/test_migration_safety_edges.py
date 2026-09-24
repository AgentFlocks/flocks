"""Migration rejects lossy values and preserves application isolation on PG."""

import json
import os
import sqlite3

import asyncpg
import pytest

from flocks.db.migrate import migrate
from flocks.storage import Storage, fts_search
from flocks.workflow.triggers.compat import legacy_schedule_trigger_from_config

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="Requires isolated PostgreSQL"),
]


async def test_fractional_values_in_integer_column_abort_the_whole_import(tmp_path):
    source = tmp_path / "mixed-numeric.db"
    with sqlite3.connect(source) as db:
        db.executescript("""
            CREATE TABLE a_first_table(id TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE mixed_numeric(id TEXT PRIMARY KEY, amount INTEGER);
        """)
        db.execute("INSERT INTO a_first_table VALUES ('one','must roll back')")
        db.executemany("INSERT INTO mixed_numeric VALUES (?,?)", [
            ("positive", 1.75), ("negative", -2.5), ("whole", 3),
        ])
        assert dict(db.execute("SELECT id,typeof(amount) FROM mixed_numeric")) == {
            "positive": "real", "negative": "real", "whole": "integer",
        }
    original = source.read_bytes()

    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)

    assert not report.ok and not report.applied
    assert report.inserted == report.verified == 0
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_name=ANY($1::text[])",
            ["a_first_table", "mixed_numeric"],
        ) == 0
    finally:
        await connection.close()
    assert source.read_bytes() == original


async def test_report_directory_inside_source_is_excluded_from_repeat_discovery(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "append.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE append_events(text TEXT)")
        db.execute("INSERT INTO append_events VALUES ('one event')")
    original = source.read_bytes()
    report_dir = source_dir / "reports"

    first = await migrate([source_dir], report_dir=report_dir, apply=True)
    second = await migrate([source_dir], report_dir=report_dir, apply=True)

    assert first.ok and second.ok, (first.errors, second.errors)
    assert len(first.sources) == len(second.sources) == 1
    assert first.inserted == 1
    assert second.inserted == 0 and second.unchanged == 1
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        assert await connection.fetchval("SELECT COUNT(*) FROM append_events") == 1
    finally:
        await connection.close()
    assert source.read_bytes() == original


def _legacy_project_memory(tmp_path, fts_visibility):
    source = tmp_path / "project-memory.db"
    path = "projects/private/secret.md"
    with sqlite3.connect(source) as db:
        db.executescript("""
            CREATE TABLE memory_files(path TEXT PRIMARY KEY,project_id TEXT,source TEXT,hash TEXT,mtime REAL,size INTEGER,indexed_at REAL);
            CREATE TABLE memory_chunks(id TEXT PRIMARY KEY,path TEXT,project_id TEXT,source TEXT,start_line INTEGER,end_line INTEGER,hash TEXT,text TEXT,created_at REAL,updated_at REAL);
        """)
        scope_columns, scope_values = {
            "legacy-project": (",project_id TEXT", ["private"]),
            "absent": ("", []),
            "conflicting-global": (",scope TEXT,scope_id TEXT", ["global", ""]),
        }[fts_visibility]
        db.execute("CREATE TABLE memory_fts(chunk_id TEXT PRIMARY KEY,path TEXT" + scope_columns +
                   ",source TEXT,start_line INTEGER,end_line INTEGER,text TEXT)")
        db.execute("INSERT INTO memory_files VALUES (?,?,?,?,?,?,?)", (path, "private", "memory", "h", 1, 12, 1))
        db.execute("INSERT INTO memory_chunks VALUES (?,?,?,?,?,?,?,?,?,?)", ("private-chunk", path, "private", "memory", 1, 1, "h", "privateword content", 1, 1))
        values = ["private-chunk", path, *scope_values, "memory", 1, 1, "privateword content"]
        db.execute("INSERT INTO memory_fts VALUES (" + ",".join("?" for _ in values) + ")", values)
    return source, path


@pytest.mark.parametrize("fts_visibility", ["legacy-project", "absent"])
async def test_prescope_project_memory_is_visible_only_in_its_project_after_bootstrap(tmp_path, fts_visibility):
    source, path = _legacy_project_memory(tmp_path, fts_visibility)
    original = source.read_bytes()

    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)

    assert report.ok and report.verified == 3, report.errors
    await Storage.init()
    own_hits = await fts_search(Storage.get_db_path(), "private", "privateword")
    other_hits = await fts_search(Storage.get_db_path(), "unrelated-project", "privateword")
    assert [hit["id"] for hit in own_hits] == ["private-chunk"]
    assert other_hits == []
    async with Storage.transaction() as db:
        scopes = await db.fetch_all("""
            SELECT scope,scope_id FROM memory_files WHERE path=?
            UNION ALL SELECT scope,scope_id FROM memory_chunks WHERE id=?
            UNION ALL SELECT scope,scope_id FROM memory_fts WHERE chunk_id=?
        """, (path, "private-chunk", "private-chunk"))
        assert scopes == [("project", "private")] * 3
    assert source.read_bytes() == original


async def test_explicit_keyword_index_scope_cannot_widen_chunk_visibility(tmp_path):
    source, _ = _legacy_project_memory(tmp_path, "conflicting-global")
    original = source.read_bytes()
    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert not report.ok and not report.applied
    assert report.inserted == report.verified == 0
    assert source.read_bytes() == original


@pytest.mark.parametrize("scope,include_scope_id", [("unknown-scope", True), ("project", False)])
async def test_invalid_explicit_memory_scope_is_rejected(tmp_path, scope, include_scope_id):
    source = tmp_path / "invalid-scope.db"
    with sqlite3.connect(source) as db:
        scope_id_column = ",scope_id TEXT" if include_scope_id else ""
        db.execute(
            "CREATE TABLE memory_files(path TEXT PRIMARY KEY,scope TEXT" + scope_id_column +
            ",source TEXT,hash TEXT,mtime REAL,size INTEGER,indexed_at REAL)"
        )
        values = ["note.md", scope, *(["private"] if include_scope_id else []), "memory", "h", 1, 1, 1]
        db.execute("INSERT INTO memory_files VALUES (" + ",".join("?" for _ in values) + ")", values)
    original = source.read_bytes()

    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)

    assert not report.ok and not report.applied
    assert report.inserted == report.verified == 0
    assert source.read_bytes() == original


async def test_all_runtime_truthy_workflow_switches_are_disabled(tmp_path):
    source = tmp_path / "enabled.db"
    switches = {"bool": True, "one": 1, "other_number": 2, "true_text": "true", "false_text": "false"}
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE workflow_configs(workflow_id TEXT,kind TEXT,config TEXT,updated_at INTEGER,PRIMARY KEY(workflow_id,kind))")
        db.executemany("INSERT INTO workflow_configs VALUES (?,?,?,?)", [
            (name, "workflow_poller_config", json.dumps({"enabled": value, "intervalSeconds": 15}), 1)
            for name, value in switches.items()
        ])
    original = source.read_bytes()

    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)

    assert report.ok, report.errors
    assert report.paused == len(switches)
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        rows = await connection.fetch("SELECT workflow_id,config FROM workflow_configs ORDER BY workflow_id")
        originals = await connection.fetchval("SELECT COUNT(*) FROM legacy_migration_adjustments WHERE table_name='workflow_configs'")
        assert originals == len(switches)
    finally:
        await connection.close()
    for row in rows:
        config = json.loads(row["config"])
        assert config["enabled"] is False
        assert legacy_schedule_trigger_from_config(config).enabled is False
    assert source.read_bytes() == original


async def test_absent_and_falsy_workflow_switches_stay_disabled(tmp_path):
    source = tmp_path / "disabled.db"
    configurations = [{}, {"enabled": None}, {"enabled": ""}, {"enabled": 0}, {"enabled": False}]
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE workflow_configs(workflow_id TEXT,kind TEXT,config TEXT,updated_at INTEGER,PRIMARY KEY(workflow_id,kind))")
        db.executemany("INSERT INTO workflow_configs VALUES (?,?,?,?)", [
            (str(index), "workflow_poller_config", json.dumps({**config, "intervalSeconds": 15}), 1)
            for index, config in enumerate(configurations)
        ])
    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert report.ok, report.errors
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        configs = [json.loads(row[0]) for row in await connection.fetch("SELECT config FROM workflow_configs")]
    finally:
        await connection.close()
    assert len(configs) == len(configurations)
    assert all(legacy_schedule_trigger_from_config(config).enabled is False for config in configs)
