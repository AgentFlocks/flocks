from __future__ import annotations

import ast
import datetime as datetime_module
import json
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "flockshub"
    / "plugins"
    / "workflows"
    / "stream_alert_triage"
    / "workflow.json"
)


def _concurrent_triage_code() -> str:
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return next(node["code"] for node in workflow["nodes"] if node["id"] == "concurrent_triage")


def _load_functions(*names: str) -> dict[str, object]:
    tree = ast.parse(_concurrent_triage_code())
    body = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    namespace: dict[str, object] = {
        "_datetime": datetime_module,
        "inputs": {"loaded_files": []},
        "json": json,
        "os": os,
        "re": re,
        "time": time,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(WORKFLOW_PATH), "exec"), namespace)
    return namespace


def test_soc_db_selects_only_verified_first_seen_unique_alerts() -> None:
    functions = _load_functions("_input_bool", "_select_first_seen_soc_alerts")
    select_alerts = functions["_select_first_seen_soc_alerts"]

    selected, stats = select_alerts(
        [
            {"dedup_key": "first", "is_duplicate": False, "id": "1"},
            {"dedup_key": "first", "is_duplicate": False, "id": "2"},
            {"dedup_key": "duplicate", "is_duplicate": True, "id": "3"},
            {"dedup_key": "string-false", "is_duplicate": "false", "id": "4"},
            {"dedup_key": "string-true", "is_duplicate": "true", "id": "5"},
            {"is_duplicate": False, "id": "missing-key"},
            {"dedup_key": "missing-flag", "id": "6"},
        ]
    )

    assert [alert["dedup_key"] for alert in selected] == ["first", "string-false"]
    assert stats == {
        "input_rows": 7,
        "first_seen_rows": 2,
        "skipped_not_first_seen_rows": 3,
        "skipped_missing_dedup_key_rows": 1,
        "skipped_repeated_dedup_key_rows": 1,
    }


def test_soc_db_persistence_uses_filtered_first_seen_alerts() -> None:
    tree = ast.parse(_concurrent_triage_code())
    write_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_triage_write_soc_db"
    ]

    assert len(write_calls) == 1
    assert isinstance(write_calls[0].args[1], ast.Name)
    assert write_calls[0].args[1].id == "first_seen_soc_alerts"


def test_soc_db_persistence_failure_is_reraised() -> None:
    tree = ast.parse(_concurrent_triage_code())
    persistence_try = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_triage_write_soc_db"
            for child in ast.walk(node)
        )
    )

    assert any(
        isinstance(child, ast.Raise)
        for handler in persistence_try.handlers
        for child in ast.walk(handler)
    )


def test_soc_db_writer_receives_only_selected_first_seen_alerts(tmp_path: Path) -> None:
    functions = _load_functions(
        "_input_bool",
        "_select_first_seen_soc_alerts",
        "_ensure_soc_db_schema",
        "_event_time_value",
        "_asset_date_value",
        "_source_type_value",
        "_record_id_value",
        "_stable_row_id",
        "_load_existing_soc_rows",
        "_merge_triage_record",
        "_triage_write_soc_db",
    )
    select_alerts = functions["_select_first_seen_soc_alerts"]
    write_soc_db = functions["_triage_write_soc_db"]
    alerts = [
        {"dedup_key": "first", "is_duplicate": False, "id": "1", "time": 1784026800},
        {"dedup_key": "first", "is_duplicate": False, "id": "2", "time": 1784026801},
        {"dedup_key": "duplicate", "is_duplicate": True, "id": "3", "time": 1784026802},
        {"dedup_key": "second", "is_duplicate": False, "id": "4", "time": 1784026803},
        {"is_duplicate": False, "id": "missing-key", "time": 1784026804},
    ]

    selected, stats = select_alerts(alerts)
    db_path = tmp_path / "soc.db"
    result = write_soc_db(str(db_path), selected, "first-seen-test")

    with sqlite3.connect(db_path) as connection:
        records = connection.execute(
            "SELECT is_duplicate, json_extract(record_json, '$.dedup_key') "
            "FROM alert_records ORDER BY event_time"
        ).fetchall()

    assert stats["first_seen_rows"] == 2
    assert result["rows"] == 2
    assert records == [(0, "first"), (0, "second")]


def test_soc_db_keeps_one_dedup_key_across_runs_and_preserves_first_event(tmp_path: Path) -> None:
    functions = _load_functions(
        "_input_bool",
        "_select_first_seen_soc_alerts",
        "_ensure_soc_db_schema",
        "_event_time_value",
        "_asset_date_value",
        "_source_type_value",
        "_record_id_value",
        "_stable_row_id",
        "_load_existing_soc_rows",
        "_merge_triage_record",
        "_triage_write_soc_db",
    )
    select_alerts = functions["_select_first_seen_soc_alerts"]
    write_soc_db = functions["_triage_write_soc_db"]
    db_path = tmp_path / "soc.db"
    first = {
        "dedup_key": "same-key",
        "is_duplicate": False,
        "id": "same-id",
        "time": 1784026800,
        "source_file": "/source/first.jsonl",
        "triage_report": "first report",
        "triage_status": "ok",
    }
    replay = {
        **first,
        "time": 1784027800,
        "source_file": "/source/replay.jsonl",
        "triage_report": "updated report",
    }

    first_result = write_soc_db(str(db_path), select_alerts([first])[0], "first-run")
    replay_result = write_soc_db(str(db_path), select_alerts([replay])[0], "replay-run")

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*), event_time, source_file, "
            "json_extract(record_json, '$.time'), "
            "json_extract(record_json, '$.triage_report'), "
            "json_extract(record_json, '$._triage_run_id') "
            "FROM alert_records WHERE dedup_key = 'same-key'"
        ).fetchone()
        indexes = {item[1] for item in connection.execute("PRAGMA index_list(alert_records)")}

    assert row == (
        1,
        1784026800,
        "/source/first.jsonl",
        1784026800,
        "updated report",
        "replay-run",
    )
    assert first_result["inserted_rows"] == 1
    assert first_result["updated_rows"] == 0
    assert replay_result["inserted_rows"] == 0
    assert replay_result["updated_rows"] == 1
    assert "idx_alert_records_first_seen_dedup_key" in indexes


def test_soc_db_schema_migrates_legacy_rows_and_removes_duplicate_keys(tmp_path: Path) -> None:
    ensure_schema = _load_functions("_ensure_soc_db_schema")["_ensure_soc_db_schema"]
    db_path = tmp_path / "legacy-soc.db"
    first_record = json.dumps({"dedup_key": "legacy-key", "marker": "first"})
    repeated_record = json.dumps({"dedup_key": "legacy-key", "marker": "repeated"})

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE alert_records (
                row_id TEXT PRIMARY KEY,
                record_id TEXT,
                asset_date TEXT NOT NULL,
                source_file TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                event_time INTEGER,
                source_type TEXT,
                threat_name TEXT,
                is_duplicate INTEGER NOT NULL DEFAULT 0,
                record_json TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO alert_records (
                row_id, record_id, asset_date, source_file, line_number,
                event_time, source_type, threat_name, is_duplicate, record_json
            ) VALUES (?, '', '2026-07-14', ?, 1, ?, '', '', 0, ?)
            """,
            [
                ("first-row", "/source/first.jsonl", 100, first_record),
                ("repeated-row", "/source/repeated.jsonl", 200, repeated_record),
            ],
        )

        ensure_schema(connection)
        connection.commit()

        rows = connection.execute(
            "SELECT row_id, dedup_key, json_extract(record_json, '$.marker') "
            "FROM alert_records"
        ).fetchall()
        indexes = {item[1] for item in connection.execute("PRAGMA index_list(alert_records)")}

    assert rows == [("first-row", "legacy-key", "first")]
    assert "idx_alert_records_first_seen_dedup_key" in indexes


def test_soc_db_serializes_concurrent_writes_for_the_same_dedup_key(tmp_path: Path) -> None:
    functions = _load_functions(
        "_ensure_soc_db_schema",
        "_event_time_value",
        "_asset_date_value",
        "_source_type_value",
        "_record_id_value",
        "_stable_row_id",
        "_load_existing_soc_rows",
        "_merge_triage_record",
        "_triage_write_soc_db",
    )
    write_soc_db = functions["_triage_write_soc_db"]
    db_path = tmp_path / "concurrent-soc.db"
    alerts = [
        {
            "dedup_key": "concurrent-key",
            "is_duplicate": False,
            "id": "first",
            "time": 1784026800,
            "source_file": "/source/first.jsonl",
            "triage_report": "first report",
        },
        {
            "dedup_key": "concurrent-key",
            "is_duplicate": False,
            "id": "second",
            "time": 1784027800,
            "source_file": "/source/second.jsonl",
            "triage_report": "second report",
        },
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: write_soc_db(str(db_path), [item[1]], item[0]),
                [("first-run", alerts[0]), ("second-run", alerts[1])],
            )
        )

    with sqlite3.connect(db_path) as connection:
        row_count = connection.execute(
            "SELECT COUNT(*) FROM alert_records WHERE dedup_key = 'concurrent-key'"
        ).fetchone()[0]

    assert row_count == 1
    assert sum(result["inserted_rows"] for result in results) == 1
    assert sum(result["updated_rows"] for result in results) == 1
