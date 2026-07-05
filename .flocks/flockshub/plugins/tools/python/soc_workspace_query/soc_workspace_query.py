from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


SOC_DB = Path.home() / ".flocks" / "data" / "soc.db"
WORKFLOW_DB = Path.home() / ".flocks" / "data" / "workflow.db"
SOC_UI_ROOT = Path.home() / ".flocks" / "plugins" / "contracts" / "webui" / "soc_ui"
SOC_TZ = ZoneInfo("Asia/Shanghai")

ALERT_FIELDS = {
    "row_id": "row_id",
    "record_id": "record_id",
    "source_type": "COALESCE(source_type, json_extract(record_json, '$._source_type'), json_extract(record_json, '$.source_type'))",
    "threat_name": "COALESCE(threat_name, json_extract(record_json, '$.threat_name'))",
    "threat_type": "json_extract(record_json, '$.threat_type')",
    "threat_phase": "json_extract(record_json, '$.threat_phase')",
    "attack_result": "COALESCE(json_extract(record_json, '$.attach_result'), json_extract(record_json, '$.threat_result'), json_extract(record_json, '$.attack_verdict'))",
    "attack_verdict": "json_extract(record_json, '$.attack_verdict')",
    "direction": "json_extract(record_json, '$.direction')",
    "sip": "json_extract(record_json, '$.sip')",
    "dip": "json_extract(record_json, '$.dip')",
    "sport": "json_extract(record_json, '$.sport')",
    "dport": "json_extract(record_json, '$.dport')",
    "req_host": "json_extract(record_json, '$.req_host')",
    "req_http_url": "json_extract(record_json, '$.req_http_url')",
    "rsp_status_code": "json_extract(record_json, '$.rsp_status_code')",
    "threat_rule_id": "json_extract(record_json, '$.threat_rule_id')",
}

PAGE_VALUES = ["all", "dashboard", "overview", "alerts", "alert_detail", "workflow", "schema"]


class _Request:
    def __init__(self, query_params: dict[str, Any]) -> None:
        self.query_params = query_params


def _parameters() -> list[ToolParameter]:
    return [
        ToolParameter(
            name="page",
            type=ParameterType.STRING,
            description=(
                "SOC workspace page to query. Use all for a compact workspace snapshot, "
                "dashboard for SOC dashboard stats, overview for SOC overview stats, "
                "alerts for the investigation list, alert_detail for one alert record, "
                "workflow for workflow execution status, or schema for available fields."
            ),
            required=False,
            default="all",
            enum=PAGE_VALUES,
        ),
        ToolParameter(
            name="filters",
            type=ParameterType.OBJECT,
            description=(
                "Optional query filters. Supported keys include date, startDate, endDate, "
                "startTime, endTime, keyword, record_id, row_id, source_type, threat_name, "
                "threat_type, threat_phase, attack_result, attack_verdict, direction, sip, "
                "dip, req_host, req_http_url, rsp_status_code, include_duplicates, and "
                "workflow_id. startTime/endTime may be epoch seconds, epoch milliseconds, "
                "or YYYY-MM-DD HH:MM:SS strings."
            ),
            required=False,
            default=None,
        ),
        ToolParameter(
            name="limit",
            type=ParameterType.INTEGER,
            description="Maximum alert list or workflow execution rows to return. Default 50, max 500.",
            required=False,
            default=50,
        ),
        ToolParameter(
            name="offset",
            type=ParameterType.INTEGER,
            description="Alert list offset for pagination.",
            required=False,
            default=0,
        ),
        ToolParameter(
            name="order",
            type=ParameterType.STRING,
            description="Alert ordering by event time. desc matches the SOC list default.",
            required=False,
            default="desc",
            enum=["desc", "asc"],
        ),
        ToolParameter(
            name="include_raw",
            type=ParameterType.BOOLEAN,
            description="Include the raw record_json payload for alert rows and detail results.",
            required=False,
            default=False,
        ),
        ToolParameter(
            name="include_reports",
            type=ParameterType.BOOLEAN,
            description="Include triage_report/final_report and report title fields when present.",
            required=False,
            default=True,
        ),
    ]


@ToolRegistry.register_function(
    name="soc_workspace_query",
    description=(
        "Query information rendered by Flocks' own SOC workspace pages: dashboard, overview, "
        "alert investigation list, alert detail/triage report, and workflow status. This tool "
        "is for inspecting the local Flocks SOC workspace itself, not for querying an external "
        "SOC product. It reads only local Flocks databases: ~/.flocks/data/soc.db and "
        "~/.flocks/data/workflow.db."
    ),
    description_cn=(
        "查询 Flocks 自身 SOC 工作区页面信息，包括 dashboard、overview、告警调查列表、"
        "单条告警详情、研判报告和工作流状态。这个工具用于查看本机 Flocks 自身的 SOC "
        "工作区数据，不是查询外部 SOC 系统；只读取 ~/.flocks/data/soc.db 和 "
        "~/.flocks/data/workflow.db。"
    ),
    category=ToolCategory.CUSTOM,
    parameters=_parameters(),
    tags=["soc", "flocks", "workspace", "dashboard", "overview", "alerts", "sqlite"],
)
async def soc_workspace_query(
    ctx: ToolContext,
    page: str = "all",
    filters: Optional[dict[str, Any]] = None,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
    include_raw: bool = False,
    include_reports: bool = True,
) -> ToolResult:
    page = (page or "all").strip().lower()
    if page not in PAGE_VALUES:
        return ToolResult(success=False, error=f"Unsupported SOC page: {page!r}")

    safe_filters = filters if isinstance(filters, dict) else {}
    safe_limit = max(1, min(int(limit or 50), 500))
    safe_offset = max(0, int(offset or 0))
    safe_order = "asc" if str(order).lower() == "asc" else "desc"

    try:
        output = _query_page(
            page=page,
            filters=safe_filters,
            limit=safe_limit,
            offset=safe_offset,
            order=safe_order,
            include_raw=bool(include_raw),
            include_reports=bool(include_reports),
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Failed to query Flocks SOC workspace: {exc}",
            metadata={"session_id": ctx.session_id, "page": page},
        )

    return ToolResult(
        success=True,
        output=output,
        title=f"Flocks SOC workspace: {page}",
        metadata={
            "session_id": ctx.session_id,
            "page": page,
            "soc_db": _display_path(SOC_DB),
            "workflow_db": _display_path(WORKFLOW_DB),
        },
    )


def _query_page(
    *,
    page: str,
    filters: dict[str, Any],
    limit: int,
    offset: int,
    order: str,
    include_raw: bool,
    include_reports: bool,
) -> dict[str, Any]:
    if page == "schema":
        return _schema()
    if page == "dashboard":
        return _with_source("dashboard", _page_stats("soc_dashboard", filters))
    if page == "overview":
        return _with_source("overview", _page_stats("soc_overview", filters))
    if page == "alerts":
        return _query_alerts(filters, limit, offset, order, include_raw, include_reports)
    if page == "alert_detail":
        return _query_alert_detail(filters, include_raw, include_reports)
    if page == "workflow":
        return _query_workflow(filters, limit)

    alerts = _query_alerts(filters, min(limit, 20), offset, order, include_raw, include_reports)
    return {
        "page": "all",
        "generatedAt": _now(),
        "source": _source_info(),
        "dashboard": _safe_page_stats("soc_dashboard", filters),
        "overview": _safe_page_stats("soc_overview", filters),
        "alerts": alerts,
        "workflow": _query_workflow(filters, min(limit, 20)),
    }


def _schema() -> dict[str, Any]:
    return {
        "page": "schema",
        "generatedAt": _now(),
        "source": _source_info(),
        "pages": PAGE_VALUES,
        "alertFilterFields": sorted(ALERT_FIELDS),
        "alertRecordFields": _record_field_sample(),
        "notes": [
            "dashboard and overview reuse the installed SOC WebUI API handlers.",
            "alerts and alert_detail read alert_records from ~/.flocks/data/soc.db.",
            "workflow reads workflow_stats and workflow_executions from ~/.flocks/data/workflow.db.",
        ],
    }


def _page_stats(page_dir_name: str, filters: dict[str, Any]) -> dict[str, Any]:
    handler_path = SOC_UI_ROOT / page_dir_name / "api" / "handlers.py"
    if not handler_path.is_file():
        raise FileNotFoundError(f"SOC page handler not found: {handler_path}")

    module_name = f"_flocks_soc_{page_dir_name}_handler"
    spec = importlib.util.spec_from_file_location(module_name, handler_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load SOC page handler: {handler_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    query = _page_query_params(filters)
    return module.get_stats(None, _Request(query))


def _safe_page_stats(page_dir_name: str, filters: dict[str, Any]) -> dict[str, Any]:
    try:
        return _with_source(page_dir_name.replace("soc_", ""), _page_stats(page_dir_name, filters))
    except Exception as exc:
        return {"page": page_dir_name.replace("soc_", ""), "error": str(exc), "source": _source_info()}


def _page_query_params(filters: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "date": "date",
        "startDate": "startDate",
        "endDate": "endDate",
        "start_date": "startDate",
        "end_date": "endDate",
        "startTime": "startTime",
        "endTime": "endTime",
        "start_time": "startTime",
        "end_time": "endTime",
    }
    query: dict[str, Any] = {}
    for source, target in mapping.items():
        value = filters.get(source)
        if value not in (None, ""):
            query[target] = value
    return query


def _with_source(page: str, data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    result.setdefault("page", page)
    result.setdefault("source", _source_info())
    return result


def _query_alerts(
    filters: dict[str, Any],
    limit: int,
    offset: int,
    order: str,
    include_raw: bool,
    include_reports: bool,
) -> dict[str, Any]:
    _require_db(SOC_DB, "SOC alert database")
    where, params, applied = _alert_where(filters)
    order_sql = "ASC" if order == "asc" else "DESC"
    query = f"""
        SELECT row_id, record_id, asset_date, source_file, line_number, event_time,
               source_type, threat_name, is_duplicate, record_json
        FROM alert_records
        WHERE {where}
        ORDER BY event_time {order_sql}, row_id {order_sql}
        LIMIT ? OFFSET ?
    """
    count_query = f"SELECT COUNT(*) FROM alert_records WHERE {where}"

    with sqlite3.connect(SOC_DB) as conn:
        conn.row_factory = sqlite3.Row
        total = int(conn.execute(count_query, params).fetchone()[0])
        rows = conn.execute(query, [*params, limit, offset]).fetchall()

    items = [_normalize_alert_row(row, include_raw, include_reports) for row in rows]
    return {
        "page": "alerts",
        "generatedAt": _now(),
        "source": _source_info(),
        "summary": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "returned": len(items),
            "order": order,
            "latestDate": _latest_asset_date(),
        },
        "filtersApplied": applied,
        "facets": _alert_facets(where, params),
        "items": items,
    }


def _query_alert_detail(filters: dict[str, Any], include_raw: bool, include_reports: bool) -> dict[str, Any]:
    detail_filters = dict(filters)
    if not any(detail_filters.get(key) for key in ("row_id", "record_id", "id")):
        raise ValueError("alert_detail requires filters.row_id, filters.record_id, or filters.id")
    if detail_filters.get("id") and not detail_filters.get("record_id"):
        detail_filters["record_id"] = detail_filters["id"]

    result = _query_alerts(detail_filters, 1, 0, "desc", include_raw=True, include_reports=include_reports)
    if not result["items"]:
        return {
            "page": "alert_detail",
            "generatedAt": _now(),
            "source": _source_info(),
            "found": False,
            "filtersApplied": result["filtersApplied"],
        }

    item = result["items"][0]
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    detail = {
        "page": "alert_detail",
        "generatedAt": _now(),
        "source": _source_info(),
        "found": True,
        "summary": item["summary"],
        "network": item["network"],
        "http": item["http"],
        "triage": item.get("triage", {}),
        "reports": item.get("reports", {}),
        "raw": raw if include_raw else None,
    }
    if not include_raw:
        detail.pop("raw", None)
    return detail


def _query_workflow(filters: dict[str, Any], limit: int) -> dict[str, Any]:
    _require_db(WORKFLOW_DB, "workflow database")
    workflow_id = _clean(filters.get("workflow_id") or "")
    limit = max(1, min(limit, 100))
    stats_params: list[Any] = []
    stats_where = ""
    if workflow_id:
        stats_where = "WHERE workflow_id = ?"
        stats_params.append(workflow_id)

    exec_where = []
    exec_params: list[Any] = []
    if workflow_id:
        exec_where.append("workflow_id = ?")
        exec_params.append(workflow_id)
    start_ms, end_ms = _workflow_time_range(filters)
    if start_ms is not None:
        exec_where.append("started_at >= ?")
        exec_params.append(start_ms)
    if end_ms is not None:
        exec_where.append("started_at <= ?")
        exec_params.append(end_ms)
    exec_where_sql = " AND ".join(exec_where) if exec_where else "1=1"

    with sqlite3.connect(WORKFLOW_DB) as conn:
        conn.row_factory = sqlite3.Row
        stats_rows = conn.execute(
            f"""
            SELECT workflow_id, call_count, success_count, error_count, total_runtime,
                   avg_runtime, thumbs_up, thumbs_down, updated_at
            FROM workflow_stats
            {stats_where}
            ORDER BY updated_at DESC
            """,
            stats_params,
        ).fetchall()
        execution_rows = conn.execute(
            f"""
            SELECT id, workflow_id, status, current_phase, current_node_id,
                   current_node_type, current_step_index, step_count,
                   error_message, trigger_id, trigger_type, started_at,
                   finished_at, duration, updated_at
            FROM workflow_executions
            WHERE {exec_where_sql}
            ORDER BY started_at DESC
            LIMIT ?
            """,
            [*exec_params, limit],
        ).fetchall()

    return {
        "page": "workflow",
        "generatedAt": _now(),
        "source": _source_info(),
        "summary": {
            "statsRows": len(stats_rows),
            "executionRows": len(execution_rows),
            "workflowId": workflow_id or "all",
        },
        "stats": [_normalize_workflow_stat(row) for row in stats_rows],
        "executions": [_normalize_workflow_execution(row) for row in execution_rows],
    }


def _alert_where(filters: dict[str, Any]) -> tuple[str, list[Any], list[dict[str, Any]]]:
    clauses = []
    params: list[Any] = []
    applied: list[dict[str, Any]] = []

    if not _truthy(filters.get("include_duplicates")):
        clauses.append("is_duplicate = 0")
        applied.append({"field": "include_duplicates", "value": False})

    date_value = filters.get("date")
    start_date = filters.get("startDate") or filters.get("start_date")
    end_date = filters.get("endDate") or filters.get("end_date")
    if date_value and not start_date and not end_date:
        start_date = end_date = date_value
    if start_date:
        clauses.append("asset_date >= ?")
        params.append(str(start_date))
        applied.append({"field": "startDate", "value": str(start_date)})
    if end_date:
        clauses.append("asset_date <= ?")
        params.append(str(end_date))
        applied.append({"field": "endDate", "value": str(end_date)})

    start_time = _to_epoch_seconds(filters.get("startTime", filters.get("start_time")))
    end_time = _to_epoch_seconds(filters.get("endTime", filters.get("end_time")))
    if start_time is not None:
        clauses.append("event_time >= ?")
        params.append(start_time)
        applied.append({"field": "startTime", "value": start_time})
    if end_time is not None:
        clauses.append("event_time <= ?")
        params.append(end_time)
        applied.append({"field": "endTime", "value": end_time})

    keyword = _clean(filters.get("keyword"))
    if keyword:
        clauses.append("(record_json LIKE ? OR threat_name LIKE ? OR record_id LIKE ? OR row_id LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like, like])
        applied.append({"field": "keyword", "value": keyword})

    for key, expression in ALERT_FIELDS.items():
        if key in {"row_id", "record_id"}:
            value = filters.get(key)
        else:
            value = filters.get(key)
        if value in (None, "", []):
            continue
        if isinstance(value, (list, tuple, set)):
            cleaned = [_clean(item) for item in value if _clean(item)]
            if not cleaned:
                continue
            placeholders = ", ".join("?" for _ in cleaned)
            clauses.append(f"{expression} IN ({placeholders})")
            params.extend(cleaned)
            applied.append({"field": key, "value": cleaned})
        else:
            clauses.append(f"{expression} = ?")
            params.append(_clean(value))
            applied.append({"field": key, "value": _clean(value)})

    return " AND ".join(clauses) if clauses else "1=1", params, applied


def _alert_facets(where: str, params: list[Any]) -> dict[str, list[dict[str, Any]]]:
    facets: dict[str, list[dict[str, Any]]] = {}
    facet_fields = {
        "source_type": "COALESCE(source_type, json_extract(record_json, '$._source_type'), json_extract(record_json, '$.source_type'))",
        "threat_name": "COALESCE(threat_name, json_extract(record_json, '$.threat_name'))",
        "threat_type": "json_extract(record_json, '$.threat_type')",
        "threat_phase": "json_extract(record_json, '$.threat_phase')",
        "attack_result": "COALESCE(json_extract(record_json, '$.attach_result'), json_extract(record_json, '$.threat_result'), json_extract(record_json, '$.attack_verdict'))",
        "direction": "json_extract(record_json, '$.direction')",
    }
    with sqlite3.connect(SOC_DB) as conn:
        for key, expression in facet_fields.items():
            rows = conn.execute(
                f"""
                SELECT {expression} AS value, COUNT(*) AS count
                FROM alert_records
                WHERE {where}
                GROUP BY value
                ORDER BY count DESC
                LIMIT 20
                """,
                params,
            ).fetchall()
            facets[key] = [
                {"value": _clean(value) or "unknown", "count": int(count or 0)}
                for value, count in rows
                if value not in (None, "")
            ]
    return facets


def _normalize_alert_row(row: sqlite3.Row, include_raw: bool, include_reports: bool) -> dict[str, Any]:
    record = _loads(row["record_json"])
    event_time = _safe_int(record.get("time"), _safe_int(row["event_time"]))
    threat_name = _clean(record.get("threat_name") or row["threat_name"] or "未知告警")
    attack_result = _attack_result(record)
    normalized = {
        "rowId": row["row_id"],
        "recordId": row["record_id"] or record.get("id"),
        "summary": {
            "eventTime": event_time,
            "eventTimeText": _format_epoch(event_time),
            "assetDate": row["asset_date"],
            "sourceType": _clean(row["source_type"] or record.get("_source_type") or record.get("source_type")),
            "threatName": threat_name,
            "threatMessage": _clean(record.get("threat_msg")),
            "threatType": _clean(record.get("threat_type")),
            "threatPhase": _clean(record.get("threat_phase")),
            "attackBehavior": _clean(record.get("attack_verdict") or record.get("triage_status") or "unknown"),
            "attackResult": attack_result,
            "riskLevel": _clean(record.get("risk_level") or record.get("threat_level") or record.get("threat_severity")),
            "isDuplicate": bool(row["is_duplicate"]),
        },
        "network": {
            "direction": _clean(record.get("direction")),
            "sourceIp": _clean(record.get("sip")),
            "sourcePort": _safe_int(record.get("sport")),
            "destinationIp": _clean(record.get("dip")),
            "destinationPort": _safe_int(record.get("dport")),
            "protocol": _clean(record.get("net_type") or record.get("net_app_proto")),
        },
        "http": {
            "host": _clean(record.get("req_host")),
            "url": _clean(record.get("req_http_url")),
            "requestLine": _clean(record.get("req_line")),
            "responseStatus": _safe_int(record.get("rsp_status_code")),
            "userAgent": _clean(record.get("req_user_agent")),
        },
        "triage": {
            "ruleId": _clean(record.get("threat_rule_id")),
            "reportTitle": _clean(record.get("report_title")),
            "hasTriageReport": bool(_clean(record.get("triage_report"))),
            "hasFinalReport": bool(_clean(record.get("final_report"))),
        },
    }
    if include_reports:
        normalized["reports"] = {
            "reportTitle": _clean(record.get("report_title")),
            "triageReport": _clean(record.get("triage_report")),
            "finalReport": _clean(record.get("final_report")),
        }
    if include_raw:
        normalized["raw"] = record
    return normalized


def _normalize_workflow_stat(row: sqlite3.Row) -> dict[str, Any]:
    updated_at = _safe_int(row["updated_at"])
    return {
        "workflowId": row["workflow_id"],
        "callCount": _safe_int(row["call_count"]),
        "successCount": _safe_int(row["success_count"]),
        "errorCount": _safe_int(row["error_count"]),
        "totalRuntime": float(row["total_runtime"] or 0),
        "avgRuntime": float(row["avg_runtime"] or 0),
        "thumbsUp": _safe_int(row["thumbs_up"]),
        "thumbsDown": _safe_int(row["thumbs_down"]),
        "updatedAt": updated_at,
        "updatedAtText": _format_epoch_ms(updated_at),
    }


def _normalize_workflow_execution(row: sqlite3.Row) -> dict[str, Any]:
    started_at = _safe_int(row["started_at"])
    finished_at = _safe_int(row["finished_at"])
    return {
        "id": row["id"],
        "workflowId": row["workflow_id"],
        "status": row["status"],
        "currentPhase": row["current_phase"],
        "currentNodeId": row["current_node_id"],
        "currentNodeType": row["current_node_type"],
        "currentStepIndex": _safe_int(row["current_step_index"]),
        "stepCount": _safe_int(row["step_count"]),
        "errorMessage": row["error_message"],
        "triggerId": row["trigger_id"],
        "triggerType": row["trigger_type"],
        "startedAt": started_at,
        "startedAtText": _format_epoch_ms(started_at),
        "finishedAt": finished_at,
        "finishedAtText": _format_epoch_ms(finished_at),
        "duration": float(row["duration"] or 0),
        "updatedAt": _safe_int(row["updated_at"]),
    }


def _record_field_sample() -> list[str]:
    if not SOC_DB.is_file():
        return []
    try:
        with sqlite3.connect(SOC_DB) as conn:
            row = conn.execute("SELECT record_json FROM alert_records LIMIT 1").fetchone()
        if not row:
            return []
        value = _loads(row[0])
        return sorted(str(key) for key in value.keys())
    except Exception:
        return []


def _latest_asset_date() -> str:
    if not SOC_DB.is_file():
        return ""
    try:
        with sqlite3.connect(SOC_DB) as conn:
            row = conn.execute("SELECT MAX(asset_date) FROM alert_records").fetchone()
        return str(row[0] or "")
    except Exception:
        return ""


def _source_info() -> dict[str, Any]:
    return {
        "type": "flocks-local-soc-workspace",
        "socDb": _display_path(SOC_DB),
        "socDbExists": SOC_DB.is_file(),
        "workflowDb": _display_path(WORKFLOW_DB),
        "workflowDbExists": WORKFLOW_DB.is_file(),
        "jsonlEnabled": False,
        "externalSoc": False,
    }


def _workflow_time_range(filters: dict[str, Any]) -> tuple[int | None, int | None]:
    start = _to_epoch_seconds(filters.get("startTime", filters.get("start_time")))
    end = _to_epoch_seconds(filters.get("endTime", filters.get("end_time")))
    if start is None and filters.get("startDate"):
        start = _to_epoch_seconds(f"{filters['startDate']} 00:00:00")
    if end is None and filters.get("endDate"):
        end = _to_epoch_seconds(f"{filters['endDate']} 23:59:59")
    return (start * 1000 if start is not None else None, end * 1000 if end is not None else None)


def _to_epoch_seconds(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number // 1000 if number > 10_000_000_000 else number
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        number = int(float(text))
        return number // 1000 if number > 10_000_000_000 else number
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text[:19] if "T" in fmt else text, fmt)
            if fmt == "%Y-%m-%d":
                parsed = datetime.combine(parsed.date(), dt_time.min)
            return int(parsed.replace(tzinfo=SOC_TZ).timestamp())
        except ValueError:
            continue
    return None


def _attack_result(record: dict[str, Any]) -> str:
    for key in ("attach_result", "threat_result", "attack_success", "attack_verdict"):
        value = _clean(record.get(key))
        if value:
            return value
    status = _safe_int(record.get("rsp_status_code"))
    if status in {401, 403, 404, 405, 406, 410}:
        return "failed"
    return "unknown"


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _format_epoch(value: int) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value, SOC_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _format_epoch_ms(value: int) -> str:
    if not value:
        return ""
    return _format_epoch(value // 1000 if value > 10_000_000_000 else value)


def _now() -> str:
    return datetime.now(SOC_TZ).isoformat(timespec="seconds")


def _display_path(path: Path) -> str:
    try:
        return str(path.expanduser())
    except Exception:
        return str(path)


def _require_db(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
