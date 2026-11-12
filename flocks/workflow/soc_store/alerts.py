"""
Alert records and workflow runs as the SOC query tool reads them, from SQLite.

``soc_workspace_query`` holds no SQL: it turns its filters into the criteria
:func:`search` takes and lays the rows out.  The alerts come from ``soc.db``,
the workflow figures from the runtime's ``workflow.db``.

A database backend replaces ``flocks.workflow.soc_store`` as a whole (see
``flocks.db_backend``) and answers the same questions from its own tables.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from . import _paths

#: What the tool and the alert page report as the kind of store they read.
DATA_SOURCE = "sqlite"
DATA_SOURCE_LABEL = "SQLite"

# The triage workflow stores its verdict in triage_attack_verdict /
# triage_attack_success; the SOC overview and alert pages read those first.
# Older records only carry the NDR fields (attach_result, threat_result,
# attack_verdict). triage_status is the triage run's own status ("ok"), not a
# verdict about the attack.
_TRIAGE_VERDICT_SQL = "lower(trim(CAST(json_extract(record_json, '$.triage_attack_verdict') AS TEXT)))"
_TRIAGE_SUCCESS_SQL = "lower(trim(CAST(json_extract(record_json, '$.triage_attack_success') AS TEXT)))"
ATTACK_VERDICT_SQL = (
    f"CASE WHEN COALESCE({_TRIAGE_VERDICT_SQL}, '') <> '' THEN "
    f"CASE WHEN {_TRIAGE_VERDICT_SQL} IN ('attack', 'attack_success', 'attack_failed') THEN 'attack' "
    f"WHEN {_TRIAGE_VERDICT_SQL} IN ('non_attack', 'benign') THEN 'non_attack' ELSE 'unknown' END "
    "ELSE json_extract(record_json, '$.attack_verdict') END"
)
ATTACK_RESULT_SQL = (
    f"CASE WHEN COALESCE({_TRIAGE_VERDICT_SQL}, '') <> '' THEN "
    f"CASE WHEN {_TRIAGE_VERDICT_SQL} NOT IN ('attack', 'attack_success', 'attack_failed') THEN 'unknown' "
    f"WHEN {_TRIAGE_SUCCESS_SQL} IN ('success', 'failed', 'unknown') THEN {_TRIAGE_SUCCESS_SQL} "
    f"WHEN {_TRIAGE_VERDICT_SQL} = 'attack_success' THEN 'success' "
    f"WHEN {_TRIAGE_VERDICT_SQL} = 'attack_failed' THEN 'failed' "
    f"WHEN {_TRIAGE_SUCCESS_SQL} IN ('1', 'true') THEN 'success' ELSE 'unknown' END "
    "ELSE COALESCE(json_extract(record_json, '$.attach_result'), json_extract(record_json, '$.threat_result'), "
    "json_extract(record_json, '$.attack_verdict')) END"
)

#: The fields an alert search can match exactly, and where each is read from.
ALERT_FIELDS = {
    "row_id": "row_id",
    "record_id": "record_id",
    "source_type": "COALESCE(source_type, json_extract(record_json, '$._source_type'), json_extract(record_json, '$.source_type'))",
    "threat_name": "COALESCE(threat_name, json_extract(record_json, '$.threat_name'))",
    "threat_type": "json_extract(record_json, '$.threat_type')",
    "threat_phase": "json_extract(record_json, '$.threat_phase')",
    "attack_result": ATTACK_RESULT_SQL,
    "attack_verdict": ATTACK_VERDICT_SQL,
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
FILTER_FIELDS = tuple(ALERT_FIELDS)

_FACET_FIELDS = {
    "source_type": ALERT_FIELDS["source_type"],
    "threat_name": ALERT_FIELDS["threat_name"],
    "threat_type": ALERT_FIELDS["threat_type"],
    "threat_phase": ALERT_FIELDS["threat_phase"],
    "attack_result": ATTACK_RESULT_SQL,
    "direction": ALERT_FIELDS["direction"],
}

_ALERT_COLUMNS = (
    "row_id", "record_id", "asset_date", "source_file", "line_number", "event_time",
    "source_type", "threat_name", "is_duplicate", "record_json",
)
_WORKFLOW_STAT_COLUMNS = (
    "workflow_id", "call_count", "success_count", "error_count", "total_runtime",
    "avg_runtime", "thumbs_up", "thumbs_down", "updated_at",
)
_WORKFLOW_EXECUTION_COLUMNS = (
    "id", "workflow_id", "status", "current_phase", "current_node_id",
    "current_node_type", "current_step_index", "step_count",
    "error_message", "trigger_id", "trigger_type", "started_at",
    "finished_at", "duration", "updated_at",
)


def source_info() -> dict[str, Any]:
    """Where the alerts and workflow runs are kept, and whether those stores are there."""
    soc_db, workflow_db = _paths.soc_db(), _paths.workflow_db()
    return {
        "dataSource": DATA_SOURCE,
        "socDb": str(soc_db),
        "socDbExists": soc_db.is_file(),
        "workflowDb": str(workflow_db),
        "workflowDbExists": workflow_db.is_file(),
    }


def _require(path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")


def _where(criteria: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if not criteria.get("include_duplicates"):
        clauses.append("is_duplicate = 0")
    if criteria.get("start_date"):
        clauses.append("asset_date >= ?")
        params.append(str(criteria["start_date"]))
    if criteria.get("end_date"):
        clauses.append("asset_date <= ?")
        params.append(str(criteria["end_date"]))
    if criteria.get("start_time") is not None:
        clauses.append("event_time >= ?")
        params.append(criteria["start_time"])
    if criteria.get("end_time") is not None:
        clauses.append("event_time <= ?")
        params.append(criteria["end_time"])
    keyword = criteria.get("keyword")
    if keyword:
        clauses.append("(record_json LIKE ? OR threat_name LIKE ? OR record_id LIKE ? OR row_id LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like, like])
    for key, value in (criteria.get("fields") or {}).items():
        expression = ALERT_FIELDS.get(key)
        if expression is None:
            raise ValueError(f"Unsupported alert filter field: {key}")
        # Compared as text: a JSON number (dport 443) comes back from
        # json_extract as an integer and would never equal the text "443".
        compared = expression if key in {"row_id", "record_id"} else f"CAST(({expression}) AS TEXT)"
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            if not values:
                continue
            placeholders = ", ".join("?" for _ in values)
            clauses.append(f"{compared} IN ({placeholders})")
            params.extend(values)
        else:
            clauses.append(f"{compared} = ?")
            params.append(value)
    return " AND ".join(clauses) if clauses else "1=1", params


def search(criteria: dict[str, Any], *, limit: int, offset: int, order: str = "desc") -> dict[str, Any]:
    """The alerts matching *criteria*, newest or oldest first: their count, one page of rows, and facets.

    *criteria* may hold ``include_duplicates``, ``start_date`` / ``end_date``
    (``YYYY-MM-DD``), ``start_time`` / ``end_time`` (epoch seconds), ``keyword``
    and ``fields`` (exact matches on :data:`FILTER_FIELDS`, a value or a list).
    """
    soc_db = _paths.soc_db()
    _require(soc_db, "SOC alert database")
    where, params = _where(criteria)
    order_sql = "ASC" if order == "asc" else "DESC"
    query = f"""
        SELECT {', '.join(_ALERT_COLUMNS)}
        FROM alert_records
        WHERE {where}
        ORDER BY event_time {order_sql}, row_id {order_sql}
        LIMIT ? OFFSET ?
    """
    count_query = f"SELECT COUNT(*) FROM alert_records WHERE {where}"

    with sqlite3.connect(soc_db) as conn:
        conn.row_factory = sqlite3.Row
        total = int(conn.execute(count_query, params).fetchone()[0])
        rows = [dict(row) for row in conn.execute(query, [*params, limit, offset]).fetchall()]
        facets: dict[str, list[dict[str, Any]]] = {}
        for key, expression in _FACET_FIELDS.items():
            facet_rows = conn.execute(
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
                {"value": str(value).strip() or "unknown", "count": int(count or 0)}
                for value, count in facet_rows
                if value not in (None, "")
            ]
    return {"total": total, "rows": rows, "facets": facets}


def record_fields() -> list[str]:
    """The field names of one stored alert, as a sample of what a record holds."""
    soc_db = _paths.soc_db()
    if not soc_db.is_file():
        return []
    try:
        with sqlite3.connect(soc_db) as conn:
            row = conn.execute("SELECT record_json FROM alert_records LIMIT 1").fetchone()
        if not row:
            return []
        value = json.loads(row[0]) if isinstance(row[0], str) and row[0].strip() else {}
        return sorted(str(key) for key in value.keys()) if isinstance(value, dict) else []
    except Exception:
        return []


def latest_date() -> str:
    """The latest date alerts are stored for; empty without any."""
    soc_db = _paths.soc_db()
    if not soc_db.is_file():
        return ""
    try:
        with sqlite3.connect(soc_db) as conn:
            row = conn.execute("SELECT MAX(asset_date) FROM alert_records").fetchone()
        return str(row[0] or "")
    except Exception:
        return ""


def workflow_runs(
    workflow_id: str = "",
    *,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    limit: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    """Workflow call statistics and the latest executions, of one workflow or all."""
    workflow_db = _paths.workflow_db()
    _require(workflow_db, "workflow database")
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
    if start_ms is not None:
        exec_where.append("started_at >= ?")
        exec_params.append(start_ms)
    if end_ms is not None:
        exec_where.append("started_at <= ?")
        exec_params.append(end_ms)
    exec_where_sql = " AND ".join(exec_where) if exec_where else "1=1"

    with sqlite3.connect(workflow_db) as conn:
        conn.row_factory = sqlite3.Row
        stats_rows = conn.execute(
            f"""
            SELECT {', '.join(_WORKFLOW_STAT_COLUMNS)}
            FROM workflow_stats
            {stats_where}
            ORDER BY updated_at DESC
            """,
            stats_params,
        ).fetchall()
        execution_rows = conn.execute(
            f"""
            SELECT {', '.join(_WORKFLOW_EXECUTION_COLUMNS)}
            FROM workflow_executions
            WHERE {exec_where_sql}
            ORDER BY started_at DESC
            LIMIT ?
            """,
            [*exec_params, limit],
        ).fetchall()
    return {
        "stats": [dict(row) for row in stats_rows],
        "executions": [dict(row) for row in execution_rows],
    }


def contract_source(options: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Where the alert contract's records are, for ``flocks.contracts.access.driver``.

    *options* are the binding's driver options.  Their ``settings`` is the
    content of ``access/soc_alerts.json`` (its ``sqlite`` part may name another
    file, table or columns) and ``settingsDir`` the directory a relative path
    in it starts from.
    """
    from .overview import settings as source_settings

    options = options if isinstance(options, dict) else {}
    resolved = source_settings(options.get("settings"), options.get("settingsDir"))
    return {
        "db_path": resolved["db_path"],
        "table": resolved["table"].strip('"'),
        "recordColumn": resolved["record_column"].strip('"'),
        "dateColumn": resolved["date_column"].strip('"'),
        "eventTimeColumn": resolved["event_time_column"].strip('"'),
    }
