import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta
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


def _load_overview_handlers():
    handler_path = (
        Path(__file__).resolve().parents[2]
        / ".flocks"
        / "flockshub"
        / "plugins"
        / "webuis"
        / "soc_ui"
        / "soc_overview"
        / "api"
        / "handlers.py"
    )
    spec = importlib.util.spec_from_file_location("soc_overview_fields_test", handler_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _load_alert_operations():
    operations_path = (
        Path(__file__).resolve().parents[2]
        / ".flocks"
        / "flockshub"
        / "plugins"
        / "webuis"
        / "soc_ui"
        / "access"
        / "soc_alerts_operations.py"
    )
    spec = importlib.util.spec_from_file_location("soc_alert_operations_test", operations_path)
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
        "_threat_type": "web_attack",
        "triage_status": "ok",
        "_triage_persisted_at": "2026-07-14T13:00:00",
        "attack_verdict": "benign",
        "attack_success": False,
        "triage_attack_verdict": "attack",
        "triage_attack_success": "unknown",
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
        "threat_type": "malware",
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

    updated_first_record = {
        **first_record,
        "_triage_persisted_at": "2026-07-14T13:02:00",
        "triage_attack_verdict": "attack_failed",
        "triage_attack_success": False,
        "triage_report": "# Updated report",
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO alert_records (
                row_id, record_id, asset_date, source_file, line_number,
                event_time, source_type, threat_name, is_duplicate, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id) DO UPDATE SET
                record_id=excluded.record_id,
                asset_date=excluded.asset_date,
                source_file=excluded.source_file,
                line_number=excluded.line_number,
                event_time=excluded.event_time,
                source_type=excluded.source_type,
                threat_name=excluded.threat_name,
                is_duplicate=excluded.is_duplicate,
                record_json=excluded.record_json
            """,
            (
                "1",
                "1",
                "2026-07-14",
                "triage-replay.jsonl",
                1,
                1784014800,
                "tdp",
                "SQL injection",
                0,
                json.dumps(updated_first_record),
            ),
        )
        conn.commit()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(alert_records)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(alert_records)")}
        facts = conn.execute(
            "SELECT alert_row_id, row_key, source_type, threat_name, threat_type, has_triage "
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
        updated_fact = conn.execute(
            "SELECT triage_persisted_at, triage_attack_verdict, triage_attack_success "
            "FROM soc_dashboard_alert_facts "
            "WHERE row_key = '1'"
        ).fetchone()

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
        (1, "1", "tdp", "SQL injection", "web_attack", 1),
        (2, "2", "hids", "Malware download", "malware", 1),
    ]
    assert "COALESCE(NULLIF(NEW.row_id, ''), CAST(NEW.rowid AS TEXT))" in trigger_sql
    assert updated_fact == ("2026-07-14T13:02:00", "attack", "failed")
    assert schema_version == "4"


def test_soc_dashboard_triage_outcomes_partition_records(tmp_path: Path):
    db_path = tmp_path / "soc.db"
    asset_date = "2026-07-14"
    records = [
        {
            "triage_status": "ok",
            "attack_verdict": "attack_failed",
            "attack_success": False,
            "triage_attack_verdict": "attack",
            "triage_attack_success": "success",
        },
        {
            "triage_status": "ok",
            "triage_attack_verdict": "attack",
            "triage_attack_success": "unknown",
        },
        {
            "triage_status": "ok",
            "attack_verdict": "attack_success",
            "attack_success": True,
            "triage_attack_verdict": "attack",
            "triage_attack_success": "failed",
        },
        {
            "triage_status": "ok",
            "triage_attack_verdict": "non_attack",
            "triage_attack_success": "success",
        },
        {
            "triage_status": "ok",
            "triage_attack_verdict": "unknown",
            "triage_attack_success": "unknown",
        },
        {
            "triage_status": "failed",
            "triage_attack_verdict": "unknown",
            "triage_attack_success": "unknown",
        },
        {
            "triage_status": "failed",
            "triage_attack_verdict": "non_attack",
            "triage_attack_success": "unknown",
        },
        {"triage_status": "ok", "attack_verdict": "legacy", "attack_success": True},
    ]
    severity_values = ["low", "critical", "high", "medium", "low", "critical", "high", "medium"]
    for index, record in enumerate(records):
        record.update(
            {
                "threat_name": f"threat-name-{index}",
                "_threat_type": f"threat-type-{index}",
                "threat_type": f"fallback-type-{index}",
                "threat_severity": severity_values[index],
                "threat_level": "ignored-threat-level",
                "risk_level": "High",
            }
        )
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
        conn.executemany(
            "INSERT INTO alert_records(record_json, asset_date, event_time) VALUES (?, ?, ?)",
            [
                (json.dumps(record), asset_date, 1784014800 + index)
                for index, record in enumerate(records)
            ],
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.DEFAULT_SQLITE_DB = db_path
    handlers._schema_ready.clear()

    assert handlers._ensure_sqlite_schema() is True
    sources = handlers._find_sqlite_sources(
        asset_date,
        asset_date,
        1784014800,
        1784014800 + len(records),
    )
    triage = handlers._read_triage(sources)
    closed_loop = handlers._build_closed_loop(triage)
    with sqlite3.connect(db_path) as conn:
        timeline = handlers._sqlite_timeline(
            conn,
            handlers._sqlite_settings(),
            "asset_date = ? AND event_time BETWEEN ? AND ?",
            [asset_date, 1784014800, 1784014800 + len(records)],
            [asset_date],
            1784014800,
            1784014800 + len(records),
        )
        first_fact = conn.execute(
            "SELECT threat_name, threat_type, severity, risk_level "
            "FROM soc_dashboard_alert_facts ORDER BY alert_row_id LIMIT 1"
        ).fetchone()
        normalized_non_attack = conn.execute(
            "SELECT triage_attack_verdict, triage_attack_success "
            "FROM soc_dashboard_alert_facts ORDER BY alert_row_id LIMIT 1 OFFSET 3"
        ).fetchone()

    assert triage["totalRecords"] == 8
    assert triage["newTriaged"] == 6
    assert triage["attackSuccess"] == 1
    assert triage["attack"] == 1
    assert triage["attackFailed"] == 1
    assert triage["attackTotal"] == 3
    assert triage["benign"] == 2
    assert triage["unknown"] == 3
    assert triage["triageFailed"] == 2
    assert first_fact == ("threat-name-0", "threat-type-0", "low", "High")
    assert normalized_non_attack == ("non_attack", "unknown")
    assert dict(triage["threatTypeCounter"]) == {
        f"threat-type-{index}": 1 for index in range(len(records))
    }
    assert dict(triage["severityCounter"]) == {
        "critical": 2,
        "high": 2,
        "low": 2,
        "medium": 2,
    }
    assert dict(triage["riskCounter"]) == {"high": 8}
    assert closed_loop["manualDecision"] == triage["unknown"]
    assert closed_loop["pending"] == triage["unknown"]
    assert triage["attackTotal"] + triage["benign"] + triage["unknown"] == 8
    assert sum(timeline["attack"]) == triage["attackTotal"]

    overview_handlers = _load_overview_handlers()
    overview_handlers.DEFAULT_SQLITE_DB = db_path
    overview_triage = overview_handlers._read_triage(
        [
            overview_handlers._RecordSource(
                path=db_path,
                role="triage",
                date=asset_date,
                data_source="sqlite",
            )
        ]
    )

    assert overview_triage["attackSuccess"] == 1
    assert overview_triage["attack"] == 1
    assert overview_triage["attackFailed"] == 1
    assert overview_triage["attackTotal"] == 3
    assert overview_triage["benign"] == 2
    assert overview_triage["unknown"] == 3


def test_soc_dashboard_command_graph_uses_model_outcome_partition():
    page_path = (
        Path(__file__).resolve().parents[2]
        / ".flocks"
        / "flockshub"
        / "plugins"
        / "webuis"
        / "soc_ui"
        / "soc_dashboard"
        / "src"
        / "Page.tsx"
    )
    source = page_path.read_text(encoding="utf-8")
    start = source.index("function CommandGraph(")
    end = source.index("\nfunction CommandMetric(", start)
    command_graph = source[start:end]

    assert "value: stats.triage.attackTotal" in command_graph
    assert "value: stats.triage.benign" in command_graph
    assert "value: stats.triage.unknown" in command_graph
    assert "value: stats.closedLoop.pending" not in command_graph
    assert "'安全事件'" in command_graph
    assert "'非安全事件'" in command_graph
    assert "'待人工复核'" in command_graph


def test_soc_overview_uses_backend_five_class_verdicts():
    page_path = (
        Path(__file__).resolve().parents[2]
        / ".flocks"
        / "flockshub"
        / "plugins"
        / "webuis"
        / "soc_ui"
        / "soc_overview"
        / "src"
        / "index.tsx"
    )
    source = page_path.read_text(encoding="utf-8")

    assert "verdicts?: CounterItem[]" in source
    assert "verdicts: list(value.verdicts)" in source
    assert "stats.triage.totalRecords - success - failed" not in source
    for key in ("attack_success", "attack", "attack_failed", "non_attack", "unknown"):
        assert key in source


def test_soc_overview_keeps_threat_names_and_types_separate(tmp_path: Path):
    db_path = tmp_path / "soc.db"
    asset_date = "2026-07-14"
    records = [
        {
            "threat_name": "SQL injection attempt",
            "_threat_type": "web_attack",
            "threat_type": "ignored_fallback",
        },
        {
            "threat_name": "Suspicious crawler",
        },
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE alert_records "
            "(record_json TEXT NOT NULL, asset_date TEXT NOT NULL, event_time INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO alert_records VALUES (?, ?, ?)",
            [
                (json.dumps(record), asset_date, 1784014800 + index)
                for index, record in enumerate(records)
            ],
        )
        conn.commit()

    handlers = _load_overview_handlers()
    handlers.DEFAULT_SQLITE_DB = db_path
    source = handlers._RecordSource(
        path=db_path,
        role="denoise",
        date=asset_date,
        data_source="sqlite",
    )
    denoise = handlers._read_denoise([source])
    field_stats = handlers._build_field_stats([source])

    assert dict(denoise["threatCounter"]) == {
        "sql injection attempt": 1,
        "suspicious crawler": 1,
    }
    assert {
        item["label"]: item["value"] for item in field_stats["threatTypes"]
    } == {"web_attack": 1, "unknown": 1}


def test_soc_dashboard_activity_does_not_mix_name_type_or_risk_fields():
    handlers = _load_dashboard_handlers()
    row = {
        "activity_row_id": 1,
        "activity_event_time": 1784014800,
        "sample_count": 1,
        "record_json": json.dumps(
            {
                "_threat_type": "web_attack",
                "triage_status": "ok",
                "triage_attack_verdict": "attack",
                "triage_attack_success": "failed",
                "threat_level": "critical",
            }
        ),
    }

    event = handlers._activity_event(row)

    assert event["alert"]["threatName"] == "未知告警"
    assert event["alert"]["threatType"] == "web_attack"
    assert event["result"]["threatSeverity"] == ""
    assert event["result"]["riskLevel"] == ""
    assert event["result"]["verdict"] == "attack"
    assert event["result"]["attackSuccess"] == "failed"
    assert event["result"]["verdictLabel"] == "攻击失败"


def test_soc_alert_verdict_does_not_fall_back_to_risk_or_threat_level():
    operations = _load_alert_operations()

    assert operations._verdict_bucket(
        {"risk_level": "attack_success", "threat_level": "attack_failed"}
    ) == "unknown"
    assert operations._verdict_bucket(
        {"triage_attack_verdict": "attack", "triage_attack_success": "success"}
    ) == "success"


def test_soc_alert_verdict_uses_model_triage_instead_of_raw_attack_result():
    operations = _load_alert_operations()

    assert operations._verdict_bucket(
        {
            "attack_verdict": "attack_success",
            "attack_success": True,
            "threat_result": "success",
            "triage_attack_verdict": "non_attack",
            "triage_attack_success": "unknown",
        }
    ) == "benign"
    assert operations._verdict_bucket(
        {
            "attack_verdict": "attack_success",
            "attack_success": True,
            "threat_result": "success",
            "triage_attack_verdict": "attack",
            "triage_attack_success": "failed",
        }
    ) == "failed"
    assert operations._verdict_bucket(
        {
            "attack_verdict": "attack_failed",
            "attack_success": False,
            "threat_result": "failed",
            "triage_attack_verdict": "attack",
            "triage_attack_success": "success",
        }
    ) == "success"
    assert operations._verdict_bucket(
        {"triage_attack_verdict": "attack", "triage_attack_success": "unknown"}
    ) == "attack"
    assert operations._verdict_bucket(
        {"triage_attack_verdict": "unknown", "triage_attack_success": "unknown"}
    ) == "unknown"
    assert operations._triage_attack_success(
        {"triage_attack_verdict": "non_attack", "triage_attack_success": "success"}
    ) == "unknown"
    assert operations._verdict_bucket(
        {"attack_verdict": "attack_success", "attack_success": True, "threat_result": "success"}
    ) == "unknown"
    assert operations._verdict_bucket({"threat_result": "success"}) == "unknown"


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


def test_soc_dashboard_activity_exposes_triage_workflow_link_context(tmp_path: Path):
    workflow_db = tmp_path / "workflow.db"
    now_ms = int(datetime.now().timestamp() * 1000)
    with sqlite3.connect(workflow_db) as conn:
        conn.execute(
            """
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                input_params TEXT NOT NULL DEFAULT '{}',
                output_results TEXT NOT NULL DEFAULT '{}',
                started_at INTEGER NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO workflow_executions
            (id, workflow_id, status, input_params, output_results, started_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "triage-running",
                "stream_alert_triage",
                "running",
                json.dumps({"input_date": "2026-07-23"}),
                json.dumps(
                    {
                        "enriched_alerts_with_triage": [
                            {"threat_name": "远程命令执行攻击"}
                        ]
                    },
                    ensure_ascii=False,
                ),
                now_ms,
                json.dumps({"sessionId": "session-triage-1", "messageId": "msg-triage-1"}),
            ),
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.WORKFLOW_DB = workflow_db
    handlers.DEFAULT_SQLITE_DB = tmp_path / "missing-soc.db"
    handlers._activity_pruned_at = float("inf")

    payload = handlers._get_activity({"bootstrap": "latest"})
    triage_events = [
        event for event in payload["workflowEvents"]
        if event["workflowId"] == "stream_alert_triage"
    ]

    assert len(triage_events) == 1
    triage = triage_events[0]
    assert triage["stage"] == "triage"
    assert triage["status"] == "running"
    assert triage["alert"]["threatName"] == "远程命令执行攻击"
    assert triage["sessionId"] == "session-triage-1"
    assert triage["messageId"] == "msg-triage-1"


def test_soc_dashboard_activity_tolerates_empty_soc_db_with_workflow_events(tmp_path: Path):
    soc_db = tmp_path / "soc.db"
    soc_db.touch()
    workflow_db = tmp_path / "workflow.db"
    now_ms = int(datetime.now().timestamp() * 1000)
    with sqlite3.connect(workflow_db) as conn:
        conn.execute(
            """
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                input_params TEXT NOT NULL DEFAULT '{}',
                output_results TEXT NOT NULL DEFAULT '{}',
                started_at INTEGER NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "INSERT INTO workflow_executions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "triage-running",
                "stream_alert_triage",
                "running",
                "{}",
                json.dumps({"triage_results": [{"alert_name": "Mock 研判"}]}, ensure_ascii=False),
                now_ms,
                json.dumps({"sessionId": "session-1", "messageId": "message-1"}),
            ),
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.DEFAULT_SQLITE_DB = soc_db
    handlers.WORKFLOW_DB = workflow_db
    handlers._activity_pruned_at = float("inf")

    payload = handlers._get_activity({"bootstrap": "latest"})

    assert "error" not in payload
    assert payload["workflowEvents"][0]["alert"]["threatName"] == "Mock 研判"
    assert payload["workflowEvents"][0]["sessionId"] == "session-1"


def test_soc_dashboard_task_center_summarizes_tasks_and_workflows(tmp_path: Path):
    tasks_db = tmp_path / "tasks.db"
    today_at_1100 = datetime.now().astimezone().replace(
        hour=11,
        minute=0,
        second=0,
        microsecond=0,
    )
    today_at_1105 = today_at_1100.replace(minute=5)
    today_at_1110 = today_at_1100.replace(minute=10)
    tomorrow_at_1205 = (today_at_1100 + timedelta(days=1)).replace(hour=12, minute=5)
    yesterday_at_1100 = today_at_1100 - timedelta(days=1)
    yesterday_at_1105 = today_at_1105 - timedelta(days=1)
    today_ms = int(today_at_1100.timestamp() * 1000)
    yesterday_ms = int(yesterday_at_1100.timestamp() * 1000)
    with sqlite3.connect(tasks_db) as conn:
        conn.execute(
            """
            CREATE TABLE task_schedulers (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                workflow_id TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE task_executions (
                id TEXT PRIMARY KEY,
                scheduler_id TEXT NOT NULL,
                status TEXT NOT NULL,
                queued_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT,
                created_at TEXT,
                session_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO task_schedulers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "sched-1",
                    "每日告警巡检",
                    "cron",
                    "active",
                    json.dumps({"cron": "*/5 * * * *", "nextRun": tomorrow_at_1205.isoformat(timespec="seconds")}),
                    "workflow",
                    "stream_alert_denoise",
                    today_at_1100.isoformat(timespec="seconds"),
                ),
            )
        conn.executemany(
            "INSERT INTO task_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                        "exec-1",
                        "sched-1",
                        "completed",
                        yesterday_at_1100.isoformat(timespec="seconds"),
                        (yesterday_at_1100 + timedelta(seconds=1)).isoformat(timespec="seconds"),
                        (yesterday_at_1100 + timedelta(seconds=10)).isoformat(timespec="seconds"),
                        (yesterday_at_1100 + timedelta(seconds=10)).isoformat(timespec="seconds"),
                        yesterday_at_1100.isoformat(timespec="seconds"),
                        "session-a",
                    ),
                (
                        "exec-2",
                        "sched-1",
                        "failed",
                        yesterday_at_1105.isoformat(timespec="seconds"),
                        (yesterday_at_1105 + timedelta(seconds=1)).isoformat(timespec="seconds"),
                        (yesterday_at_1105 + timedelta(seconds=10)).isoformat(timespec="seconds"),
                        (yesterday_at_1105 + timedelta(seconds=10)).isoformat(timespec="seconds"),
                        yesterday_at_1105.isoformat(timespec="seconds"),
                        "session-a",
                    ),
                (
                        "exec-3",
                        "sched-1",
                        "running",
                        today_at_1110.isoformat(timespec="seconds"),
                        (today_at_1110 + timedelta(seconds=1)).isoformat(timespec="seconds"),
                        None,
                        (today_at_1110 + timedelta(seconds=1)).isoformat(timespec="seconds"),
                        today_at_1110.isoformat(timespec="seconds"),
                        "session-b",
                    ),
            ],
        )
        conn.commit()

    workflow_db = tmp_path / "workflow.db"
    with sqlite3.connect(workflow_db) as conn:
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
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                current_phase TEXT,
                current_node_id TEXT,
                current_node_type TEXT,
                current_step_index INTEGER,
                step_count INTEGER NOT NULL DEFAULT 0,
                input_params TEXT NOT NULL DEFAULT '{}',
                output_results TEXT NOT NULL DEFAULT '{}',
                error_message TEXT,
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                updated_at INTEGER,
                payload TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_configs (
                workflow_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                version INTEGER,
                config TEXT NOT NULL,
                updated_at INTEGER,
                PRIMARY KEY (workflow_id, kind)
            )
            """
        )
        conn.executemany(
            "INSERT INTO workflow_stats VALUES (?, ?, ?, ?, ?)",
            [
                ("custom_workflow", 2, 1, 1, 1784775000000),
                ("stream_alert_denoise", 10, 9, 1, today_ms),
                ("stream_alert_triage", 1, 0, 0, yesterday_ms + 5000),
                ("e6d5581a-b105-4c75-a102-1d8e6c97e1c1", 1, 0, 0, 1784775700000),
            ],
        )
        conn.executemany(
            """
            INSERT INTO workflow_executions (
                id, workflow_id, status, current_phase, current_node_id, current_node_type,
                current_step_index, step_count, input_params, output_results, error_message,
                started_at, finished_at, updated_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "wf-1",
                    "custom_workflow",
                    "error",
                    "error",
                    "node-1",
                    "python",
                    1,
                    1,
                    "{}",
                    "{}",
                    "boom",
                    yesterday_ms,
                    yesterday_ms + 1000,
                    yesterday_ms + 1000,
                    "{}",
                ),
                (
                    "wf-2",
                    "stream_alert_denoise",
                    "success",
                    "success",
                    "dedup",
                    "python",
                    1,
                    1,
                    json.dumps({"alerts": [{"threat_name": "SQL注入攻击"}]}),
                    json.dumps({"unique_alerts": [{"threat_name": "SQL注入攻击"}]}),
                    None,
                    today_ms,
                    today_ms + 1000,
                    today_ms + 1000,
                    "{}",
                ),
                (
                    "wf-triage",
                    "stream_alert_triage",
                    "running",
                    "running",
                    "concurrent_triage",
                    "python",
                    2,
                    1,
                    json.dumps({"input_date": yesterday_at_1100.date().isoformat()}),
                    json.dumps(
                        {
                            "enriched_alerts_with_triage": [
                                {
                                    "threat_name": "远程命令执行攻击",
                                    "report_title": "高危RCE研判",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    None,
                    yesterday_ms + 5000,
                    None,
                    yesterday_ms + 6000,
                    json.dumps({"sessionId": "session-triage-1", "messageId": "msg-triage-1"}),
                ),
                (
                    "wf-dynamic",
                    "e6d5581a-b105-4c75-a102-1d8e6c97e1c1",
                    "running",
                    "running",
                    "node-1",
                    "python",
                    1,
                    0,
                    "{}",
                    "{}",
                    None,
                    1784775700000,
                    None,
                    1784775700000,
                    "{}",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_configs VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "custom_workflow",
                    "workflow.integration-config",
                    1,
                    json.dumps({"workflow": {"id": "custom_workflow", "name": "自定义处置流"}}),
                    1784775000000,
                ),
                (
                    "stream_alert_denoise",
                    "workflow.integration-config",
                    1,
                    json.dumps({"workflow": {"id": "stream_alert_denoise", "name": "流式告警降噪"}}),
                    1784775600000,
                ),
                (
                    "stream_alert_triage",
                    "workflow_poller_config",
                    1,
                    json.dumps({"enabled": True, "timeoutSeconds": 604800}),
                    yesterday_ms + 6000,
                ),
            ],
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.TASK_DB = tasks_db
    handlers.WORKFLOW_DB = workflow_db

    payload = handlers._get_task_center()

    assert payload["sessionCount"] == 2
    assert payload["scheduledTasks"] == [
        {
            "id": "sched-1",
            "name": "每日告警巡检",
            "mode": "cron",
            "status": "active",
            "executionMode": "workflow",
            "workflowId": "stream_alert_denoise",
            "executionCount": 3,
            "todayExecutionCount": 1,
            "successCount": 1,
            "successRate": 0.3333,
            "activeCount": 1,
            "lastStatus": "running",
            "lastRunAt": (today_at_1110 + timedelta(seconds=1)).isoformat(timespec="seconds"),
            "nextRunAt": tomorrow_at_1205.isoformat(timespec="seconds"),
            "cron": "*/5 * * * *",
            "cronDescription": "",
        }
    ]
    assert payload["scheduledExecutionCount"] == 3
    assert payload["scheduledTodayExecutionCount"] == 1
    assert payload["workflowExecutionCount"] == 13
    assert payload["workflowTodayExecutionCount"] == 1
    assert [workflow["id"] for workflow in payload["workflows"]] == [
        "stream_alert_triage",
        "stream_alert_denoise",
        "custom_workflow",
    ]
    assert [workflow["name"] for workflow in payload["workflows"]] == [
        "告警研判工作流",
        "告警降噪工作流",
        "自定义处置流",
    ]
    triage = payload["workflows"][0]
    assert triage["latestExecutionHash"] == "wf-triage"
    assert triage["latestAlertName"] == "远程命令执行攻击"
    assert triage["progressLabel"] == "第 2/3 步"
    assert triage["progressPercent"] == 0.6667
    assert triage["sessionId"] == "session-triage-1"
    assert triage["messageId"] == "msg-triage-1"
    denoise = payload["workflows"][1]
    assert denoise["executionCount"] == 10
    assert denoise["todayExecutionCount"] == 1
    assert denoise["successCount"] == 9
    assert denoise["successRate"] == 0.9
    assert denoise["latestExecutionHash"] == "wf-2"
    assert denoise["latestAlertName"] == "SQL注入攻击"
    assert denoise["progressLabel"] == "已完成"


def test_soc_dashboard_task_center_hides_mock_pinned_workflows_by_default(tmp_path: Path):
    workflow_db = tmp_path / "workflow.db"
    with sqlite3.connect(workflow_db) as conn:
        conn.execute(
            """
            CREATE TABLE workflow_stats (
                workflow_id TEXT PRIMARY KEY,
                call_count INTEGER NOT NULL,
                success_count INTEGER NOT NULL,
                error_count INTEGER NOT NULL,
                updated_at INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.TASK_DB = tmp_path / "missing-tasks.db"
    handlers.WORKFLOW_DB = workflow_db

    payload = handlers._get_task_center()
    assert payload["workflows"] == []

    mock_payload = handlers._get_task_center(include_mock=True)
    assert [workflow["id"] for workflow in mock_payload["workflows"]] == [
        "stream_alert_denoise",
        "stream_alert_triage",
    ]


def test_soc_dashboard_token_usage_uses_grouped_cached_reads(tmp_path: Path, monkeypatch):
    handlers = _load_dashboard_handlers()
    usage_db = tmp_path / "flocks.db"
    handlers.USAGE_DB = usage_db
    handlers._token_usage_cache.update({"updatedAt": 0.0, "mtimeNs": 0, "value": None})

    now = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    yesterday = now - timedelta(days=1)
    with sqlite3.connect(usage_db) as conn:
        conn.execute("CREATE TABLE usage_records (created_at TEXT NOT NULL, total_tokens INTEGER NOT NULL)")
        conn.executemany(
            "INSERT INTO usage_records VALUES (?, ?)",
            [
                (handlers._usage_iso(now), 10),
                (handlers._usage_iso(now + timedelta(minutes=5)), 15),
                (handlers._usage_iso(yesterday), 7),
            ],
        )
        conn.commit()

    first = handlers._read_token_usage()

    assert first["totalTokens"] == 32
    assert first["todayTokens"] == 25
    assert first["todayRequests"] == 2
    assert first["dailySeries"][-1] == 25
    assert first["dailySeries"][-2] == 7

    def fail_connect(*args, **kwargs):
        raise AssertionError("token usage should be served from cache")

    monkeypatch.setattr(handlers.sqlite3, "connect", fail_connect)
    assert handlers._read_token_usage() == first


def test_soc_dashboard_task_center_supports_legacy_workflow_execution_schema(tmp_path: Path):
    workflow_db = tmp_path / "workflow.db"
    today_at_1100 = datetime.now().astimezone().replace(
        hour=11,
        minute=0,
        second=0,
        microsecond=0,
    )
    today_ms = int(today_at_1100.timestamp() * 1000)
    with sqlite3.connect(workflow_db) as conn:
        conn.execute(
            """
            CREATE TABLE workflow_stats (
                workflow_id TEXT PRIMARY KEY,
                call_count INTEGER NOT NULL,
                success_count INTEGER NOT NULL,
                error_count INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO workflow_stats VALUES (?, ?, ?, ?)",
            ("stream_alert_denoise", 1, 0, 0),
        )
        conn.execute(
            "INSERT INTO workflow_executions VALUES (?, ?, ?, ?)",
            ("wf-running", "stream_alert_denoise", "running", today_ms),
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.TASK_DB = tmp_path / "missing-tasks.db"
    handlers.WORKFLOW_DB = workflow_db

    payload = handlers._get_task_center()

    denoise = next(
        workflow for workflow in payload["workflows"] if workflow["id"] == "stream_alert_denoise"
    )
    assert payload["workflowExecutionCount"] == 1
    assert payload["workflowTodayExecutionCount"] == 1
    assert denoise["executionCount"] == 1
    assert denoise["todayExecutionCount"] == 1
    assert denoise["activeCount"] == 1
    assert denoise["latestExecutionHash"] == "wf-running"
    assert denoise["lastRunAt"] == today_ms


def test_soc_dashboard_task_center_ignores_disabled_trigger_workflow_runs(tmp_path: Path):
    workflow_db = tmp_path / "workflow.db"
    now_ms = int(datetime.now().astimezone().timestamp() * 1000)
    with sqlite3.connect(workflow_db) as conn:
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
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step_index INTEGER,
                step_count INTEGER,
                started_at INTEGER NOT NULL,
                updated_at INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_configs (
                workflow_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                version INTEGER,
                config TEXT NOT NULL,
                updated_at INTEGER,
                PRIMARY KEY (workflow_id, kind)
            )
            """
        )
        conn.execute(
            "INSERT INTO workflow_stats VALUES (?, ?, ?, ?, ?)",
            ("stream_alert_triage", 1, 0, 0, now_ms),
        )
        conn.execute(
            "INSERT INTO workflow_executions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "wf-disabled-running",
                "stream_alert_triage",
                "running",
                2,
                4,
                now_ms - 60_000,
                now_ms - 60_000,
            ),
        )
        conn.execute(
            "INSERT INTO workflow_configs VALUES (?, ?, ?, ?, ?)",
            (
                "stream_alert_triage",
                "workflow_poller_config",
                1,
                json.dumps({"enabled": False, "timeoutSeconds": 7200}),
                now_ms,
            ),
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.TASK_DB = tmp_path / "missing-tasks.db"
    handlers.WORKFLOW_DB = workflow_db

    payload = handlers._get_task_center()

    triage = next(
        workflow for workflow in payload["workflows"] if workflow["id"] == "stream_alert_triage"
    )
    assert triage["executionCount"] == 1
    assert triage["activeCount"] == 0
    assert triage["lastStatus"] == "disabled"
    assert triage["progressLabel"] == "已关闭"
    assert triage["progressPercent"] == 0


def test_soc_dashboard_task_center_uses_trigger_runtime_window_for_active_workflows(tmp_path: Path):
    workflow_db = tmp_path / "workflow.db"
    now_ms = int(datetime.now().astimezone().timestamp() * 1000)
    old_ms = now_ms - 3 * 60 * 60 * 1000
    with sqlite3.connect(workflow_db) as conn:
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
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step_index INTEGER,
                step_count INTEGER,
                started_at INTEGER NOT NULL,
                updated_at INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_configs (
                workflow_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                version INTEGER,
                config TEXT NOT NULL,
                updated_at INTEGER,
                PRIMARY KEY (workflow_id, kind)
            )
            """
        )
        conn.executemany(
            "INSERT INTO workflow_stats VALUES (?, ?, ?, ?, ?)",
            [
                ("stream_alert_denoise", 1, 0, 0, now_ms),
                ("stream_alert_triage", 1, 0, 0, old_ms),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_executions VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "wf-recent-running",
                    "stream_alert_denoise",
                    "running",
                    1,
                    4,
                    now_ms - 30_000,
                    now_ms - 30_000,
                ),
                (
                    "wf-stale-running",
                    "stream_alert_triage",
                    "running",
                    3,
                    4,
                    old_ms,
                    old_ms,
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_configs VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "stream_alert_denoise",
                    "workflow_poller_config",
                    1,
                    json.dumps({"enabled": True, "timeoutSeconds": 7200}),
                    now_ms,
                ),
                (
                    "stream_alert_triage",
                    "workflow_poller_config",
                    1,
                    json.dumps({"enabled": True, "timeoutSeconds": 7200}),
                    old_ms,
                ),
            ],
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.TASK_DB = tmp_path / "missing-tasks.db"
    handlers.WORKFLOW_DB = workflow_db

    payload = handlers._get_task_center()

    denoise = next(
        workflow for workflow in payload["workflows"] if workflow["id"] == "stream_alert_denoise"
    )
    triage = next(
        workflow for workflow in payload["workflows"] if workflow["id"] == "stream_alert_triage"
    )
    assert denoise["activeCount"] == 1
    assert denoise["lastStatus"] == "running"
    assert denoise["progressLabel"].startswith("第 ")
    assert triage["activeCount"] == 0
    assert triage["lastStatus"] == "stale"
    assert triage["progressLabel"] == "已停止"


def test_soc_dashboard_task_center_marks_stale_running_without_trigger_config(tmp_path: Path):
    workflow_db = tmp_path / "workflow.db"
    now_ms = int(datetime.now().astimezone().timestamp() * 1000)
    old_ms = now_ms - 3 * 60 * 60 * 1000
    with sqlite3.connect(workflow_db) as conn:
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
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step_index INTEGER,
                step_count INTEGER,
                started_at INTEGER NOT NULL,
                updated_at INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO workflow_stats VALUES (?, ?, ?, ?, ?)",
            ("custom_workflow", 1, 0, 0, old_ms),
        )
        conn.execute(
            "INSERT INTO workflow_executions VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("wf-stale-manual", "custom_workflow", "running", 1, 2, old_ms, old_ms),
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.TASK_DB = tmp_path / "missing-tasks.db"
    handlers.WORKFLOW_DB = workflow_db

    payload = handlers._get_task_center()

    workflow = next(item for item in payload["workflows"] if item["id"] == "custom_workflow")
    assert workflow["activeCount"] == 0
    assert workflow["lastStatus"] == "stale"
    assert workflow["progressLabel"] == "已停止"


def test_soc_dashboard_task_center_orders_dynamic_rows(tmp_path: Path):
    now = datetime.now().astimezone().replace(microsecond=0)
    older = now - timedelta(hours=3)
    recent = now - timedelta(minutes=5)
    future = now + timedelta(hours=2)
    older_ms = int(older.timestamp() * 1000)
    recent_ms = int(recent.timestamp() * 1000)

    tasks_db = tmp_path / "tasks.db"
    with sqlite3.connect(tasks_db) as conn:
        conn.execute(
            """
            CREATE TABLE task_schedulers (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                workflow_id TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE task_executions (
                id TEXT PRIMARY KEY,
                scheduler_id TEXT NOT NULL,
                status TEXT NOT NULL,
                queued_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT,
                created_at TEXT,
                session_id TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO task_schedulers VALUES (?, ?, 'cron', 'active', ?, 'agent', '', ?)",
            [
                ("sched-active", "正在执行任务", "{}", older.isoformat()),
                ("sched-recent", "最近完成任务", "{}", recent.isoformat()),
                ("sched-popular", "高频历史任务", "{}", older.isoformat()),
                (
                    "sched-future",
                    "未来待执行任务",
                    json.dumps({"nextRun": future.isoformat(timespec="seconds")}),
                    now.isoformat(),
                ),
            ],
        )
        task_exec_rows = [
            (
                "exec-active",
                "sched-active",
                "running",
                older.isoformat(timespec="seconds"),
                older.isoformat(timespec="seconds"),
                None,
                older.isoformat(timespec="seconds"),
                older.isoformat(timespec="seconds"),
                "s-active",
            ),
            (
                "exec-recent",
                "sched-recent",
                "completed",
                recent.isoformat(timespec="seconds"),
                recent.isoformat(timespec="seconds"),
                (recent + timedelta(seconds=3)).isoformat(timespec="seconds"),
                (recent + timedelta(seconds=3)).isoformat(timespec="seconds"),
                recent.isoformat(timespec="seconds"),
                "s-recent",
            ),
        ]
        for index in range(5):
            when = older - timedelta(minutes=index)
            task_exec_rows.append(
                (
                    f"exec-popular-{index}",
                    "sched-popular",
                    "completed",
                    when.isoformat(timespec="seconds"),
                    when.isoformat(timespec="seconds"),
                    (when + timedelta(seconds=2)).isoformat(timespec="seconds"),
                    (when + timedelta(seconds=2)).isoformat(timespec="seconds"),
                    when.isoformat(timespec="seconds"),
                    "s-popular",
                )
            )
        conn.executemany(
            "INSERT INTO task_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            task_exec_rows,
        )
        conn.commit()

    workflow_db = tmp_path / "workflow.db"
    with sqlite3.connect(workflow_db) as conn:
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
            CREATE TABLE workflow_executions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                updated_at INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE workflow_configs (
                workflow_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                version INTEGER,
                config TEXT NOT NULL,
                updated_at INTEGER,
                PRIMARY KEY (workflow_id, kind)
            )
            """
        )
        conn.executemany(
            "INSERT INTO workflow_stats VALUES (?, ?, ?, ?, ?)",
            [
                ("wf-active", 1, 0, 0, older_ms),
                ("wf-recent", 1, 1, 0, recent_ms),
                ("wf-popular", 6, 6, 0, older_ms),
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_executions VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("run-active", "wf-active", "running", older_ms, None, older_ms),
                ("run-recent", "wf-recent", "success", recent_ms, recent_ms + 1000, recent_ms + 1000),
                ("run-popular", "wf-popular", "success", older_ms, older_ms + 1000, older_ms + 1000),
            ],
        )
        conn.execute(
            "INSERT INTO workflow_configs VALUES (?, ?, ?, ?, ?)",
            (
                "wf-active",
                "workflow_poller_config",
                1,
                json.dumps({"enabled": True, "timeoutSeconds": 14400}),
                older_ms,
            ),
        )
        conn.commit()

    handlers = _load_dashboard_handlers()
    handlers.TASK_DB = tasks_db
    handlers.WORKFLOW_DB = workflow_db

    payload = handlers._get_task_center()

    assert [task["id"] for task in payload["scheduledTasks"][:4]] == [
        "sched-active",
        "sched-recent",
        "sched-popular",
        "sched-future",
    ]
    assert [workflow["id"] for workflow in payload["workflows"][:3]] == [
        "wf-active",
        "wf-recent",
        "wf-popular",
    ]


def test_soc_dashboard_workflow_progress_marks_unavailable_database(tmp_path: Path):
    handlers = _load_dashboard_handlers()
    handlers.WORKFLOW_DB = tmp_path / "missing-workflow.db"

    assert handlers._get_workflow_progress("stream_alert_denoise") == {
        "callCount": None,
        "latestStartedAt": None,
    }


def test_soc_dashboard_corrects_legacy_single_alert_duplicate_metrics():
    handlers = _load_dashboard_handlers()
    output = {
        "stats": {
            "raw_count": 1,
            "normalized_count": 1,
            "after_filter_count": 1,
            "after_dedup_count": 1,
            "dedup_removed_count": 0,
        },
        "is_duplicate": True,
    }

    metrics = handlers._workflow_execution_metrics(json.dumps(output))

    assert metrics["uniqueCount"] == 0
    assert metrics["duplicateCount"] == 1
    assert metrics["reducedCount"] == 1
    assert metrics["reductionRate"] == 1
    assert metrics["dedupRate"] == 1

    flag_only_metrics = handlers._workflow_execution_metrics(
        json.dumps({"is_duplicate": True})
    )

    assert flag_only_metrics["metricsAvailable"] is True
    assert flag_only_metrics["rawCount"] == 1
    assert flag_only_metrics["uniqueCount"] == 0
    assert flag_only_metrics["duplicateCount"] == 1
    assert flag_only_metrics["reductionRate"] == 1

    output["is_duplicate"] = False
    first_seen_metrics = handlers._workflow_execution_metrics(json.dumps(output))

    assert first_seen_metrics["uniqueCount"] == 1
    assert first_seen_metrics["duplicateCount"] == 0
    assert first_seen_metrics["reductionRate"] == 0

    output["is_duplicate"] = True
    output["stats"].update(
        raw_count=2,
        normalized_count=2,
        after_filter_count=2,
        after_dedup_count=2,
    )
    batch_metrics = handlers._workflow_execution_metrics(json.dumps(output))

    assert batch_metrics["uniqueCount"] == 2
    assert batch_metrics["duplicateCount"] == 0


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
    assert events[0]["result"]["uniqueCount"] == 0
    assert events[0]["result"]["duplicateCount"] == 1
    assert events[0]["result"]["reductionRate"] == 1
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
