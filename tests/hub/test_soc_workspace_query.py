"""SOC workspace query tool over the SQLite alert store: time units and triage verdicts."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# 2001-01-01 00:00:00 in Asia/Shanghai (the SOC workspace's time zone).
_DAY_START = 978278400


@pytest.fixture
def workspace_tool(tmp_path, monkeypatch):
    path = (
        Path(__file__).resolve().parents[2]
        / ".flocks/flockshub/plugins/tools/python/soc_workspace_query/soc_workspace_query.py"
    )
    spec = importlib.util.spec_from_file_location("soc_workspace_query_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.SOC_DB = tmp_path / "soc.db"
    module.WORKFLOW_DB = tmp_path / "workflow.db"
    return module


@pytest.fixture
def soc_time_rows(workspace_tool):
    """Rows from the 10-19 test pass: a millisecond raw time and a triaged alert."""
    rows = [
        # Persistence stored event_time in seconds; the raw record keeps milliseconds.
        ("row-ms", "record-ms", "2001-01-01", _DAY_START + 2, {"id": "record-ms", "time": (_DAY_START + 2) * 1000}),
        ("row-triaged", "record-triaged", "2001-01-01", _DAY_START + 60, {
            "id": "record-triaged", "time": _DAY_START + 60, "triage_status": "ok",
            "triage_attack_verdict": "attack", "triage_attack_success": "failed",
        }),
        ("row-benign", "record-benign", "2001-01-01", _DAY_START + 120, {
            "id": "record-benign", "time": _DAY_START + 120, "triage_status": "ok",
            "triage_attack_verdict": "non_attack", "triage_attack_success": "unknown",
        }),
        ("row-legacy", "record-legacy", "2001-01-01", _DAY_START + 180, {
            "id": "record-legacy", "time": _DAY_START + 180, "attack_verdict": "attack", "attach_result": "success",
        }),
        ("row-next-day", "record-next-day", "2001-01-02", _DAY_START + 86400 + 5, {
            "id": "record-next-day", "time": _DAY_START + 86400 + 5,
        }),
    ]
    with sqlite3.connect(workspace_tool.SOC_DB) as conn:
        conn.execute(
            "CREATE TABLE alert_records (row_id TEXT PRIMARY KEY, record_id TEXT, asset_date TEXT, "
            "source_file TEXT, line_number INTEGER, event_time INTEGER, source_type TEXT, "
            "threat_name TEXT, is_duplicate INTEGER, record_json TEXT)"
        )
        conn.executemany(
            "INSERT INTO alert_records (row_id, record_id, asset_date, event_time, source_type, "
            "threat_name, is_duplicate, record_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (row_id, record_id, day, event_time, "ndr", "example", 0, json.dumps(record))
                for row_id, record_id, day, event_time, record in rows
            ],
        )
    return rows


def test_millisecond_raw_time_does_not_fail_detail_or_list(workspace_tool, soc_time_rows):
    """BUG-013: a raw time in milliseconds was formatted as seconds (year 32970) and failed the page."""
    detail = workspace_tool._query_alert_detail({"row_id": "row-ms"}, False, False)
    assert detail["found"]
    assert detail["summary"]["eventTime"] == _DAY_START + 2
    assert detail["summary"]["eventTimeText"] == "2001-01-01 00:00:02"
    listing = workspace_tool._query_alerts({"date": "2001-01-01"}, 10, 0, "asc", False, False)
    assert [item["rowId"] for item in listing["items"]] == ["row-ms", "row-triaged", "row-benign", "row-legacy"]


def test_unreadable_epoch_formats_as_empty_text(workspace_tool):
    assert workspace_tool._format_epoch((_DAY_START + 2) * 1000) == "2001-01-01 00:00:02"
    assert workspace_tool._format_epoch(10 ** 30) == ""


def test_summary_reports_the_triage_verdict(workspace_tool, soc_time_rows):
    """BUG-014: triage_status "ok" was reported as the attack behavior and the result as unknown."""
    triaged = workspace_tool._query_alert_detail({"row_id": "row-triaged"}, False, False)
    assert triaged["summary"]["attackBehavior"] == "attack"
    assert triaged["summary"]["attackResult"] == "failed"
    benign = workspace_tool._query_alert_detail({"row_id": "row-benign"}, False, False)
    assert (benign["summary"]["attackBehavior"], benign["summary"]["attackResult"]) == ("non_attack", "unknown")
    legacy = workspace_tool._query_alert_detail({"row_id": "row-legacy"}, False, False)
    assert (legacy["summary"]["attackBehavior"], legacy["summary"]["attackResult"]) == ("attack", "success")

    failed = workspace_tool._query_alerts({"attack_result": "failed"}, 10, 0, "asc", False, False)
    assert [item["rowId"] for item in failed["items"]] == ["row-triaged"]
    attacks = workspace_tool._query_alerts({"attack_verdict": "attack"}, 10, 0, "asc", False, False)
    assert [item["rowId"] for item in attacks["items"]] == ["row-triaged", "row-legacy"]
    assert {"value": "failed", "count": 1} in attacks["facets"]["attack_result"]


@pytest.mark.parametrize(
    "bounds",
    [
        (_DAY_START, _DAY_START + 86399),
        (_DAY_START * 1000, (_DAY_START + 86399) * 1000),
        ("2001-01-01 00:00:00", "2001-01-01 23:59:59"),
    ],
    ids=["seconds", "milliseconds", "text"],
)
def test_time_filters_select_the_same_alerts_in_every_unit(workspace_tool, soc_time_rows, bounds):
    start, end = bounds
    result = workspace_tool._query_alerts({"startTime": start, "endTime": end}, 10, 0, "asc", False, False)
    assert result["summary"]["total"] == 4


def test_page_params_hand_the_handlers_epoch_seconds(workspace_tool):
    """BUG-015: the overview handler read milliseconds and date strings as no/other filters."""
    for start, end in [
        (_DAY_START, _DAY_START + 86399),
        (_DAY_START * 1000, (_DAY_START + 86399) * 1000),
        ("2001-01-01 00:00:00", "2001-01-01 23:59:59"),
        (str(_DAY_START * 1000), "2001-01-01T23:59:59"),
    ]:
        query = workspace_tool._page_query_params({"startTime": start, "end_time": end, "date": "2001-01-01"})
        assert query == {"date": "2001-01-01", "startTime": _DAY_START, "endTime": _DAY_START + 86399}


@pytest.mark.parametrize("bad", ["yesterday", "2001-13-01 00:00:00", "12:00"])
def test_unreadable_time_filters_are_rejected(workspace_tool, bad):
    with pytest.raises(ValueError, match="startTime"):
        workspace_tool._page_query_params({"startTime": bad})
    with pytest.raises(ValueError, match="startTime"):
        workspace_tool._alert_where({"startTime": bad})


def test_numeric_json_fields_filter_like_their_text(workspace_tool, soc_time_rows):
    """A JSON number (dport 443) matches the filter whether it is given as a number or as text."""
    with sqlite3.connect(workspace_tool.SOC_DB) as conn:
        conn.execute(
            "INSERT INTO alert_records (row_id, record_id, asset_date, event_time, source_type, "
            "threat_name, is_duplicate, record_json) VALUES ('row-port', 'record-port', '2001-01-01', ?, "
            "'ndr', 'example', 0, ?)",
            (_DAY_START + 240, json.dumps({"id": "record-port", "dport": 443, "rsp_status_code": 200})),
        )
    for filters in ({"dport": 443}, {"dport": "443"}, {"dport": [443, 80]}, {"rsp_status_code": 200}):
        result = workspace_tool._query_alerts(filters, 10, 0, "asc", False, False)
        assert [item["rowId"] for item in result["items"]] == ["row-port"], filters


async def test_the_dashboard_page_answers_through_the_tool(workspace_tool, monkeypatch):
    """The dashboard's handler is a coroutine function: the tool must wait for it."""
    from flocks.tool.registry import ToolContext

    monkeypatch.setattr(
        workspace_tool, "SOC_UI_ROOT", Path(__file__).resolve().parents[2] / ".flocks/flockshub/plugins/webuis/soc_ui",
    )
    result = await workspace_tool.soc_workspace_query(ToolContext(session_id="s", message_id="m"), page="dashboard")
    assert result.success, result.error
    payload = json.loads(result.output) if isinstance(result.output, str) else result.output
    assert payload["page"] == "dashboard" and "error" not in payload
