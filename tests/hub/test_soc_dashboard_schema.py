import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


def _load_dashboard_handlers():
    handler_path = (
        Path(__file__).resolve().parents[2]
        / ".flocks"
        / "flockshub"
        / "plugins"
        / "webuis"
        / "soc_ui"
        / "soc_dashboard"
        / "api"
        / "handlers.py"
    )
    spec = importlib.util.spec_from_file_location("soc_dashboard_schema_test", handler_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def test_soc_dashboard_migrates_legacy_alert_records_schema(tmp_path: Path):
    db_path = tmp_path / "soc.db"
    first_record = {
        "_source_type": "tdp",
        "threat_name": "SQL injection",
        "triage_status": "ok",
        "_triage_persisted_at": "2026-07-14T13:00:00",
        "attack_verdict": "attack",
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE alert_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_json TEXT NOT NULL,
                asset_date TEXT NOT NULL,
                event_time INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO alert_records(record_json, asset_date, event_time) VALUES (?, ?, ?)",
            (json.dumps(first_record), "2026-07-14", 1784014800),
        )
        conn.execute(
            "CREATE TABLE soc_dashboard_meta (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO soc_dashboard_meta VALUES ('schema_version', '1')")
        conn.execute(
            "CREATE TABLE soc_dashboard_alert_facts "
            "(alert_row_id INTEGER PRIMARY KEY, row_key TEXT)"
        )
        conn.execute("INSERT INTO soc_dashboard_alert_facts VALUES (999, 'stale')")
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.DEFAULT_SQLITE_DB = db_path
    handlers._schema_ready.clear()

    assert handlers._ensure_sqlite_schema() is True

    second_record = {
        "_source_type": "hids",
        "threat_name": "Malware download",
        "triage_status": "ok",
        "_triage_persisted_at": "2026-07-14T13:01:00",
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO alert_records(record_json, asset_date, event_time) VALUES (?, ?, ?)",
            (json.dumps(second_record), "2026-07-14", 1784014860),
        )
        conn.commit()

    handlers._schema_ready.clear()
    assert handlers._ensure_sqlite_schema() is True

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(alert_records)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(alert_records)")}
        facts = conn.execute(
            "SELECT alert_row_id, row_key, source_type, threat_name, has_triage "
            "FROM soc_dashboard_alert_facts ORDER BY alert_row_id"
        ).fetchall()
        source_rows = conn.execute(
            "SELECT id, row_id, is_duplicate FROM alert_records ORDER BY id"
        ).fetchall()
        trigger_sql = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='trigger' AND name='soc_dashboard_fact_insert'"
        ).fetchone()[0]
        schema_version = conn.execute(
            "SELECT meta_value FROM soc_dashboard_meta WHERE meta_key='schema_version'"
        ).fetchone()[0]

    assert {
        "row_id",
        "record_id",
        "source_file",
        "line_number",
        "source_type",
        "threat_name",
        "is_duplicate",
    } <= columns
    assert {
        "idx_alert_records_asset_date",
        "idx_alert_records_event_time",
        "idx_alert_records_source_type",
        "idx_alert_records_threat_name",
        "idx_alert_records_row_id",
    } <= indexes
    assert source_rows == [(1, "1", 0), (2, "2", 0)]
    assert facts == [
        (1, "1", "tdp", "SQL injection", 1),
        (2, "2", "hids", "Malware download", 1),
    ]
    assert "COALESCE(NULLIF(NEW.row_id, ''), CAST(NEW.rowid AS TEXT))" in trigger_sql
    assert schema_version == "2"


def test_soc_dashboard_activity_exposes_live_denoise_workflow_progress(tmp_path: Path):
    workflow_db = tmp_path / "workflow.db"
    with sqlite3.connect(workflow_db) as conn:
        conn.execute(
            """
            CREATE TABLE workflow_stats (
                workflow_id TEXT PRIMARY KEY,
                call_count INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO workflow_stats VALUES (?, ?)",
            ("stream_alert_denoise", 42),
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.WORKFLOW_DB = workflow_db
    handlers.DEFAULT_SQLITE_DB = tmp_path / "missing-soc.db"
    handlers._activity_pruned_at = float("inf")

    payload = handlers._get_activity({"bootstrap": "latest"})

    assert payload["workflowStats"] == {"callCount": 42}
    assert payload["events"] == []

    with sqlite3.connect(workflow_db) as conn:
        conn.execute(
            "UPDATE workflow_stats SET call_count = 43 WHERE workflow_id = ?",
            ("stream_alert_denoise",),
        )
        conn.commit()

    assert handlers._get_activity({"bootstrap": "latest"})["workflowStats"] == {
        "callCount": 43
    }


def test_soc_dashboard_workflow_progress_marks_unavailable_database(tmp_path: Path):
    handlers = _load_dashboard_handlers()
    handlers.WORKFLOW_DB = tmp_path / "missing-workflow.db"

    assert handlers._get_workflow_progress("stream_alert_denoise") == {"callCount": None}
