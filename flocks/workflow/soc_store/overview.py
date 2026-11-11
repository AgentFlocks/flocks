"""
What the SOC overview page reads, from SQLite.

The page's handlers (``soc_ui/soc_overview/api/handlers.py``) hold no SQL: they
ask here for the alert records of a date range and for a workflow's call
counts, and work the overview out of them.

The alert records come from ``soc.db``.  ``access/soc_alerts.json`` beside the
suite's pages may name another file, table or columns under ``sqlite``; the
page hands that file's content to :func:`settings` as it read it.

A database backend replaces ``flocks.workflow.soc_store`` as a whole (see
``flocks.db_backend``) and answers the same questions from its own tables.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import _paths

#: What the page shows as the kind of store behind its figures.
DATA_SOURCE = "sqlite"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_SQLITE_TABLE = "alert_records"
DEFAULT_SQLITE_RECORD_COLUMN = "record_json"
DEFAULT_SQLITE_DATE_COLUMN = "asset_date"
DEFAULT_SQLITE_EVENT_TIME_COLUMN = "event_time"

_workflow_stats_cache: dict = {}
_cache_updated_at: float = 0
_CACHE_TTL: float = 30.0


@dataclass(frozen=True)
class RecordSource:
    path: Path
    role: str
    date: str
    data_source: str
    record_count: int = 0
    start_time: int | None = None
    end_time: int | None = None


def workflow_call_count(workflow_name: str, date: str = None) -> dict:
    global _workflow_stats_cache, _cache_updated_at
    now = time.time()
    cache_key = f"{workflow_name}:{date or 'total'}"

    if now - _cache_updated_at < _CACHE_TTL and cache_key in _workflow_stats_cache:
        return _workflow_stats_cache[cache_key]

    empty = {"callCount": 0, "dupCount": 0, "uniqueCount": 0}
    workflow_db = _paths.workflow_db()
    if not workflow_db.is_file():
        return empty

    try:
        if date:
            start = int(datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)
            end = start + 86400 * 1000
            with sqlite3.connect(workflow_db) as conn:
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
            with sqlite3.connect(workflow_db) as conn:
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


def _read_config_string(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_config_path(value, fallback, config_dir):
    text = _read_config_string(value, str(fallback))
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (Path(config_dir) / path).resolve() if config_dir is not None else path.resolve()


def settings(config: Optional[dict[str, Any]] = None, config_dir: Optional[Path] = None) -> dict[str, Any]:
    """Which file, table and columns hold the alert records.

    *config* is the content of ``access/soc_alerts.json`` (its ``sqlite`` part
    is read here), *config_dir* the directory a relative path in it starts from.
    """
    raw = config.get("sqlite") if isinstance(config, dict) else None
    section = raw if isinstance(raw, dict) else {}
    configured = _read_config_string(os.environ.get("FLOCKS_SOC_ALERTS_SQLITE_DB"), section.get("dbPath"))
    return {
        "db_path": _resolve_config_path(configured, _paths.soc_db(), config_dir) if configured else _paths.soc_db(),
        "table": _sql_identifier(section.get("table"), DEFAULT_SQLITE_TABLE),
        "record_column": _sql_identifier(section.get("recordColumn"), DEFAULT_SQLITE_RECORD_COLUMN),
        "date_column": _sql_identifier(section.get("dateColumn"), DEFAULT_SQLITE_DATE_COLUMN),
        "event_time_column": _sql_identifier(
            section.get("eventTimeColumn"),
            DEFAULT_SQLITE_EVENT_TIME_COLUMN,
        ),
    }


def _sql_identifier(value, fallback):
    text = _read_config_string(value, fallback)
    if not SQL_IDENTIFIER_RE.match(text):
        text = fallback
    return f'"{text}"'


def source_status(settings: dict[str, Any]) -> dict[str, Any]:
    """Where the overview's figures come from and whether the alert store is there."""
    return {
        "dataSource": DATA_SOURCE,
        "path": settings["db_path"],
        "exists": settings["db_path"].is_file(),
        "workflowStatsDb": str(_paths.workflow_db()),
    }


def find_sources(settings, start_date, end_date, start_time=None, end_time=None):
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
            RecordSource(
                path=db_path,
                role="denoise",
                date=asset_date,
                data_source=DATA_SOURCE,
                record_count=int(record_count or 0),
                start_time=start_time,
                end_time=end_time,
            )
        )
    return sources


def available_dates(settings):
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


def iter_records(settings, source):
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


def _safe_int(value):
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except Exception:
        return 0
