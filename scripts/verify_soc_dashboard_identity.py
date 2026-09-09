#!/usr/bin/env python3
"""Read-only pre-deployment verification for SOC dashboard P0/P2 data chains.

The script intentionally opens SQLite databases with ``mode=ro`` and does not
start the WebUI, create schemas, update metrics, or modify execution records.
Run it from a Flocks checkout containing the candidate SOC dashboard changes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import Any


PLACEHOLDERS = {"", "none", "null", "unknown", "待识别", "待识别资产"}


class Results:
    def __init__(self) -> None:
        self.failed = 0
        self.warned = 0

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        if not ok:
            self.failed += 1
        suffix = f" - {detail}" if detail else ""
        print(f"[{status}] {label}{suffix}")

    def warn(self, label: str, detail: str = "") -> None:
        self.warned += 1
        suffix = f" - {detail}" if detail else ""
        print(f"[WARN] {label}{suffix}")


class _ReadOnlySQLiteProxy:
    """Force handler helper connections into SQLite read-only URI mode."""

    Row = sqlite3.Row

    @staticmethod
    def connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if kwargs.get("uri") and str(database).startswith("file:"):
            return sqlite3.connect(database, *args, **kwargs)
        kwargs["uri"] = True
        return sqlite3.connect(f"file:{Path(database)}?mode=ro", *args, **kwargs)


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _usable(value: Any) -> bool:
    return value is not None and str(value).strip().lower() not in PLACEHOLDERS


def _identified(event: dict[str, Any]) -> bool:
    alert = event.get("alert") if isinstance(event, dict) else None
    return bool(
        isinstance(alert, dict)
        and _usable(alert.get("sourceType"))
        and _usable(alert.get("srcIp"))
        and _usable(alert.get("dstIp"))
    )


def _masked(value: Any) -> str:
    text = str(value or "")
    parts = text.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return f"{parts[0]}.{parts[1]}.*.*"
    if ":" in text:
        return f"{text[:6]}…"
    return "present" if text else "missing"


def _load_handlers(repo: Path) -> ModuleType:
    path = (
        repo
        / ".flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/api/handlers.py"
    )
    if not path.is_file():
        raise FileNotFoundError(f"dashboard handler not found: {path}")
    name = "_soc_dashboard_identity_verify_handlers"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import dashboard handler: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _git_revision(repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _latest_fact_rows(soc_db: Path, limit: int) -> tuple[int, list[sqlite3.Row]]:
    with closing(_read_only(soc_db)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "alert_records" not in tables:
            raise RuntimeError("soc.db is missing alert_records")
        total = int(connection.execute("SELECT COUNT(*) FROM alert_records").fetchone()[0])
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(alert_records)").fetchall()
        }
        if "record_json" not in columns:
            raise RuntimeError("alert_records is missing record_json")
        time_expr = "event_time" if "event_time" in columns else "rowid"
        rows = connection.execute(
            f"SELECT rowid AS activity_row_id, {time_expr} AS activity_event_time, "
            "record_json, 1 AS sample_count FROM alert_records "
            f"ORDER BY {time_expr} DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return total, rows


def _latest_workflow_shapes(
    handlers: ModuleType, workflow_db: Path, limit: int
) -> dict[str, int]:
    result = {"checked": 0, "empty": 0, "unidentified": 0, "identified": 0}
    with closing(_read_only(workflow_db)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "workflow_executions" not in tables:
            raise RuntimeError("workflow.db is missing workflow_executions")
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workflow_executions)")
        }
        input_expr = "input_params" if "input_params" in columns else "'{}'"
        output_expr = "output_results" if "output_results" in columns else "'{}'"
        updated_expr = "updated_at" if "updated_at" in columns else "started_at"
        rows = connection.execute(
            f"SELECT {input_expr} AS input_params, {output_expr} AS output_results "
            "FROM workflow_executions WHERE workflow_id='stream_alert_denoise' "
            f"ORDER BY COALESCE(NULLIF({updated_expr}, 0), started_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    for row in rows:
        result["checked"] += 1
        inputs = handlers._safe_json_object(row["input_params"])
        metrics = handlers._workflow_execution_metrics(
            row["output_results"], row["input_params"]
        )
        preview = metrics["preview"]
        count = handlers._workflow_task_input_count(inputs)
        source = metrics["sourceType"]
        src = handlers._workflow_preview_value(
            preview, "sip", "src_ip", "source_ip", "net_real_src_ip"
        )
        dst = handlers._workflow_preview_value(
            preview, "dip", "dst_ip", "destination_ip", "net_dest_ip"
        )
        if count == 0:
            result["empty"] += 1
        if _usable(source) and _usable(src) and _usable(dst) and count != 0:
            result["identified"] += 1
        else:
            result["unidentified"] += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only validation of the SOC dashboard P0/P2 data chains"
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Flocks repository root (default: inferred from this script)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".flocks/data",
        help="Flocks data directory (default: ~/.flocks/data)",
    )
    parser.add_argument("--sample", type=int, default=30, help="recent rows to inspect")
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    data_dir = args.data_dir.expanduser().resolve()
    sample_limit = max(1, min(args.sample, 200))
    soc_db = data_dir / "soc.db"
    workflow_db = data_dir / "workflow.db"
    results = Results()

    print("SOC dashboard P0/P2 read-only verification")
    print(f"revision={_git_revision(repo)}")
    print(f"data_dir={data_dir}")

    try:
        sys.path.insert(0, str(repo))
        from flocks.ingest.syslog import manager as syslog_manager

        handlers = _load_handlers(repo)
        handlers.sqlite3 = _ReadOnlySQLiteProxy
    except Exception as exc:
        results.check(False, "candidate code can be imported", str(exc))
        return 1

    results.check(
        hasattr(syslog_manager, "_first_recursive")
        and hasattr(syslog_manager, "extract_embedded_json_payload"),
        "candidate recursive vendor parser is present",
    )
    results.check(
        hasattr(handlers, "_workflow_preview_value"),
        "candidate dashboard placeholder filter is present",
    )
    page_source = (
        repo
        / ".flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/src/Page.tsx"
    ).read_text(encoding="utf-8")
    results.check(
        "quality-banner" not in page_source,
        "yellow metric/SOC quality banners are absent from the page",
    )
    results.check(
        "const sourceMetricsUnavailable = metricQuality.sourceMetricsAvailable === false;"
        in page_source,
        "source cards are independent from denoise metric availability",
    )
    results.check(soc_db.is_file(), "soc.db exists", str(soc_db))
    results.check(workflow_db.is_file(), "workflow.db exists", str(workflow_db))
    if not soc_db.is_file() or not workflow_db.is_file():
        return 1

    fact_rows: list[sqlite3.Row] = []
    try:
        total_facts, fact_rows = _latest_fact_rows(soc_db, sample_limit)
        fact_events = [handlers._activity_event(row) for row in fact_rows]
        fact_events = [event for event in fact_events if isinstance(event, dict)]
        identified_facts = [event for event in fact_events if _identified(event)]
        results.check(total_facts > 0, "SOC alert records are available", f"rows={total_facts}")
        results.check(
            bool(identified_facts),
            "recent SOC records contain source and both endpoints",
            f"identified={len(identified_facts)}/{len(fact_events)}",
        )
    except Exception as exc:
        fact_events = []
        identified_facts = []
        results.check(False, "recent SOC records can be read", str(exc))

    if fact_rows:
        try:
            sample_record = json.loads(fact_rows[0]["record_json"])
            wrapped = {
                "format": "rfc3164",
                "data": [],
                "message": f"vendor-prefix {json.dumps(sample_record, ensure_ascii=False)} vendor-tail",
            }
            count, preview = syslog_manager._soc_alert_preview(wrapped)
            parser_ok = bool(
                count
                and _usable(preview.get("_source_type"))
                and _usable(preview.get("sip"))
                and _usable(preview.get("dip"))
            )
            results.check(
                parser_ok,
                "actual SOC record survives vendor-envelope preview parsing",
                "source={} src={} dst={}".format(
                    preview.get("_source_type") or "missing",
                    _masked(preview.get("sip")),
                    _masked(preview.get("dip")),
                ),
            )
        except Exception as exc:
            results.check(False, "vendor-envelope preview parsing", str(exc))

    handlers.WORKFLOW_DB = workflow_db
    handlers.DEFAULT_SQLITE_DB = soc_db
    try:
        end_time = int(time.time())
        rollup = handlers._get_workflow_metric_rollups(
            "stream_alert_denoise", end_time - 7 * 86400, end_time
        )
        results.check(
            isinstance(rollup, dict),
            "recent seven-day denoise rollups can be read",
        )
        if isinstance(rollup, dict):
            results.check(
                rollup.get("metricsAvailable") is True,
                "failed executions do not hide valid denoise aggregates",
                "raw={} reduced={} errors={} unprocessed={}".format(
                    rollup.get("rawCount"),
                    rollup.get("reducedCount"),
                    rollup.get("errorCount"),
                    rollup.get("unprocessedInputCount"),
                ),
            )
    except Exception as exc:
        results.check(False, "denoise rollup visibility can be evaluated", str(exc))

    try:
        shapes = _latest_workflow_shapes(handlers, workflow_db, sample_limit)
        print(
            "[INFO] recent workflow rows: "
            f"checked={shapes['checked']} identified={shapes['identified']} "
            f"empty={shapes['empty']} unidentified={shapes['unidentified']}"
        )
        if shapes["empty"] or shapes["unidentified"]:
            results.warn(
                "historical workflow rows contain empty/unidentified inputs",
                "expected before redeployment; candidate display code must filter them",
            )
        workflow_events = handlers._get_workflow_recent_events(
            "stream_alert_denoise", limit=10
        )
        results.check(
            all(_identified(event) for event in workflow_events),
            "dashboard workflow events expose no unknown/pending identity",
            f"returned={len(workflow_events)}",
        )
    except Exception as exc:
        workflow_events = []
        results.check(False, "workflow event identity can be evaluated", str(exc))

    try:
        tasks_payload = handlers._get_ai_tasks()
        tasks = tasks_payload.get("tasks", [])
        empty_visible = [task for task in tasks if task.get("emptyInput")]
        results.check(
            not empty_visible,
            "empty-input workflow executions are absent from AI task rows",
            "visible={} filtered={}".format(
                len(tasks), tasks_payload.get("summary", {}).get("emptyInput", 0)
            ),
        )
    except Exception as exc:
        results.check(False, "AI task filtering can be evaluated", str(exc))

    identified_workflows = [event for event in workflow_events if _identified(event)]
    selected = (identified_workflows or identified_facts or [None])[0]
    results.check(
        selected is not None and _identified(selected),
        "centre visualization has an identified workflow-or-SOC fallback event",
        "selected={}".format(
            "workflow" if identified_workflows else "soc_record" if identified_facts else "none"
        ),
    )

    print(
        f"SUMMARY pass={results.failed == 0} failures={results.failed} "
        f"warnings={results.warned} read_only=true"
    )
    return 1 if results.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
