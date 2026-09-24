from __future__ import annotations

import ast
import datetime as datetime_module
import json
import os
import pickle
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from flocks.db import sync_query
from flocks.workflow.soc_store import SOCWriteCapability, ensure_schema, persist_triage_alerts

requires_pg = pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="requires isolated PostgreSQL")


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


def _load_dedup_code() -> str:
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return next(node["code"] for node in workflow["nodes"] if node["id"] == "load_dedup_file")


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
        "soc": SOCWriteCapability(),
        "json": json,
        "os": os,
        "pickle": pickle,
        "re": re,
        "time": time,
        "MAX_TRIAGE_CACHE_BYTES": 128 * 1024 * 1024,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(WORKFLOW_PATH), "exec"), namespace)
    return namespace


def test_load_node_defaults_to_safe_single_alert_concurrency(tmp_path: Path) -> None:
    input_path = tmp_path / "dedup_result_001.jsonl"
    input_path.write_text(
        json.dumps({"dedup_key": "first", "is_duplicate": False}) + "\n",
        encoding="utf-8",
    )
    namespace: dict[str, object] = {
        "inputs": {"input_path": str(input_path)},
        "outputs": {},
    }

    exec(compile(_load_dedup_code(), str(WORKFLOW_PATH), "exec"), namespace)

    assert namespace["outputs"]["concurrency"] == 1


def test_llm_calls_share_the_run_concurrency_budget() -> None:
    functions = _load_functions("_ask_llm")
    active = 0
    peak = 0
    lock = threading.Lock()

    class FakeLLM:
        def ask(self, _prompt: str, **_kwargs: object) -> str:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.02)
                return "ok"
            finally:
                with lock:
                    active -= 1

    functions.update(
        {
            "llm": FakeLLM(),
            "LLM_CALL_TIMEOUT_S": 120.0,
            "LLM_CALL_MAX_RETRIES": 1,
            "_llm_slots": threading.BoundedSemaphore(5),
        }
    )
    ask_llm = functions["_ask_llm"]

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(ask_llm, [f"prompt-{i}" for i in range(40)]))

    assert results == ["ok"] * 40
    assert peak == 5
    assert re.search(
        r"_llm_slots\s*=\s*threading\.BoundedSemaphore\(concurrency\)",
        _concurrent_triage_code(),
    )


def test_triage_does_not_truncate_content_or_limit_output_tokens() -> None:
    code = _concurrent_triage_code()

    for forbidden in (
        "MAX_HTTP_FIELD_CHARS",
        "MAX_LOG_TEXT_CHARS",
        "MAX_LLM_PROMPT_CHARS",
        "MAX_LLM_RESPONSE_CHARS",
        "MAX_TRIAGE_REPORT_CHARS",
        "LLM_CALL_MAX_TOKENS",
        "_clip_prompt_text",
        "'max_tokens':",
    ):
        assert forbidden not in code


def test_cache_eviction_enforces_serialized_byte_budget() -> None:
    evict = _load_functions("_evict_lru")["_evict_lru"]
    cache = {f"key-{index}": {"report": chr(65 + index) * 2048} for index in range(3)}

    evicted = evict(cache, max_keys=100, max_bytes=2500)

    assert evicted == 2
    assert list(cache) == ["key-2"]
    assert len(pickle.dumps(cache, protocol=pickle.HIGHEST_PROTOCOL)) <= 2500


def test_oversized_cache_is_quarantined_before_unpickling(tmp_path: Path) -> None:
    functions = _load_functions("_load_cache")
    cache_path = tmp_path / "triage_cache.pkl"
    cache_path.write_bytes(b"x" * 9)

    class FailIfLoaded:
        @staticmethod
        def load(_stream: object) -> object:
            raise AssertionError("oversized cache must not be deserialized")

    functions["pickle"] = FailIfLoaded
    functions["MAX_TRIAGE_CACHE_BYTES"] = 8

    assert functions["_load_cache"](str(cache_path)) == {}
    assert not cache_path.exists()
    assert len(list(tmp_path.glob("triage_cache.pkl.*.oversized"))) == 1


def test_cache_is_loaded_once_while_batch_lock_is_held() -> None:
    code = _concurrent_triage_code()
    tree = ast.parse(code)
    cache_path_loads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_load_cache"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "cache_path"
    ]

    assert len(cache_path_loads) == 1
    assert "if new_results or evicted:" in code
    assert "MAX_TRIAGE_CACHE_BYTES if new_results else None" in code


def test_memory_heavy_triage_node_uses_fatal_process_isolation() -> None:
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    node = next(item for item in workflow["nodes"] if item["id"] == "concurrent_triage")

    assert node["processIsolated"] is True
    assert node["processRetainFdKeys"] == ["_batch_lease_fd"]
    assert "processInheritFdKeys" not in node
    assert node["timeoutFatal"] is True
    assert "outputs['enriched_alerts_with_triage']" not in node["code"]
    assert "top_triage_result['triage_report'] = a.get('triage_report', '')" in node["code"]


def test_triage_work_units_are_submitted_in_small_batches() -> None:
    code = _concurrent_triage_code()

    assert "TRIAGE_SUB_BATCH_SIZE = 3" in code
    assert "range(0, len(work_units), TRIAGE_SUB_BATCH_SIZE)" in code
    assert "pool.submit(_process_unit, *u) for u in work_batch" in code


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
    assert isinstance(write_calls[0].args[0], ast.Name)
    assert write_calls[0].args[0].id == "first_seen_soc_alerts"


def test_apply_triage_fields_preserves_original_alert_fields() -> None:
    apply_triage_fields = _load_functions("_apply_triage_fields")["_apply_triage_fields"]
    record = {
        "attack_verdict": "raw-verdict",
        "attack_success": True,
        "threat_result": "raw-result",
    }

    apply_triage_fields(
        record,
        {
            "triage_attack_verdict": "attack",
            "triage_attack_success": "failed",
            "risk_level": "High",
            "report_title": "Model report",
            "triage_report": "# Model report",
            "attack_verdict": "model-verdict",
            "attack_success": False,
            "threat_result": "model-result",
        },
    )

    assert record == {
        "attack_verdict": "raw-verdict",
        "attack_success": True,
        "threat_result": "raw-result",
        "triage_attack_verdict": "attack",
        "triage_attack_success": "failed",
        "risk_level": "High",
        "report_title": "Model report",
        "triage_report": "# Model report",
    }


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


def test_cache_save_failure_is_reraised_and_cleans_unique_temp_file(tmp_path: Path) -> None:
    functions = _load_functions("_save_cache_atomic")
    functions["pickle"] = pickle
    functions["threading"] = threading
    cache_path = tmp_path / "cache-target"
    cache_path.mkdir()

    with pytest.raises(RuntimeError, match="failed to save triage cache"):
        functions["_save_cache_atomic"](str(cache_path), {"key": {"value": 1}})

    assert not list(tmp_path.glob("cache-target.*.tmp"))


@requires_pg
def test_soc_db_merge_preserves_original_attack_fields_and_updates_triage_fields() -> None:
    original = {"dedup_key": "original", "is_duplicate": False, "attack_success": True,
                "attack_verdict": "attack_success", "threat_result": "success"}
    persist_triage_alerts([original], "original")
    persist_triage_alerts([{**original, "triage_attack_success": "unknown", "triage_attack_verdict": "non_attack"}], "updated")
    with sync_query.connect(readonly=True) as connection:
        merged = json.loads(connection.execute("SELECT record_json FROM alert_records WHERE dedup_key=?", ("original",)).fetchone()[0])
    assert merged["attack_success"] is True
    assert merged["attack_verdict"] == "attack_success"
    assert merged["threat_result"] == "success"
    assert merged["triage_attack_success"] == "unknown"
    assert merged["triage_attack_verdict"] == "non_attack"


def test_apply_triage_fields_namespaces_model_attack_fields() -> None:
    apply_fields = _load_functions("_apply_triage_fields")["_apply_triage_fields"]
    record = {"attack_success": True, "attack_verdict": "attack_success"}

    apply_fields(
        record,
        {
            "triage_attack_success": "unknown",
            "triage_attack_verdict": "non_attack",
            "risk_level": "Low",
        },
    )

    assert record["attack_success"] is True
    assert record["attack_verdict"] == "attack_success"
    assert record["triage_attack_success"] == "unknown"
    assert record["triage_attack_verdict"] == "non_attack"
    assert record["risk_level"] == "Low"


def test_apply_triage_fields_validates_direct_model_dimensions() -> None:
    apply_fields = _load_functions("_apply_triage_fields")["_apply_triage_fields"]
    cases = [
        (("attack", "success"), ("attack", "success")),
        (("attack", "failed"), ("attack", "failed")),
        (("attack", "unknown"), ("attack", "unknown")),
        (("non_attack", "success"), ("non_attack", "unknown")),
        (("unknown", "failed"), ("unknown", "unknown")),
        (("invalid", "invalid"), ("unknown", "unknown")),
    ]

    for (model_verdict, model_success), (attack_verdict, attack_success) in cases:
        record: dict[str, object] = {}
        apply_fields(
            record,
            {
                "triage_attack_verdict": model_verdict,
                "triage_attack_success": model_success,
            },
        )
        assert record["triage_attack_verdict"] == attack_verdict
        assert record["triage_attack_success"] == attack_success


def test_llm_attack_outcome_generates_the_two_persisted_fields_directly() -> None:
    functions = _load_functions("_normalize_triage_outcome", "_llm_attack_outcome")
    functions.update(
        {
            "_strip_think": lambda value: value,
            "_ask_llm": lambda _prompt: json.dumps(
                {
                    "triage_attack_verdict": "attack",
                    "triage_attack_success": "failed",
                }
            ),
        }
    )

    assert functions["_llm_attack_outcome"]("analysis") == {
        "triage_attack_verdict": "attack",
        "triage_attack_success": "failed",
    }


def test_current_triage_cache_requires_the_two_field_schema() -> None:
    functions = _load_functions("_normalize_triage_outcome", "_is_current_triage_fields")
    functions.update(
        {
            "TRIAGE_FIELDS": (
                "triage_attack_verdict",
                "triage_attack_success",
                "risk_level",
                "report_title",
                "triage_report",
            ),
            "_is_valid_triage_report": lambda value: value == "valid-report",
        }
    )
    is_current = functions["_is_current_triage_fields"]

    assert is_current(
        {
            "triage_attack_verdict": "attack",
            "triage_attack_success": "success",
            "risk_level": "High",
            "report_title": "title",
            "triage_report": "valid-report",
        }
    )
    assert not is_current(
        {
            "attack_verdict": "attack_success",
            "attack_success": True,
            "risk_level": "High",
            "report_title": "title",
            "triage_report": "valid-report",
        }
    )


@requires_pg
def test_soc_db_writer_receives_only_selected_first_seen_alerts() -> None:
    functions = _load_functions("_input_bool", "_select_first_seen_soc_alerts", "_triage_write_soc_db")
    alerts = [
        {"dedup_key": "first", "is_duplicate": False, "id": "1", "time": 1784026800},
        {"dedup_key": "first", "is_duplicate": False, "id": "2", "time": 1784026801},
        {"dedup_key": "duplicate", "is_duplicate": True, "id": "3", "time": 1784026802},
        {"dedup_key": "second", "is_duplicate": False, "id": "4", "time": 1784026803},
        {"is_duplicate": False, "id": "missing-key", "time": 1784026804},
    ]
    selected, stats = functions["_select_first_seen_soc_alerts"](alerts)
    result = functions["_triage_write_soc_db"](selected, "first-seen-test")
    with sync_query.connect(readonly=True) as connection:
        records = connection.execute("SELECT is_duplicate, record_json->>'dedup_key' FROM alert_records ORDER BY event_time").fetchall()
    assert stats["first_seen_rows"] == 2
    assert result["rows"] == 2
    assert [tuple(row) for row in records] == [(0, "first"), (0, "second")]


@requires_pg
def test_soc_db_keeps_one_dedup_key_across_runs_and_preserves_first_event() -> None:
    write_soc_db = _load_functions("_triage_write_soc_db")["_triage_write_soc_db"]
    first = {"dedup_key": "same-key", "is_duplicate": False, "id": "same-id", "time": 1784026800,
             "source_file": "/source/first.jsonl", "triage_report": "first report", "triage_status": "ok"}
    replay = {**first, "time": 1784027800, "source_file": "/source/replay.jsonl", "triage_report": "updated report"}
    first_result = write_soc_db([first], "first-run")
    replay_result = write_soc_db([replay], "replay-run")
    with sync_query.connect(readonly=True) as connection:
        rows = connection.execute("SELECT event_time, source_file, (record_json->>'time')::bigint, record_json->>'triage_report', record_json->>'_triage_run_id' FROM alert_records WHERE dedup_key=?", ("same-key",)).fetchall()
        indexes = {row[0] for row in connection.execute("SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND tablename=?", ("alert_records",)).fetchall()}
    assert [tuple(row) for row in rows] == [(1784026800, "/source/first.jsonl", 1784026800, "updated report", "replay-run")]
    assert first_result["inserted_rows"] == 1 and first_result["updated_rows"] == 0
    assert replay_result["inserted_rows"] == 0 and replay_result["updated_rows"] == 1
    assert "idx_alert_records_first_seen_dedup_key" in indexes


@requires_pg
def test_soc_db_schema_refuses_conflicts_without_removing_rows() -> None:
    import asyncpg
    with sync_query.connect() as connection:
        connection.execute("CREATE TABLE alert_records (row_id TEXT PRIMARY KEY, dedup_key TEXT, asset_date TEXT, event_time BIGINT)")
        connection.executemany("INSERT INTO alert_records(row_id,dedup_key) VALUES(?,?)", [("first", "same"), ("second", "same")])
    with pytest.raises(asyncpg.UniqueViolationError):
        ensure_schema()
    with sync_query.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alert_records WHERE dedup_key=?", ("same",)).fetchone()[0] == 2


@requires_pg
def test_soc_db_schema_is_idempotent_and_retains_existing_rows() -> None:
    from flocks.workflow import soc_store
    ensure_schema()
    with sync_query.connect() as connection:
        connection.execute("INSERT INTO alert_records(row_id,dedup_key,asset_date,source_file,line_number,is_duplicate,record_json) VALUES(?,?,?,?,?,?,?::jsonb)",
                           ("historical-follower", "legacy-key", "2026-07-14", "legacy.jsonl", 1, 1, '{"is_duplicate":true}'))
    soc_store._schema_ready.discard(sync_query.identity())
    ensure_schema()
    with sync_query.connect(readonly=True) as connection:
        row = connection.execute("SELECT row_id,is_duplicate FROM alert_records WHERE dedup_key=?", ("legacy-key",)).fetchone()
    assert tuple(row) == ("historical-follower", 1)


@requires_pg
def test_soc_db_serializes_concurrent_writes_for_the_same_dedup_key() -> None:
    write_soc_db = _load_functions("_triage_write_soc_db")["_triage_write_soc_db"]
    alerts = [{"dedup_key": "concurrent-key", "is_duplicate": False, "id": str(index),
               "time": 1784026800 + index, "source_file": f"/source/{index}.jsonl", "triage_report": f"report {index}"}
              for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda index: write_soc_db([alerts[index]], f"run-{index}"), range(2)))
    with sync_query.connect(readonly=True) as connection:
        row_count = connection.execute("SELECT COUNT(*) FROM alert_records WHERE dedup_key=?", ("concurrent-key",)).fetchone()[0]
    assert row_count == 1
    assert sum(result["inserted_rows"] for result in results) == 1
    assert sum(result["updated_rows"] for result in results) == 1
