import base64
import json
import math
import re
from contextvars import ContextVar
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, RLock

_database = ContextVar("soc_dashboard_database")


def _connect(*, timeout=5.0, readonly=False):
    return _database.get().connect(timeout=timeout, readonly=readonly)


def _available(table):
    try:
        with _connect(readonly=True) as conn:
            return conn.table_exists(table)
    except Exception:
        return False


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_POSTGRES_DB = Path("postgres/alert_records")
DEFAULT_POSTGRES_TABLE = "alert_records"
DEFAULT_POSTGRES_RECORD_COLUMN = "record_json"
DEFAULT_POSTGRES_DATE_COLUMN = "asset_date"
DEFAULT_POSTGRES_EVENT_TIME_COLUMN = "event_time"
FACTS_TABLE = "soc_dashboard_alert_facts"
ACTIVITY_TABLE = "soc_dashboard_activity_events"
META_TABLE = "soc_dashboard_meta"
SCHEMA_VERSION = "postgres-1"
ACTIVITY_DEFAULT_LIMIT = 20
ACTIVITY_MAX_LIMIT = 50
ACTIVITY_WINDOW_MS = 3000
ACTIVITY_NORMAL_LIMIT = 5
ACTIVITY_SURGE_LIMIT = 100
ACTIVITY_RETENTION_ROWS = 100_000
ACTIVITY_PRUNE_INTERVAL = 3600.0

WORKFLOW_DB = Path("postgres/workflow_executions")
WORKFLOW_SNAPSHOT_TABLE = "soc_dashboard_workflow_stats_samples"
TASK_DB = Path("postgres/task_schedulers")
USAGE_DB = Path("postgres/usage_records")
SOC_PINNED_WORKFLOW_NAMES = {
    "stream_alert_denoise": "告警降噪工作流",
    "stream_alert_triage": "告警研判工作流",
}
TRIAGE_WORKFLOW_IDS = {
    "stream_alert_triage",
    "onesec_kafka_investigation",
    "tdp_alert_triage",
    "sec_alert_unified_ops",
}
WORKFLOW_DISPLAY_NAMES = {
    **SOC_PINNED_WORKFLOW_NAMES,
    "onesec_kafka_investigation": "OneSEC Kafka 告警研判工作流",
    "tdp_alert_triage": "TDP 告警研判工作流",
    "sec_alert_unified_ops": "统一告警运营工作流",
}
WORKFLOW_RUNNING_STATUSES = {"running", "queued", "pending"}
WORKFLOW_SUCCESS_STATUSES = {"success", "completed"}
WORKFLOW_TRIGGER_CONFIG_KINDS = {
    "workflow_kafka_config",
    "workflow_poller_config",
    "workflow_syslog_config",
}
WORKFLOW_DEFAULT_ACTIVE_TIMEOUT_SECONDS = 7200
WORKFLOW_MIN_ACTIVE_TIMEOUT_SECONDS = 60
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
_token_usage_cache = {"updatedAt": 0.0, "mtimeNs": 0, "value": None}
_TOKEN_USAGE_CACHE_TTL = 30.0
_workflow_file_cache: OrderedDict = OrderedDict()
_WORKFLOW_FILE_CACHE_MAX = 64
_WORKFLOW_FILE_CACHE_TTL = 30.0
_cache_lock = RLock()
_workflow_activity_read_lock = Lock()
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
    "threat_type",
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
    "triage_attack_verdict",
    "risk_level",
    "triage_ms",
    "triage_attack_success",
)


def _json_value(prefix, path):
    return f"({prefix}.record_json #>> '{{{path.replace('.', ',')}}}')"


def _fact_backfill_sql():
    columns = ", ".join(_FACT_COLUMNS)
    values = ", ".join(_fact_expressions("source"))
    updates = ", ".join(f"{name} = EXCLUDED.{name}" for name in _FACT_COLUMNS[1:])
    return (f"INSERT INTO {FACTS_TABLE} ({columns}) SELECT {values} FROM {DEFAULT_POSTGRES_TABLE} AS source "
            f"ON CONFLICT (alert_row_id) DO UPDATE SET {updates}")


def _ensure_postgres_schema():
    identity = _database.get().identity()
    if identity in _schema_ready:
        return True
    with _schema_lock:
        if identity in _schema_ready:
            return True
        with _connect(timeout=30) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema() || ':soc-dashboard'))").fetchone()
            if not conn.table_exists(DEFAULT_POSTGRES_TABLE):
                return False
            conn.execute(f"CREATE TABLE IF NOT EXISTS {META_TABLE} (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL)")
            conn.execute(f"""CREATE TABLE IF NOT EXISTS {FACTS_TABLE} (
                alert_row_id BIGINT PRIMARY KEY, row_key TEXT NOT NULL UNIQUE, asset_date TEXT NOT NULL,
                event_time BIGINT, source_type TEXT, threat_name TEXT, threat_type TEXT,
                is_duplicate INTEGER NOT NULL DEFAULT 0, phase TEXT, direction TEXT, result TEXT,
                protocol TEXT, severity TEXT, response_code TEXT, port TEXT,
                has_triage INTEGER NOT NULL DEFAULT 0, triage_persisted_at TEXT, triage_status TEXT,
                triage_source TEXT, triage_attack_verdict TEXT, risk_level TEXT,
                triage_ms BIGINT NOT NULL DEFAULT 0, triage_attack_success TEXT NOT NULL DEFAULT 'unknown')""")
            existing_columns = conn.columns(FACTS_TABLE)
            schema_updates = []
            for column in _FACT_COLUMNS:
                if column in existing_columns:
                    continue
                definition = (
                    'BIGINT' if column in {'alert_row_id', 'event_time', 'triage_ms'}
                    else 'INTEGER NOT NULL DEFAULT 0' if column in {'is_duplicate', 'has_triage'}
                    else 'TEXT'
                )
                schema_updates.append(f"ALTER TABLE {FACTS_TABLE} ADD COLUMN {column} {definition}")
            for column in ('alert_row_id', 'event_time', 'triage_ms'):
                schema_updates.append(f"ALTER TABLE {FACTS_TABLE} ALTER COLUMN {column} TYPE BIGINT")
            for name, columns in (
                ('asset_event', 'asset_date, event_time'), ('event', 'event_time, asset_date'),
                ('triage_event', 'has_triage, asset_date, event_time'), ('triage_time', 'has_triage, event_time, asset_date'),
            ):
                schema_updates.append(f"CREATE INDEX IF NOT EXISTS idx_soc_dashboard_facts_{name} ON {FACTS_TABLE}({columns})")
            conn.execute(";\n".join(schema_updates))
            # The primary key is the event key. The separate identity column is
            # a monotonic polling cursor, matching alert_records.rowid.
            conn.execute(f"""CREATE TABLE IF NOT EXISTS {ACTIVITY_TABLE} (
                event_key TEXT PRIMARY KEY, activity_id BIGINT GENERATED BY DEFAULT AS IDENTITY UNIQUE,
                alert_row_id BIGINT NOT NULL, record_row_id TEXT NOT NULL, asset_date TEXT NOT NULL,
                event_time BIGINT, record_json JSONB NOT NULL)""")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_soc_dashboard_activity_pg_event ON {ACTIVITY_TABLE}(asset_date, event_time, activity_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_soc_dashboard_activity_pg_time ON {ACTIVITY_TABLE}(event_time, activity_id)")
            version = conn.execute(f"SELECT meta_value FROM {META_TABLE} WHERE meta_key = 'schema_version'").fetchone()
            if not version or version[0] != SCHEMA_VERSION:
                conn.execute(_fact_backfill_sql())
                conn.execute(f"DELETE FROM {FACTS_TABLE} WHERE NOT EXISTS (SELECT 1 FROM {DEFAULT_POSTGRES_TABLE} source WHERE source.rowid = {FACTS_TABLE}.alert_row_id)")
                if conn.table_exists('soc_dashboard_activity'):
                    conn.execute(f"""INSERT INTO {ACTIVITY_TABLE}(event_key, activity_id, alert_row_id, record_row_id, asset_date, event_time, record_json)
                        SELECT event_key, activity_id, alert_row_id, record_row_id, asset_date, event_time, record_json::jsonb
                        FROM soc_dashboard_activity ON CONFLICT (event_key) DO NOTHING""")
                    conn.execute(f"SELECT setval(pg_get_serial_sequence('{ACTIVITY_TABLE}', 'activity_id'), GREATEST(COALESCE((SELECT MAX(activity_id) FROM {ACTIVITY_TABLE}), 1), COALESCE(pg_sequence_last_value(pg_get_serial_sequence('{ACTIVITY_TABLE}', 'activity_id')::regclass), 1)), true)").fetchone()
                conn.execute(f"""INSERT INTO {ACTIVITY_TABLE}(event_key, alert_row_id, record_row_id, asset_date, event_time, record_json)
                    SELECT source.row_id || ':bootstrap', source.rowid, source.row_id, source.asset_date,
                        source.event_time, source.record_json FROM {DEFAULT_POSTGRES_TABLE} source
                    WHERE {_triage_marker('source')} ON CONFLICT (event_key) DO NOTHING""")
                conn.execute(f"INSERT INTO {META_TABLE}(meta_key, meta_value) VALUES('schema_version', ?) ON CONFLICT (meta_key) DO UPDATE SET meta_value = EXCLUDED.meta_value", (SCHEMA_VERSION,))
            columns = ', '.join(_FACT_COLUMNS)
            values = ', '.join(_fact_expressions('NEW'))
            updates = ', '.join(f'{name} = EXCLUDED.{name}' for name in _FACT_COLUMNS[1:])
            conn.execute(f"""CREATE OR REPLACE FUNCTION soc_dashboard_project_alert() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        DELETE FROM {FACTS_TABLE} WHERE alert_row_id = OLD.rowid;
                        RETURN OLD;
                    END IF;
                    INSERT INTO {FACTS_TABLE} ({columns}) VALUES ({values})
                        ON CONFLICT (alert_row_id) DO UPDATE SET {updates};
                    IF {_triage_marker('NEW')} AND
                        (TG_OP = 'INSERT' OR NEW.record_json IS DISTINCT FROM OLD.record_json) THEN
                        INSERT INTO {ACTIVITY_TABLE}(event_key, alert_row_id, record_row_id, asset_date, event_time, record_json)
                        VALUES (NEW.row_id || ':' || md5(NEW.record_json::text), NEW.rowid, NEW.row_id,
                            NEW.asset_date, NEW.event_time, NEW.record_json) ON CONFLICT (event_key) DO NOTHING;
                    END IF;
                    RETURN NEW;
                END $$""")
            conn.execute(f"""DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'soc_dashboard_project_alert_trigger'
                    AND tgrelid = '{DEFAULT_POSTGRES_TABLE}'::regclass) THEN
                    CREATE TRIGGER soc_dashboard_project_alert_trigger AFTER INSERT OR UPDATE OR DELETE
                        ON {DEFAULT_POSTGRES_TABLE} FOR EACH ROW EXECUTE FUNCTION soc_dashboard_project_alert();
                END IF;
                END $$""")
        _schema_ready.add(identity)
    return True

def _fact_expressions(prefix):
    source_type = _json_value(prefix, "_source_type")
    source_type_fallback = _json_value(prefix, "source_type")
    threat_name = _json_value(prefix, "threat_name")
    threat_type = _json_value(prefix, "_threat_type")
    threat_type_fallback = _json_value(prefix, "threat_type")
    phase = _json_value(prefix, "threat_phase")
    attack_phase = _json_value(prefix, "attack_phase")
    kill_chain = _json_value(prefix, "kill_chain_phase")
    direction = _json_value(prefix, "direction")
    traffic_direction = _json_value(prefix, "traffic_direction")
    raw_result = _json_value(prefix, "threat_result")
    raw_verdict = _json_value(prefix, "attack_verdict")
    triage_attack_verdict = _json_value(prefix, "triage_attack_verdict")
    triage_attack_success = _json_value(prefix, "triage_attack_success")
    normalized_triage_verdict = (
        f"CASE WHEN LOWER(COALESCE({triage_attack_verdict}, '')) "
        "IN ('attack', 'non_attack', 'unknown') "
        f"THEN LOWER({triage_attack_verdict}) "
        f"WHEN LOWER(COALESCE({triage_attack_verdict}, '')) "
        "IN ('attack_success', 'attack_failed') THEN 'attack' "
        f"WHEN LOWER(COALESCE({triage_attack_verdict}, '')) = 'benign' "
        "THEN 'non_attack' ELSE 'unknown' END"
    )
    normalized_triage_success = (
        f"CASE WHEN {normalized_triage_verdict} != 'attack' THEN 'unknown' "
        f"WHEN LOWER(COALESCE({triage_attack_success}, '')) "
        "IN ('success', 'failed', 'unknown') "
        f"THEN LOWER({triage_attack_success}) "
        f"WHEN LOWER(COALESCE({triage_attack_verdict}, '')) = 'attack_success' "
        "THEN 'success' "
        f"WHEN LOWER(COALESCE({triage_attack_verdict}, '')) = 'attack_failed' "
        "THEN 'failed' "
        f"WHEN LOWER(COALESCE({triage_attack_success}, '')) IN ('1', 'true') "
        "THEN 'success' ELSE 'unknown' END"
    )
    protocol = _json_value(prefix, "net_type")
    app_protocol = _json_value(prefix, "net_app_proto")
    protocol_fallback = _json_value(prefix, "protocol")
    severity = _json_value(prefix, "threat_severity")
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
        f"COALESCE(NULLIF({prefix}.threat_name, ''), NULLIF({threat_name}, ''), 'unknown')",
        f"COALESCE(NULLIF({threat_type}, ''), NULLIF({threat_type_fallback}, ''), 'unknown')",
        f"COALESCE({prefix}.is_duplicate, 0)",
        f"COALESCE(NULLIF({phase}, ''), NULLIF({attack_phase}, ''), NULLIF({kill_chain}, ''), 'unknown')",
        f"COALESCE(NULLIF({direction}, ''), NULLIF({traffic_direction}, ''), 'unknown')",
        f"CASE WHEN {has_triage} = 1 "
        f"THEN {normalized_triage_success} "
        f"ELSE COALESCE(NULLIF({raw_result}, ''), NULLIF({raw_verdict}, ''), 'unknown') END",
        f"COALESCE(NULLIF({protocol}, ''), NULLIF({app_protocol}, ''), "
        f"NULLIF({protocol_fallback}, ''), 'unknown')",
        f"COALESCE(NULLIF({severity}, ''), 'unknown')",
        f"COALESCE(NULLIF({response}, ''), NULLIF({status_code}, ''), 'unknown')",
        f"COALESCE(NULLIF({destination_port}, ''), NULLIF({destination_port_fallback}, ''), "
        f"NULLIF({destination_port_legacy}, ''), 'unknown')",
        has_triage,
        f"COALESCE({triage_persisted_at}, '')",
        f"COALESCE({triage_status}, '')",
        f"COALESCE({triage_source}, '')",
        normalized_triage_verdict,
        f"COALESCE(NULLIF({risk_level}, ''), 'unknown')",
        f"COALESCE(CAST(CASE WHEN {triage_ms} ~ '^[0-9]+$' THEN {triage_ms} ELSE '0' END AS BIGINT), 0)",
        normalized_triage_success,
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










def _maybe_prune_activity():
    global _activity_pruned_at
    now = time.monotonic()
    if now - _activity_pruned_at < ACTIVITY_PRUNE_INTERVAL:
        return
    with _schema_lock:
        if now - _activity_pruned_at < ACTIVITY_PRUNE_INTERVAL:
            return
        try:
            with _connect() as conn:
                conn.execute(
                    f"DELETE FROM {ACTIVITY_TABLE} WHERE activity_id <= "
                    f"(SELECT COALESCE(MAX(activity_id), 0) - ? FROM {ACTIVITY_TABLE})",
                    (ACTIVITY_RETENTION_ROWS,),
                )

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


def _read_token_usage():
    empty = {
        "totalTokens": 0,
        "todayTokens": 0,
        "todayRequests": 0,
        "dailySeries": [],
        "dailyLabels": [],
        "source": "usage_records",
    }
    try:
        mtime_ns = _database.get().identity()
    except Exception:
        mtime_ns = 0
    now_monotonic = time.monotonic()
    with _cache_lock:
        cached_value = _token_usage_cache.get("value")
        if (
            cached_value is not None
            and _token_usage_cache.get("mtimeNs") == mtime_ns
            and now_monotonic - float(_token_usage_cache.get("updatedAt") or 0) < _TOKEN_USAGE_CACHE_TTL
        ):
            return cached_value

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
        with _connect() as conn:

            if not _table_exists(conn, "usage_records"):
                return {**empty, "dailyLabels": labels, "dailySeries": [0] * 7}
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
                "SELECT to_char(created_at::timestamptz AT TIME ZONE current_setting('TIMEZONE'), 'YYYY-MM-DD') AS usage_day, "
                "COALESCE(SUM(total_tokens), 0) AS token_count "
                "FROM usage_records WHERE created_at >= ? AND created_at < ? "
                "GROUP BY usage_day",
                (_usage_iso(first_day), _usage_iso(tomorrow_start)),
            ).fetchall()
    except Exception:
        return {**empty, "dailyLabels": labels, "dailySeries": [0] * 7}

    for usage_day, total in series_rows:
        key = str(usage_day or "")
        if key in series_by_date:
            series_by_date[key] += max(_safe_int(total), 0)

    result = {
        **empty,
        "totalTokens": max(total_tokens, 0),
        "todayTokens": max(_safe_int(today_row[0]), 0) if today_row else 0,
        "todayRequests": max(_safe_int(today_row[1]), 0) if today_row else 0,
        "dailySeries": list(series_by_date.values()),
        "dailyLabels": labels,
    }
    with _cache_lock:
        _token_usage_cache.update({"updatedAt": now_monotonic, "mtimeNs": mtime_ns, "value": result})
    return result


def _workflow_stats_sample_deltas(conn, workflow_name, start_time=0, end_time=0):
    stats_exists = conn.table_exists("workflow_stats")
    if not stats_exists:
        return None
    columns = conn.columns("workflow_stats")
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
            sampled_at    BIGINT NOT NULL,
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


def _workflow_stats_call_delta_from_samples(conn, workflow_name, start_ms, end_ms):
    if not _table_exists(conn, WORKFLOW_SNAPSHOT_TABLE):
        return None
    previous = conn.execute(
        f"SELECT call_count FROM {WORKFLOW_SNAPSHOT_TABLE} "
        "WHERE workflow_id = ? AND sampled_at < ? "
        "ORDER BY sampled_at DESC LIMIT 1",
        (workflow_name, start_ms),
    ).fetchone()
    if previous is None:
        return None
    rows = conn.execute(
        f"SELECT sampled_at, call_count FROM {WORKFLOW_SNAPSHOT_TABLE} "
        "WHERE workflow_id = ? AND sampled_at >= ? AND sampled_at < ? "
        "ORDER BY sampled_at",
        (workflow_name, start_ms, end_ms),
    ).fetchall()
    if not rows:
        return None
    total = 0
    previous_count = max(_safe_int(previous[0]), 0)
    for _, call_count in rows:
        current_count = max(_safe_int(call_count), 0)
        total += (
            current_count - previous_count
            if current_count >= previous_count
            else current_count
        )
        previous_count = current_count
    return max(total, 0)


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
    if not _available("workflow_executions"):
        return empty

    try:
        with _connect() as conn:
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
    try:
        with _connect() as conn:
            table = "workflow_executions" if start_time > 0 and end_time > 0 else "workflow_stats"
            if not conn.table_exists(table):
                return unavailable
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


def _dashboard_execution_detail(output_text, input_text):
    """Read a bounded preview for this execution only; never open result files.

    Kept separate from metric aggregation and task-center/workflow contracts.
    A missing count is not zero (nor an assumed single-alert batch).
    """
    output = _safe_json_object(output_text)
    inputs = _safe_json_object(input_text)
    def text(*values):
        return _first_text(*(value[:512] for value in values if isinstance(value, str)))

    queue = [(value, 0) for value in (
        output.get("enriched_alerts_with_triage"), output.get("triage_results"),
        output.get("unique_alerts"), output.get("enriched_alerts"),
        inputs.get("_soc_alert_preview"), inputs.get("alerts"),
        inputs.get("alert_list"), inputs.get("syslog_message"), inputs.get("syslog"),
    ) if value is not None]
    best, best_score = {}, 0
    for _ in range(48):
        if not queue:
            break
        value, depth = queue.pop(0)
        if depth > 6:
            continue
        if isinstance(value, str):
            if len(value) > 65536:
                continue
            # Syslog prefixes can precede a vendor JSON envelope.
            start = value.find("{")
            parsed = _safe_json_object(value[start:] if start >= 0 else value)
            if parsed:
                queue.append((parsed, depth + 1))
        elif isinstance(value, list):
            queue.extend((item, depth + 1) for item in value[:3])
        elif isinstance(value, dict):
            if value.get("_type") == "dict":
                continue  # Persisted key-only summary, not an alert.
            alert = {
                "id": text(value.get("id"), value.get("alert_id"), value.get("uuid")),
                "threatName": text(value.get("threat_name"), value.get("alert_name"), value.get("attack_name")),
                "sourceType": text(value.get("_source_type"), value.get("source_type"), value.get("device_type")),
                "srcIp": text(value.get("sip"), value.get("src_ip"), value.get("source_ip"), value.get("net_real_src_ip")),
                "dstIp": text(value.get("dip"), value.get("dst_ip"), value.get("destination_ip"), value.get("net_dest_ip")),
            }
            score = sum(bool(alert[key]) for key in ("threatName", "srcIp", "dstIp"))
            if score > best_score:
                best, best_score = alert, score
            for key in ("preview", "data", "alert", "alerts", "message", "msg", "log", "event", "payload"):
                if key in value:
                    queue.append((value[key], depth + 1))
    stats = output.get("stats") if isinstance(output.get("stats"), dict) else {}
    raw = stats.get("raw_count", inputs.get("_soc_alert_count"))
    try:
        count = int(raw) if raw is not None and not isinstance(raw, bool) else None
        if count is not None and (count < 0 or str(raw).strip() != str(count)):
            count = None
    except (ValueError, TypeError, OverflowError):
        count = None
    if count is None:
        alerts = inputs.get("alerts", inputs.get("alert_list"))
        if isinstance(alerts, list):
            count = len(alerts)
    if best:
        best["sourceType"] = best["sourceType"] or text(
            inputs.get("source_log_type"), output.get("source_log_type")
        )
    return best, count


def _get_workflow_recent_events(
    workflow_name: str,
    start_time: int = 0,
    end_time: int = 0,
    limit: int = 10,
    snapshot=None,
) -> list:
    # Polling must not multiply payload reads across dashboard tabs.
    # The worker owns this lock until its reads/parsing actually finish, even if
    # the HTTP request awaiting the bounded plugin executor is cancelled.
    if not _workflow_activity_read_lock.acquire(blocking=False):
        if snapshot is not None:
            snapshot.update(complete=False, available=False)
        return []
    try:
        return _read_workflow_recent_events(workflow_name, start_time, end_time, limit, snapshot)
    finally:
        _workflow_activity_read_lock.release()


def _read_workflow_recent_events(workflow_name, start_time, end_time, limit, snapshot):
    if not _available("workflow_executions"):
        if snapshot is not None:
            snapshot.update(complete=False, available=False)
        return []
    workflow_stage = "triage" if workflow_name in TRIAGE_WORKFLOW_IDS else "denoise"
    try:
        with _connect(timeout=0.2, readonly=True) as conn:
            deadline = time.monotonic() + 0.2

            execution_columns = conn.columns("workflow_executions")
            def preview_column(name):
                if name not in execution_columns:
                    return f"'{{}}' AS {name}"
                return f"CASE WHEN octet_length({name}) <= 262144 THEN {name} ELSE '{{}}' END AS {name}"

            metadata_select = ", ".join([
                "id", "status", "started_at",
                _workflow_execution_column_expr(execution_columns, "updated_at", "started_at"),
            ])
            query = f"SELECT {metadata_select} FROM workflow_executions WHERE workflow_id = ?"
            query_params = [workflow_name]
            if start_time > 0 and end_time > 0:
                query += " AND started_at >= ? AND started_at <= ?"
                query_params.extend((int(start_time * 1000), int(end_time * 1000)))
            row_limit = max(1, min(_safe_int(limit), 10))
            active_rows = conn.execute(
                query + " AND status IN ('running', 'queued', 'pending') ORDER BY started_at DESC LIMIT ?",
                [*query_params, row_limit],
            ).fetchall()
            recent_rows = conn.execute(query + " ORDER BY started_at DESC LIMIT ?", [*query_params, row_limit]).fetchall()
            ids = list(dict.fromkeys(row["id"] for row in [*active_rows, *recent_rows]))
            if time.monotonic() > deadline:
                raise RuntimeError("activity read budget exceeded")
            # Select payloads only AFTER bounding IDs; a sorting query must not
            # materialize previews for every matching execution before LIMIT.
            rows = []
            if ids:
                previews = ", ".join(preview_column(name) for name in ("output_results", "input_params", "payload"))
                placeholders = ",".join("?" for _ in ids)
                by_id = {row["id"]: row for row in conn.execute(
                    f"SELECT {metadata_select}, {previews} FROM workflow_executions WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()}
                rows = [by_id[key] for key in ids]
            if time.monotonic() > deadline:
                raise RuntimeError("activity read budget exceeded")
            if snapshot is not None and len(active_rows) == row_limit:
                snapshot["complete"] = False
    except Exception:
        if snapshot is not None:
            snapshot.update(complete=False, available=False)
        return []

    events = []
    for row in rows:
        execution_id = row["id"]
        status = row["status"]
        started_at = row["started_at"]
        output_text = row["output_results"]
        input_text = row["input_params"]
        payload_text = row["payload"]
        metrics = _workflow_execution_metrics(output_text, input_text)
        alert, raw_count = _dashboard_execution_detail(output_text, input_text)
        if workflow_stage == "denoise" and raw_count == 0 and not alert:
            continue  # No alert work; keep the execution itself untouched.
        unique_count = metrics["uniqueCount"]
        threat_name = alert.get("threatName", "")
        if not threat_name and workflow_stage == "triage":
            threat_name = _workflow_latest_alert_name(workflow_name, output_text, input_text)
        if not threat_name:
            threat_name = f"降噪批次 · 原始 {raw_count} 条" if raw_count and raw_count > 0 else "降噪批次 · 数量未提供"
        normalized_status = str(status or "").lower()
        event_status = (
            "completed"
            if normalized_status in {"success", "completed"}
            else normalized_status
            if normalized_status in {"running", "queued", "pending"}
            else "failed"
        )
        session_id, message_id = _workflow_link_context(
            {
                "payload": payload_text,
                "input_params": input_text,
            }
        )
        events.append(
            {
                "eventId": f"workflow-execution:{execution_id}",
                "stage": workflow_stage,
                "status": event_status,
                "occurredAt": datetime.fromtimestamp(
                    _safe_int(started_at) / 1000
                ).astimezone().isoformat(timespec="seconds"),
                "updatedAt": datetime.fromtimestamp(
                    _safe_int(row["updated_at"] or started_at) / 1000
                ).astimezone().isoformat(timespec="milliseconds"),
                "triggerSource": "workflow_execution",
                "workflowId": workflow_name,
                "sessionId": session_id,
                "messageId": message_id,
                "sampleCount": max(unique_count, 1),
                "alert": {
                    **alert,
                    "id": alert.get("id") or "",
                    "threatName": threat_name,
                },
                "result": {
                    "isDuplicate": metrics["isDuplicate"],
                    "clusterId": str(metrics["clusterCount"] or "--"),
                    **{
                        key: value
                        for key, value in metrics.items()
                        if key not in {"preview", "sourceCounts", "sourceType"}
                    },
                    "rawCount": raw_count,
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
    "attack_success": "攻击成功",
    "attack": "攻击行为",
    "attack_failed": "攻击失败",
    "non_attack": "非攻击",
    "unknown": "待确认",
}


def _triage_outcome_key(record):
    verdict = _triage_attack_verdict_value(record)
    result = _triage_attack_success_value(record)
    if verdict == "attack":
        if result == "success":
            return "attack_success"
        if result == "failed":
            return "attack_failed"
        return "attack"
    if verdict == "non_attack":
        return "non_attack"
    return "unknown"


def _triage_attack_verdict_value(record):
    verdict = _norm(record.get("triage_attack_verdict") or "unknown")
    if verdict in {"attack", "non_attack", "unknown"}:
        return verdict
    if verdict in {"attack_success", "attack_failed"}:
        return "attack"
    if verdict == "benign":
        return "non_attack"
    return "unknown"


def _triage_attack_success_value(record):
    raw_verdict = _norm(record.get("triage_attack_verdict") or "unknown")
    verdict = _triage_attack_verdict_value(record)
    result = _norm(record.get("triage_attack_success") or "unknown")
    if verdict == "attack" and result in {"success", "failed", "unknown"}:
        return result
    if verdict != "attack":
        return "unknown"
    if raw_verdict == "attack_success" or record.get("triage_attack_success") is True:
        return "success"
    if raw_verdict == "attack_failed":
        return "failed"
    return "unknown"


def get_activity(ctx, request):
    _database.set(ctx.database)
    params = dict(request.query_params)
    return _get_activity(params)


def get_task_center(ctx, request):
    _database.set(ctx.database)
    params = dict(request.query_params)
    include_mock = _truthy(params.get("mockActivity")) or _truthy(params.get("mockTaskCenter"))
    return _get_task_center(include_mock)


def _table_exists(conn, table_name):
    return conn.table_exists(table_name)

def _task_center_empty():
    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sessionCount": 0,
        "activeExecutionCount": 0,
        "scheduledActiveCount": 0,
        "workflowActiveCount": 0,
        "scheduledTasks": [],
        "workflows": [],
        "sourceStatus": {
            "tasksDb": str(TASK_DB),
            "workflowDb": str(WORKFLOW_DB),
            "tasksAvailable": _available("task_schedulers"),
            "workflowAvailable": _available("workflow_executions"),
        },
    }


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _task_trigger_value(trigger, key):
    if not isinstance(trigger, dict):
        return ""
    return trigger.get(key) or trigger.get(key[0].lower() + key[1:]) or ""


def _today_bounds():
    now_local = datetime.now().astimezone()
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start, today_start + timedelta(days=1)


def _task_center_task_rows(limit=12):
    if not _available("task_schedulers"):
        return 0, [], 0, 0, 0
    try:
        with _connect() as conn:


            if not (
                _table_exists(conn, "task_schedulers")
                and _table_exists(conn, "task_executions")
            ):
                return 0, [], 0, 0, 0
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
            summaries = {
                row["scheduler_id"]: row for row in conn.execute(
                    "SELECT scheduler_id, COUNT(*) AS execution_count, "
                    "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS success_count, "
                    "SUM(CASE WHEN status IN ('pending', 'queued', 'running') THEN 1 ELSE 0 END) AS active_count, "
                    "SUM(CASE WHEN COALESCE(started_at, queued_at, created_at)::timestamptz >= ?::text::timestamptz "
                    "AND COALESCE(started_at, queued_at, created_at)::timestamptz < ?::text::timestamptz "
                    "THEN 1 ELSE 0 END) AS today_execution_count "
                    "FROM task_executions GROUP BY scheduler_id", (today_start_iso, tomorrow_start_iso),
                ).fetchall()
            }
            latest_rows = {
                row["scheduler_id"]: row for row in conn.execute(
                    "SELECT DISTINCT ON (scheduler_id) scheduler_id, status, queued_at, started_at, completed_at, updated_at "
                    "FROM task_executions ORDER BY scheduler_id, "
                    "COALESCE(completed_at, updated_at, started_at, queued_at, created_at)::timestamptz DESC, id DESC"
                ).fetchall()
            }
            empty_summary = {"execution_count": 0, "success_count": 0, "active_count": 0, "today_execution_count": 0}
            tasks = []
            for scheduler in scheduler_rows:
                summary = summaries.get(scheduler["id"], empty_summary)
                latest = latest_rows.get(scheduler["id"])
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
                sum(task["activeCount"] for task in tasks),
            )
    except Exception:
        return 0, [], 0, 0, 0


def _cached_workflow_files(key, read):
    # Display metadata tolerates a short refresh interval; duplicate dashboard
    # polls must not reopen every local manifest and workflow definition.
    now = time.monotonic()
    with _cache_lock:
        entry = _workflow_file_cache.get(key)
        if entry and now - entry[0] < _WORKFLOW_FILE_CACHE_TTL:
            _workflow_file_cache.move_to_end(key)
            return entry[1]
        value = read()
        _workflow_file_cache[key] = (now, value)
        _workflow_file_cache.move_to_end(key)
        while len(_workflow_file_cache) > _WORKFLOW_FILE_CACHE_MAX:
            _workflow_file_cache.popitem(last=False)
        return value


def _workflow_manifest_name_map():
    return _cached_workflow_files(("names", str(Path.home())), _read_workflow_manifest_names)


def _read_workflow_manifest_names():
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


def _workflow_config_name_map(conn, rows=None):
    rows = _workflow_config_projection_rows(conn) if rows is None else rows
    names = {}
    for row in rows:
        if row["kind"] == "workflow.integration-config" and row["workflow_id"] and row["workflow_name"]:
            names.setdefault(str(row["workflow_id"]), str(row["workflow_name"]))
    return names



def _truthy_config_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text in {"1", "true", "yes", "on", "enabled"}


def _workflow_trigger_state(conn, workflow_id, rows=None):
    rows = _workflow_config_projection_rows(conn) if rows is None else rows
    matching = [row for row in rows if row["workflow_id"] == workflow_id and (
        row["kind"] in WORKFLOW_TRIGGER_CONFIG_KINDS or any(
            str(row["kind"]).startswith(prefix + "/") for prefix in WORKFLOW_TRIGGER_CONFIG_KINDS
        )
    )]
    return {
        "hasConfig": bool(matching),
        "enabled": (any(not row["enabled_present"] or _workflow_config_enabled(row["enabled"]) for row in matching)
                    if matching else True),
        "timeoutSeconds": max(
            WORKFLOW_DEFAULT_ACTIVE_TIMEOUT_SECONDS, WORKFLOW_MIN_ACTIVE_TIMEOUT_SECONDS,
            max((_safe_int(row["timeout_seconds"]) for row in matching), default=0),
        ),
    }



def _workflow_effective_status(latest, active_count, trigger_state):
    if not latest:
        return ""
    status = str(latest["status"] or "").lower()
    if status not in WORKFLOW_RUNNING_STATUSES:
        return status
    if active_count > 0:
        return status
    if trigger_state.get("hasConfig") and not trigger_state.get("enabled"):
        return "disabled"
    return "stale"


def _workflow_execution_column_expr(columns, column_name, fallback):
    return column_name if column_name in columns else f"{fallback} AS {column_name}"


def _workflow_node_count(workflow_id):
    return _cached_workflow_files(("nodes", str(Path.home()), str(workflow_id)),
                                  lambda: _read_workflow_node_count(workflow_id))


def _read_workflow_node_count(workflow_id):
    roots = [
        Path.home() / ".flocks" / "plugins" / "workflows",
        Path.home() / ".flocks" / "workspace" / "workflows",
        Path(__file__).resolve().parents[4] / "workflows",
    ]
    for root in roots:
        workflow_path = root / str(workflow_id) / "workflow.json"
        if not workflow_path.is_file():
            continue
        try:
            workflow_json = _safe_json_object(workflow_path.read_text(encoding="utf-8"))
            nodes = workflow_json.get("nodes")
            if isinstance(nodes, list):
                return max(len(nodes), 0)
        except Exception:
            continue
    return 0


def _first_text(*values):
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() not in {"unknown", "none", "null", "--"}:
            return text
    return ""


def _alert_name_from_record(record):
    if not isinstance(record, dict):
        return ""
    return _first_text(
        record.get("threat_name"),
        record.get("_threat_type"),
        record.get("threat_type"),
        record.get("alert_name"),
        record.get("name"),
        record.get("report_title"),
        record.get("title"),
    )


def _workflow_latest_alert_name(workflow_id, output_text="", input_text=""):
    output = _safe_json_object(output_text)
    inputs = _safe_json_object(input_text)
    preview = _workflow_alert_preview(output) or _workflow_input_preview(inputs)
    name = _alert_name_from_record(preview)
    if name:
        return name
    for key in (
        "enriched_alerts_with_triage",
        "triage_results",
        "unique_alerts",
        "enriched_alerts",
    ):
        value = output.get(key)
        if isinstance(value, dict):
            value = value.get("preview")
        if isinstance(value, list):
            for item in value:
                name = _alert_name_from_record(item)
                if name:
                    return name
    name = _first_text(
        output.get("top_report_title"),
        output.get("report_title"),
        output.get("title"),
    )
    if name:
        return name
    if workflow_id in TRIAGE_WORKFLOW_IDS:
        input_date = _first_text(inputs.get("input_date"))
        return f"研判批次 {input_date}" if input_date else "研判批次"
    if workflow_id == "stream_alert_denoise":
        return "降噪批次"
    return ""


def _workflow_progress(status, current_step_index, completed_steps, total_steps):
    normalized = str(status or "").lower()
    current = max(_safe_int(current_step_index), _safe_int(completed_steps), 0)
    total = max(_safe_int(total_steps), current, 1)
    if normalized == "disabled":
        return 0, "已关闭"
    if normalized == "stale":
        return 0, "已停止"
    if normalized in WORKFLOW_SUCCESS_STATUSES:
        return 1, "已完成"
    if normalized in {"error", "failed", "timeout"}:
        percent = _ratio(current or total, total)
        return percent, f"失败于 {current or total}/{total} 步"
    if normalized == "cancelled":
        percent = _ratio(current or total, total)
        return percent, f"已取消 {current or total}/{total} 步"
    if normalized in WORKFLOW_RUNNING_STATUSES:
        visible_current = current if current > 0 else 1
        return max(_ratio(visible_current, total), 0.03), f"第 {visible_current}/{total} 步"
    return 0, "待执行"


def _workflow_link_context(latest):
    payload = _safe_json_object(latest["payload"] if latest and "payload" in latest.keys() else "")
    input_params = _safe_json_object(latest["input_params"] if latest and "input_params" in latest.keys() else "")
    session_id = _first_text(
        payload.get("sessionId"),
        payload.get("sessionID"),
        payload.get("session_id"),
        input_params.get("sessionId"),
        input_params.get("sessionID"),
        input_params.get("session_id"),
    )
    message_id = _first_text(
        payload.get("messageId"),
        payload.get("messageID"),
        payload.get("message_id"),
        input_params.get("messageId"),
        input_params.get("messageID"),
        input_params.get("message_id"),
    )
    return session_id, message_id


def _task_center_workflow_rows(limit=12, include_mock=False):
    try:
        with _connect() as conn:
            has_executions = _table_exists(conn, "workflow_executions")
            has_stats = _table_exists(conn, "workflow_stats")
            if not (has_executions or has_stats):
                return [], 0, 0, 0
            today_start, tomorrow_start = _today_bounds()
            today_start_ms, tomorrow_start_ms = int(today_start.timestamp() * 1000), int(tomorrow_start.timestamp() * 1000)
            now_ms = int(time.time() * 1000)
            configs = _workflow_config_projection_rows(conn)
            configs_by_id = {}
            for row in configs:
                configs_by_id.setdefault(row["workflow_id"], []).append(row)
            states = {workflow_id: _workflow_trigger_state(conn, workflow_id, rows)
                      for workflow_id, rows in configs_by_id.items()}
            names = {**_workflow_manifest_name_map(), **_workflow_config_name_map(conn, configs), **WORKFLOW_DISPLAY_NAMES}
            stats_by_id = {}
            if has_stats:
                columns = conn.columns("workflow_stats")
                success_expr = "success_count" if "success_count" in columns else "0 AS success_count"
                stats_by_id = {row["workflow_id"]: row for row in conn.execute(
                    f"SELECT workflow_id, call_count, {success_expr} FROM workflow_stats"
                ).fetchall()}
            daily_counts = _workflow_daily_count_map(conn, today_start_ms, tomorrow_start_ms)
            summaries, latest_by_id = {}, {}
            execution_columns = conn.columns("workflow_executions") if has_executions else set()
            if has_executions:
                finished = "finished_at" if "finished_at" in execution_columns else "NULL"
                updated = "updated_at" if "updated_at" in execution_columns else "NULL"
                active_time = f"COALESCE({updated}, started_at, 0)"
                effective_time = f"COALESCE({finished}, {updated}, started_at, 0)"
                default_cutoff = now_ms - WORKFLOW_DEFAULT_ACTIVE_TIMEOUT_SECONDS * 1000 if "updated_at" in execution_columns else 0
                bounds = {workflow_id: {
                    "enabled": state["enabled"],
                    "cutoff": now_ms - state["timeoutSeconds"] * 1000,
                } for workflow_id, state in states.items() if state["hasConfig"]}
                rows = conn.execute(
                    "WITH bounds AS (SELECT key AS workflow_id, (value ->> 'enabled')::boolean AS enabled, "
                    "(value ->> 'cutoff')::bigint AS cutoff FROM jsonb_each(?::jsonb)) "
                    "SELECT e.workflow_id, COUNT(*) AS execution_count, "
                    "COUNT(*) FILTER (WHERE status IN ('success','completed')) AS success_count, "
                    "COUNT(*) FILTER (WHERE started_at >= ? AND started_at < ?) AS today_execution_count, "
                    "COUNT(*) FILTER (WHERE status IN ('running','queued','pending') "
                    f"AND COALESCE(b.enabled, TRUE) AND {active_time} >= COALESCE(b.cutoff, ?)) AS active_count "
                    "FROM workflow_executions e LEFT JOIN bounds b ON b.workflow_id = e.workflow_id GROUP BY e.workflow_id",
                    (json.dumps(bounds), today_start_ms, tomorrow_start_ms, default_cutoff),
                ).fetchall()
                summaries = {row["workflow_id"]: row for row in rows}
                metadata_columns = ["workflow_id", "id", "status", "started_at"] + [
                    _workflow_execution_column_expr(execution_columns, column, fallback)
                    for column, fallback in (("finished_at", "NULL"), ("updated_at", "0"),
                        ("current_phase", "''"), ("current_step_index", "0"), ("step_count", "0"))
                ]
                latest_by_id = {row["workflow_id"]: row for row in conn.execute(
                    f"SELECT DISTINCT ON (workflow_id) {', '.join(metadata_columns)} "
                    f"FROM workflow_executions ORDER BY workflow_id, {effective_time} DESC, id DESC"
                ).fetchall()}
            workflow_ids = set(stats_by_id) | set(summaries)
            if include_mock:
                workflow_ids.update(SOC_PINNED_WORKFLOW_NAMES)
            workflows = []
            total_execution_count = total_today_execution_count = total_active_count = 0
            for workflow_id in workflow_ids:
                stats, summary = stats_by_id.get(workflow_id), summaries.get(workflow_id, {})
                latest = latest_by_id.get(workflow_id)
                state = states.get(workflow_id) or _workflow_trigger_state(conn, workflow_id, [])
                execution_count = max(_safe_int(stats["call_count"] if stats else summary.get("execution_count")), 0)
                today_count = max(_safe_int(daily_counts.get(workflow_id, summary.get("today_execution_count", 0))
                                           if stats else summary.get("today_execution_count", 0)), 0)
                success_count = max(_safe_int(stats["success_count"] if stats else summary.get("success_count")), 0)
                active_count = max(_safe_int(summary.get("active_count")), 0)
                total_execution_count += execution_count
                total_today_execution_count += today_count
                total_active_count += active_count
                name = names.get(workflow_id, workflow_id)
                if UUID_RE.match(str(workflow_id)) and name == workflow_id:
                    continue
                last_run_at = (_safe_int(latest["finished_at"]) or _safe_int(latest["updated_at"])
                               or _safe_int(latest["started_at"])) if latest else 0
                workflows.append({
                    "id": workflow_id, "name": name, "executionCount": execution_count,
                    "todayExecutionCount": today_count, "successCount": success_count,
                    "successRate": _ratio(success_count, execution_count), "activeCount": active_count,
                    "lastStatus": _workflow_effective_status(latest, active_count, state),
                    "lastRunAt": last_run_at, "latestExecutionHash": str(latest["id"] if latest else ""),
                    "latestAlertName": "", "progressPercent": 0, "progressLabel": "待执行",
                    "currentPhase": "", "sessionId": "", "messageId": "",
                })
            soc_order = {"stream_alert_denoise": 3, "stream_alert_triage": 2,
                         "onesec_kafka_investigation": 1, "tdp_alert_triage": 1, "sec_alert_unified_ops": 1}
            workflows.sort(key=lambda item: (item["activeCount"] > 0, item["lastRunAt"],
                                              item["executionCount"], soc_order.get(item["id"], 0)), reverse=True)
            selected = workflows[:min(max(int(limit), 0), 12)]
            selected_ids = [item["latestExecutionHash"] for item in selected if item["latestExecutionHash"]]
            payloads = {}
            if selected_ids:
                bounded = [f"CASE WHEN octet_length({column}) <= 262144 THEN {column} ELSE '{{}}' END AS {column}"
                           if column in execution_columns else f"'{{}}' AS {column}"
                           for column in ("input_params", "output_results", "payload")]
                payloads = {row["id"]: row for row in conn.execute(
                    f"SELECT id, {', '.join(bounded)} FROM workflow_executions WHERE id = ANY(?::text[])",
                    (selected_ids,),
                ).fetchall()}
            for item in selected:
                latest = latest_by_id.get(item["id"])
                if latest:
                    payload = payloads.get(latest["id"], {})
                    steps = max(_workflow_node_count(item["id"]), _safe_int(latest["step_count"]))
                    item["progressPercent"], item["progressLabel"] = _workflow_progress(
                        item["lastStatus"], latest["current_step_index"], latest["step_count"], steps,
                    )
                    item["latestAlertName"] = _workflow_latest_alert_name(
                        item["id"], payload.get("output_results", "{}"), payload.get("input_params", "{}"),
                    )
                    item["currentPhase"] = str(latest["current_phase"] or "")
                    item["sessionId"], item["messageId"] = _workflow_link_context(payload)
            return selected, total_execution_count, total_today_execution_count, total_active_count
    except Exception:
        return [], 0, 0, 0



def _get_task_center(include_mock=False):
    (
        session_count,
        tasks,
        scheduled_execution_count,
        scheduled_today_execution_count,
        scheduled_active_count,
    ) = _task_center_task_rows()
    (
        workflows,
        workflow_execution_count,
        workflow_today_execution_count,
        workflow_active_count,
    ) = _task_center_workflow_rows(
        include_mock=include_mock,
    )
    return {
        **_task_center_empty(),
        "sessionCount": session_count,
        "activeExecutionCount": scheduled_active_count + workflow_active_count,
        "scheduledActiveCount": scheduled_active_count,
        "workflowActiveCount": workflow_active_count,
        "scheduledTasks": tasks,
        "scheduledExecutionCount": scheduled_execution_count,
        "scheduledTodayExecutionCount": scheduled_today_execution_count,
        "workflowExecutionCount": workflow_execution_count,
        "workflowTodayExecutionCount": workflow_today_execution_count,
        "workflows": workflows,
    }


def _get_activity(params):
    _ensure_postgres_schema()
    _maybe_prune_activity()
    settings = _postgres_settings()
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
    workflow_snapshot = {"complete": True, "available": True}
    workflow_events = [
        *_get_workflow_recent_events("stream_alert_denoise", start_time, end_time, snapshot=workflow_snapshot),
        *_get_workflow_recent_events("stream_alert_triage", start_time, end_time, snapshot=workflow_snapshot),
    ]
    raw_cursor = str(params.get("cursor") or "").strip()
    bootstrap = str(params.get("bootstrap") or "").strip().lower() == "latest"
    limit = max(1, min(_safe_int(params.get("limit") or ACTIVITY_DEFAULT_LIMIT), ACTIVITY_MAX_LIMIT))

    if not _available(DEFAULT_POSTGRES_TABLE):
        return _activity_response(
            [],
            0,
            "",
            cursor_reset=bool(raw_cursor),
            workflow_stats=workflow_stats,
            workflow_events=workflow_events,
            workflow_snapshot=workflow_snapshot,
        )

    cursor = _decode_activity_cursor(raw_cursor) if raw_cursor else None
    cursor_reset = bool(raw_cursor and cursor is None)

    try:
        with _connect() as conn:


            if not (
                _table_exists(conn, DEFAULT_POSTGRES_TABLE)
                and _table_exists(conn, ACTIVITY_TABLE)
            ):
                return _activity_response(
                    [],
                    0,
                    0,
                    cursor_reset=cursor_reset,
                    workflow_stats=workflow_stats,
                    workflow_events=workflow_events,
                    workflow_snapshot=workflow_snapshot,
                )
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
                    workflow_snapshot=workflow_snapshot,
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
                workflow_snapshot=workflow_snapshot,
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
            workflow_snapshot=workflow_snapshot,
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
    workflow_snapshot=None,
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
        "workflowSnapshotComplete": bool(workflow_snapshot and workflow_snapshot["complete"]),
        "workflowSnapshotAvailable": bool(workflow_snapshot and workflow_snapshot.get("available", True)),
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
    return conn.columns(settings["table"].strip(chr(34)))

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
            f"WITH samples AS (SELECT MAX(rowid) AS activity_row_id, MAX({settings['event_time_column']}) AS activity_event_time, "
            f"COUNT(*) AS sample_count "
            f"FROM {table} WHERE rowid > ? AND rowid <= ? "
            f"GROUP BY COALESCE(NULLIF(\"source_type\", ''), 'unknown'), "
            f"COALESCE(NULLIF(\"threat_name\", ''), 'unknown'), \"is_duplicate\" "
            f"ORDER BY sample_count DESC, activity_row_id DESC LIMIT ?) "
            f"SELECT samples.*, source.{record_column} AS record_json FROM samples "
            f"JOIN {table} source ON source.rowid = samples.activity_row_id "
            f"ORDER BY sample_count DESC, activity_row_id DESC",
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
            "threatName": _first_activity_text(record, "threat_name") or "未知告警",
            "srcIp": _first_activity_text(record, "sip", "src_ip", "source_ip"),
            "dstIp": _first_activity_text(record, "dip", "dst_ip", "destination_ip"),
            "requestUri": _first_activity_text(record, "req_http_url", "uri", "url"),
            "threatPhase": _first_activity_text(record, "threat_phase"),
            "threatType": _first_activity_text(record, "_threat_type", "threat_type"),
        },
    }
    if stage == "denoise":
        event["result"] = {
            "isDuplicate": record.get("is_duplicate") is True,
            "clusterId": _first_activity_text(record, "_lsh_cluster_id"),
            "dedupKey": _first_activity_text(record, "dedup_key"),
        }
    else:
        verdict = _triage_attack_verdict_value(record)
        attack_success = _triage_attack_success_value(record)
        outcome = _triage_outcome_key(record)
        event["result"] = {
            "triageStatus": triage_status or "completed",
            "triageSource": str(record.get("triage_source") or "").strip().lower() or "triaged",
            "durationMs": _safe_int(record.get("triage_ms")),
            "verdict": verdict,
            "attackSuccess": attack_success,
            "verdictLabel": RESULT_LABELS.get(outcome, "待确认"),
            "threatSeverity": _first_activity_text(record, "threat_severity"),
            "riskLevel": _first_activity_text(record, "risk_level"),
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
    return _database.get().identity(), int(time.monotonic() // 15)

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


def get_stats(ctx, request):
    _database.set(ctx.database)
    params = dict(request.query_params)
    return _get_stats(params)


def _get_stats(params):
    _ensure_postgres_schema()
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
                "mode": "postgres",
                "jsonlEnabled": False,
                "allowedDatabases": [
                    _display_path(DEFAULT_POSTGRES_DB),
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
            {"key": "non_attack", "label": "非攻击", "value": triage["benign"], "color": "#58a6ff"},
            {"key": "unknown", "label": "未知", "value": triage["unknown"], "color": "#9b8cff"},
        ],
        "topThreatTypes": _counter_items(
            triage["threatTypeCounter"] or denoise["threatTypeCounter"],
            14,
        ),
        "severityLevels": _counter_items(
            _profile_counter(denoise, triage, "severityCounter"),
            8,
        ),
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
    return _find_postgres_sources(start_date, end_date, start_time, end_time)


def _asset_file_date(path):
    if isinstance(path, _RecordSource):
        return path.date
    return ""


def _latest_asset_date():
    dates = _available_asset_dates()
    return dates[-1] if dates else datetime.now().strftime("%Y-%m-%d")


def _available_asset_dates():
    return _available_postgres_dates()


def _active_data_source():
    return "postgres"


def _postgres_settings():
    return {
        "db_path": DEFAULT_POSTGRES_DB,
        "table": f'"{DEFAULT_POSTGRES_TABLE}"',
        "facts_table": f'"{FACTS_TABLE}"',
        "activity_table": f'"{ACTIVITY_TABLE}"',
        "record_column": f'"{DEFAULT_POSTGRES_RECORD_COLUMN}"',
        "date_column": f'"{DEFAULT_POSTGRES_DATE_COLUMN}"',
        "event_time_column": f'"{DEFAULT_POSTGRES_EVENT_TIME_COLUMN}"',
    }


def _active_source_path():
    return _postgres_settings()["db_path"]


def _active_source_exists():
    path = _active_source_path()
    return _available(DEFAULT_POSTGRES_TABLE)


def _find_postgres_sources(start_date, end_date, start_time=0, end_time=0):
    settings = _postgres_settings()
    db_path = settings["db_path"]
    if not _available(DEFAULT_POSTGRES_TABLE):
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
        with _connect() as conn:
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
                data_source="postgres",
                record_count=int(record_count or 0),
                start_time=start_time,
                end_time=end_time,
            )
        )
    return sources


def _available_postgres_dates():
    settings = _postgres_settings()
    db_path = settings["db_path"]
    if not _available(DEFAULT_POSTGRES_TABLE):
        return []
    query = (
        f"SELECT DISTINCT {settings['date_column']} AS asset_date "
        f"FROM {settings['facts_table']} "
        f"WHERE {settings['date_column']} IS NOT NULL "
        f"ORDER BY {settings['date_column']}"
    )
    try:
        with _connect() as conn:
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
    if paths and all(isinstance(path, _RecordSource) and path.data_source == "postgres" for path in paths):
        optimized = _read_postgres_denoise(paths, workflow_call_count)
        if optimized is not None:
            return optimized

    total_raw = 0
    duplicates = 0
    parse_errors = 0
    headers = []
    source_counter = Counter()
    threat_type_counter = Counter()
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
            threat_type_counter[_norm(obj.get("_threat_type") or obj.get("threat_type"))] += 1
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
        "threatTypeCounter": threat_type_counter,
        **profile_counters,
        "seriesRaw": series_raw,
        "seriesUnique": series_unique,
        "workflowCallCount": workflow_call_count,
    }


def _read_postgres_denoise(paths, workflow_call_count):
    settings = _postgres_settings()
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
        with _connect() as conn:
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
            profile_counters, threat_type_counter = _postgres_detail_counters(
                conn,
                settings,
                where_clause,
                query_params,
            )
            timeline = _postgres_timeline(
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
        "threatTypeCounter": threat_type_counter,
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


def _postgres_timeline(conn, settings, where_clause, query_params, dates, start_time, end_time):
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
        f"COALESCE(SUM(CASE WHEN has_triage = 1 "
        f"AND LOWER(triage_status) NOT IN ('failed', 'error') "
        f"AND LOWER(triage_attack_verdict) = 'attack' "
        f"THEN 1 ELSE 0 END), 0) AS attack_count "
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


def _postgres_detail_counters(conn, settings, where_clause, query_params):
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
            Counter(cached["threatTypeCounter"]),
        )

    rows = conn.execute(
        f"SELECT phase, direction, result, protocol, severity, response_code, "
        f"port, threat_type, COUNT(*) AS profile_count FROM {table} "
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
    threat_type_counter = Counter()
    for row in rows:
        count = _safe_int(row[8])
        for index, key in enumerate(profile_keys):
            profile_counters[key][_norm(row[index])] += count
        port_value = row[6]
        port = str(_safe_int(port_value)) if _safe_int(port_value) > 0 else _norm(port_value)
        profile_counters["portCounter"][port] += count
        threat_type_counter[_norm(row[7])] += count
    with _cache_lock:
        _denoise_detail_cache[cache_key] = {
            "lastRowId": latest_row_id,
            "lastTriagePersistedAt": latest_triage_at,
            "updatedAt": time.monotonic(),
            "profileCounters": {key: Counter(value) for key, value in profile_counters.items()},
            "threatTypeCounter": Counter(threat_type_counter),
        }
        _denoise_detail_cache.move_to_end(cache_key)
        while len(_denoise_detail_cache) > _DENOISE_DETAIL_CACHE_MAX:
            _denoise_detail_cache.popitem(last=False)
    return profile_counters, threat_type_counter


def _read_postgres_triage(paths):
    settings = _postgres_settings()
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
    cache_condition = "(LOWER(triage_source) = 'cache' OR LOWER(triage_status) = 'cached')"
    follower_condition = (
        "(LOWER(triage_source) IN ('follower', 'followers', 'follower_reused') "
        "OR LOWER(triage_status) = 'follower_reused')"
    )
    failed_condition = "LOWER(triage_status) IN ('failed', 'error')"
    new_triage_condition = (
        f"NOT {cache_condition} AND NOT {follower_condition} AND NOT ({failed_condition})"
    )
    try:
        with _connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*), "
                f"COALESCE(SUM(CASE WHEN {cache_condition} THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN {follower_condition} THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN {failed_condition} "
                f"THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN {new_triage_condition} "
                f"THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(triage_attack_verdict) = 'attack' "
                f"AND LOWER(triage_attack_success) = 'success' THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(triage_attack_verdict) = 'attack' "
                f"AND LOWER(triage_attack_success) NOT IN ('success', 'failed') "
                f"THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(triage_attack_verdict) = 'attack' "
                f"AND LOWER(triage_attack_success) = 'failed' "
                f"THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(triage_attack_verdict) = 'non_attack' "
                f"THEN 1 ELSE 0 END), 0), "
                f"COALESCE(SUM(CASE WHEN LOWER(triage_attack_verdict) "
                f"NOT IN ('attack', 'non_attack') "
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
            threat_type_rows = conn.execute(
                f"SELECT threat_type, COUNT(*) FROM {settings['facts_table']} "
                f"WHERE {triage_where} GROUP BY threat_type",
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
    new_triaged = _safe_int(row[4])
    attack_success = _safe_int(row[5])
    attack = _safe_int(row[6])
    attack_failed = _safe_int(row[7])
    benign = _safe_int(row[8])
    unknown = _safe_int(row[9])
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
        "avgTriageMs": avg_triage_ms,
        "headers": 0,
        "files": len(paths),
        "parseErrors": 0,
        "eventStart": _format_event_time(_parse_event_time(row[10])),
        "eventEnd": _format_event_time(_parse_event_time(row[11])),
        "sourceCounter": Counter({_norm(key): _safe_int(value) for key, value in source_rows}),
        "threatTypeCounter": Counter(
            {_norm(key): _safe_int(value) for key, value in threat_type_rows}
        ),
        "riskCounter": Counter({_norm(key): _safe_int(value) for key, value in risk_rows}),
        "statusCounter": Counter({_norm(key): _safe_int(value) for key, value in status_rows}),
        **profile_counters,
        "seriesTotal": [],
        "seriesAttack": [],
    }


def _read_triage(paths):
    if paths and all(isinstance(path, _RecordSource) and path.data_source == "postgres" for path in paths):
        optimized = _read_postgres_triage(paths)
        if optimized is not None:
            return optimized
    total_records = 0
    parse_errors = 0
    headers = []
    verdict_counter = Counter()
    source_counter = Counter()
    threat_type_counter = Counter()
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
            verdict = _triage_attack_verdict_value(obj)
            outcome = _triage_outcome_key(obj)
            source = _norm(obj.get("_source_type") or obj.get("source_type") or obj.get("device_type"))
            source_counter[source] += 1
            threat_type_counter[_norm(obj.get("_threat_type") or obj.get("threat_type"))] += 1
            risk_counter[_norm(obj.get("risk_level"))] += 1
            triage_ms = _safe_int(obj.get("triage_ms"))
            if triage_ms > 0:
                triage_ms_total += triage_ms
                triage_ms_count += 1
            _update_profile_counters(obj, profile_counters, triage_result=True)
            event_start, event_end = _merge_record_time(event_start, event_end, obj)
            triage_source = _norm(obj.get("triage_source"))
            triage_status = _norm(obj.get("triage_status"))
            status_counter[triage_status or triage_source] += 1
            triage_failed = triage_status in {"failed", "error"}

            if not triage_failed:
                verdict_counter[outcome] += 1
                if verdict == "attack":
                    file_attack += 1
            else:
                fallback_failed += 1

            if triage_source == "cache" or triage_status == "cached":
                fallback_cache += 1
            elif triage_source in {"follower", "followers", "follower_reused"} or triage_status == "follower_reused":
                fallback_followers += 1
            elif not triage_failed:
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

    attack_success = verdict_counter["attack_success"]
    attack = verdict_counter["attack"]
    attack_failed = verdict_counter["attack_failed"]
    attack_total = attack_success + attack + attack_failed
    benign = verdict_counter["non_attack"]
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
        "threatTypeCounter": threat_type_counter,
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


def _update_profile_counters(obj, counters, *, triage_result=False):
    counters["phaseCounter"][_norm(obj.get("threat_phase") or obj.get("attack_phase") or obj.get("kill_chain_phase"))] += 1
    counters["directionCounter"][_norm(obj.get("direction") or obj.get("traffic_direction"))] += 1
    result = (
        _triage_attack_success_value(obj)
        if triage_result
        else obj.get("threat_result") or obj.get("attack_verdict")
    )
    counters["resultCounter"][_norm(result)] += 1
    counters["protocolCounter"][_norm(obj.get("net_type") or obj.get("net_app_proto") or obj.get("protocol"))] += 1
    counters["severityCounter"][_norm(obj.get("threat_severity"))] += 1
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
    pending = triage["unknown"]
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
    if isinstance(path, _RecordSource) and path.data_source == "postgres":
        yield from _iter_postgres_records(path, triage_only=triage_only)
    return


def _iter_postgres_records(source, *, triage_only=False):
    settings = _postgres_settings()
    time_filter = ""
    query_params = [source.date]
    if source.start_time > 0 and source.end_time > 0:
        time_filter = f" AND {settings['event_time_column']} BETWEEN ? AND ?"
        query_params.extend((source.start_time, source.end_time))
    triage_filter = ""
    if triage_only:
        triage_filter = (
            f" AND (NULLIF(({settings['record_column']}->>'triage_status'), '') IS NOT NULL "
            f"OR NULLIF(({settings['record_column']}->>'_triage_persisted_at'), '') IS NOT NULL "
            f"OR ({settings['record_column']}->>'triage_report') IS NOT NULL)"
        )
    query = (
        f"SELECT {settings['record_column']} AS record_json "
        f"FROM {settings['table']} "
        f"WHERE {settings['date_column']} = ?{time_filter}{triage_filter} "
        f"ORDER BY {settings['event_time_column']}, rowid"
    )
    try:
        with _connect() as conn:
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
        stat = None
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


def _workflow_config_projection_rows(conn):
    if not _table_exists(conn, "workflow_configs"):
        return []
    return conn.execute(
        "SELECT workflow_id, kind, "
        "jsonb_exists(config::jsonb, 'enabled') AS enabled_present, "
        "config::jsonb -> 'enabled' AS enabled, "
        "COALESCE(NULLIF(config::jsonb ->> 'timeoutSeconds', ''), "
        "config::jsonb ->> 'timeout_seconds') AS timeout_seconds, "
        "COALESCE(NULLIF(config::jsonb #>> '{workflow,name}', ''), "
        "NULLIF(config::jsonb #>> '{workflow,title}', ''), "
        "config::jsonb #>> '{workflow,id}') AS workflow_name "
        "FROM workflow_configs"
    ).fetchall()



def _workflow_config_enabled(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            pass
    return _truthy_config_value(value)



def _workflow_daily_count_map(conn, start_ms, end_ms):
    if not _table_exists(conn, WORKFLOW_SNAPSHOT_TABLE):
        return {}
    rows = conn.execute(
        f"WITH previous AS (SELECT DISTINCT ON (workflow_id) workflow_id, "
        f"GREATEST(COALESCE(call_count, 0), 0) AS previous_count FROM {WORKFLOW_SNAPSHOT_TABLE} "
        "WHERE sampled_at < ? ORDER BY workflow_id, sampled_at DESC), "
        f"daily AS (SELECT s.workflow_id, s.sampled_at, GREATEST(COALESCE(s.call_count, 0), 0) AS current_count, "
        "LAG(GREATEST(COALESCE(s.call_count, 0), 0), 1, p.previous_count) "
        "OVER (PARTITION BY s.workflow_id ORDER BY s.sampled_at) AS previous_count "
        f"FROM {WORKFLOW_SNAPSHOT_TABLE} s JOIN previous p USING(workflow_id) "
        "WHERE s.sampled_at >= ? AND s.sampled_at < ?) "
        "SELECT p.workflow_id, COALESCE(SUM(CASE WHEN d.current_count >= d.previous_count "
        "THEN d.current_count - d.previous_count ELSE d.current_count END), 0) AS today_count "
        "FROM previous p LEFT JOIN daily d USING(workflow_id) GROUP BY p.workflow_id",
        (start_ms, start_ms, end_ms),
    ).fetchall()
    return {row["workflow_id"]: max(_safe_int(row["today_count"]), 0) for row in rows}
