"""SOC overview and access binding against each test's retained PG schema."""
import importlib.util
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from flocks.db import registry, sync_query

pytestmark = pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="requires isolated PostgreSQL")
PLUGIN = Path(__file__).resolve().parents[2] / ".flocks/flockshub/plugins/webuis/soc_ui"


def load(relative):
    name = "soc_pg_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(name, PLUGIN / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def soc_database():
    with sync_query.connect() as db:
        db.execute("CREATE TABLE alert_records(row_id TEXT PRIMARY KEY, rowid BIGINT UNIQUE, asset_date TEXT, event_time BIGINT, record_json JSONB)")
        db.execute("CREATE TABLE workflow_executions(id TEXT PRIMARY KEY, workflow_id TEXT, started_at BIGINT, output_results TEXT)")
        db.execute("CREATE TABLE workflow_stats(workflow_id TEXT PRIMARY KEY, call_count BIGINT)")
    return sync_query


def seed_alerts(count, *, start="2026-09-01"):
    first = datetime.strptime(start, "%Y-%m-%d")
    rows = []
    for index in range(count):
        event = first + timedelta(days=index)
        record = {"id": str(index), "time": int(event.timestamp() * 1000), "threat_name": "scan", "_source_type": "ndr", "is_duplicate": index == 1}
        rows.append((uuid4().hex, index + 1, event.strftime("%Y-%m-%d"), int(event.timestamp() * 1000), json.dumps(record)))
    with sync_query.connect() as db:
        db.executemany("INSERT INTO alert_records VALUES (?, ?, ?, ?, ?::jsonb)", rows)


def stats(handlers, database=sync_query, **params):
    return handlers.get_stats(SimpleNamespace(database=database), SimpleNamespace(query_params=params))


def test_overview_real_pg_aggregates_and_reuses_range_query(soc_database):
    handlers = load("soc_overview/api/handlers.py")
    seed_alerts(14)
    started = int(datetime(2026, 9, 1).timestamp() * 1000)
    with sync_query.connect() as db:
        db.execute("INSERT INTO workflow_executions VALUES (?, ?, ?, ?)", (uuid4().hex, "stream_alert_denoise", started, json.dumps({"stats": {"raw_count": 5, "after_dedup_count": 4}, "is_duplicate": True})))
    queries = []
    @contextmanager
    def traced(**kwargs):
        with sync_query.connect(**kwargs) as db:
            original = db.execute
            def execute(sql, params=()):
                queries.append(sql)
                return original(sql, params)
            db.execute = execute
            yield db
    database = SimpleNamespace(connect=traced, identity=sync_query.identity)
    result = stats(handlers, database, startDate="2026-09-01", endDate="2026-09-14", date="2026-09-01")
    assert result["denoise"]["totalRaw"] == 14
    assert result["denoise"]["duplicates"] == 1
    assert result["denoise"]["totalUnique"] == 13
    assert result["sourceStatus"]["workflowStats"] == {"callCount": 1, "dupCount": 1, "uniqueCount": 4}
    assert result["sourceStatus"]["assets"]["dataSource"] == "postgres"
    assert len(result["sourceStatus"]["assets"]["availableDates"]) == 14
    assert sum('AS record_json' in query for query in queries) == 1
    assert handlers._request_state.get() is None


def test_overview_reports_the_model_verdicts_instead_of_estimating_them(soc_database):
    # Raw fields that the removed estimate read as "attack succeeded" on every row.
    first = datetime(2026, 9, 3, 10, 0)
    rows = []
    for index in range(10):
        verdict, result = ("attack", "failed") if index == 0 else ("non_attack", "unknown")
        cached = index < 3
        record = {
            "id": f"alert-{index}", "time": int((first + timedelta(minutes=index)).timestamp()),
            "threat_name": "SQL injection", "_source_type": "tdp", "threat_result": "success",
            "rsp_status_code": 200, "rsp_body_len": 120, "is_duplicate": False, "dedup_key": f"key-{index}",
            "triage_attack_verdict": verdict, "triage_attack_success": result,
            "triage_source": "cache" if cached else "triaged", "triage_status": "cached" if cached else "ok",
        }
        rows.append((uuid4().hex, index + 1, "2026-09-03", record["time"], json.dumps(record)))
    with sync_query.connect() as db:
        db.executemany("INSERT INTO alert_records VALUES (?, ?, ?, ?, ?::jsonb)", rows)

    result = stats(load("soc_overview/api/handlers.py"), date="2026-09-03")

    assert {item["key"]: item["value"] for item in result["verdicts"]} == {
        "attack_success": 0, "attack": 0, "attack_failed": 1, "non_attack": 9, "unknown": 0,
    }
    assert (result["triage"]["totalRecords"], result["triage"]["newTriaged"], result["triage"]["cacheHit"]) == (10, 7, 3)
    assert result["timeline"] == {
        "labels": ["2026-09-03"], "window": "按日统计",
        "denoiseRaw": [10], "denoiseUnique": [10], "triageTotal": [10], "triageAttack": [1],
    }


def test_overview_counts_each_triage_row_once_and_reads_legacy_outcomes(soc_database):
    records = [
        {"triage_status": "ok", "triage_attack_verdict": "non_attack", "triage_attack_success": "unknown"},
        {"triage_status": "failed", "triage_attack_verdict": "unknown", "triage_attack_success": "unknown"},
        # Older rows kept the outcome in the verdict, with a boolean or no result.
        {"triage_status": "ok", "triage_attack_verdict": "attack_failed", "triage_attack_success": False},
        {"triage_status": "ok", "triage_attack_verdict": "attack_failed"},
        {"triage_status": "ok", "triage_attack_verdict": "attack_success"},
        {"triage_status": "ok", "triage_attack_verdict": "attack", "triage_attack_success": True},
        # A migrated alert that was never triaged.
        {"threat_name": "not triaged"},
    ]
    start = int(datetime(2026, 9, 4, 9, 0).timestamp())
    with sync_query.connect() as db:
        db.executemany("INSERT INTO alert_records VALUES (?, ?, ?, ?, ?::jsonb)", [
            (uuid4().hex, index + 1, "2026-09-04", start + index, json.dumps({"id": str(index), **record}))
            for index, record in enumerate(records)
        ])

    result = stats(load("soc_overview/api/handlers.py"), date="2026-09-04")

    assert {item["key"]: item["value"] for item in result["verdicts"]} == {
        "attack_success": 2, "attack": 0, "attack_failed": 2, "non_attack": 1, "unknown": 1,
    }
    assert (result["triage"]["totalRecords"], result["triage"]["triageFailed"], result["denoise"]["totalRaw"]) == (6, 1, 7)
    # The failed row is pending once, not once as failed and again as unknown.
    assert result["closedLoop"] == {
        "autoClosed": 3, "resolved": 5, "manualDecision": 1, "pending": 1, "resolutionRate": round(5 / 6, 4),
    }


def test_overview_missing_tables_returns_empty_without_database_files():
    result = stats(load("soc_overview/api/handlers.py"), date="2026-09-01")
    assert result["denoise"]["totalRaw"] == 0
    assert result["sourceStatus"]["assets"]["exists"] is False
    assert result["sourceStatus"]["workflowStats"]["callCount"] == 0


def test_overview_millisecond_range_excludes_adjacent_days(soc_database):
    seed_alerts(3)
    day = int(datetime(2026, 9, 2).timestamp() * 1000)
    result = stats(load("soc_overview/api/handlers.py"), startTime=str(day), endTime=str(day), date="2026-09-02")
    assert result["denoise"]["totalRaw"] == 1
    assert result["denoise"]["duplicates"] == 1


def test_overview_workflow_cache_is_isolated_by_database(soc_database, monkeypatch):
    handlers = load("soc_overview/api/handlers.py")
    with sync_query.connect() as db:
        db.execute("INSERT INTO workflow_stats VALUES (?, ?)", ("worker", 7))
    state = {"database": sync_query, "records": {}, "decoded": {}, "config": None}
    token = handlers._request_state.set(state)
    try:
        assert handlers._get_workflow_call_count("worker")["callCount"] == 7
        schema = "overview_other_" + uuid4().hex
        with sync_query.connect() as db:
            db.execute(f'CREATE SCHEMA "{schema}"')
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        parts = urlsplit(registry.current().dsn)
        query = dict(parse_qsl(parts.query))
        query["search_path"] = schema + ",public"
        monkeypatch.setattr(registry, "_settings", registry.DatabaseSettings("postgres", urlunsplit(parts._replace(query=urlencode(query)))))
        with sync_query.connect() as db:
            db.execute("CREATE TABLE workflow_stats(workflow_id TEXT PRIMARY KEY, call_count BIGINT)")
            db.execute("INSERT INTO workflow_stats VALUES (?, ?)", ("worker", 19))
        assert handlers._get_workflow_call_count("worker")["callCount"] == 19
    finally:
        handlers._request_state.reset(token)


def test_access_contract_uses_pg_binding_and_stable_order():
    module = load("access/soc_alerts_operations.py")
    binding = module._BindingResolver().resolve(page_id="soc-alerts", slot_id="main", contract_id=module.CONTRACT_ID, contract_version=module.CONTRACT_VERSION)
    assert binding.adapter_kind == "builtin-postgres-json"
    assert binding.driver_options == {"table": "alert_records", "recordColumn": "record_json", "dateColumn": "asset_date", "eventTimeColumn": "event_time", "orderColumn": "rowid", "rowIdColumn": "row_id"}
    assert str(binding.source_root) == "/postgresql/alert_records"


def test_invalid_table_identifier_falls_back_without_sql_interpolation():
    handlers = load("soc_overview/api/handlers.py")
    token = handlers._request_state.set({"database": sync_query, "records": {}, "decoded": {}, "config": {"postgres": {"table": "invalid table"}}})
    try:
        assert handlers._postgres_settings()["table"] == '"alert_records"'
    finally:
        handlers._request_state.reset(token)


@pytest.mark.parametrize("budget", ["MAX_OVERVIEW_RECORDS", "MAX_OVERVIEW_BYTES", "MAX_RECORD_BYTES"])
def test_overview_budget_failure_never_returns_partial_totals(soc_database, monkeypatch, budget):
    seed_alerts(3)
    handlers = load("soc_overview/api/handlers.py")
    monkeypatch.setattr(handlers, budget, 2)
    with pytest.raises(handlers.OverviewRangeTooLarge, match="缩小时间范围"):
        stats(handlers, startDate="2026-09-01", endDate="2026-09-03", date="2026-09-01")
    assert handlers._request_state.get() is None
