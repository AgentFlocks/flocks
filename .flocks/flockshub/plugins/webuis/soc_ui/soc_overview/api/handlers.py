import json
import os
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CONTRACTS_ROOT = Path(__file__).resolve().parents[4]
ACCESS_CONFIG_PATH = CONTRACTS_ROOT / "access" / "soc_alerts.json"
DEFAULT_DATA_SOURCE = "sqlite"
DEFAULT_SQLITE_DB = Path.home() / ".flocks" / "data" / "soc.db"
DEFAULT_SQLITE_TABLE = "alert_records"
DEFAULT_SQLITE_RECORD_COLUMN = "record_json"
DEFAULT_SQLITE_DATE_COLUMN = "asset_date"
DEFAULT_SQLITE_EVENT_TIME_COLUMN = "event_time"

WORKFLOW_DB = Path.home() / ".flocks" / "data" / "workflow.db"

_workflow_stats_cache: dict = {}
_cache_updated_at: float = 0
_CACHE_TTL: float = 30.0


@dataclass(frozen=True)
class _RecordSource:
    path: Path
    role: str
    date: str
    data_source: str
    record_count: int = 0
    start_time: int | None = None
    end_time: int | None = None


def _get_workflow_call_count(workflow_name: str, date: str = None) -> int:
    global _workflow_stats_cache, _cache_updated_at
    now = time.time()
    cache_key = f"{workflow_name}:{date or 'total'}"

    if now - _cache_updated_at < _CACHE_TTL and cache_key in _workflow_stats_cache:
        return _workflow_stats_cache[cache_key]

    empty = {"callCount": 0, "dupCount": 0, "uniqueCount": 0}
    if not WORKFLOW_DB.is_file():
        return empty

    try:
        if date:
            start = int(datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)
            end = start + 86400 * 1000
            with sqlite3.connect(WORKFLOW_DB) as conn:
                rows = conn.execute(
                    """
                    SELECT output_results
                    FROM workflow_executions
                    WHERE workflow_id = ? AND started_at >= ? AND started_at < ?
                    """,
                    (workflow_name, start, end),
                ).fetchall()
            dup_count = 0
            unique_count = 0
            for (output_text,) in rows:
                try:
                    output = json.loads(output_text or "{}")
                except Exception:
                    output = {}
                stats = output.get("stats") if isinstance(output.get("stats"), dict) else {}
                raw = _safe_int(stats.get("raw_count"))
                if "after_dedup_count" in stats and stats.get("after_dedup_count") is not None:
                    unique_count += _safe_int(stats.get("after_dedup_count"))
                else:
                    unique_count += raw
                if output.get("is_duplicate") is True:
                    dup_count += 1
            result_dict = {
                "callCount": len(rows),
                "dupCount": dup_count,
                "uniqueCount": unique_count,
            }
        else:
            with sqlite3.connect(WORKFLOW_DB) as conn:
                row = conn.execute(
                    "SELECT call_count FROM workflow_stats WHERE workflow_id = ?",
                    (workflow_name,),
                ).fetchone()
            result_dict = {"callCount": _safe_int(row[0] if row else 0), "dupCount": 0, "uniqueCount": 0}

        _workflow_stats_cache[cache_key] = result_dict
        _cache_updated_at = now
        return result_dict
    except Exception:
        return _workflow_stats_cache.get(cache_key, empty)


SOURCE_DEFS = [
    ("ndr", "NDR 网络流量", ("ndr", "tdp", "network")),
    ("edr", "EDR 主机告警", ("edr", "hids", "linux")),
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
    # Same precedence as the posture dashboard's SQL projection: an explicit
    # result string wins; older records carry the outcome in the verdict
    # ("attack_success" / "attack_failed") or as a boolean result.
    if _triage_attack_verdict_value(record) != "attack":
        return "unknown"
    raw_result = record.get("triage_attack_success")
    result = str(raw_result).strip().lower() if raw_result is not None else ""
    if result in {"success", "failed", "unknown"}:
        return result
    raw_verdict = _norm(record.get("triage_attack_verdict"))
    if raw_verdict == "attack_success":
        return "success"
    if raw_verdict == "attack_failed":
        return "failed"
    if result in {"1", "true"}:
        return "success"
    return "unknown"


def _has_triage_result(record):
    """Rows the triage workflow wrote (the posture dashboard's triage filter)."""
    return bool(
        str(record.get("triage_status") or "").strip()
        or str(record.get("_triage_persisted_at") or "").strip()
        or record.get("triage_report") is not None
    )


def get_stats(ctx, request):
    start_time, end_time = _normalize_time_range(
        request.query_params.get("startTime"),
        request.query_params.get("endTime"),
    )
    date = _normalize_date(
        request.query_params.get("date")
        or _date_from_epoch(end_time)
        or _latest_asset_date()
    )
    if start_time is not None and end_time is not None:
        start_date = _date_from_epoch(start_time) or date
        end_date = _date_from_epoch(end_time) or date
        if start_date > end_date:
            start_date, end_date = end_date, start_date
    else:
        start_date, end_date = _normalize_range(
            request.query_params.get("startDate"),
            request.query_params.get("endDate"),
            date,
        )
    started = time.time()

    denoise_files, denoise_locations = [], []
    triage_files, triage_locations = [], []

    asset_files = _find_asset_files(start_date, end_date, start_time, end_time)
    asset_denoise_files = [path for path in asset_files if _asset_file_role(path) == "denoise"]
    asset_triage_files = [path for path in asset_files if _asset_file_role(path) == "triage"]
    sample_mode = bool(asset_denoise_files or asset_triage_files)
    if asset_denoise_files:
        denoise_files = asset_denoise_files
    # alert_records holds the triaged first-seen alerts, so the same rows carry
    # the model verdicts the triage statistics are built from (as on the
    # posture dashboard). Nothing is estimated when a field is missing.
    triage_files = asset_triage_files or asset_denoise_files

    workflow_stats = _get_workflow_call_count("stream_alert_denoise", date=start_date)
    denoise = _read_denoise(denoise_files)
    triage = _read_triage(triage_files)
    sources = _build_sources(denoise["sourceCounter"] or triage["sourceCounter"])
    closed_loop = _build_closed_loop(triage)
    pipeline = _build_pipeline(denoise, triage)
    date_range = _build_date_range(start_date, end_date, asset_files)
    event_range = _build_event_range(date_range, denoise, triage)

    return {
        "date": start_date,
        "dateRange": date_range,
        "eventRange": event_range,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "latencyMs": round((time.time() - started) * 1000),
        "sourceStatus": {
            "workflowStatsDb": _display_path(WORKFLOW_DB),
            "workflowStats": workflow_stats,
            "sampleMode": sample_mode,
            "sampleFile": ", ".join(_source_label(path) for path in asset_files) if sample_mode else "",
            "assets": {
                "path": _display_path(_active_source_path()),
                "exists": _active_source_exists(),
                "dataSource": _active_data_source(),
                "config": _display_path(ACCESS_CONFIG_PATH),
                "fileCount": len(asset_files),
                "availableDates": _available_asset_dates(),
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
        "fieldStats": _build_field_stats(denoise_files),
        "attackProfile": _build_attack_profile(denoise, triage),
        "verdicts": [
            {"key": "attack_success", "label": "攻击成功", "value": triage["attackSuccess"], "color": "#ff4d6d"},
            {"key": "attack", "label": "攻击行为", "value": triage["attack"], "color": "#ffb020"},
            {"key": "attack_failed", "label": "攻击失败", "value": triage["attackFailed"], "color": "#2ee6a6"},
            {"key": "non_attack", "label": "非攻击", "value": triage["benign"], "color": "#58a6ff"},
            {"key": "unknown", "label": "未知", "value": triage["unknown"], "color": "#9b8cff"},
        ],
        "topThreats": _counter_items(triage["threatCounter"] or denoise["threatCounter"], 14),
        "riskLevels": _counter_items(triage["riskCounter"], 5),
        "timeline": {
            "labels": [_asset_file_date(path) for path in denoise_files],
            "window": _timeline_window(start_date, end_date),
            "denoiseRaw": denoise["seriesRaw"],
            "denoiseUnique": denoise["seriesUnique"],
            "triageTotal": triage["seriesTotal"],
            "triageAttack": triage["seriesAttack"],
        },
    }


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


def _normalize_time_range(start_value, end_value):
    start_time = _safe_int(start_value)
    end_time = _safe_int(end_value)
    if start_time <= 0 or end_time <= 0:
        return None, None
    if start_time > end_time:
        start_time, end_time = end_time, start_time
    return start_time, end_time


def _date_from_epoch(value):
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _date_span(start_date, end_date):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    current = start
    while current <= end:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


def _find_asset_files(start_date, end_date, start_time=None, end_time=None):
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


def _load_alerts_config():
    raw = {}
    if ACCESS_CONFIG_PATH.is_file():
        try:
            value = json.loads(ACCESS_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            value = {}
        if isinstance(value, dict):
            raw = value

    sqlite_config = raw.get("sqlite") if isinstance(raw.get("sqlite"), dict) else {}
    return {
        "dataSource": DEFAULT_DATA_SOURCE,
        "sqlite": {
            "dbPath": _read_config_string(
                os.environ.get("FLOCKS_SOC_ALERTS_SQLITE_DB"),
                sqlite_config.get("dbPath"),
                str(DEFAULT_SQLITE_DB),
            ),
            "table": _read_config_string(sqlite_config.get("table"), DEFAULT_SQLITE_TABLE),
            "recordColumn": _read_config_string(sqlite_config.get("recordColumn"), DEFAULT_SQLITE_RECORD_COLUMN),
            "dateColumn": _read_config_string(sqlite_config.get("dateColumn"), DEFAULT_SQLITE_DATE_COLUMN),
            "eventTimeColumn": _read_config_string(
                sqlite_config.get("eventTimeColumn"),
                DEFAULT_SQLITE_EVENT_TIME_COLUMN,
            ),
        },
    }


def _active_data_source():
    return "sqlite"


def _read_config_string(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_config_path(value, fallback):
    text = _read_config_string(value, str(fallback))
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (ACCESS_CONFIG_PATH.parent / path).resolve()


def _sqlite_settings():
    config = _load_alerts_config()["sqlite"]
    return {
        "db_path": _resolve_config_path(config.get("dbPath"), DEFAULT_SQLITE_DB),
        "table": _sql_identifier(config.get("table"), DEFAULT_SQLITE_TABLE),
        "record_column": _sql_identifier(config.get("recordColumn"), DEFAULT_SQLITE_RECORD_COLUMN),
        "date_column": _sql_identifier(config.get("dateColumn"), DEFAULT_SQLITE_DATE_COLUMN),
        "event_time_column": _sql_identifier(
            config.get("eventTimeColumn"),
            DEFAULT_SQLITE_EVENT_TIME_COLUMN,
        ),
    }


def _sql_identifier(value, fallback):
    text = _read_config_string(value, fallback)
    if not SQL_IDENTIFIER_RE.match(text):
        text = fallback
    return f'"{text}"'


def _active_source_path():
    return _sqlite_settings()["db_path"]


def _active_source_exists():
    path = _active_source_path()
    return path.is_file()


def _find_sqlite_sources(start_date, end_date, start_time=None, end_time=None):
    settings = _sqlite_settings()
    db_path = settings["db_path"]
    if not db_path.is_file():
        return []

    where = [f"{settings['date_column']} BETWEEN ? AND ?"]
    params = [start_date, end_date]
    if start_time is not None and end_time is not None:
        where.append(f"{settings['event_time_column']} BETWEEN ? AND ?")
        params.extend([start_time, end_time])

    query = (
        f"SELECT {settings['date_column']} AS asset_date, COUNT(*) AS record_count "
        f"FROM {settings['table']} "
        f"WHERE {' AND '.join(where)} "
        f"GROUP BY {settings['date_column']} "
        f"ORDER BY {settings['date_column']}"
    )
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query, params).fetchall()
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
        f"FROM {settings['table']} "
        f"WHERE {settings['date_column']} IS NOT NULL "
        f"ORDER BY {settings['date_column']}"
    )
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query).fetchall()
    except Exception:
        return []
    return [str(row[0]) for row in rows if DATE_RE.match(str(row[0]))]


def _build_date_range(start_date, end_date, asset_files):
    file_dates = sorted({date for date in (_asset_file_date(path) for path in asset_files) if date})
    return {
        "start": start_date,
        "end": end_date,
        "label": start_date if start_date == end_date else f"{start_date} 至 {end_date}",
        "availableDates": _available_asset_dates(),
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


def _timeline_window(start_date, end_date):
    if start_date != end_date:
        days = len(list(_date_span(start_date, end_date)))
        return f"{days} 天范围聚合"
    return "按日统计"


def _asset_file_role(path):
    if isinstance(path, _RecordSource):
        return path.role
    name = path.name.lower()
    if "triage" in name or "研判" in name:
        return "triage"
    return "denoise"


def _read_denoise(paths, workflow_call_count: int = 0):
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
            threat_counter[_norm(obj.get("threat_name"))] += 1
            _update_profile_counters(obj, profile_counters)
            event_start, event_end = _merge_record_time(event_start, event_end, obj)
        total_raw += file_raw
        duplicates += file_duplicates
        series_raw.append(file_raw)
        series_unique.append(max(file_raw - file_duplicates, 0))

    total_unique = max(total_raw - duplicates, 0)
    series_raw = _per_date_series(series_raw)
    series_unique = _per_date_series(series_unique)

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


def _read_triage(paths):
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
    series_total = []
    series_attack = []

    for path in paths:
        file_total = 0
        file_attack = 0
        for obj in _iter_source_records(path):
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
            if not _has_triage_result(obj):
                # Migrated alert rows may never have been triaged.
                continue

            total_records += 1
            file_total += 1
            verdict = _triage_attack_verdict_value(obj)
            outcome = _triage_outcome_key(obj)
            verdict_counter[outcome] += 1
            if verdict == "attack":
                file_attack += 1

            source = _norm(obj.get("_source_type") or obj.get("source_type") or obj.get("device_type"))
            source_counter[source] += 1
            threat_counter[_norm(obj.get("threat_name"))] += 1
            risk_counter[_norm(obj.get("risk_level"))] += 1
            _update_profile_counters(obj, profile_counters, triage_result=True)
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

    attack_success = verdict_counter["attack_success"]
    attack = verdict_counter["attack"]
    attack_failed = verdict_counter["attack_failed"]
    attack_total = attack_success + attack + attack_failed
    benign = verdict_counter["non_attack"]
    unknown = verdict_counter["unknown"]

    series_total = _per_date_series(series_total)
    series_attack = _per_date_series(series_attack)

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


def _per_date_series(values):
    """Counts per selected date in date order; never a synthesized curve."""
    return [max(_safe_int(value), 0) for value in values]


def _build_field_stats(paths):
    total = 0
    duplicates = 0
    source_ips = set()
    destination_ips = set()
    destination_ports = set()
    hosts = set()
    urls = set()
    rules = set()
    source_ip_counter = Counter()
    destination_ip_counter = Counter()
    host_counter = Counter()
    url_counter = Counter()
    rule_counter = Counter()
    port_counter = Counter()
    status_counter = Counter()
    direction_counter = Counter()
    protocol_counter = Counter()
    threat_type_counter = Counter()
    threat_result_counter = Counter()
    threat_phase_counter = Counter()

    for path in paths:
        for obj in _iter_source_records(path):
            if obj is None or obj.get("_type") == "file_header":
                continue
            total += 1
            if obj.get("is_duplicate") is True:
                duplicates += 1

            sip = _field_text(obj.get("sip"))
            dip = _field_text(obj.get("dip"))
            host = _field_text(obj.get("req_host"))
            url = _field_text(obj.get("req_http_url"))
            rule = _field_text(obj.get("threat_rule_id"))
            dport = _field_text(obj.get("dport"))
            status_code = _field_text(obj.get("rsp_status_code"))
            direction = _norm(obj.get("direction") or "unknown")
            protocol = _norm(obj.get("net_type") or obj.get("net_app_proto") or "unknown")
            threat_type = _norm(obj.get("_threat_type") or obj.get("threat_type"))
            threat_result = _norm(obj.get("threat_result") or "unknown")
            threat_phase = _norm(obj.get("threat_phase") or "unknown")

            if sip:
                source_ips.add(sip)
                source_ip_counter[sip] += 1
            if dip:
                destination_ips.add(dip)
                destination_ip_counter[dip] += 1
            if host:
                hosts.add(host)
                host_counter[host] += 1
            if url:
                urls.add(url)
                url_counter[url] += 1
            if rule:
                rules.add(rule)
                rule_counter[rule] += 1
            if dport:
                destination_ports.add(dport)
                port_counter[dport] += 1
            if status_code:
                status_counter[status_code] += 1
            if direction and direction != "none":
                direction_counter[direction] += 1
            if protocol and protocol != "none":
                protocol_counter[protocol] += 1
            if threat_type and threat_type != "none":
                threat_type_counter[threat_type] += 1
            if threat_result and threat_result != "none":
                threat_result_counter[threat_result] += 1
            if threat_phase and threat_phase != "none":
                threat_phase_counter[threat_phase] += 1

    return {
        "totalRecords": total,
        "duplicates": duplicates,
        "uniqueRecords": max(total - duplicates, 0),
        "uniqueSourceIps": len(source_ips),
        "uniqueDestinationIps": len(destination_ips),
        "uniqueDestinationPorts": len(destination_ports),
        "uniqueHosts": len(hosts),
        "uniqueUrls": len(urls),
        "uniqueRules": len(rules),
        "topSourceIps": _counter_items(source_ip_counter, 8),
        "topDestinationIps": _counter_items(destination_ip_counter, 8),
        "topHosts": _counter_items(host_counter, 8),
        "topUrls": _counter_items(url_counter, 8),
        "topRules": _counter_items(rule_counter, 8),
        "ports": _counter_items(port_counter, 8),
        "statusCodes": _counter_items(status_counter, 8),
        "directions": _counter_items(direction_counter, 8),
        "protocols": _counter_items(protocol_counter, 8),
        "threatTypes": _counter_items(threat_type_counter, 8),
        "threatResults": _counter_items(threat_result_counter, 8),
        "threatPhases": _counter_items(threat_phase_counter, 8),
    }


def _field_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "none":
        return ""
    return text

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
    # A failed triage is already counted under its (normally unknown) verdict;
    # adding triageFailed again counted the same row twice.
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
    raw = denoise.get("workflowCallCount") or denoise["totalRaw"]
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


def _iter_source_records(path):
    if isinstance(path, _RecordSource) and path.data_source == "sqlite":
        yield from _iter_sqlite_records(path)
    return


def _iter_sqlite_records(source):
    settings = _sqlite_settings()
    where = [f"{settings['date_column']} = ?"]
    params = [source.date]
    if source.start_time is not None and source.end_time is not None:
        where.append(f"{settings['event_time_column']} BETWEEN ? AND ?")
        params.extend([source.start_time, source.end_time])
    query = (
        f"SELECT {settings['record_column']} AS record_json "
        f"FROM {settings['table']} "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {settings['event_time_column']}, rowid"
    )
    try:
        with sqlite3.connect(settings["db_path"]) as conn:
            rows = conn.execute(query, params).fetchall()
    except Exception:
        return

    for row in rows:
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
        if not isinstance(value, Counter)
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
