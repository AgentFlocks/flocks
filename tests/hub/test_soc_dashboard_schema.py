import importlib.util
import json
import sqlite3
import sys
from datetime import datetime
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

    assert payload["workflowStats"] == {"callCount": 42, "latestStartedAt": 0}
    assert payload["workflowEvents"] == []
    assert payload["events"] == []

    with sqlite3.connect(workflow_db) as conn:
        conn.execute(
            "UPDATE workflow_stats SET call_count = 43 WHERE workflow_id = ?",
            ("stream_alert_denoise",),
        )
        conn.commit()

    assert handlers._get_activity({"bootstrap": "latest"})["workflowStats"] == {
        "callCount": 43,
        "latestStartedAt": 0,
    }


def test_soc_dashboard_workflow_progress_marks_unavailable_database(tmp_path: Path):
    handlers = _load_dashboard_handlers()
    handlers.WORKFLOW_DB = tmp_path / "missing-workflow.db"

    assert handlers._get_workflow_progress("stream_alert_denoise") == {
        "callCount": None,
        "latestStartedAt": None,
    }


def test_soc_dashboard_uses_workflow_stats_and_soc_unique_for_reduction(tmp_path: Path):
    start_time = 1783987200
    end_time = start_time + 3600
    asset_date = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d")
    soc_db = tmp_path / "soc.db"
    with sqlite3.connect(soc_db) as conn:
        conn.execute(
            """
            CREATE TABLE alert_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_json TEXT NOT NULL,
                asset_date TEXT NOT NULL,
                event_time INTEGER NOT NULL,
                is_duplicate INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        for offset, is_duplicate in ((60, 0), (120, 1)):
            conn.execute(
                "INSERT INTO alert_records(record_json, asset_date, event_time, is_duplicate) "
                "VALUES (?, ?, ?, ?)",
                (
                    json.dumps({"_source_type": "soc"}),
                    asset_date,
                    start_time + offset,
                    is_duplicate,
                ),
            )
        conn.commit()

    workflow_db = tmp_path / "workflow.db"
    with sqlite3.connect(workflow_db) as conn:
        conn.execute(
            """
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                input_params TEXT NOT NULL,
                output_results TEXT NOT NULL,
                started_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_stats (
                workflow_id TEXT PRIMARY KEY,
                call_count INTEGER NOT NULL,
                success_count INTEGER NOT NULL,
                error_count INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE soc_dashboard_workflow_stats_samples (
                workflow_id TEXT NOT NULL,
                sampled_at INTEGER NOT NULL,
                call_count INTEGER NOT NULL,
                success_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (workflow_id, sampled_at)
            )
            """
        )
        conn.execute(
            "INSERT INTO workflow_stats VALUES (?, ?, ?, ?, ?)",
            ("stream_alert_denoise", 100, 99, 1, (start_time + 200) * 1000),
        )
        conn.executemany(
            "INSERT INTO soc_dashboard_workflow_stats_samples VALUES (?, ?, ?, ?, ?)",
            [
                ("stream_alert_denoise", (start_time + 100) * 1000, 60, 60, 0),
                ("stream_alert_denoise", (start_time + 200) * 1000, 100, 99, 1),
            ],
        )

        def insert_execution(
            execution_id,
            started_at,
            raw,
            normalized,
            filtered,
            unique,
            source,
            threat,
            *,
            summarized_source_counts=False,
            empty_output=False,
        ):
            source_counts = (
                {"_type": "dict", "keys": [source, "skyeye"]}
                if summarized_source_counts
                else {source: normalized}
            )
            output = {
                "unique_alerts": [{"threat_name": threat, "_source_type": source}],
                "stats": {
                    "raw_count": raw,
                    "normalized_count": normalized,
                    "after_filter_count": filtered,
                    "after_dedup_count": unique,
                    "filter_removed_count": max(normalized - filtered, 0),
                    "dedup_removed_count": max(filtered - unique, 0),
                    "normalize_type_counts": source_counts,
                    "lsh_total_clusters": 3,
                },
            }
            conn.execute(
                "INSERT INTO workflow_executions VALUES (?, ?, 'success', ?, ?, ?)",
                (
                    execution_id,
                    "stream_alert_denoise",
                    json.dumps({"source_log_type": source}),
                    json.dumps({} if empty_output else output),
                    started_at * 1000,
                ),
            )

        insert_execution("outside", start_time - 60, 100, 100, 90, 80, "tdp", "Outside")
        insert_execution(
            "first",
            start_time + 100,
            10,
            9,
            8,
            6,
            "tdp",
            "SQL injection",
            summarized_source_counts=True,
            empty_output=True,
        )
        insert_execution("second", start_time + 200, 5, 5, 5, 4, "skyeye", "Malware")
        duplicate_input = {
            "syslog_message": {
                "app_name": "tdp",
                "message": json.dumps(
                    {
                        "id": "syslog-duplicate",
                        "net_real_src_ip": "10.10.10.10",
                        "net_dest_ip": "192.168.10.10",
                        "threat_name": "Syslog duplicate",
                    }
                ),
            }
        }
        duplicate_output = {
            "unique_alerts": {
                "_type": "list",
                "count": 1,
                "preview": [{"_type": "dict", "keys": ["id", "threat_name"]}],
            },
            "stats": {
                "raw_count": 1,
                "normalized_count": 1,
                "after_filter_count": 1,
                "after_dedup_count": 1,
                "dedup_removed_count": 0,
            },
            "is_duplicate": True,
        }
        conn.execute(
            "UPDATE workflow_executions SET input_params = ?, output_results = ? WHERE id = ?",
            (json.dumps(duplicate_input), json.dumps(duplicate_output), "second"),
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.DEFAULT_SQLITE_DB = soc_db
    handlers.WORKFLOW_DB = workflow_db
    handlers._schema_ready.clear()

    stats = handlers._get_stats(
        {"startTime": str(start_time), "endTime": str(end_time), "force": "1"}
    )

    expected_denoise = {
        "totalRaw": 100,
        "totalNormalized": 100,
        "afterFilter": 100,
        "totalUnique": 1,
        "filterRemoved": 0,
        "dedupRemoved": 99,
        "duplicates": 99,
    }
    assert {
        key: stats["denoise"][key]
        for key in expected_denoise
    } == expected_denoise
    assert stats["denoise"]["duplicateRate"] == 0.99
    assert stats["denoise"]["dedupRate"] == 0.99
    assert stats["pipeline"]["raw"] == 100
    assert stats["pipeline"]["unique"] == 1
    assert sum(stats["timeline"]["denoiseRaw"]) == 100
    assert sum(stats["timeline"]["denoiseUnique"]) == 1
    assert stats["sourceStatus"]["workflowStats"]["callCount"] == 100
    assert {source["key"]: source["value"] for source in stats["sources"]} == {
        "ndr": 100,
        "edr": 0,
        "waf": 0,
        "ids": 0,
        "cloud": 0,
        "vuln": 0,
        "other": 0,
    }

    activity = handlers._get_activity(
        {
            "bootstrap": "latest",
            "startTime": str(start_time),
            "endTime": str(end_time),
        }
    )
    assert activity["workflowStats"] == {
        "callCount": 100,
        "latestStartedAt": (start_time + 200) * 1000,
    }
    assert [event["alert"]["threatName"] for event in activity["workflowEvents"]] == [
        "Syslog duplicate",
        "降噪批次 · 原始 1 条",
    ]

    events = handlers._get_workflow_recent_events(
        "stream_alert_denoise",
        start_time,
        end_time,
    )
    assert [event["alert"]["threatName"] for event in events] == [
        "Syslog duplicate",
        "降噪批次 · 原始 1 条",
    ]
    assert events[0]["result"]["rawCount"] == 1
    assert events[0]["result"]["uniqueCount"] == 1
    assert events[0]["result"]["isDuplicate"] is True
    assert events[0]["alert"]["srcIp"] == "10.10.10.10"

    narrowed = handlers._get_stats(
        {
            "startTime": str(start_time + 150),
            "endTime": str(end_time),
            "force": "1",
        }
    )
    assert narrowed["denoise"]["totalRaw"] == 40
    assert narrowed["denoise"]["totalUnique"] == 0
    assert narrowed["denoise"]["duplicateRate"] == 1

    no_workflow_calls = handlers._get_stats(
        {
            "startTime": str(start_time + 50),
            "endTime": str(start_time + 90),
            "force": "1",
        }
    )
    assert no_workflow_calls["denoise"]["totalRaw"] == 0
    assert no_workflow_calls["denoise"]["totalUnique"] == 1
    assert no_workflow_calls["denoise"]["duplicateRate"] == 0
    assert next(
        source["value"] for source in no_workflow_calls["sources"] if source["key"] == "ndr"
    ) == 0
