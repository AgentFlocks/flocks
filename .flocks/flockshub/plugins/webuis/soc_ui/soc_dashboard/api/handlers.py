import asyncio
import base64
import json
import math
import re
import sqlite3
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_SQLITE_DB = Path.home() / ".flocks" / "data" / "soc.db"
DEFAULT_SQLITE_TABLE = "alert_records"
DEFAULT_SQLITE_RECORD_COLUMN = "record_json"
DEFAULT_SQLITE_DATE_COLUMN = "asset_date"
DEFAULT_SQLITE_EVENT_TIME_COLUMN = "event_time"
FACTS_TABLE = "soc_dashboard_alert_facts"
ACTIVITY_TABLE = "soc_dashboard_activity"
META_TABLE = "soc_dashboard_meta"
SCHEMA_VERSION = "2"
ACTIVITY_DEFAULT_LIMIT = 20
ACTIVITY_MAX_LIMIT = 50
ACTIVITY_WINDOW_MS = 3000
ACTIVITY_NORMAL_LIMIT = 5
ACTIVITY_SURGE_LIMIT = 100
ACTIVITY_RETENTION_ROWS = 100_000
ACTIVITY_PRUNE_INTERVAL = 3600.0

WORKFLOW_DB = Path.home() / ".flocks" / "data" / "workflow.db"
WORKFLOW_SNAPSHOT_TABLE = "soc_dashboard_workflow_stats_samples"
TASK_DB = Path.home() / ".flocks" / "data" / "tasks.db"
USAGE_DB = Path.home() / ".flocks" / "data" / "flocks.db"
SOC_PINNED_WORKFLOW_NAMES = {
    "stream_alert_denoise": "告警降噪工作流",
    "stream_alert_triage": "告警研判工作流",
}
WORKFLOW_DISPLAY_NAMES = {
    **SOC_PINNED_WORKFLOW_NAMES,
    "onesec_kafka_investigation": "OneSEC Kafka 告警研判工作流",
    "tdp_alert_triage": "TDP 告警研判工作流",
    "sec_alert_unified_ops": "统一告警运营工作流",
}
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_workflow_stats_cache: OrderedDict = OrderedDict()
_CACHE_TTL: float = 30.0
_WORKFLOW_CACHE_MAX = 64
_denoise_detail_cache: OrderedDict = OrderedDict()
_DENOISE_DETAIL_CACHE_TTL = 300.0
_DENOISE_DETAIL_CACHE_MAX = 64
_stats_response_cache: OrderedDict = OrderedDict()
_STATS_RESPONSE_CACHE_TTL = 300.0
_STATS_RESPONSE_CACHE_MAX = 32
_cache_lock = RLock()
_schema_lock = RLock()
_schema_ready: set = set()
_activity_pruned_at: float = 0


@dataclass(frozen=True)
class _RecordSource:
    path: Path
    role: str
    date: str
    data_source: str
    record_count: int = 0
    start_time: int = 0
    end_time: int = 0


_FACT_COLUMNS = (
    "alert_row_id",
    "row_key",
    "asset_date",
    "event_time",
    "source_type",
    "threat_name",
    "is_duplicate",
    "phase",
    "direction",
    "result",
    "protocol",
    "severity",
    "response_code",
    "port",
    "has_triage",
    "triage_persisted_at",
    "triage_status",
    "triage_source",
    "verdict",
    "risk_level",
    "triage_ms",
    "attack_success",
)


def _json_value(prefix, path):
    return (
        f"CASE WHEN json_valid({prefix}.record_json) "
        f"THEN json_extract({prefix}.record_json, '$.{path}') END"
    )


def _fact_expressions(prefix):
    source_type = _json_value(prefix, "_source_type")
    source_type_fallback = _json_value(prefix, "source_type")
    threat_name = _json_value(prefix, "threat_name")
    threat_type = _json_value(prefix, "_threat_type")
    phase = _json_value(prefix, "threat_phase")
    attack_phase = _json_value(prefix, "attack_phase")
    kill_chain = _json_value(prefix, "kill_chain_phase")
    direction = _json_value(prefix, "direction")
    traffic_direction = _json_value(prefix, "traffic_direction")
    result = _json_value(prefix, "threat_result")
    verdict = _json_value(prefix, "attack_verdict")
    protocol = _json_value(prefix, "net_type")
    app_protocol = _json_value(prefix, "net_app_proto")
    protocol_fallback = _json_value(prefix, "protocol")
    severity = _json_value(prefix, "threat_severity")
    threat_level = _json_value(prefix, "threat_level")
    risk_level = _json_value(prefix, "risk_level")
    response = _json_value(prefix, "rsp_status_code")
    status_code = _json_value(prefix, "status_code")
    destination_port = _json_value(prefix, "dport")
    destination_port_fallback = _json_value(prefix, "dst_port")
    destination_port_legacy = _json_value(prefix, "destination_port")
    triage_persisted_at = _json_value(prefix, "_triage_persisted_at")
    triage_status = _json_value(prefix, "triage_status")
    triage_source = _json_value(prefix, "triage_source")
    triage_report = _json_value(prefix, "triage_report")
    triage_ms = _json_value(prefix, "triage_ms")
    attack_success = _json_value(prefix, "attack_success")
    has_triage = (
        "CASE WHEN "
        f"NULLIF({triage_status}, '') IS NOT NULL "
        f"OR NULLIF({triage_persisted_at}, '') IS NOT NULL "
        f"OR {triage_report} IS NOT NULL THEN 1 ELSE 0 END"
    )
    return (
        f"{prefix}.rowid",
        f"COALESCE(NULLIF({prefix}.row_id, ''), CAST({prefix}.rowid AS TEXT))",
        f"{prefix}.asset_date",
        f"{prefix}.event_time",
        f"COALESCE(NULLIF({prefix}.source_type, ''), NULLIF({source_type}, ''), "
        f"NULLIF({source_type_fallback}, ''), 'unknown')",
        f"COALESCE(NULLIF({prefix}.threat_name, ''), NULLIF({threat_name}, ''), "
        f"NULLIF({threat_type}, ''), 'unknown')",
        f"COALESCE({prefix}.is_duplicate, 0)",
        f"COALESCE(NULLIF({phase}, ''), NULLIF({attack_phase}, ''), NULLIF({kill_chain}, ''), 'unknown')",
        f"COALESCE(NULLIF({direction}, ''), NULLIF({traffic_direction}, ''), 'unknown')",
        f"COALESCE(NULLIF({result}, ''), NULLIF({verdict}, ''), 'unknown')",
        f"COALESCE(NULLIF({protocol}, ''), NULLIF({app_protocol}, ''), "
        f"NULLIF({protocol_fallback}, ''), 'unknown')",
        f"COALESCE(NULLIF({severity}, ''), NULLIF({threat_level}, ''), NULLIF({risk_level}, ''), 'unknown')",
        f"COALESCE(NULLIF({response}, ''), NULLIF({status_code}, ''), 'unknown')",
        f"COALESCE(NULLIF({destination_port}, ''), NULLIF({destination_port_fallback}, ''), "
        f"NULLIF({destination_port_legacy}, ''), 'unknown')",
        has_triage,
        f"COALESCE({triage_persisted_at}, '')",
        f"COALESCE({triage_status}, '')",
        f"COALESCE({triage_source}, '')",
        f"COALESCE({verdict}, 'unknown')",
        f"COALESCE({risk_level}, {threat_level}, {severity}, 'unknown')",
        f"COALESCE(CAST({triage_ms} AS INTEGER), 0)",
        f"CASE WHEN {attack_success} IN (1, '1', 'true') THEN 1 ELSE 0 END",
    )


def _fact_upsert_sql(prefix):
    columns = ", ".join(_FACT_COLUMNS)
    values = ", ".join(_fact_expressions(prefix))
    return f"INSERT OR REPLACE INTO {FACTS_TABLE} ({columns}) VALUES ({values})"


def _fact_backfill_sql():
    columns = ", ".join(_FACT_COLUMNS)
    values = ", ".join(_fact_expressions("source"))
    return (
        f"INSERT OR REPLACE INTO {FACTS_TABLE} ({columns}) "
        f"SELECT {values} FROM {DEFAULT_SQLITE_TABLE} AS source"
    )


def _triage_marker(prefix):
    status_value = _json_value(prefix, "triage_status")
    persisted_value = _json_value(prefix, "_triage_persisted_at")
    report_value = _json_value(prefix, "triage_report")
    return (
        f"(NULLIF({status_value}, '') IS NOT NULL "
        f"OR NULLIF({persisted_value}, '') IS NOT NULL "
        f"OR {report_value} IS NOT NULL)"
    )


def _column_exists(conn, table_name, column_name):
    return any(
        str(row[1]) == column_name
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    )


def _add_column_if_missing(conn, table_name, column_name, column_definition):
    if not _column_exists(conn, table_name, column_name):
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )


def _drop_dashboard_triggers(conn):
    for trigger_name in (
        "soc_dashboard_fact_insert",
        "soc_dashboard_fact_update",
        "soc_dashboard_fact_delete",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _ensure_alert_record_columns(conn):
    for column_name, column_definition in (
        ("row_id", "TEXT"),
        ("record_id", "TEXT"),
        ("source_file", "TEXT"),
        ("line_number", "INTEGER"),
        ("source_type", "TEXT"),
        ("threat_name", "TEXT"),
        ("is_duplicate", "INTEGER DEFAULT 0"),
    ):
        _add_column_if_missing(
            conn,
            DEFAULT_SQLITE_TABLE,
            column_name,
            column_definition,
        )
    conn.execute(
        f"UPDATE {DEFAULT_SQLITE_TABLE} "
        f"SET row_id = CAST(rowid AS TEXT) "
        f"WHERE row_id IS NULL OR row_id = ''"
    )


def _create_dashboard_triggers(conn):
    insert_fact = _fact_upsert_sql("NEW")
    new_row_key = _fact_expressions("NEW")[1]
    new_persisted = _json_value("NEW", "_triage_persisted_at")
    old_persisted = _json_value("OLD", "_triage_persisted_at")
    new_status = _json_value("NEW", "triage_status")
    old_status = _json_value("OLD", "triage_status")
    new_report = _json_value("NEW", "triage_report")
    old_report = _json_value("OLD", "triage_report")
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS soc_dashboard_fact_insert
        AFTER INSERT ON {DEFAULT_SQLITE_TABLE}
        BEGIN
            {insert_fact};
            INSERT OR IGNORE INTO {ACTIVITY_TABLE} (
                event_key, alert_row_id, record_row_id, asset_date, event_time, record_json
            )
            SELECT COALESCE(NULLIF(NEW.row_id, ''), CAST(NEW.rowid AS TEXT)) ||
                       ':insert:' || COALESCE({new_persisted}, {new_status}, CAST(NEW.rowid AS TEXT)),
                   NEW.rowid, COALESCE(NULLIF(NEW.row_id, ''), CAST(NEW.rowid AS TEXT)),
                   NEW.asset_date, NEW.event_time, NEW.record_json
            WHERE {_triage_marker('NEW')};
        END
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS soc_dashboard_fact_update
        AFTER UPDATE OF row_id, asset_date, event_time, source_type, threat_name, is_duplicate, record_json
        ON {DEFAULT_SQLITE_TABLE}
        BEGIN
            DELETE FROM {FACTS_TABLE}
            WHERE alert_row_id = NEW.rowid OR row_key = {new_row_key};
            {insert_fact};
            INSERT OR IGNORE INTO {ACTIVITY_TABLE} (
                event_key, alert_row_id, record_row_id, asset_date, event_time, record_json
            )
            SELECT COALESCE(NULLIF(NEW.row_id, ''), CAST(NEW.rowid AS TEXT)) ||
                       ':update:' || strftime('%s', 'now') || ':' || lower(hex(randomblob(6))),
                   NEW.rowid, COALESCE(NULLIF(NEW.row_id, ''), CAST(NEW.rowid AS TEXT)),
                   NEW.asset_date, NEW.event_time, NEW.record_json
            WHERE {_triage_marker('NEW')}
              AND NEW.record_json <> OLD.record_json
              AND (
                  COALESCE({new_persisted}, '') <> COALESCE({old_persisted}, '')
                  OR COALESCE({new_status}, '') <> COALESCE({old_status}, '')
                  OR COALESCE({new_report}, '') <> COALESCE({old_report}, '')
              );
        END
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS soc_dashboard_fact_delete
        AFTER DELETE ON {DEFAULT_SQLITE_TABLE}
        BEGIN
            DELETE FROM {FACTS_TABLE} WHERE alert_row_id = OLD.rowid;
        END
        """
    )


def _ensure_sqlite_schema():
    db_path = DEFAULT_SQLITE_DB
    if not db_path.is_file():
        return False
    stat = db_path.stat()
    identity = (str(db_path), getattr(stat, "st_ino", 0))
    if identity in _schema_ready:
        return True
    with _schema_lock:
        if identity in _schema_ready:
            return True
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (DEFAULT_SQLITE_TABLE,),
            ).fetchone()
            if not exists:
                return False
            _drop_dashboard_triggers(conn)
            _ensure_alert_record_columns(conn)
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_alert_records_asset_event "
                f"ON {DEFAULT_SQLITE_TABLE}(asset_date, event_time)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_alert_records_event_time "
                f"ON {DEFAULT_SQLITE_TABLE}(event_time)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_alert_records_asset_date "
                f"ON {DEFAULT_SQLITE_TABLE}(asset_date)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_alert_records_source_type "
                f"ON {DEFAULT_SQLITE_TABLE}(source_type)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_alert_records_threat_name "
                f"ON {DEFAULT_SQLITE_TABLE}(threat_name)"
            )
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_records_row_id "
                f"ON {DEFAULT_SQLITE_TABLE}(row_id)"
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {META_TABLE} (
                    meta_key TEXT PRIMARY KEY,
                    meta_value TEXT NOT NULL
                )
                """
            )
            version_row = conn.execute(
                f"SELECT meta_value FROM {META_TABLE} WHERE meta_key='schema_version'"
            ).fetchone()
            needs_rebuild = not version_row or str(version_row[0]) != SCHEMA_VERSION
            if needs_rebuild:
                conn.execute(f"DROP TABLE IF EXISTS {FACTS_TABLE}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {FACTS_TABLE} (
                    alert_row_id INTEGER PRIMARY KEY,
                    row_key TEXT NOT NULL UNIQUE,
                    asset_date TEXT NOT NULL,
                    event_time INTEGER,
                    source_type TEXT,
                    threat_name TEXT,
                    is_duplicate INTEGER NOT NULL DEFAULT 0,
                    phase TEXT,
                    direction TEXT,
                    result TEXT,
                    protocol TEXT,
                    severity TEXT,
                    response_code TEXT,
                    port TEXT,
                    has_triage INTEGER NOT NULL DEFAULT 0,
                    triage_persisted_at TEXT,
                    triage_status TEXT,
                    triage_source TEXT,
                    verdict TEXT,
                    risk_level TEXT,
                    triage_ms INTEGER NOT NULL DEFAULT 0,
                    attack_success INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_soc_dashboard_facts_asset_event "
                f"ON {FACTS_TABLE}(asset_date, event_time)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_soc_dashboard_facts_event "
                f"ON {FACTS_TABLE}(event_time, asset_date)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_soc_dashboard_facts_triage_event "
                f"ON {FACTS_TABLE}(has_triage, asset_date, event_time)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_soc_dashboard_facts_triage_time "
                f"ON {FACTS_TABLE}(has_triage, event_time, asset_date)"
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {ACTIVITY_TABLE} (
                    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    alert_row_id INTEGER NOT NULL,
                    record_row_id TEXT NOT NULL,
                    asset_date TEXT NOT NULL,
                    event_time INTEGER,
                    record_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_soc_dashboard_activity_event "
                f"ON {ACTIVITY_TABLE}(asset_date, event_time, activity_id)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_soc_dashboard_activity_time "
                f"ON {ACTIVITY_TABLE}(event_time, activity_id)"
            )
            if needs_rebuild:
                conn.execute(_fact_backfill_sql())
                persisted = _json_value("source", "_triage_persisted_at")
                status_value = _json_value("source", "triage_status")
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO {ACTIVITY_TABLE} (
                        event_key, alert_row_id, record_row_id, asset_date, event_time, record_json
                    )
                    SELECT COALESCE(NULLIF(source.row_id, ''), CAST(source.rowid AS TEXT)) || ':bootstrap:' ||
                           COALESCE({persisted}, {status_value}, CAST(source.rowid AS TEXT)),
                           source.rowid,
                           COALESCE(NULLIF(source.row_id, ''), CAST(source.rowid AS TEXT)),
                           source.asset_date,
                           source.event_time, source.record_json
                    FROM {DEFAULT_SQLITE_TABLE} AS source
                    WHERE {_triage_marker('source')}
                    """
                )
                conn.execute(
                    f"INSERT OR REPLACE INTO {META_TABLE}(meta_key, meta_value) "
                    f"VALUES('schema_version', ?)",
                    (SCHEMA_VERSION,),
                )
            _create_dashboard_triggers(conn)
            conn.commit()
        _schema_ready.add(identity)
    return True


def _maybe_prune_activity():
    global _activity_pruned_at
    now = time.monotonic()
    if now - _activity_pruned_at < ACTIVITY_PRUNE_INTERVAL:
        return
    with _schema_lock:
        if now - _activity_pruned_at < ACTIVITY_PRUNE_INTERVAL:
            return
        try:
            with sqlite3.connect(DEFAULT_SQLITE_DB, timeout=5) as conn:
                conn.execute(
                    f"DELETE FROM {ACTIVITY_TABLE} WHERE activity_id <= "
                    f"(SELECT COALESCE(MAX(activity_id), 0) - ? FROM {ACTIVITY_TABLE})",
                    (ACTIVITY_RETENTION_ROWS,),
                )
                conn.commit()
        except Exception:
            pass
        _activity_pruned_at = now


def _safe_json_object(value):
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage_iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _parse_usage_created_at(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def _read_token_usage():
    empty = {
        "totalTokens": 0,
        "todayTokens": 0,
        "todayRequests": 0,
        "dailySeries": [],
        "dailyLabels": [],
        "source": "usage_records",
    }
    if not USAGE_DB.is_file():
        return empty

    now_local = datetime.now().astimezone()
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    first_day = today_start - timedelta(days=6)
    labels = [(first_day + timedelta(days=index)).strftime("%m/%d") for index in range(7)]
    series_by_date = {
        (first_day + timedelta(days=index)).date().isoformat(): 0
        for index in range(7)
    }

    try:
        with sqlite3.connect(f"file:{USAGE_DB}?mode=ro", uri=True, timeout=1.0) as conn:
            conn.execute("PRAGMA query_only = ON")
            total_tokens = _safe_int(
                conn.execute(
                    "SELECT COALESCE(SUM(total_tokens), 0) FROM usage_records"
                ).fetchone()[0]
            )
            today_row = conn.execute(
                "SELECT COALESCE(SUM(total_tokens), 0), COUNT(*) "
                "FROM usage_records WHERE created_at >= ? AND created_at < ?",
                (_usage_iso(today_start), _usage_iso(tomorrow_start)),
            ).fetchone()
            series_rows = conn.execute(
                "SELECT created_at, total_tokens FROM usage_records "
                "WHERE created_at >= ? AND created_at < ?",
                (_usage_iso(first_day), _usage_iso(tomorrow_start)),
            ).fetchall()
    except Exception:
        return {**empty, "dailyLabels": labels, "dailySeries": [0] * 7}

    for created_at, total in series_rows:
        parsed = _parse_usage_created_at(created_at)
        if parsed is None:
            continue
        key = parsed.date().isoformat()
        if key in series_by_date:
            series_by_date[key] += max(_safe_int(total), 0)

    return {
        **empty,
        "totalTokens": max(total_tokens, 0),
        "todayTokens": max(_safe_int(today_row[0]), 0) if today_row else 0,
        "todayRequests": max(_safe_int(today_row[1]), 0) if today_row else 0,
        "dailySeries": list(series_by_date.values()),
        "dailyLabels": labels,
    }


def _workflow_stats_sample_deltas(conn, workflow_name, start_time=0, end_time=0):
    stats_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_stats'"
    ).fetchone()
    if not stats_exists:
        return None
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(workflow_stats)").fetchall()
    }
    success_expr = "success_count" if "success_count" in columns else "0"
    error_expr = "error_count" if "error_count" in columns else "0"
    updated_expr = "updated_at" if "updated_at" in columns else "0"
    current = conn.execute(
        f"SELECT call_count, {success_expr}, {error_expr}, {updated_expr} "
        "FROM workflow_stats WHERE workflow_id = ?",
        (workflow_name,),
    ).fetchone()
    if current is None:
        return None

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {WORKFLOW_SNAPSHOT_TABLE} (
            workflow_id   TEXT NOT NULL,
            sampled_at    INTEGER NOT NULL,
            call_count    INTEGER NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 0,
            error_count   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (workflow_id, sampled_at)
        )
        """
    )
    last = conn.execute(
        f"SELECT sampled_at, call_count, success_count, error_count "
        f"FROM {WORKFLOW_SNAPSHOT_TABLE} WHERE workflow_id = ? "
        "ORDER BY sampled_at DESC LIMIT 1",
        (workflow_name,),
    ).fetchone()
    current_count = max(_safe_int(current[0]), 0)
    success_count = max(_safe_int(current[1]), 0)
    error_count = max(_safe_int(current[2]), 0)
    if last is None or (current_count, success_count, error_count) != tuple(last[1:4]):
        stats_updated_at = _safe_int(current[3]) or int(time.time() * 1000)
        sampled_at = stats_updated_at - (stats_updated_at % 60000)
        if last is not None and sampled_at <= _safe_int(last[0]):
            sampled_at = _safe_int(last[0])
            conn.execute(
                f"UPDATE {WORKFLOW_SNAPSHOT_TABLE} "
                "SET call_count = ?, success_count = ?, error_count = ? "
                "WHERE workflow_id = ? AND sampled_at = ?",
                (current_count, success_count, error_count, workflow_name, sampled_at),
            )
        else:
            conn.execute(
                f"INSERT INTO {WORKFLOW_SNAPSHOT_TABLE} "
                "(workflow_id, sampled_at, call_count, success_count, error_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (workflow_name, sampled_at, current_count, success_count, error_count),
            )
        conn.commit()
        last = (sampled_at, current_count, success_count, error_count)

    if not (start_time > 0 and end_time > 0):
        sampled_at = max(_safe_int(current[3]), _safe_int(last[0] if last else 0))
        return [(current_count, success_count, error_count, sampled_at)] if current_count else []

    start_ms = int(start_time * 1000)
    end_ms = int(end_time * 1000)
    previous = conn.execute(
        f"SELECT call_count, success_count, error_count "
        f"FROM {WORKFLOW_SNAPSHOT_TABLE} "
        "WHERE workflow_id = ? AND sampled_at < ? "
        "ORDER BY sampled_at DESC LIMIT 1",
        (workflow_name, start_ms),
    ).fetchone()
    rows = conn.execute(
        f"SELECT sampled_at, call_count, success_count, error_count "
        f"FROM {WORKFLOW_SNAPSHOT_TABLE} "
        "WHERE workflow_id = ? AND sampled_at >= ? AND sampled_at <= ? "
        "ORDER BY sampled_at",
        (workflow_name, start_ms, end_ms),
    ).fetchall()
    previous_counts = tuple(previous) if previous is not None else (0, 0, 0)
    deltas = []
    for sampled_at, call_count, sample_success, sample_error in rows:
        current_counts = (
            max(_safe_int(call_count), 0),
            max(_safe_int(sample_success), 0),
            max(_safe_int(sample_error), 0),
        )
        delta_counts = tuple(
            current_value - previous_value
            if current_value >= previous_value
            else current_value
            for current_value, previous_value in zip(current_counts, previous_counts)
        )
        if delta_counts[0] > 0:
            deltas.append((*delta_counts, _safe_int(sampled_at)))
        previous_counts = current_counts
    return deltas


def _workflow_metric_value(stats, key, fallback=0):
    value = stats.get(key)
    return max(_safe_int(fallback if value is None else value), 0)


def _workflow_alert_preview(output):
    for key in ("unique_alerts", "enriched_alerts"):
        value = output.get(key)
        if isinstance(value, dict):
            value = value.get("preview")
        if isinstance(value, list):
            return next(
                (
                    item
                    for item in value
                    if isinstance(item, dict) and item.get("_type") != "dict"
                ),
                {},
            )
    return {}


def _workflow_input_preview(inputs):
    syslog_message = inputs.get("syslog_message") or inputs.get("syslog")
    if isinstance(syslog_message, dict):
        message = syslog_message.get("message")
        if isinstance(message, str):
            parsed = _safe_json_object(message)
            if parsed:
                return parsed
    alerts = inputs.get("alerts") or inputs.get("alert_list")
    if isinstance(alerts, dict):
        alerts = alerts.get("data")
    if isinstance(alerts, list):
        return next((item for item in alerts if isinstance(item, dict)), {})
    return {}


def _workflow_execution_metrics(output_text, input_text=""):
    output = _safe_json_object(output_text)
    inputs = _safe_json_object(input_text)
    stats = output.get("stats") if isinstance(output.get("stats"), dict) else {}
    duplicate_flag_available = isinstance(output.get("is_duplicate"), bool)
    metrics_available = duplicate_flag_available or any(
        stats.get(key) is not None
        for key in (
            "raw_count",
            "normalized_count",
            "after_filter_count",
            "after_dedup_count",
        )
    )
    raw_count = _workflow_metric_value(stats, "raw_count", 1)
    normalized_count = _workflow_metric_value(stats, "normalized_count", raw_count)
    after_filter_count = _workflow_metric_value(
        stats,
        "after_filter_count",
        normalized_count,
    )
    unique_count = _workflow_metric_value(
        stats,
        "after_dedup_count",
        after_filter_count,
    )
    filter_removed_count = _workflow_metric_value(
        stats,
        "filter_removed_count",
        max(normalized_count - after_filter_count, 0),
    )
    duplicate_count = _workflow_metric_value(
        stats,
        "dedup_removed_count",
        max(after_filter_count - unique_count, 0),
    )
    is_duplicate = output.get("is_duplicate") is True
    if (
        is_duplicate
        and raw_count == 1
        and after_filter_count == 1
        and unique_count == 1
        and duplicate_count == 0
    ):
        # Older streaming executions only counted duplicates within the current
        # one-alert batch, even when cross-batch state marked it as duplicate.
        unique_count = 0
        duplicate_count = 1
    reduced_count = (
        max(raw_count - unique_count, filter_removed_count + duplicate_count, 0)
        if metrics_available
        else 0
    )

    source_counts = {}
    raw_source_counts = stats.get("normalize_type_counts")
    if isinstance(raw_source_counts, dict) and raw_source_counts.get("_type") != "dict":
        source_counts = {
            _norm(key): max(_safe_int(value), 0)
            for key, value in raw_source_counts.items()
            if max(_safe_int(value), 0) > 0
        }
    preview = _workflow_alert_preview(output) or _workflow_input_preview(inputs)
    syslog_message = inputs.get("syslog_message") or inputs.get("syslog")
    syslog_app = syslog_message.get("app_name") if isinstance(syslog_message, dict) else ""
    source_type = _norm(
        preview.get("_source_type")
        or inputs.get("source_log_type")
        or output.get("source_log_type")
        or output.get("input_mode")
        or syslog_app
    )
    if not source_counts and normalized_count > 0:
        source_counts[source_type] = normalized_count

    return {
        "metricsAvailable": metrics_available,
        "rawCount": raw_count,
        "normalizedCount": normalized_count,
        "afterFilterCount": after_filter_count,
        "uniqueCount": unique_count,
        "filterRemovedCount": filter_removed_count,
        "duplicateCount": duplicate_count,
        "reducedCount": reduced_count,
        "reductionRate": _ratio(reduced_count, raw_count) if metrics_available else 0,
        "dedupRate": _ratio(duplicate_count, after_filter_count) if metrics_available else 0,
        "clusterCount": _workflow_metric_value(stats, "lsh_total_clusters"),
        "isDuplicate": is_duplicate,
        "sourceCounts": source_counts,
        "sourceType": source_type,
        "preview": preview,
    }


def _empty_workflow_denoise_stats():
    return {
        "callCount": 0,
        "successCount": 0,
        "errorCount": 0,
        "earliestStartedAt": 0,
        "latestStartedAt": 0,
        "rawCount": 0,
        "normalizedCount": 0,
        "afterFilterCount": 0,
        "uniqueCount": 0,
        "filterRemovedCount": 0,
        "duplicateCount": 0,
        "reducedCount": 0,
        "reductionRate": 0,
        "dedupRate": 0,
        "sourceCounts": {},
        "seriesRaw": [],
        "seriesUnique": [],
        "timelineLabels": [],
        "timelineWindow": "",
    }


def _get_workflow_denoise_stats(
    workflow_name: str,
    start_time: int = 0,
    end_time: int = 0,
    *,
    force: bool = False,
) -> dict:
    now = time.time()
    cache_key = f"denoise:{workflow_name}:{start_time or 0}:{end_time or 0}"

    with _cache_lock:
        cached = _workflow_stats_cache.get(cache_key)
        if not force and cached and now - float(cached.get("updatedAt") or 0) < _CACHE_TTL:
            _workflow_stats_cache.move_to_end(cache_key)
            return cached["value"]

    empty = _empty_workflow_denoise_stats()
    if not WORKFLOW_DB.is_file():
        return empty

    try:
        with sqlite3.connect(WORKFLOW_DB) as conn:
            sample_deltas = _workflow_stats_sample_deltas(
                conn,
                workflow_name,
                start_time,
                end_time,
            )
            if sample_deltas is None:
                query = (
                    "SELECT status, started_at FROM workflow_executions "
                    "WHERE workflow_id = ?"
                )
                query_params = [workflow_name]
                if start_time > 0 and end_time > 0:
                    query += " AND started_at >= ? AND started_at <= ?"
                    query_params.extend((int(start_time * 1000), int(end_time * 1000)))
                query += " ORDER BY started_at"
                execution_rows = conn.execute(query, query_params).fetchall()
                samples = [
                    (
                        1,
                        1 if str(status).lower() == "success" else 0,
                        0 if str(status).lower() == "success" else 1,
                        _safe_int(started_at),
                    )
                    for status, started_at in execution_rows
                ]
            else:
                samples = sample_deltas

        result_dict = _empty_workflow_denoise_stats()
        processed_total = sum(call_count for call_count, _, _, _ in samples)
        if processed_total:
            result_dict.update(
                {
                    "callCount": processed_total,
                    "successCount": sum(success_count for _, success_count, _, _ in samples),
                    "errorCount": sum(error_count for _, _, error_count, _ in samples),
                    "earliestStartedAt": samples[0][3],
                    "latestStartedAt": samples[-1][3],
                    "rawCount": processed_total,
                    "normalizedCount": processed_total,
                    "afterFilterCount": processed_total,
                    "sourceCounts": {"ndr": processed_total},
                }
            )
            first_started = samples[0][3] // 1000
            last_started = samples[-1][3] // 1000
            bucket_start, bucket_seconds, bucket_count, labels, window = _timeline_spec(
                [],
                start_time or first_started,
                end_time or max(last_started, first_started),
            )
            series_raw = [0] * bucket_count
            for call_count, _, _, started_at in samples:
                index = int(((started_at // 1000) - bucket_start) / bucket_seconds)
                if 0 <= index < bucket_count:
                    series_raw[index] += call_count
            result_dict["seriesRaw"] = series_raw
            result_dict["timelineLabels"] = labels
            result_dict["timelineWindow"] = window

        with _cache_lock:
            _workflow_stats_cache[cache_key] = {"updatedAt": now, "value": result_dict}
            _workflow_stats_cache.move_to_end(cache_key)
            while len(_workflow_stats_cache) > _WORKFLOW_CACHE_MAX:
                _workflow_stats_cache.popitem(last=False)
        return result_dict
    except Exception:
        with _cache_lock:
            cached = _workflow_stats_cache.get(cache_key)
            return cached["value"] if cached else empty


def _get_workflow_progress(
    workflow_name: str,
    start_time: int = 0,
    end_time: int = 0,
) -> dict:
    unavailable = {"callCount": None, "latestStartedAt": None}
    if not WORKFLOW_DB.is_file():
        return unavailable

    try:
        with sqlite3.connect(WORKFLOW_DB) as conn:
            if start_time > 0 and end_time > 0:
                sample_deltas = _workflow_stats_sample_deltas(
                    conn,
                    workflow_name,
                    start_time,
                    end_time,
                )
                if sample_deltas is None:
                    row = conn.execute(
                        "SELECT COUNT(*), COALESCE(MAX(started_at), 0) "
                        "FROM workflow_executions "
                        "WHERE workflow_id = ? AND started_at >= ? AND started_at <= ?",
                        (workflow_name, int(start_time * 1000), int(end_time * 1000)),
                    ).fetchone()
                else:
                    latest_row = conn.execute(
                        "SELECT COALESCE(MAX(started_at), 0) FROM workflow_executions "
                        "WHERE workflow_id = ? AND started_at >= ? AND started_at <= ?",
                        (workflow_name, int(start_time * 1000), int(end_time * 1000)),
                    ).fetchone()
                    latest_sample = sample_deltas[-1][3] if sample_deltas else 0
                    row = (
                        sum(max(_safe_int(item[0]), 0) for item in sample_deltas),
                        max(_safe_int(latest_row[0] if latest_row else 0), _safe_int(latest_sample)),
                    )
            else:
                row = conn.execute(
                    "SELECT call_count FROM workflow_stats WHERE workflow_id = ?",
                    (workflow_name,),
                ).fetchone()
    except Exception:
        return unavailable

    if row is None:
        return {"callCount": 0, "latestStartedAt": 0}
    return {
        "callCount": max(_safe_int(row[0]), 0),
        "latestStartedAt": max(_safe_int(row[1] if len(row) > 1 else 0), 0),
    }


def _get_workflow_recent_events(
    workflow_name: str,
    start_time: int = 0,
    end_time: int = 0,
    limit: int = 10,
) -> list:
    if not WORKFLOW_DB.is_file():
        return []
    query = (
        "SELECT id, status, started_at, output_results, input_params "
        "FROM workflow_executions WHERE workflow_id = ?"
    )
    query_params = [workflow_name]
    if start_time > 0 and end_time > 0:
        query += " AND started_at >= ? AND started_at <= ?"
        query_params.extend((int(start_time * 1000), int(end_time * 1000)))
    query += " ORDER BY started_at DESC LIMIT ?"
    query_params.append(max(1, min(_safe_int(limit), 10)))
    try:
        with sqlite3.connect(WORKFLOW_DB) as conn:
            rows = conn.execute(query, query_params).fetchall()
    except Exception:
        return []

    events = []
    for execution_id, status, started_at, output_text, input_text in rows:
        metrics = _workflow_execution_metrics(output_text, input_text)
        preview = metrics["preview"]
        raw_count = metrics["rawCount"]
        unique_count = metrics["uniqueCount"]
        threat_name = str(
            preview.get("threat_name")
            or preview.get("_threat_type")
            or preview.get("threat_type")
            or f"降噪批次 · 原始 {raw_count} 条"
        )
        events.append(
            {
                "eventId": f"workflow-execution:{execution_id}",
                "stage": "denoise",
                "status": "completed" if str(status).lower() == "success" else "failed",
                "occurredAt": datetime.fromtimestamp(
                    _safe_int(started_at) / 1000
                ).astimezone().isoformat(timespec="seconds"),
                "triggerSource": "workflow_execution",
                "sampleCount": max(unique_count, 1),
                "alert": {
                    "id": str(preview.get("id") or execution_id),
                    "sourceType": metrics["sourceType"],
                    "threatName": threat_name,
                    "srcIp": preview.get("sip") or preview.get("src_ip") or preview.get("net_real_src_ip"),
                    "dstIp": preview.get("dip") or preview.get("dst_ip") or preview.get("net_dest_ip"),
                },
                "result": {
                    "isDuplicate": metrics["isDuplicate"],
                    "clusterId": str(metrics["clusterCount"] or "--"),
                    **{
                        key: value
                        for key, value in metrics.items()
                        if key not in {"preview", "sourceCounts", "sourceType"}
                    },
                },
            }
        )
    return events


SOURCE_DEFS = [
    ("ndr", "NDR", ("ndr", "tdp", "network")),
    ("edr", "HIDS", ("edr", "hids", "linux")),
    ("waf", "WAF Web 防护", ("waf", "web")),
    ("ids", "IDS/IPS 入侵检测", ("ids", "ips", "skyeye")),
    ("cloud", "云日志", ("cloud", "aliyun", "qcloud")),
    ("vuln", "漏洞情报", ("vuln", "cve", "qingteng")),
    ("other", "其他接入", ("other", "unknown", "none")),
]

PHASE_LABELS = {
    "recon": "侦察探测",
    "exploit": "漏洞利用",
    "post_exploit": "后渗透",
    "control": "控制通信",
    "unknown": "未知阶段",
}

DIRECTION_LABELS = {
    "in": "入站",
    "out": "出站",
    "lateral": "横向",
    "unknown": "未知方向",
}

RESULT_LABELS = {
    "success": "攻击成功",
    "succeeded": "攻击成功",
    "failed": "攻击失败",
    "blocked": "已阻断",
    "attack_success": "攻击成功",
    "attack": "攻击行为",
    "attack_failed": "攻击失败",
    "benign": "良性",
    "unknown": "待确认",
}


async def get_activity(ctx, request):
    params = dict(request.query_params)
    return await asyncio.to_thread(_get_activity, params)


async def get_task_center(ctx, request):
    return await asyncio.to_thread(_get_task_center)


def _table_exists(conn, table_name):
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    )


def _task_center_empty():
    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sessionCount": 0,
        "scheduledTasks": [],
        "workflows": [],
        "sourceStatus": {
            "tasksDb": str(TASK_DB),
            "workflowDb": str(WORKFLOW_DB),
            "tasksAvailable": TASK_DB.is_file(),
            "workflowAvailable": WORKFLOW_DB.is_file(),
        },
    }


def _task_trigger_value(trigger, key):
    if not isinstance(trigger, dict):
        return ""
    return trigger.get(key) or trigger.get(key[0].lower() + key[1:]) or ""


def _today_bounds():
    now_local = datetime.now().astimezone()
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start, today_start + timedelta(days=1)


def _task_center_task_rows(limit=12):
    if not TASK_DB.is_file():
        return 0, [], 0, 0
    try:
        with sqlite3.connect(f"file:{TASK_DB}?mode=ro", uri=True, timeout=1.0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            if not (
                _table_exists(conn, "task_schedulers")
                and _table_exists(conn, "task_executions")
            ):
                return 0, [], 0, 0
            today_start, tomorrow_start = _today_bounds()
            today_start_iso = today_start.isoformat(timespec="seconds")
            tomorrow_start_iso = tomorrow_start.isoformat(timespec="seconds")
            session_count = _safe_int(
                conn.execute(
                    "SELECT COUNT(DISTINCT session_id) FROM task_executions "
                    "WHERE session_id IS NOT NULL AND session_id <> ''"
                ).fetchone()[0]
            )
            scheduler_rows = conn.execute(
                "SELECT id, title, mode, status, trigger, execution_mode, workflow_id, updated_at "
                "FROM task_schedulers WHERE status <> 'archived' "
                "ORDER BY updated_at DESC"
            ).fetchall()
            tasks = []
            for scheduler in scheduler_rows:
                summary = conn.execute(
                    "SELECT COUNT(*) AS execution_count, "
                    "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS success_count, "
                    "SUM(CASE WHEN status IN ('pending', 'queued', 'running') THEN 1 ELSE 0 END) AS active_count, "
                    "SUM(CASE WHEN "
                    "julianday(COALESCE(completed_at, updated_at, started_at, queued_at, created_at)) >= julianday(?) "
                    "AND julianday(COALESCE(completed_at, updated_at, started_at, queued_at, created_at)) < julianday(?) "
                    "THEN 1 ELSE 0 END) AS today_execution_count "
                    "FROM task_executions WHERE scheduler_id = ?",
                    (today_start_iso, tomorrow_start_iso, scheduler["id"]),
                ).fetchone()
                latest = conn.execute(
                    "SELECT status, queued_at, started_at, completed_at, updated_at "
                    "FROM task_executions WHERE scheduler_id = ? "
                    "ORDER BY julianday(COALESCE(completed_at, updated_at, started_at, queued_at, created_at)) DESC "
                    "LIMIT 1",
                    (scheduler["id"],),
                ).fetchone()
                trigger = _safe_json_object(scheduler["trigger"])
                execution_count = max(_safe_int(summary["execution_count"]), 0)
                success_count = max(_safe_int(summary["success_count"]), 0)
                active_count = max(_safe_int(summary["active_count"]), 0)
                today_execution_count = max(_safe_int(summary["today_execution_count"]), 0)
                last_run_at = ""
                if latest:
                    last_run_at = latest["completed_at"] or latest["updated_at"] or latest["started_at"] or latest["queued_at"] or ""
                tasks.append(
                    {
                        "id": scheduler["id"],
                        "name": scheduler["title"] or scheduler["id"],
                        "mode": scheduler["mode"] or "once",
                        "status": scheduler["status"] or "active",
                        "executionMode": scheduler["execution_mode"] or "agent",
                        "workflowId": scheduler["workflow_id"] or "",
                        "executionCount": execution_count,
                        "todayExecutionCount": today_execution_count,
                        "successCount": success_count,
                        "successRate": _ratio(success_count, execution_count),
                        "activeCount": active_count,
                        "lastStatus": latest["status"] if latest else "",
                        "lastRunAt": last_run_at,
                        "nextRunAt": _task_trigger_value(trigger, "nextRun"),
                        "cron": _task_trigger_value(trigger, "cron"),
                        "cronDescription": _task_trigger_value(trigger, "cronDescription"),
                    }
                )
            tasks.sort(
                key=lambda item: (
                    item["activeCount"] > 0,
                    item["lastRunAt"] or "",
                    item["executionCount"],
                ),
                reverse=True,
            )
            return (
                session_count,
                tasks[:limit],
                sum(task["executionCount"] for task in tasks),
                sum(task["todayExecutionCount"] for task in tasks),
            )
    except Exception:
        return 0, [], 0, 0


def _workflow_manifest_name_map():
    roots = [
        Path.home() / ".flocks" / "plugins" / "workflows",
        Path.home() / ".flocks" / "workspace" / "workflows",
        Path(__file__).resolve().parents[4] / "workflows",
    ]
    names = {}
    for root in roots:
        if not root.is_dir():
            continue
        for manifest_path in root.glob("*/manifest.json"):
            workflow_id = manifest_path.parent.name
            try:
                manifest = _safe_json_object(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
            name = (
                manifest.get("nameCn")
                or manifest.get("name")
                or manifest.get("title")
                or workflow_id
            )
            name_i18n = manifest.get("nameI18n")
            if isinstance(name_i18n, dict):
                name = name_i18n.get("zh-CN") or name_i18n.get("zh") or name
            names.setdefault(workflow_id, str(name))
    return names


def _workflow_config_name_map(conn):
    if not _table_exists(conn, "workflow_configs"):
        return {}
    names = {}
    for row in conn.execute(
        "SELECT workflow_id, config FROM workflow_configs WHERE kind = ?",
        ("workflow.integration-config",),
    ).fetchall():
        workflow_id = row["workflow_id"] if isinstance(row, sqlite3.Row) else row[0]
        config_raw = row["config"] if isinstance(row, sqlite3.Row) else row[1]
        if not workflow_id:
            continue
        config = _safe_json_object(config_raw)
        workflow = config.get("workflow")
        if not isinstance(workflow, dict):
            continue
        name = workflow.get("name") or workflow.get("title") or workflow.get("id")
        if name:
            names.setdefault(str(workflow_id), str(name))
    return names


def _task_center_workflow_rows(limit=12):
    if not WORKFLOW_DB.is_file():
        return [], 0, 0
    try:
        with sqlite3.connect(f"file:{WORKFLOW_DB}?mode=ro", uri=True, timeout=1.0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            if not (
                _table_exists(conn, "workflow_stats")
                or _table_exists(conn, "workflow_executions")
            ):
                return [], 0, 0
            today_start, tomorrow_start = _today_bounds()
            today_start_ms = int(today_start.timestamp() * 1000)
            tomorrow_start_ms = int(tomorrow_start.timestamp() * 1000)
            workflow_ids = set()
            if _table_exists(conn, "workflow_stats"):
                workflow_ids.update(
                    row[0]
                    for row in conn.execute("SELECT workflow_id FROM workflow_stats").fetchall()
                    if row[0]
                )
            if _table_exists(conn, "workflow_executions"):
                workflow_ids.update(
                    row[0]
                    for row in conn.execute(
                        "SELECT DISTINCT workflow_id FROM workflow_executions"
                    ).fetchall()
                    if row[0]
                )
            names = {
                **_workflow_manifest_name_map(),
                **_workflow_config_name_map(conn),
                **WORKFLOW_DISPLAY_NAMES,
            }
            workflow_ids.update(SOC_PINNED_WORKFLOW_NAMES)
            stats_columns = (
                {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(workflow_stats)").fetchall()
                }
                if _table_exists(conn, "workflow_stats")
                else set()
            )
            stats_success_expr = "success_count" if "success_count" in stats_columns else "0"
            stats_error_expr = "error_count" if "error_count" in stats_columns else "0"
            stats_updated_expr = "updated_at" if "updated_at" in stats_columns else "0"
            workflows = []
            for workflow_id in workflow_ids:
                workflow_name = names.get(workflow_id, workflow_id)
                if UUID_RE.match(str(workflow_id)) and workflow_name == workflow_id:
                    continue
                stats = None
                if _table_exists(conn, "workflow_stats"):
                    stats = conn.execute(
                        f"SELECT call_count, {stats_success_expr}, {stats_error_expr}, {stats_updated_expr} "
                        "FROM workflow_stats WHERE workflow_id = ?",
                        (workflow_id,),
                    ).fetchone()
                latest = None
                exec_summary = None
                if _table_exists(conn, "workflow_executions"):
                    exec_summary = conn.execute(
                        "SELECT COUNT(*) AS execution_count, "
                        "SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count, "
                        "SUM(CASE WHEN status IN ('running') THEN 1 ELSE 0 END) AS active_count, "
                        "SUM(CASE WHEN COALESCE(finished_at, updated_at, started_at, 0) >= ? "
                        "AND COALESCE(finished_at, updated_at, started_at, 0) < ? THEN 1 ELSE 0 END) "
                        "AS today_execution_count "
                        "FROM workflow_executions WHERE workflow_id = ?",
                        (today_start_ms, tomorrow_start_ms, workflow_id),
                    ).fetchone()
                    latest = conn.execute(
                        "SELECT id, status, started_at, finished_at, updated_at "
                        "FROM workflow_executions WHERE workflow_id = ? "
                        "ORDER BY COALESCE(finished_at, updated_at, started_at, 0) DESC LIMIT 1",
                        (workflow_id,),
                    ).fetchone()
                execution_count = max(
                    _safe_int(stats["call_count"] if stats else 0),
                    _safe_int(exec_summary["execution_count"] if exec_summary else 0),
                )
                success_count = max(
                    _safe_int(stats["success_count"] if stats else 0),
                    _safe_int(exec_summary["success_count"] if exec_summary else 0),
                )
                active_count = max(_safe_int(exec_summary["active_count"] if exec_summary else 0), 0)
                today_execution_count = max(
                    _safe_int(exec_summary["today_execution_count"] if exec_summary else 0),
                    0,
                )
                last_run_at = 0
                if latest:
                    last_run_at = (
                        _safe_int(latest["finished_at"])
                        or _safe_int(latest["updated_at"])
                        or _safe_int(latest["started_at"])
                    )
                workflows.append(
                    {
                        "id": workflow_id,
                        "name": workflow_name,
                        "executionCount": execution_count,
                        "todayExecutionCount": today_execution_count,
                        "successCount": success_count,
                        "successRate": _ratio(success_count, execution_count),
                        "activeCount": active_count,
                        "lastStatus": latest["status"] if latest else "",
                        "lastRunAt": last_run_at,
                        "latestExecutionHash": str(latest["id"] if latest else ""),
                    }
                )
            soc_order = {
                "stream_alert_denoise": 3,
                "stream_alert_triage": 2,
                "onesec_kafka_investigation": 1,
                "tdp_alert_triage": 1,
                "sec_alert_unified_ops": 1,
            }
            workflows.sort(
                key=lambda item: (
                    item["activeCount"] > 0,
                    item["lastRunAt"],
                    item["executionCount"],
                    soc_order.get(item["id"], 0),
                ),
                reverse=True,
            )
            return (
                workflows[:limit],
                sum(workflow["executionCount"] for workflow in workflows),
                sum(workflow["todayExecutionCount"] for workflow in workflows),
            )
    except Exception:
        return [], 0, 0


def _get_task_center():
    session_count, tasks, scheduled_execution_count, scheduled_today_execution_count = _task_center_task_rows()
    workflows, workflow_execution_count, workflow_today_execution_count = _task_center_workflow_rows()
    return {
        **_task_center_empty(),
        "sessionCount": session_count,
        "scheduledTasks": tasks,
        "scheduledExecutionCount": scheduled_execution_count,
        "scheduledTodayExecutionCount": scheduled_today_execution_count,
        "workflowExecutionCount": workflow_execution_count,
        "workflowTodayExecutionCount": workflow_today_execution_count,
        "workflows": workflows,
    }


def _get_activity(params):
    _ensure_sqlite_schema()
    _maybe_prune_activity()
    settings = _sqlite_settings()
    db_path = settings["db_path"]
    time_window = _normalize_time_window(
        params.get("startTime"),
        params.get("endTime"),
    )
    start_time, end_time = time_window or (0, 0)
    workflow_stats = _get_workflow_progress(
        "stream_alert_denoise",
        start_time,
        end_time,
    )
    workflow_events = _get_workflow_recent_events(
        "stream_alert_denoise",
        start_time,
        end_time,
    )
    raw_cursor = str(params.get("cursor") or "").strip()
    bootstrap = str(params.get("bootstrap") or "").strip().lower() == "latest"
    limit = max(1, min(_safe_int(params.get("limit") or ACTIVITY_DEFAULT_LIMIT), ACTIVITY_MAX_LIMIT))

    if not db_path.is_file():
        return _activity_response(
            [],
            0,
            "",
            cursor_reset=bool(raw_cursor),
            workflow_stats=workflow_stats,
            workflow_events=workflow_events,
        )

    cursor = _decode_activity_cursor(raw_cursor) if raw_cursor else None
    cursor_reset = bool(raw_cursor and cursor is None)

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            latest_row_id, latest_activity_id = _activity_latest_cursor(conn, settings)

            if bootstrap or cursor is None:
                recent_events = _activity_recent_events(
                    conn,
                    settings,
                    limit=10,
                    start_time=start_time,
                    end_time=end_time,
                )
                return _activity_response(
                    [],
                    latest_row_id,
                    latest_activity_id,
                    cursor_reset=cursor_reset,
                    recent_events=recent_events,
                    workflow_stats=workflow_stats,
                    workflow_events=workflow_events,
                )

            last_row_id = max(_safe_int(cursor.get("lastRowId")), 0)
            last_activity_id = max(_safe_int(cursor.get("lastActivityId")), 0)
            if last_row_id > latest_row_id or last_activity_id > latest_activity_id:
                last_row_id = 0
                last_activity_id = 0
                cursor_reset = True
            rows, overflow_count, batch = _activity_rows(
                conn,
                settings,
                last_row_id=last_row_id,
                last_activity_id=last_activity_id,
                latest_row_id=latest_row_id,
                latest_activity_id=latest_activity_id,
                limit=limit,
            )
    except Exception as exc:
        return {
            **_activity_response(
                [],
                0,
                "",
                workflow_stats=workflow_stats,
                workflow_events=workflow_events,
            ),
            "error": f"activity query failed: {exc}",
        }

    events = []
    seen = set()
    for row in rows:
        event = _activity_event(row)
        if event is None or event["eventId"] in seen:
            continue
        seen.add(event["eventId"])
        events.append(event)

    return {
        **_activity_response(
            events,
            latest_row_id,
            latest_activity_id,
            cursor_reset=cursor_reset,
            batch=batch,
            workflow_stats=workflow_stats,
            workflow_events=workflow_events,
        ),
        "overflowCount": overflow_count,
    }


def _activity_response(
    events,
    last_row_id,
    last_activity_id,
    *,
    cursor_reset=False,
    recent_events=None,
    batch=None,
    workflow_stats=None,
    workflow_events=None,
):
    return {
        "cursor": _encode_activity_cursor(last_row_id, last_activity_id),
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "events": events,
        "recentEvents": recent_events or [],
        "overflowCount": 0,
        "batch": batch or _empty_activity_batch(),
        "cursorReset": cursor_reset,
        "workflowStats": workflow_stats or {"callCount": None, "latestStartedAt": None},
        "workflowEvents": workflow_events or [],
        "tokenUsage": _read_token_usage(),
    }


def _empty_activity_batch():
    return {
        "mode": "normal",
        "windowMs": ACTIVITY_WINDOW_MS,
        "receivedCount": 0,
        "duplicateCount": 0,
        "uniqueCount": 0,
        "clusterCount": 0,
        "triageUpdatedCount": 0,
        "sampledCount": 0,
        "suppressedCount": 0,
        "ratePerSecond": 0,
    }


def _encode_activity_cursor(last_row_id, last_activity_id):
    payload = json.dumps(
        {
            "lastRowId": max(_safe_int(last_row_id), 0),
            "lastActivityId": max(_safe_int(last_activity_id), 0),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_activity_cursor(value):
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode((value + padding).encode("ascii")))
    except Exception:
        return None
    if not isinstance(payload, dict) or "lastRowId" not in payload:
        return None
    return payload


def _activity_latest_cursor(conn, settings):
    latest_row_id = _safe_int(
        conn.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {settings['table']}").fetchone()[0]
    )
    latest_activity_id = _safe_int(
        conn.execute(
            f"SELECT COALESCE(MAX(activity_id), 0) FROM {settings['activity_table']}"
        ).fetchone()[0]
    )
    return latest_row_id, latest_activity_id


def _activity_rows(
    conn,
    settings,
    *,
    last_row_id,
    last_activity_id,
    latest_row_id,
    latest_activity_id,
    limit,
):
    summary = _activity_insert_summary(conn, settings, last_row_id, latest_row_id)
    new_count = summary["receivedCount"]
    updated_count = _safe_int(
        conn.execute(
            f"SELECT COUNT(*) FROM {settings['activity_table']} "
            f"WHERE alert_row_id <= ? AND activity_id > ? AND activity_id <= ?",
            (last_row_id, last_activity_id, latest_activity_id),
        ).fetchone()[0]
    )

    triage_reserve = min(updated_count, 3)
    inserted = _activity_insert_samples(
        conn,
        settings,
        last_row_id=last_row_id,
        latest_row_id=latest_row_id,
        received_count=new_count,
        limit=max(limit - triage_reserve, 1),
    )
    remaining = max(limit - len(inserted), 0)
    updated = []
    if remaining and updated_count:
        updated = conn.execute(
            f"SELECT alert_row_id AS activity_row_id, activity_id AS activity_log_id, "
            f"event_time AS activity_event_time, record_json, 1 AS sample_count "
            f"FROM {settings['activity_table']} "
            f"WHERE alert_row_id <= ? AND activity_id > ? AND activity_id <= ? "
            f"ORDER BY activity_id DESC LIMIT ?",
            (last_row_id, last_activity_id, latest_activity_id, remaining),
        ).fetchall()

    rows = list(reversed(inserted)) + list(reversed(updated))
    sampled_count = len(inserted)
    batch = {
        **summary,
        "mode": (
            "surge"
            if new_count > ACTIVITY_SURGE_LIMIT
            else "burst" if new_count > ACTIVITY_NORMAL_LIMIT else "normal"
        ),
        "windowMs": ACTIVITY_WINDOW_MS,
        "triageUpdatedCount": updated_count,
        "sampledCount": sampled_count,
        "suppressedCount": max(new_count - sampled_count, 0),
        "ratePerSecond": round(new_count / (ACTIVITY_WINDOW_MS / 1000), 1),
    }
    return rows, max(new_count + updated_count - len(rows), 0), batch


def _activity_table_columns(conn, settings):
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({settings['table']})").fetchall()
    }


def _activity_insert_summary(conn, settings, last_row_id, latest_row_id):
    columns = _activity_table_columns(conn, settings)
    table = settings["table"]
    if {"is_duplicate", "threat_name", "source_type"}.issubset(columns):
        row = conn.execute(
            f"SELECT COUNT(*) AS received_count, "
            f"COALESCE(SUM(CASE WHEN \"is_duplicate\" = 1 THEN 1 ELSE 0 END), 0) AS duplicate_count, "
            f"COUNT(DISTINCT COALESCE(NULLIF(\"source_type\", ''), 'unknown') || '|' || "
            f"COALESCE(NULLIF(\"threat_name\", ''), 'unknown') || '|' || CAST(\"is_duplicate\" AS TEXT)) "
            f"AS cluster_count FROM {table} WHERE rowid > ? AND rowid <= ?",
            (last_row_id, latest_row_id),
        ).fetchone()
        received_count = _safe_int(row[0])
        duplicate_count = _safe_int(row[1])
        cluster_count = _safe_int(row[2])
    else:
        received_count = _safe_int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE rowid > ? AND rowid <= ?",
                (last_row_id, latest_row_id),
            ).fetchone()[0]
        )
        duplicate_count = 0
        cluster_count = received_count
    return {
        "receivedCount": received_count,
        "duplicateCount": duplicate_count,
        "uniqueCount": max(received_count - duplicate_count, 0),
        "clusterCount": cluster_count,
    }


def _activity_insert_samples(conn, settings, *, last_row_id, latest_row_id, received_count, limit):
    if received_count <= 0:
        return []
    table = settings["table"]
    record_column = settings["record_column"]
    if received_count <= limit:
        return conn.execute(
            f"SELECT rowid AS activity_row_id, {settings['event_time_column']} AS activity_event_time, "
            f"{record_column} AS record_json, 1 AS sample_count "
            f"FROM {table} WHERE rowid > ? AND rowid <= ? ORDER BY rowid DESC",
            (last_row_id, latest_row_id),
        ).fetchall()

    columns = _activity_table_columns(conn, settings)
    if {"is_duplicate", "threat_name", "source_type"}.issubset(columns):
        return conn.execute(
            f"SELECT MAX(rowid) AS activity_row_id, MAX({settings['event_time_column']}) AS activity_event_time, "
            f"{record_column} AS record_json, COUNT(*) AS sample_count "
            f"FROM {table} WHERE rowid > ? AND rowid <= ? "
            f"GROUP BY COALESCE(NULLIF(\"source_type\", ''), 'unknown'), "
            f"COALESCE(NULLIF(\"threat_name\", ''), 'unknown'), \"is_duplicate\" "
            f"ORDER BY sample_count DESC, activity_row_id DESC LIMIT ?",
            (last_row_id, latest_row_id, limit),
        ).fetchall()
    return conn.execute(
        f"SELECT rowid AS activity_row_id, {settings['event_time_column']} AS activity_event_time, "
        f"{record_column} AS record_json, 1 AS sample_count "
        f"FROM {table} WHERE rowid > ? AND rowid <= ? ORDER BY rowid DESC LIMIT ?",
        (last_row_id, latest_row_id, limit),
    ).fetchall()


def _activity_recent_events(conn, settings, *, limit, start_time=0, end_time=0):
    raw_time_condition = ""
    activity_time_condition = ""
    time_params = []
    if start_time > 0 and end_time > 0:
        start_date = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d")
        end_date = datetime.fromtimestamp(end_time).strftime("%Y-%m-%d")
        raw_time_condition = (
            f"source.{settings['date_column']} BETWEEN ? AND ? "
            f"AND source.{settings['event_time_column']} BETWEEN ? AND ?"
        )
        activity_time_condition = (
            "asset_date BETWEEN ? AND ? AND event_time BETWEEN ? AND ?"
        )
        time_params = [start_date, end_date, start_time, end_time]
    recent_where = f" WHERE {raw_time_condition}" if raw_time_condition else ""
    recent_rows = conn.execute(
        f"SELECT source.rowid AS activity_row_id, "
        f"source.{settings['event_time_column']} AS activity_event_time, "
        f"source.{settings['record_column']} AS record_json, "
        f"1 AS sample_count FROM {settings['table']} AS source{recent_where} "
        f"ORDER BY source.{settings['event_time_column']} DESC, source.rowid DESC LIMIT ?",
        (*time_params, limit),
    ).fetchall()
    triage_where = f" WHERE {activity_time_condition}" if activity_time_condition else ""
    triage_rows = conn.execute(
        f"SELECT alert_row_id AS activity_row_id, activity_id AS activity_log_id, "
        f"event_time AS activity_event_time, record_json, 1 AS sample_count "
        f"FROM {settings['activity_table']}{triage_where} "
        f"ORDER BY event_time DESC, activity_id DESC LIMIT ?",
        (*time_params, limit),
    ).fetchall()
    events = []
    seen = set()
    for row in [*recent_rows, *triage_rows]:
        event = _activity_event(row)
        if event is None or event["eventId"] in seen:
            continue
        seen.add(event["eventId"])
        events.append(event)
    return events


def _activity_event(row):
    try:
        record = json.loads(row["record_json"])
    except Exception:
        return None
    if not isinstance(record, dict):
        return None

    row_id = str(row["activity_row_id"])
    triage_status = str(record.get("triage_status") or "").strip().lower()
    triage_at = str(record.get("_triage_persisted_at") or "")
    is_triage = bool(triage_status or triage_at or record.get("triage_report"))
    stage = "triage" if is_triage else "denoise"
    version = triage_at or row_id
    event_time = _activity_event_time(record)
    if not event_time and "activity_event_time" in row.keys():
        parsed_event_time = _parse_event_time(row["activity_event_time"])
        event_time = parsed_event_time.astimezone().isoformat(timespec="seconds") if parsed_event_time else ""
    event = {
        "eventId": f"{row_id}:{stage}:{version}",
        "stage": stage,
        "status": "failed" if triage_status in {"failed", "error"} else "completed",
        "occurredAt": event_time,
        "sampleCount": max(_safe_int(row["sample_count"]), 1) if "sample_count" in row.keys() else 1,
        "alert": {
            "id": _first_activity_text(record, "id", "record_id", "uuid", "event_id", "dedup_key"),
            "sourceType": _first_activity_text(record, "_source_type", "source_type", "device_type"),
            "threatName": _first_activity_text(record, "threat_name", "_threat_type", "threat_type") or "未知告警",
            "srcIp": _first_activity_text(record, "sip", "src_ip", "source_ip"),
            "dstIp": _first_activity_text(record, "dip", "dst_ip", "destination_ip"),
            "requestUri": _first_activity_text(record, "req_http_url", "uri", "url"),
            "threatPhase": _first_activity_text(record, "threat_phase"),
            "threatType": _first_activity_text(record, "threat_type", "_threat_type"),
        },
    }
    if stage == "denoise":
        event["result"] = {
            "isDuplicate": record.get("is_duplicate") is True,
            "clusterId": _first_activity_text(record, "_lsh_cluster_id"),
            "dedupKey": _first_activity_text(record, "dedup_key"),
        }
    else:
        verdict = _norm(record.get("attack_verdict") or "unknown")
        event["result"] = {
            "triageStatus": triage_status or "completed",
            "triageSource": str(record.get("triage_source") or "").strip().lower() or "triaged",
            "durationMs": _safe_int(record.get("triage_ms")),
            "verdict": verdict,
            "verdictLabel": RESULT_LABELS.get(verdict, "待确认"),
            "riskLevel": _first_activity_text(record, "risk_level", "threat_level"),
            "reportTitle": _first_activity_text(record, "report_title"),
            "hasReport": bool(record.get("triage_report")),
        }
    return event


def _activity_event_time(record):
    for key in ("time", "event_time", "timestamp", "timestamp_real", "occur_time", "created_at"):
        value = _parse_event_time(record.get(key))
        if value is not None:
            return value.astimezone().isoformat(timespec="seconds")
    return ""


def _first_activity_text(record, *keys):
    for key in keys:
        value = record.get(key)
        if value not in (None, "", "none", "None"):
            return str(value)
    return ""


def _file_revision(path):
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except Exception:
        return 0, 0


def _stats_cache_ttl(start_time, end_time):
    span = max(end_time - start_time, 0)
    if span <= 2 * 60 * 60:
        return 15.0
    if span <= 24 * 60 * 60:
        return 30.0
    if span <= 7 * 24 * 60 * 60:
        return 120.0
    return _STATS_RESPONSE_CACHE_TTL


def _stats_cache_get(cache_key, ttl):
    now = time.monotonic()
    with _cache_lock:
        cached = _stats_response_cache.get(cache_key)
        if not cached:
            return None
        if now - float(cached.get("updatedAt") or 0) >= ttl:
            _stats_response_cache.pop(cache_key, None)
            return None
        _stats_response_cache.move_to_end(cache_key)
        return cached["value"]


def _stats_cache_put(cache_key, value):
    with _cache_lock:
        _stats_response_cache[cache_key] = {
            "updatedAt": time.monotonic(),
            "value": value,
        }
        _stats_response_cache.move_to_end(cache_key)
        while len(_stats_response_cache) > _STATS_RESPONSE_CACHE_MAX:
            _stats_response_cache.popitem(last=False)


async def get_stats(ctx, request):
    params = dict(request.query_params)
    return await asyncio.to_thread(_get_stats, params)


def _get_stats(params):
    _ensure_sqlite_schema()
    time_window = _normalize_time_window(
        params.get("startTime"),
        params.get("endTime"),
    )
    if time_window:
        start_time, end_time = time_window
        start_date = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d")
        end_date = datetime.fromtimestamp(end_time).strftime("%Y-%m-%d")
        date = start_date
    else:
        start_time = end_time = 0
        date = _normalize_date(params.get("date") or _latest_asset_date())
        start_date, end_date = _normalize_range(
            params.get("startDate"),
            params.get("endDate"),
            date,
        )
    started = time.time()
    range_start_time = start_time or int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
    range_end_time = end_time or int(
        (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).timestamp()
    ) - 1
    cache_key = (
        start_date,
        end_date,
        start_time,
        end_time,
    )
    force_refresh = str(params.get("force") or "").strip().lower() in {"1", "true", "yes"}
    cached = None if force_refresh else _stats_cache_get(
        cache_key,
        _stats_cache_ttl(range_start_time, range_end_time),
    )
    if cached is not None:
        return {
            **cached,
            "tokenUsage": _read_token_usage(),
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "latencyMs": round((time.time() - started) * 1000),
            "cacheHit": True,
        }

    denoise_files, denoise_locations = [], []
    triage_files, triage_locations = [], []

    asset_files = _find_asset_files(start_date, end_date, start_time, end_time)
    asset_denoise_files = [path for path in asset_files if _asset_file_role(path) == "denoise"]
    asset_triage_files = [path for path in asset_files if _asset_file_role(path) == "triage"]
    sample_mode = bool(asset_denoise_files or asset_triage_files)
    if asset_denoise_files:
        denoise_files = asset_denoise_files
    triage_files = asset_triage_files or asset_denoise_files

    workflow_stats = _get_workflow_denoise_stats(
        "stream_alert_denoise",
        range_start_time,
        range_end_time,
        force=force_refresh,
    )
    denoise = _read_denoise(denoise_files, workflow_stats.get("callCount", 0))
    soc_unique_count = denoise["totalUnique"]
    soc_unique_series = denoise["seriesUnique"]
    timeline_labels = workflow_stats["timelineLabels"] or denoise.get("_timelineLabels", [])
    timeline_window = workflow_stats["timelineWindow"] or denoise.get("_timelineWindow", "")
    workflow_series_raw = workflow_stats["seriesRaw"]
    if not workflow_series_raw and soc_unique_series:
        workflow_series_raw = [0] * len(soc_unique_series)
    processed_total = workflow_stats["callCount"]
    reduced_count = max(processed_total - soc_unique_count, 0)
    reduction_rate = _ratio(reduced_count, processed_total)
    denoise.update(
        {
            "totalRaw": processed_total,
            "totalNormalized": processed_total,
            "afterFilter": processed_total,
            "totalUnique": soc_unique_count,
            "filterRemoved": 0,
            "dedupRemoved": reduced_count,
            "duplicates": reduced_count,
            "duplicateRate": reduction_rate,
            "dedupRate": reduction_rate,
            "uniqueRate": _ratio(min(soc_unique_count, processed_total), processed_total),
            "files": processed_total,
            "sourceCounter": Counter(workflow_stats["sourceCounts"]),
            "seriesRaw": workflow_series_raw,
            "seriesUnique": soc_unique_series,
            "_timelineLabels": timeline_labels,
            "_timelineWindow": timeline_window,
            "workflowCallCount": processed_total,
            "dataSource": "workflow.db.workflow_stats.call_count + soc.db.unique",
        }
    )
    triage = _read_triage(triage_files)
    if denoise.get("_seriesTriage") is not None:
        triage["seriesTotal"] = denoise["_seriesTriage"]
        triage["seriesAttack"] = denoise["_seriesAttack"]
    sources = _build_sources(denoise["sourceCounter"])
    closed_loop = _build_closed_loop(triage)
    pipeline = _build_pipeline(denoise, triage)
    available_dates = _available_asset_dates()
    date_range = _build_date_range(start_date, end_date, asset_files, available_dates)
    event_range = _build_event_range(date_range, denoise, triage)

    result = {
        "date": start_date,
        "dateRange": date_range,
        "eventRange": event_range,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "latencyMs": round((time.time() - started) * 1000),
        "sourceStatus": {
            "dataPolicy": {
                "mode": "sqlite-only",
                "jsonlEnabled": False,
                "allowedDatabases": [
                    _display_path(DEFAULT_SQLITE_DB),
                    _display_path(WORKFLOW_DB),
                    _display_path(USAGE_DB),
                ],
            },
            "workflowStatsDb": _display_path(WORKFLOW_DB),
            "workflowStats": workflow_stats,
            "sampleMode": sample_mode,
            "sampleFile": ", ".join(_source_label(path) for path in asset_files) if sample_mode else "",
            "assets": {
                "path": _display_path(_active_source_path()),
                "exists": _active_source_exists(),
                "dataSource": _active_data_source(),
                "locked": True,
                "fileCount": len(asset_files),
                "availableDates": available_dates,
                "selectedDates": date_range["fileDates"],
            },
            "assetFiles": [_file_brief(path) for path in asset_files],
            "denoise": denoise_locations,
            "triage": triage_locations,
            "denoiseFiles": [_file_brief(path) for path in denoise_files],
            "triageFiles": [_file_brief(path) for path in triage_files],
            "missing": [] if sample_mode else [
                item
                for item in denoise_locations + triage_locations
                if not item["exists"] or item["fileCount"] == 0
            ],
        },
        "denoise": _without_counters(denoise),
        "triage": _without_counters(triage),
        "pipeline": pipeline,
        "sources": sources,
        "closedLoop": closed_loop,
        "attackProfile": _build_attack_profile(denoise, triage),
        "verdicts": [
            {"key": "attack_success", "label": "攻击成功", "value": triage["attackSuccess"], "color": "#ff4d6d"},
            {"key": "attack", "label": "攻击行为", "value": triage["attack"], "color": "#ffb020"},
            {"key": "attack_failed", "label": "攻击失败", "value": triage["attackFailed"], "color": "#2ee6a6"},
            {"key": "benign", "label": "良性", "value": triage["benign"], "color": "#58a6ff"},
            {"key": "unknown", "label": "未知", "value": triage["unknown"], "color": "#9b8cff"},
        ],
        "topThreats": _counter_items(triage["threatCounter"] or denoise["threatCounter"], 14),
        "riskLevels": _counter_items(triage["riskCounter"], 5),
        "tokenUsage": _read_token_usage(),
        "timeline": {
            "labels": denoise.get("_timelineLabels")
            or _series_labels(max(len(denoise["seriesRaw"]), len(triage["seriesTotal"]))),
            "window": denoise.get("_timelineWindow")
            or _timeline_window(start_date, end_date, len(denoise["seriesRaw"])),
            "denoiseRaw": denoise["seriesRaw"],
            "denoiseUnique": denoise["seriesUnique"],
            "triageTotal": triage["seriesTotal"],
            "triageAttack": triage["seriesAttack"],
        },
    }
    result["cacheHit"] = False
    _stats_cache_put(cache_key, result)
    return result


def _normalize_date(value):
    if value and DATE_RE.match(str(value)):
        return str(value)
    return datetime.now().strftime("%Y-%m-%d")


def _normalize_range(start_value, end_value, fallback_date):
    start_date = _normalize_date(start_value or fallback_date)
    end_date = _normalize_date(end_value or start_date)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def _normalize_time_window(start_value, end_value):
    start_time = _safe_int(start_value)
    end_time = _safe_int(end_value)
    if start_time <= 0 or end_time <= 0:
        return None
    if start_time > end_time:
        start_time, end_time = end_time, start_time
    return start_time, end_time


def _date_span(start_date, end_date):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    current = start
    while current <= end:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


def _find_asset_files(start_date, end_date, start_time=0, end_time=0):
    return _find_sqlite_sources(start_date, end_date, start_time, end_time)


def _asset_file_date(path):
    if isinstance(path, _RecordSource):
        return path.date
    return ""


def _latest_asset_date():
    dates = _available_asset_dates()
    return dates[-1] if dates else datetime.now().strftime("%Y-%m-%d")


def _available_asset_dates():
    return _available_sqlite_dates()


def _active_data_source():
    return "sqlite"


def _sqlite_settings():
    return {
        "db_path": DEFAULT_SQLITE_DB,
        "table": f'"{DEFAULT_SQLITE_TABLE}"',
        "facts_table": f'"{FACTS_TABLE}"',
        "activity_table": f'"{ACTIVITY_TABLE}"',
        "record_column": f'"{DEFAULT_SQLITE_RECORD_COLUMN}"',
        "date_column": f'"{DEFAULT_SQLITE_DATE_COLUMN}"',
        "event_time_column": f'"{DEFAULT_SQLITE_EVENT_TIME_COLUMN}"',
    }


def _active_source_path():
    return _sqlite_settings()["db_path"]


def _active_source_exists():
    path = _active_source_path()
    return path.is_file()


def _find_sqlite_sources(start_date, end_date, start_time=0, end_time=0):
    settings = _sqlite_settings()
    db_path = settings["db_path"]
    if not db_path.is_file():
        return []

    time_clause = ""
    query_params = [start_date, end_date]
    if start_time > 0 and end_time > 0:
        time_clause = f" AND {settings['event_time_column']} BETWEEN ? AND ?"
        query_params.extend((start_time, end_time))
    query = (
        f"SELECT {settings['date_column']} AS asset_date, COUNT(*) AS record_count "
        f"FROM {settings['facts_table']} "
        f"WHERE {settings['date_column']} BETWEEN ? AND ?{time_clause} "
        f"GROUP BY {settings['date_column']} "
        f"ORDER BY {settings['date_column']}"
    )
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query, query_params).fetchall()
    except Exception:
        return []

    sources = []
    for asset_date, record_count in rows:
        asset_date = str(asset_date or "")
        if not DATE_RE.match(asset_date):
            continue
        sources.append(
            _RecordSource(
                path=db_path,
                role="denoise",
                date=asset_date,
                data_source="sqlite",
                record_count=int(record_count or 0),
                start_time=start_time,
                end_time=end_time,
            )
        )
    return sources


def _available_sqlite_dates():
    settings = _sqlite_settings()
    db_path = settings["db_path"]
    if not db_path.is_file():
        return []
    query = (
        f"SELECT DISTINCT {settings['date_column']} AS asset_date "
        f"FROM {settings['facts_table']} "
        f"WHERE {settings['date_column']} IS NOT NULL "
        f"ORDER BY {settings['date_column']}"
    )
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query).fetchall()
    except Exception:
        return []
    return [str(row[0]) for row in rows if DATE_RE.match(str(row[0]))]


def _build_date_range(start_date, end_date, asset_files, available_dates=None):
    file_dates = sorted({date for date in (_asset_file_date(path) for path in asset_files) if date})
    return {
        "start": start_date,
        "end": end_date,
        "label": start_date if start_date == end_date else f"{start_date} 至 {end_date}",
        "availableDates": available_dates if available_dates is not None else _available_asset_dates(),
        "fileDates": file_dates,
    }


def _build_event_range(date_range, denoise, triage):
    values = [
        _parse_event_time(denoise.get("eventStart")),
        _parse_event_time(denoise.get("eventEnd")),
        _parse_event_time(triage.get("eventStart")),
        _parse_event_time(triage.get("eventEnd")),
    ]
    values = [value for value in values if value]
    if not values:
        return {
            "start": "",
            "end": "",
            "label": date_range["label"],
            "source": "dateRange",
        }

    start = min(values)
    end = max(values)
    return {
        "start": _format_event_time(start),
        "end": _format_event_time(end),
        "label": _format_event_range_label(start, end),
        "source": "recordTime",
    }


def _timeline_window(start_date, end_date, series_length):
    if start_date != end_date:
        days = len(list(_date_span(start_date, end_date)))
        return f"{days} 天范围聚合"
    return "按批次统计"


def _asset_file_role(path):
    if isinstance(path, _RecordSource):
        return path.role
    name = path.name.lower()
    if "triage" in name or "研判" in name:
        return "triage"
    return "denoise"


def _read_denoise(paths, workflow_call_count: int = 0):
    if paths and all(isinstance(path, _RecordSource) and path.data_source == "sqlite" for path in paths):
        optimized = _read_sqlite_denoise(paths, workflow_call_count)
        if optimized is not None:
            return optimized

    total_raw = 0
    duplicates = 0
    parse_errors = 0
    headers = []
    source_counter = Counter()
    threat_counter = Counter()
    profile_counters = _new_profile_counters()
    event_start = None
    event_end = None
    series_raw = []
    series_unique = []

    for path in paths:
        file_raw = 0
        file_duplicates = 0
        for obj in _iter_source_records(path):
            if obj is None:
                parse_errors += 1
                continue
            if obj.get("_type") == "file_header":
                headers.append(obj)
                continue
            file_raw += 1
            if obj.get("is_duplicate") is True:
                file_duplicates += 1
            source_counter[_norm(obj.get("_source_type") or obj.get("source_type") or obj.get("device_type"))] += 1
            threat_counter[_norm(obj.get("_threat_type") or obj.get("threat_name") or obj.get("threat_type"))] += 1
            _update_profile_counters(obj, profile_counters)
            event_start, event_end = _merge_record_time(event_start, event_end, obj)
        total_raw += file_raw
        duplicates += file_duplicates
        series_raw.append(file_raw)
        series_unique.append(max(file_raw - file_duplicates, 0))

    total_unique = max(total_raw - duplicates, 0)
    series_raw = _expand_series(series_raw, total_raw, seed=7)
    series_unique = _expand_series(series_unique, total_unique, seed=11)

    return {
        "totalRaw": total_raw,
        "totalUnique": total_unique,
        "duplicates": duplicates,
        "duplicateRate": _ratio(duplicates, total_raw),
        "uniqueRate": _ratio(total_unique, total_raw),
        "headers": len(headers),
        "files": len(paths),
        "parseErrors": parse_errors,
        "eventStart": _format_event_time(event_start),
        "eventEnd": _format_event_time(event_end),
        "sourceCounter": source_counter,
        "threatCounter": threat_counter,
        **profile_counters,
        "seriesRaw": series_raw,
        "seriesUnique": series_unique,
        "workflowCallCount": workflow_call_count,
    }


def _read_sqlite_denoise(paths, workflow_call_count):
    settings = _sqlite_settings()
    dates = sorted({path.date for path in paths})
    if not dates:
        return None
    placeholders = ",".join("?" for _ in dates)
    date_column = settings["date_column"]
    event_time_column = settings["event_time_column"]
    table = settings["facts_table"]
    where_clause = f"{date_column} IN ({placeholders})"
    query_params = list(dates)
    start_time = min((path.start_time for path in paths if path.start_time > 0), default=0)
    end_time = max((path.end_time for path in paths if path.end_time > 0), default=0)
    if start_time > 0 and end_time > 0:
        where_clause += f" AND {event_time_column} BETWEEN ? AND ?"
        query_params.extend((start_time, end_time))
    try:
        with sqlite3.connect(settings["db_path"]) as conn:
            rows = conn.execute(
                f"SELECT {date_column}, COUNT(*), "
                f"COALESCE(SUM(CASE WHEN \"is_duplicate\" = 1 THEN 1 ELSE 0 END), 0), "
                f"MIN({event_time_column}), MAX({event_time_column}) "
                f"FROM {table} WHERE {where_clause} "
                f"GROUP BY {date_column} ORDER BY {date_column}",
                query_params,
            ).fetchall()
            source_rows = conn.execute(
                f"SELECT \"source_type\", COUNT(*) FROM {table} "
                f"WHERE {where_clause} GROUP BY \"source_type\"",
                query_params,
            ).fetchall()
            profile_counters, threat_counter = _sqlite_detail_counters(
                conn,
                settings,
                where_clause,
                query_params,
            )
            timeline = _sqlite_timeline(
                conn,
                settings,
                where_clause,
                query_params,
                dates,
                start_time,
                end_time,
            )
    except Exception:
        return None

    total_raw = sum(_safe_int(row[1]) for row in rows)
    duplicates = sum(_safe_int(row[2]) for row in rows)
    total_unique = max(total_raw - duplicates, 0)
    event_values = [
        _parse_event_time(value)
        for row in rows
        for value in (row[3], row[4])
        if value not in (None, "")
    ]
    series_raw = [_safe_int(row[1]) for row in rows]
    series_unique = [max(_safe_int(row[1]) - _safe_int(row[2]), 0) for row in rows]
    return {
        "totalRaw": total_raw,
        "totalUnique": total_unique,
        "duplicates": duplicates,
        "duplicateRate": _ratio(duplicates, total_raw),
        "uniqueRate": _ratio(total_unique, total_raw),
        "headers": 0,
        "files": len(paths),
        "parseErrors": 0,
        "eventStart": _format_event_time(min(event_values) if event_values else None),
        "eventEnd": _format_event_time(max(event_values) if event_values else None),
        "sourceCounter": Counter({_norm(key): _safe_int(value) for key, value in source_rows}),
        "threatCounter": threat_counter,
        **profile_counters,
        "seriesRaw": timeline["raw"],
        "seriesUnique": timeline["unique"],
        "_seriesTriage": timeline["triage"],
        "_seriesAttack": timeline["attack"],
        "_timelineLabels": timeline["labels"],
        "_timelineWindow": timeline["window"],
        "workflowCallCount": workflow_call_count,
    }


def _timeline_spec(dates, start_time, end_time):
    if start_time > 0 and end_time > 0:
        bucket_start = start_time
        bucket_end = end_time
    else:
        bucket_start = int(datetime.strptime(min(dates), "%Y-%m-%d").timestamp())
        bucket_end = int(
            (datetime.strptime(max(dates), "%Y-%m-%d") + timedelta(days=1)).timestamp()
        ) - 1
    span = max(bucket_end - bucket_start + 1, 1)
    if span <= 30 * 60:
        bucket_seconds, window = 60, "按分钟真实分布"
    elif span <= 2 * 60 * 60:
        bucket_seconds, window = 5 * 60, "按 5 分钟真实分布"
    elif span <= 24 * 60 * 60:
        bucket_seconds, window = 60 * 60, "按小时真实分布"
    elif span <= 7 * 24 * 60 * 60:
        bucket_seconds, window = 6 * 60 * 60, "按 6 小时真实分布"
    else:
        bucket_seconds, window = 24 * 60 * 60, "按天真实分布"
    bucket_count = max(1, min(math.ceil(span / bucket_seconds), 60))
    labels = []
    for index in range(bucket_count):
        point = datetime.fromtimestamp(bucket_start + index * bucket_seconds)
        if bucket_seconds >= 24 * 60 * 60:
            labels.append(point.strftime("%m-%d"))
        elif bucket_seconds >= 60 * 60:
            labels.append(point.strftime("%m-%d %H:%M"))
        else:
            labels.append(point.strftime("%H:%M"))
    return bucket_start, bucket_seconds, bucket_count, labels, window


def _sqlite_timeline(conn, settings, where_clause, query_params, dates, start_time, end_time):
    bucket_start, bucket_seconds, bucket_count, labels, window = _timeline_spec(
        dates,
        start_time,
        end_time,
    )
    rows = conn.execute(
        f"SELECT CAST((event_time - ?) / ? AS INTEGER) AS bucket_index, "
        f"COUNT(*) AS raw_count, "
        f"COALESCE(SUM(CASE WHEN is_duplicate = 0 THEN 1 ELSE 0 END), 0) AS unique_count, "
        f"COALESCE(SUM(has_triage), 0) AS triage_count, "
        f"COALESCE(SUM(CASE WHEN has_triage = 1 AND LOWER(verdict) IN "
        f"('attack_success', 'attack', 'attack_failed') THEN 1 ELSE 0 END), 0) AS attack_count "
        f"FROM {settings['facts_table']} WHERE {where_clause} AND event_time IS NOT NULL "
        f"GROUP BY bucket_index ORDER BY bucket_index",
        (bucket_start, bucket_seconds, *query_params),
    ).fetchall()
    raw = [0] * bucket_count
    unique = [0] * bucket_count
    triage = [0] * bucket_count
    attack = [0] * bucket_count
    for row in rows:
        index = _safe_int(row[0])
        if 0 <= index < bucket_count:
            raw[index] = _safe_int(row[1])
            unique[index] = _safe_int(row[2])
            triage[index] = _safe_int(row[3])
            attack[index] = _safe_int(row[4])
    return {
        "raw": raw,
        "unique": unique,
        "triage": triage,
        "attack": attack,
        "labels": labels,
        "window": window,
    }


def _sqlite_detail_counters(conn, settings, where_clause, query_params):
    table = settings["facts_table"]
    cache_key = (
        str(settings["db_path"]),
        table,
        tuple(query_params),
        _file_revision(Path(f"{settings['db_path']}-wal")),
    )
    revision = conn.execute(
        f"SELECT COALESCE(MAX(alert_row_id), 0), COALESCE(MAX(triage_persisted_at), '') "
        f"FROM {table} WHERE {where_clause}",
        query_params,
    ).fetchone()
    latest_row_id = _safe_int(revision[0])
    latest_triage_at = str(revision[1] or "")
    with _cache_lock:
        cached = _denoise_detail_cache.get(cache_key) or {}
        if cached:
            _denoise_detail_cache.move_to_end(cache_key)
    if (
        cached
        and latest_row_id == _safe_int(cached.get("lastRowId"))
        and latest_triage_at == str(cached.get("lastTriagePersistedAt") or "")
        and time.monotonic() - float(cached.get("updatedAt") or 0) < _DENOISE_DETAIL_CACHE_TTL
    ):
        return (
            {key: Counter(value) for key, value in cached["profileCounters"].items()},
            Counter(cached["threatCounter"]),
        )

    rows = conn.execute(
        f"SELECT phase, direction, result, protocol, severity, response_code, "
        f"port, threat_name, COUNT(*) AS profile_count FROM {table} "
        f"WHERE {where_clause} "
        f"GROUP BY 1, 2, 3, 4, 5, 6, 7, 8",
        query_params,
    ).fetchall()
    profile_counters = _new_profile_counters()
    profile_keys = (
        "phaseCounter",
        "directionCounter",
        "resultCounter",
        "protocolCounter",
        "severityCounter",
        "responseCounter",
    )
    threat_counter = Counter()
    for row in rows:
        count = _safe_int(row[8])
        for index, key in enumerate(profile_keys):
            profile_counters[key][_norm(row[index])] += count
        port_value = row[6]
        port = str(_safe_int(port_value)) if _safe_int(port_value) > 0 else _norm(port_value)
        profile_counters["portCounter"][port] += count
        threat_counter[_norm(row[7])] += count
    with _cache_lock:
        _denoise_detail_cache[cache_key] = {
            "lastRowId": latest_row_id,
            "lastTriagePersistedAt": latest_triage_at,
            "updatedAt": time.monotonic(),
            "profileCounters": {key: Counter(value) for key, value in profile_counters.items()},
            "threatCounter": Counter(threat_counter),
        }
        _denoise_detail_cache.move_to_end(cache_key)
        while len(_denoise_detail_cache) > _DENOISE_DETAIL_CACHE_MAX:
            _denoise_detail_cache.popitem(last=False)
    return profile_counters, threat_counter


def _read_sqlite_triage(paths):
    settings = _sqlite_settings()
    dates = sorted({path.date for path in paths})
    if not dates:
        return None
    placeholders = ",".join("?" for _ in dates)
    where_clause = f"asset_date IN ({placeholders})"
    query_params = list(dates)
    start_time = min((path.start_time for path in paths if path.start_time > 0), default=0)
    end_time = max((path.end_time for path in paths if path.end_time > 0), default=0)
    if start_time > 0 and end_time > 0:
        where_clause += " AND event_time BETWEEN ? AND ?"
        query_params.extend((start_time, end_time))
    triage_where = f"{where_clause} AND has_triage = 1"
    try:
        with sqlite3.connect(settings["db_path"]) as conn:
            row = conn.execute(
                f"SELECT COUNT(*), "
                f"COALESCE(SUM(CASE WHEN LOWER(triage_source) = 'cache' "
                f"OR LOWER(triage_status) = 'cached' THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(triage_source) IN "
                f"('follower', 'followers', 'follower_reused') "
                f"OR LOWER(triage_status) = 'follower_reused' THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(triage_status) IN ('failed', 'error') "
                f"THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(verdict) = 'attack_success' THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(verdict) = 'attack' THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(verdict) = 'attack_failed' THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(verdict) = 'benign' THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(verdict) NOT IN "
                f"('attack_success', 'attack', 'attack_failed', 'benign') THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN attack_success = 1 AND LOWER(verdict) <> 'attack_success' "
                f"THEN 1 ELSE 0 END), 0), MIN(event_time), MAX(event_time), "
                f"COALESCE(ROUND(AVG(CASE WHEN triage_ms > 0 THEN triage_ms END)), 0) "
                f"FROM {settings['facts_table']} WHERE {triage_where}",
                query_params,
            ).fetchone()
            source_rows = conn.execute(
                f"SELECT source_type, COUNT(*) FROM {settings['facts_table']} "
                f"WHERE {triage_where} GROUP BY source_type",
                query_params,
            ).fetchall()
            threat_rows = conn.execute(
                f"SELECT threat_name, COUNT(*) FROM {settings['facts_table']} "
                f"WHERE {triage_where} GROUP BY threat_name",
                query_params,
            ).fetchall()
            risk_rows = conn.execute(
                f"SELECT risk_level, COUNT(*) FROM {settings['facts_table']} "
                f"WHERE {triage_where} GROUP BY risk_level",
                query_params,
            ).fetchall()
            status_rows = conn.execute(
                f"SELECT COALESCE(NULLIF(triage_status, ''), triage_source), COUNT(*) "
                f"FROM {settings['facts_table']} WHERE {triage_where} GROUP BY 1",
                query_params,
            ).fetchall()
            profile_rows = conn.execute(
                f"SELECT phase, direction, result, protocol, severity, response_code, port, COUNT(*) "
                f"FROM {settings['facts_table']} WHERE {triage_where} "
                f"GROUP BY 1, 2, 3, 4, 5, 6, 7",
                query_params,
            ).fetchall()
    except Exception:
        return None

    total_records = _safe_int(row[0])
    cache_hit = _safe_int(row[1])
    followers_reused = _safe_int(row[2])
    triage_failed = _safe_int(row[3])
    attack_success = _safe_int(row[4]) + _safe_int(row[9])
    attack = _safe_int(row[5])
    attack_failed = _safe_int(row[6])
    benign = _safe_int(row[7])
    unknown = _safe_int(row[8])
    attack_total = attack_success + attack + attack_failed
    avg_triage_ms = _safe_int(row[12])
    profile_counters = _new_profile_counters()
    profile_keys = (
        "phaseCounter",
        "directionCounter",
        "resultCounter",
        "protocolCounter",
        "severityCounter",
        "responseCounter",
    )
    for profile_row in profile_rows:
        count = _safe_int(profile_row[7])
        for index, key in enumerate(profile_keys):
            profile_counters[key][_norm(profile_row[index])] += count
        port_value = profile_row[6]
        port = str(_safe_int(port_value)) if _safe_int(port_value) > 0 else _norm(port_value)
        profile_counters["portCounter"][port] += count
    return {
        "totalRecords": total_records,
        "batchTotal": 0,
        "newTriaged": max(total_records - cache_hit - followers_reused - triage_failed, 0),
        "cacheHit": cache_hit,
        "triageFailed": triage_failed,
        "followersReused": followers_reused,
        "attackTotal": attack_total,
        "attackSuccess": attack_success,
        "attack": attack,
        "attackFailed": attack_failed,
        "benign": benign,
        "unknown": unknown,
        "attackRate": _ratio(attack_total, total_records),
        "successRate": _ratio(attack_success, attack_total),
        "cacheRate": _ratio(cache_hit + followers_reused, total_records),
        "coverageRate": _ratio(total_records - triage_failed, total_records),
        "avgTriageMs": avg_triage_ms,
        "headers": 0,
        "files": len(paths),
        "parseErrors": 0,
        "eventStart": _format_event_time(_parse_event_time(row[10])),
        "eventEnd": _format_event_time(_parse_event_time(row[11])),
        "sourceCounter": Counter({_norm(key): _safe_int(value) for key, value in source_rows}),
        "threatCounter": Counter({_norm(key): _safe_int(value) for key, value in threat_rows}),
        "riskCounter": Counter({_norm(key): _safe_int(value) for key, value in risk_rows}),
        "statusCounter": Counter({_norm(key): _safe_int(value) for key, value in status_rows}),
        **profile_counters,
        "seriesTotal": [],
        "seriesAttack": [],
    }


def _read_triage(paths):
    if paths and all(isinstance(path, _RecordSource) and path.data_source == "sqlite" for path in paths):
        optimized = _read_sqlite_triage(paths)
        if optimized is not None:
            return optimized
    total_records = 0
    parse_errors = 0
    headers = []
    verdict_counter = Counter()
    source_counter = Counter()
    threat_counter = Counter()
    risk_counter = Counter()
    status_counter = Counter()
    profile_counters = _new_profile_counters()
    event_start = None
    event_end = None
    header_sums = Counter()
    fallback_new = 0
    fallback_cache = 0
    fallback_failed = 0
    fallback_followers = 0
    extra_success = 0
    series_total = []
    series_attack = []
    triage_ms_total = 0
    triage_ms_count = 0

    for path in paths:
        file_total = 0
        file_attack = 0
        for obj in _iter_source_records(path, triage_only=True):
            if obj is None:
                parse_errors += 1
                continue
            if obj.get("_type") == "file_header":
                headers.append(obj)
                for key in (
                    "batch_total",
                    "batch_triaged",
                    "batch_cache_hit",
                    "batch_triage_failed",
                    "batch_followers_reused",
                ):
                    header_sums[key] += _safe_int(obj.get(key))
                continue

            total_records += 1
            file_total += 1
            verdict = _norm(obj.get("attack_verdict") or "unknown")
            if verdict not in {"attack_success", "attack", "attack_failed", "benign", "unknown"}:
                verdict = "unknown"
            verdict_counter[verdict] += 1
            if obj.get("attack_success") is True and verdict != "attack_success":
                extra_success += 1
            if verdict in {"attack_success", "attack", "attack_failed"}:
                file_attack += 1

            source = _norm(obj.get("_source_type") or obj.get("source_type") or obj.get("device_type"))
            source_counter[source] += 1
            threat_counter[_norm(obj.get("_threat_type") or obj.get("threat_name") or obj.get("threat_type"))] += 1
            risk_counter[_norm(obj.get("risk_level") or obj.get("threat_level") or obj.get("threat_severity"))] += 1
            triage_ms = _safe_int(obj.get("triage_ms"))
            if triage_ms > 0:
                triage_ms_total += triage_ms
                triage_ms_count += 1
            _update_profile_counters(obj, profile_counters)
            event_start, event_end = _merge_record_time(event_start, event_end, obj)
            triage_source = _norm(obj.get("triage_source"))
            triage_status = _norm(obj.get("triage_status"))
            status_counter[triage_status or triage_source] += 1

            if triage_source == "cache" or triage_status == "cached":
                fallback_cache += 1
            elif triage_source in {"follower", "followers", "follower_reused"} or triage_status == "follower_reused":
                fallback_followers += 1
            elif triage_status in {"failed", "error"}:
                fallback_failed += 1
            else:
                fallback_new += 1

        series_total.append(file_total)
        series_attack.append(file_attack)

    has_batch_fields = any(
        _safe_int(header_sums[key]) > 0
        for key in ("batch_triaged", "batch_cache_hit", "batch_triage_failed", "batch_followers_reused")
    )
    new_triaged = fallback_new
    cache_hit = fallback_cache
    triage_failed = fallback_failed
    followers_reused = fallback_followers

    attack_success = verdict_counter["attack_success"] + extra_success
    attack = verdict_counter["attack"]
    attack_failed = verdict_counter["attack_failed"]
    attack_total = attack_success + attack + attack_failed
    benign = verdict_counter["benign"]
    unknown = verdict_counter["unknown"]

    series_total = _expand_series(series_total, total_records, seed=23)
    series_attack = _expand_series(series_attack, attack_total, seed=29)

    return {
        "totalRecords": total_records,
        "batchTotal": header_sums["batch_total"],
        "newTriaged": new_triaged,
        "cacheHit": cache_hit,
        "triageFailed": triage_failed,
        "followersReused": followers_reused,
        "attackTotal": attack_total,
        "attackSuccess": attack_success,
        "attack": attack,
        "attackFailed": attack_failed,
        "benign": benign,
        "unknown": unknown,
        "attackRate": _ratio(attack_total, total_records),
        "successRate": _ratio(attack_success, attack_total),
        "cacheRate": _ratio(cache_hit + followers_reused, total_records),
        "coverageRate": _ratio(total_records - triage_failed, total_records),
        "avgTriageMs": round(triage_ms_total / triage_ms_count) if triage_ms_count else 0,
        "headers": len(headers),
        "files": len(paths),
        "parseErrors": parse_errors,
        "eventStart": _format_event_time(event_start),
        "eventEnd": _format_event_time(event_end),
        "sourceCounter": source_counter,
        "threatCounter": threat_counter,
        "riskCounter": risk_counter,
        "statusCounter": status_counter,
        **profile_counters,
        "seriesTotal": series_total,
        "seriesAttack": series_attack,
    }


def _new_profile_counters():
    return {
        "phaseCounter": Counter(),
        "directionCounter": Counter(),
        "resultCounter": Counter(),
        "portCounter": Counter(),
        "protocolCounter": Counter(),
        "severityCounter": Counter(),
        "responseCounter": Counter(),
    }


def _update_profile_counters(obj, counters):
    counters["phaseCounter"][_norm(obj.get("threat_phase") or obj.get("attack_phase") or obj.get("kill_chain_phase"))] += 1
    counters["directionCounter"][_norm(obj.get("direction") or obj.get("traffic_direction"))] += 1
    counters["resultCounter"][_norm(obj.get("threat_result") or obj.get("attack_verdict"))] += 1
    counters["protocolCounter"][_norm(obj.get("net_type") or obj.get("net_app_proto") or obj.get("protocol"))] += 1
    counters["severityCounter"][_norm(obj.get("threat_severity") or obj.get("threat_level") or obj.get("risk_level"))] += 1
    counters["responseCounter"][_norm(obj.get("rsp_status_code") or obj.get("status_code"))] += 1

    port_value = obj.get("dport") or obj.get("dst_port") or obj.get("destination_port")
    port = str(_safe_int(port_value)) if _safe_int(port_value) > 0 else _norm(port_value)
    counters["portCounter"][port] += 1


def _expand_series(values, total, *, seed):
    values = [max(_safe_int(value), 0) for value in values if _safe_int(value) > 0]
    total = _safe_int(total)
    if total <= 0:
        return []
    return values or [total]


def _series_labels(length):
    return [f"B{index + 1:02d}" for index in range(length)]


def _build_sources(counter):
    total = sum(counter.values())
    rows = []
    for key, label, aliases in SOURCE_DEFS:
        count = 0
        for source, value in counter.items():
            if source in aliases or any(alias in source for alias in aliases):
                count += value
        rows.append(
            {
                "key": key,
                "label": label,
                "value": count,
                "rate": _ratio(count, total),
                "active": count > 0,
            }
        )

    known = sum(item["value"] for item in rows)
    unknown = max(total - known, 0)
    if unknown:
        rows.append({"key": "unknown", "label": "未归类来源", "value": unknown, "rate": _ratio(unknown, total), "active": True})
    return rows


def _build_closed_loop(triage):
    total = triage["totalRecords"]
    auto_closed = triage["attackFailed"] + triage["benign"]
    manual = triage["unknown"]
    pending = triage["triageFailed"] + triage["unknown"]
    resolved = max(total - pending, 0)
    return {
        "autoClosed": auto_closed,
        "resolved": resolved,
        "manualDecision": manual,
        "pending": pending,
        "resolutionRate": _ratio(resolved, total),
    }


def _build_pipeline(denoise, triage):
    raw = denoise["totalRaw"]
    unique = denoise["totalUnique"]
    triage_total = triage["totalRecords"]
    attack_total = triage["attackTotal"]
    reused = triage["cacheHit"] + triage["followersReused"]
    duplicates = raw - unique
    return {
        "raw": raw,
        "unique": unique,
        "triageTotal": triage_total,
        "attackTotal": attack_total,
        "reductionSaved": duplicates,
        "llmSaved": reused,
        "uniqueRate": _ratio(unique, raw),
        "workloadReuseRate": _ratio(reused, triage_total),
        "coverageRate": _ratio(unique, raw),
        "attackRate": _ratio(attack_total, triage_total),
        "successRate": _ratio(triage["attackSuccess"], attack_total) if attack_total > 0 else 0,
    }


def _build_attack_profile(denoise, triage):
    return [
        _profile_group(
            "phase",
            "攻击阶段",
            _profile_counter(denoise, triage, "phaseCounter"),
            4,
            "#9b8cff",
            lambda value: PHASE_LABELS.get(value, value),
        ),
        _profile_group(
            "direction",
            "流量方向",
            _profile_counter(denoise, triage, "directionCounter"),
            4,
            "#2be7ff",
            lambda value: DIRECTION_LABELS.get(value, value),
        ),
        _profile_group(
            "result",
            "结果状态",
            _profile_counter(denoise, triage, "resultCounter"),
            4,
            "#2ee6a6",
            lambda value: RESULT_LABELS.get(value, value),
        ),
        _profile_group(
            "port",
            "重点端口",
            _profile_counter(denoise, triage, "portCounter"),
            4,
            "#ffb020",
            _port_label,
        ),
    ]


def _profile_counter(denoise, triage, key):
    triage_counter = triage.get(key) or Counter()
    if sum(triage_counter.values()) > 0:
        return triage_counter
    return denoise.get(key) or Counter()


def _profile_group(key, label, counter, limit, color, labeler):
    total = sum(counter.values())
    return {
        "key": key,
        "label": label,
        "total": total,
        "color": color,
        "items": [
            {
                "key": value,
                "label": labeler(value),
                "value": count,
                "rate": _ratio(count, total),
            }
            for value, count in counter.most_common(limit)
            if value and value != "none"
        ],
    }


def _port_label(value):
    if value == "unknown":
        return "未知端口"
    if str(value).isdigit():
        return f"{value}/TCP"
    return str(value)


def _iter_source_records(path, *, triage_only=False):
    if isinstance(path, _RecordSource) and path.data_source == "sqlite":
        yield from _iter_sqlite_records(path, triage_only=triage_only)
    return


def _iter_sqlite_records(source, *, triage_only=False):
    settings = _sqlite_settings()
    time_filter = ""
    query_params = [source.date]
    if source.start_time > 0 and source.end_time > 0:
        time_filter = f" AND {settings['event_time_column']} BETWEEN ? AND ?"
        query_params.extend((source.start_time, source.end_time))
    triage_filter = ""
    if triage_only:
        triage_filter = (
            f" AND (NULLIF(json_extract({settings['record_column']}, '$.triage_status'), '') IS NOT NULL "
            f"OR NULLIF(json_extract({settings['record_column']}, '$._triage_persisted_at'), '') IS NOT NULL "
            f"OR json_extract({settings['record_column']}, '$.triage_report') IS NOT NULL)"
        )
    query = (
        f"SELECT {settings['record_column']} AS record_json "
        f"FROM {settings['table']} "
        f"WHERE {settings['date_column']} = ?{time_filter}{triage_filter} "
        f"ORDER BY {settings['event_time_column']}, rowid"
    )
    try:
        with sqlite3.connect(settings["db_path"]) as conn:
            cursor = conn.execute(query, query_params)
            for row in cursor:
                try:
                    payload = json.loads(row[0])
                except Exception:
                    yield None
                    continue
                if isinstance(payload, dict):
                    yield payload
                elif isinstance(payload, list):
                    yield from _iter_json_payload(payload)
                else:
                    yield None
    except Exception:
        return


def _iter_json_payload(payload):
    if isinstance(payload, list):
        for item in payload:
            yield item if isinstance(item, dict) else None
        return

    if not isinstance(payload, dict):
        yield None
        return

    records = None
    for key in ("records", "data", "items", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            records = value
            break

    if records is None:
        yield payload
        return

    header = {
        key: value
        for key, value in payload.items()
        if key not in {"records", "data", "items", "rows"}
    }
    if header:
        yield {"_type": "file_header", **header}
    for item in records:
        yield item if isinstance(item, dict) else None


def _without_counters(payload):
    return {
        key: value
        for key, value in payload.items()
        if not isinstance(value, Counter) and not key.startswith("_")
    }


def _counter_items(counter, limit):
    total = sum(counter.values())
    return [
        {"label": label, "value": value, "rate": _ratio(value, total)}
        for label, value in counter.most_common(limit)
        if label
    ]


def _file_brief(path):
    if isinstance(path, _RecordSource):
        stat = path.path.stat() if path.path.exists() else None
        return {
            "name": f"{path.path.name}:{path.date}",
            "path": _display_path(path.path),
            "date": path.date,
            "role": path.role,
            "dataSource": path.data_source,
            "recordCount": path.record_count,
            "sizeBytes": stat.st_size if stat else 0,
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds") if stat else "",
        }
    stat = path.stat()
    return {
        "name": path.name,
        "path": _display_path(path),
        "bytes": stat.st_size,
        "modifiedAt": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def _display_path(path):
    try:
        return "~/" + str(Path(path).resolve().relative_to(Path.home())).replace("\\", "/")
    except Exception:
        return str(path)


def _source_label(path):
    if isinstance(path, _RecordSource):
        return f"{_display_path(path.path)}:{path.date}"
    return _display_path(path)


def _merge_record_time(current_start, current_end, obj):
    value = None
    for key in ("time", "event_time", "timestamp", "created_at", "occur_time", "start_time"):
        value = _parse_event_time(obj.get(key))
        if value:
            break
    if not value:
        return current_start, current_end
    if current_start is None or value < current_start:
        current_start = value
    if current_end is None or value > current_end:
        current_end = value
    return current_start, current_end


def _parse_event_time(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        number = float(value)
        if number > 1_000_000_000_000:
            number = number / 1000
        if number > 10_000_000:
            return datetime.fromtimestamp(number).replace(tzinfo=None)
    except Exception:
        pass
    try:
        text = str(value).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except Exception:
        return None


def _format_event_time(value):
    if not value:
        return ""
    return value.isoformat(sep=" ", timespec="seconds")


def _format_event_range_label(start, end):
    if start.date() == end.date():
        return f"{start:%Y-%m-%d %H:%M} - {end:%H:%M}"
    return f"{start:%Y-%m-%d %H:%M} 至 {end:%Y-%m-%d %H:%M}"


def _safe_int(value):
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except Exception:
        return 0


def _ratio(part, total):
    part = _safe_int(part)
    total = _safe_int(total)
    if total <= 0:
        return 0
    return round(part / total, 4)


def _norm(value):
    if value is None:
        return "unknown"
    text = str(value).strip()
    if not text:
        return "unknown"
    return text.lower()
