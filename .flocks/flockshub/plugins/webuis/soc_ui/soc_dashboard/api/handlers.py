"""SOC dashboard page API.

flocks-soc-store-client: this file holds no database code.  The figures come
from the platform's SOC data service (``ctx.services.soc.dashboard``, the
``flocks.workflow.soc_store`` of the database backend in use), so the same
file serves every backend.
"""

import re
import time
from collections import Counter, OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The platform's SOC dashboard data service, bound by each request.
_store = None


def _bind(ctx):
    """Take the SOC data service the platform hands this page."""
    global _store
    soc = getattr(getattr(ctx, "services", None), "soc", None)
    if soc is None:
        # Not called by the page runtime: the SOC query tool loads this file itself.
        try:
            from flocks.workflow import soc_store
        except ImportError as exc:
            raise RuntimeError(
                "The SOC dashboard needs the SOC data service of the platform; update Flocks to a version that has it"
            ) from exc
        soc = soc_store.page_service()
    _store = soc.dashboard
    return _store


def _data():
    if _store is None:
        return _bind(None)
    return _store


def _is_store_source(path):
    """Whether *path* is a set of records in the alert store (what find_sources lists), not a file."""
    return hasattr(path, "data_source")


_stats_response_cache: OrderedDict = OrderedDict()
_STATS_RESPONSE_CACHE_TTL = 300.0
_STATS_RESPONSE_CACHE_MAX = 32
_cache_lock = RLock()


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
    # Same precedence as the SQL projection (normalized_triage_success): an
    # explicit result string wins; older records carry the outcome in the
    # verdict ("attack_success" / "attack_failed") or as a boolean result.
    # A missing result is not an explicit "unknown".
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


def get_activity(ctx, request):
    _bind(ctx)
    params = dict(request.query_params)
    return _data().activity(params)


def get_task_center(ctx, request):
    _bind(ctx)
    params = dict(request.query_params)
    include_mock = _truthy(params.get("mockActivity")) or _truthy(params.get("mockTaskCenter"))
    return _get_task_center(include_mock)


def _task_center_empty():
    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sessionCount": 0,
        "activeExecutionCount": 0,
        "scheduledActiveCount": 0,
        "workflowActiveCount": 0,
        "scheduledTasks": [],
        "workflows": [],
        "sourceStatus": _task_center_source_status(),
    }


def _task_center_source_status():
    status = _data().source_status()
    return {
        "tasksDb": str(status["tasksDb"]),
        "workflowDb": str(status["workflowDb"]),
        "tasksAvailable": status["tasksAvailable"],
        "workflowAvailable": status["workflowAvailable"],
    }


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _get_task_center(include_mock=False):
    (
        session_count,
        tasks,
        scheduled_execution_count,
        scheduled_today_execution_count,
        scheduled_active_count,
    ) = _data().task_center_tasks()
    (
        workflows,
        workflow_execution_count,
        workflow_today_execution_count,
        workflow_active_count,
    ) = _data().task_center_workflows(
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
    _bind(ctx)
    params = dict(request.query_params)
    return _get_stats(params)


def _get_stats(params):
    _data().ensure_schema()
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
            "tokenUsage": _data().token_usage(),
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

    workflow_stats = _data().workflow_denoise_stats(
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
            "dataSource": "workflow call count + unique alerts",
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

    source_status = _data().source_status()
    result = {
        "date": start_date,
        "dateRange": date_range,
        "eventRange": event_range,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "latencyMs": round((time.time() - started) * 1000),
        "sourceStatus": {
            "dataPolicy": {
                "mode": source_status["mode"],
                "jsonlEnabled": False,
                "allowedDatabases": [
                    _display_path(source_status["alertsDb"]),
                    _display_path(source_status["workflowDb"]),
                    _display_path(source_status["usageDb"]),
                ],
            },
            "workflowStatsDb": _display_path(source_status["workflowDb"]),
            "workflowStats": workflow_stats,
            "sampleMode": sample_mode,
            "sampleFile": ", ".join(_source_label(path) for path in asset_files) if sample_mode else "",
            "assets": {
                "path": _display_path(source_status["alertsDb"]),
                "exists": source_status["alertsAvailable"],
                "dataSource": source_status["dataSource"],
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
        "tokenUsage": _data().token_usage(),
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
    return _data().find_sources(start_date, end_date, start_time, end_time)


def _asset_file_date(path):
    if _is_store_source(path):
        return path.date
    return ""


def _latest_asset_date():
    dates = _available_asset_dates()
    return dates[-1] if dates else datetime.now().strftime("%Y-%m-%d")


def _available_asset_dates():
    return _data().available_dates()


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
    if _is_store_source(path):
        return path.role
    name = path.name.lower()
    if "triage" in name or "研判" in name:
        return "triage"
    return "denoise"


def _read_denoise(paths, workflow_call_count: int = 0):
    if paths and all(_is_store_source(path) for path in paths):
        optimized = _data().read_denoise(paths, workflow_call_count)
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


def _read_triage(paths):
    if paths and all(_is_store_source(path) for path in paths):
        optimized = _data().read_triage(paths)
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
    if _is_store_source(path):
        yield from _data().iter_records(path, triage_only=triage_only)
    return


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
    if _is_store_source(path):
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
    if not Path(path).is_absolute():
        # A store that is not a file (a table of the platform's database) is named, not located.
        return str(path)
    try:
        return "~/" + str(Path(path).resolve().relative_to(Path.home())).replace("\\", "/")
    except Exception:
        return str(path)


def _source_label(path):
    if _is_store_source(path):
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
