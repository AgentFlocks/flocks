"""SOC alert investigation WebUI access contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from flocks.contracts.access.models import (
    Binding,
    Contract,
    ContractOperation,
    DriverResult,
    InternalDataRow,
    RuntimeContext,
    WebUIContractPlugin,
)
from flocks.contracts.access.pipeline import OverlayStore


PAGE_ID = "soc-alerts"
CONTRACT_ID = "soc.alerts.operations"
CONTRACT_VERSION = "1.0"
SOURCE_PAGE_ID = "soc-alerts"
DEFAULT_SQLITE_DB = Path.home() / ".flocks" / "data" / "soc.db"
DEFAULT_SQLITE_TABLE = "alert_records"
DEFAULT_SQLITE_RECORD_COLUMN = "record_json"
DEFAULT_SQLITE_DATE_COLUMN = "asset_date"
DEFAULT_SQLITE_EVENT_TIME_COLUMN = "event_time"
SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DISPLAY_FIELDS = (
    "id",
    "time",
    "direction",
    "sip",
    "sport",
    "dip",
    "dport",
    "net_type",
    "net_app_proto",
    "req_host",
    "req_http_url",
    "req_user_agent",
    "req_line",
    "req_header",
    "req_body",
    "req_body_len",
    "rsp_status_code",
    "rsp_line",
    "rsp_header",
    "rsp_body",
    "rsp_body_len",
    "threat_rule_id",
    "threat_name",
    "threat_msg",
    "threat_level",
    "threat_severity",
    "threat_phase",
    "threat_type",
    "threat_result",
    "attack_verdict",
    "attack_success",
    "risk_level",
    "triage_report",
    "report_title",
    "asset_group_name",
    "asset_name",
    "_source_type",
    "_process_type",
    "_threat_type",
    "_lsh_cluster_id",
    "dedup_key",
    "is_duplicate",
    "_syslog_meta",
)
DRIVER_FIELDS = frozenset(DISPLAY_FIELDS)
FILTER_FIELDS = frozenset(
    {
        "_source_type",
        "net_type",
        "direction",
        "threat_name",
        "threat_type",
        "threat_phase",
        "threat_result",
        "rsp_status_code",
        "sip",
        "dport",
        "dip",
        "req_host",
        "threat_rule_id",
    }
)

TABLE_COLUMNS = (
    {"key": "time", "label": "Event Time"},
    {"key": "threat_name", "label": "Threat Name"},
    {"key": "threat_type", "label": "Threat Type"},
    {"key": "threat_phase", "label": "Attack Stage"},
    {"key": "threat_result", "label": "Attack Result"},
    {"key": "direction", "label": "Direction"},
    {"key": "sip", "label": "Source IP"},
    {"key": "sport", "label": "Source Port"},
    {"key": "dip", "label": "Destination IP"},
    {"key": "dport", "label": "Destination Port"},
    {"key": "req_http_url", "label": "Request URL"},
)


def _contract() -> Contract:
    return Contract(
        contract_id=CONTRACT_ID,
        version=CONTRACT_VERSION,
        page_id=PAGE_ID,
        operations={
            "list": ContractOperation(
                name="list",
                operation_type="query",
                adapter_required_fields=DRIVER_FIELDS,
                identity_fields=frozenset({"id"}),
                public_fields=frozenset({"source", "summary", "tableColumns", "incidents"}),
                filter_fields=FILTER_FIELDS,
                filter_param_fields={field: field for field in FILTER_FIELDS},
                cursor_fields=frozenset({"time", "id"}),
                sort_fields=frozenset({"time", "id"}),
                default_limit=10000,
                max_limit=10000,
            ),
        },
    )


class _BindingResolver:
    def resolve(self, *, page_id: str, slot_id: str, contract_id: str, contract_version: str) -> Binding:
        settings = _sqlite_settings()
        db_path = settings["db_path"]
        return Binding(
            binding_id="soc-alerts-sqlite",
            binding_version=1,
            page_id=page_id,
            slot_id=slot_id,
            contract_id=contract_id,
            contract_version=contract_version,
            adapter_kind="builtin-sqlite-json",
            source_page_id=SOURCE_PAGE_ID,
            source_root=db_path,
            driver_available_fields=DRIVER_FIELDS,
            driver_allowlist_roots=(db_path.parent,),
            driver_options={
                "table": settings["table"],
                "recordColumn": settings["record_column"],
                "dateColumn": settings["date_column"],
                "eventTimeColumn": settings["event_time_column"],
            },
            capabilities=frozenset({"query"}),
        )


class _Adapter:
    def normalize(self, driver_result: DriverResult) -> list[InternalDataRow]:
        rows: list[InternalDataRow] = []
        for record in driver_result.rows:
            record_id = _record_id(record)
            rows.append(
                InternalDataRow(
                    raw=record,
                    identity={"entityType": "soc-alert", "entityId": f"soc-alert:{record_id}"},
                )
            )
        return rows


class _ResponsePipeline:
    def __init__(self, overlay_store: OverlayStore) -> None:
        self._overlay_store = overlay_store

    def run_query(
        self,
        *,
        context: RuntimeContext,
        binding_source_page_id: str,
        driver_result: DriverResult,
        rows: list[InternalDataRow],
        filter_stages_applied: list[dict[str, str]],
    ) -> dict[str, Any]:
        merged_rows = self._overlay_store.merge(rows, context)
        incidents = [_incident_from_row(row) for row in merged_rows]
        buckets = [_verdict_bucket(incident.get("_record", {})) for incident in incidents]
        dates = [date for incident in incidents if (date := _date_part(_text(incident.get("observedAt"))))]
        for incident in incidents:
            incident.pop("_record", None)

        return {
            "schemaVersion": "soc.alerts.v1",
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "source": {
                "label": "SOC Contract SQLite",
                "pageId": binding_source_page_id,
                "sampleMode": False,
                "dataSource": "sqlite",
            },
            "summary": {
                "sourcePageId": binding_source_page_id,
                "sourceAssetDate": max(dates) if dates else "-",
                "sourceAssetFile": _source_file_label(driver_result.source_files),
                "totalRaw": driver_result.total_raw,
                "totalUnique": driver_result.total_unique,
                "duplicates": driver_result.duplicates,
                "attackSuccess": buckets.count("success"),
                "attack": buckets.count("attack"),
                "attackFailed": buckets.count("failed"),
                "benign": buckets.count("benign"),
                "unknown": buckets.count("unknown"),
                "representativeCount": driver_result.filtered_unique,
                "filterStagesApplied": filter_stages_applied,
            },
            "tableColumns": list(TABLE_COLUMNS),
            "incidents": incidents,
        }


def _incident_from_row(row: InternalDataRow) -> dict[str, Any]:
    record = row.raw
    record_id = _record_id(record)
    observed_at = _observed_at(record)
    threat_name = _first_text(record, "threat_name", "_threat_type", "report_title") or "SOC alert"
    threat_msg = _first_text(record, "threat_msg", "report_title", "threat_type")
    verdict = _verdict_bucket(record)
    table_cells = _table_cells(record)
    triage_report = _text(record.get("triage_report"))
    report_title = _first_text(record, "report_title") or _report_title_from_markdown(triage_report)

    return {
        "id": record_id,
        "sourceRecordId": record_id,
        "observedAt": observed_at,
        "rawAlerts": 1,
        "priority": "P1" if verdict == "success" else "P2",
        "reportTitle": report_title,
        "reason": threat_msg,
        "owner": "",
        "srcIp": _text(record.get("sip")),
        "ndrRule": _text(record.get("threat_rule_id")),
        "request": {
            "method": _request_method(record),
            "host": _text(record.get("req_host")),
            "uri": _text(record.get("req_http_url")),
            "payload": _text(record.get("req_body")),
            "evidence": [_text(record.get("req_line"))] if _text(record.get("req_line")) else [],
        },
        "response": {
            "statusCode": _int_or_none(record.get("rsp_status_code")),
            "sample": _text(record.get("rsp_body")),
            "evidence": [_text(record.get("rsp_line"))] if _text(record.get("rsp_line")) else [],
        },
        "asset": {
            "name": _text(record.get("asset_name")),
            "business": _text(record.get("asset_group_name")),
        },
        "conclusion": {
            "verdict": _verdict_label(verdict),
            "summary": threat_msg or threat_name,
            "recommendation": "",
        },
        "actions": [],
        "title": threat_name,
        "triageReport": triage_report,
        "tableCells": table_cells,
        "overlayVersion": _int_or_none(record.get("_overlay_version")) or 0,
        "_record": record,
    }


def _table_cells(record: dict[str, Any]) -> dict[str, dict[str, str]]:
    cells: dict[str, dict[str, str]] = {}
    for key in DISPLAY_FIELDS:
        if key == "triage_report":
            continue
        value = _display_value(record.get(key), key)
        if value:
            cells[key] = {"value": value}
    if "time" not in cells and (observed := _observed_at(record)):
        cells["time"] = {"value": observed}
    return cells


def _display_value(value: Any, key: str) -> str:
    if key == "time":
        return _observed_at({"time": value})
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    if isinstance(value, str):
        return "" if value == "none" else value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _sqlite_settings() -> dict[str, Any]:
    config = _load_config().get("sqlite", {})
    if not isinstance(config, dict):
        config = {}
    return {
        "db_path": _resolve_config_path(
            _read_config_string(os.environ.get("FLOCKS_SOC_ALERTS_SQLITE_DB"), config.get("dbPath")),
            DEFAULT_SQLITE_DB,
        ),
        "table": _sql_identifier(config.get("table"), DEFAULT_SQLITE_TABLE),
        "record_column": _sql_identifier(config.get("recordColumn"), DEFAULT_SQLITE_RECORD_COLUMN),
        "date_column": _sql_identifier(config.get("dateColumn"), DEFAULT_SQLITE_DATE_COLUMN),
        "event_time_column": _sql_identifier(config.get("eventTimeColumn"), DEFAULT_SQLITE_EVENT_TIME_COLUMN),
    }


def _load_config() -> dict[str, Any]:
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _config_path() -> Path:
    override = os.environ.get("FLOCKS_SOC_ALERTS_CONFIG")
    if override:
        return Path(override).expanduser()
    current = Path(__file__).resolve()
    for parent in current.parents:
        if parent.name == "access":
            return parent / "soc_alerts.json"
    return Path.home() / ".flocks" / "plugins" / "contracts" / "access" / "soc_alerts.json"


def _read_config_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_config_path(value: str, fallback: Path) -> Path:
    text = _read_config_string(value, str(fallback))
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (_config_path().parent / path).resolve()


def _sql_identifier(value: Any, fallback: str) -> str:
    text = _read_config_string(value, fallback)
    return text if SQL_IDENTIFIER_RE.fullmatch(text) else fallback


def _record_id(record: dict[str, Any]) -> str:
    value = _first_text(record, "id", "record_id", "dedup_key")
    if value:
        return value
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return digest[:16]


def _observed_at(record: dict[str, Any]) -> str:
    value = record.get("time")
    parsed = _datetime_from_value(value)
    if parsed is None:
        meta = record.get("_syslog_meta")
        if isinstance(meta, dict):
            parsed = _datetime_from_value(meta.get("timestamp"))
    if parsed is None:
        return _text(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _datetime_from_value(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds)
        except (OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    if seconds > 10_000_000_000:
        seconds /= 1000
    try:
        return datetime.fromtimestamp(seconds)
    except (OSError, ValueError):
        return None


def _request_method(record: dict[str, Any]) -> str:
    line = _text(record.get("req_line"))
    if line:
        return line.split(" ", 1)[0]
    return ""


def _verdict_bucket(record: dict[str, Any]) -> str:
    if record.get("attack_success") is True:
        return "success"
    raw = " ".join(
        _text(record.get(key)).lower()
        for key in ("attack_verdict", "threat_result", "risk_level", "threat_level")
    )
    if any(marker in raw for marker in ("success", "attack_success", "succeeded")):
        return "success"
    if any(marker in raw for marker in ("failed", "blocked", "attack_failed")):
        return "failed"
    if any(marker in raw for marker in ("benign", "normal", "safe")):
        return "benign"
    if "attack" in raw:
        return "attack"
    return "unknown"


def _verdict_label(bucket: str) -> str:
    return {
        "success": "success",
        "failed": "failed",
        "benign": "benign",
        "attack": "attack",
    }.get(bucket, "unknown")


def _source_file_label(paths: tuple[Path, ...]) -> str:
    if not paths:
        return ""
    first = paths[0]
    return str(first) if len(paths) == 1 else f"{first} (+{len(paths) - 1})"


def _date_part(value: str) -> str:
    return value[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", value) else ""


def _report_title_from_markdown(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _first_text(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(record.get(key))
        if value:
            return value
    return ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        return "" if text.lower() == "none" else text
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


_OVERLAY_STORE = OverlayStore()

CONTRACTS = (
    WebUIContractPlugin(
        plugin_id="soc-alerts-operations",
        contracts=(_contract(),),
        binding_resolver=_BindingResolver(),
        adapter=_Adapter(),
        response_pipeline=_ResponsePipeline(_OVERLAY_STORE),
        overlay_store=_OVERLAY_STORE,
        version=CONTRACT_VERSION,
    ),
)
