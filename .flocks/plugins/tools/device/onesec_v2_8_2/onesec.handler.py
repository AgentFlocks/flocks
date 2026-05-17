from __future__ import annotations

import base64
import datetime as dt
import hmac
import os
import time
from hashlib import sha1
from typing import Any, Callable, Optional

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult


DEFAULT_BASE_URL = "https://console.onesec.net"
DEFAULT_TIMEOUT = 60
SERVICE_ID = "onesec_api"


def _get_secret_manager():
    from flocks.security import get_secret_manager

    return get_secret_manager()


def _resolve_ref(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    if value.startswith("{secret:") and value.endswith("}"):
        return _get_secret_manager().get(value[len("{secret:") : -1])
    if value.startswith("{env:") and value.endswith("}"):
        return os.getenv(value[len("{env:") : -1])
    return value


def _service_config() -> dict[str, Any]:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    return raw if isinstance(raw, dict) else {}


def _resolve_verify_ssl(raw: dict[str, Any]) -> bool:
    # "verify_ssl" is canonical; "ssl_verify" remains as a backward-compatible alias.
    value = raw.get("verify_ssl")
    if value is None:
        value = raw.get("ssl_verify")
    if value is None:
        custom_settings = raw.get("custom_settings", {})
        if isinstance(custom_settings, dict):
            value = custom_settings.get("verify_ssl", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_runtime_config() -> tuple[str, int, str, str, bool]:
    raw = _service_config()
    base_url = (
        _resolve_ref(raw.get("base_url"))
        or _resolve_ref(raw.get("baseUrl"))
        or DEFAULT_BASE_URL
    ).rstrip("/")
    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    api_key_ref = raw.get("apiKey") or raw.get("authentication", {}).get("key")
    secret_ref = raw.get("secret") or raw.get("authentication", {}).get("secret")

    combined = _resolve_ref(api_key_ref)
    if combined and "|" in combined:
        api_key, secret = combined.split("|", 1)
        return (
            base_url,
            timeout,
            api_key.strip(),
            secret.strip(),
            _resolve_verify_ssl(raw),
        )

    secret_manager = _get_secret_manager()
    combined_candidates = [
        combined,
        secret_manager.get("onesec_credentials"),
        os.getenv("ONESEC_CREDENTIALS"),
    ]
    for candidate in combined_candidates:
        if candidate and "|" in candidate:
            api_key, secret = candidate.split("|", 1)
            return (
                base_url,
                timeout,
                api_key.strip(),
                secret.strip(),
                _resolve_verify_ssl(raw),
            )

    api_key = (
        combined
        or secret_manager.get("onesec_api_key")
        or secret_manager.get(f"{SERVICE_ID}_api_key")
        or os.getenv("ONESEC_API_KEY")
    )
    secret = (
        _resolve_ref(secret_ref)
        or secret_manager.get("onesec_api_secret")
        or secret_manager.get(f"{SERVICE_ID}_secret")
        or os.getenv("ONESEC_SECRET")
    )
    if not api_key or not secret:
        raise ValueError(
            "OneSEC API credentials not found. Configure onesec_credentials as "
            "'api_key|secret', or set apiKey/secret separately."
        )
    return base_url, timeout, api_key, secret, _resolve_verify_ssl(raw)


def _build_auth_params(api_key: str, secret: str) -> dict[str, str]:
    auth_timestamp = str(int(time.time()))
    raw = f"{api_key}{auth_timestamp}".encode()
    sign = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), raw, sha1).digest()
    ).decode().rstrip("=")
    return {
        "api_key": api_key,
        "auth_timestamp": auth_timestamp,
        "sign": sign,
    }


def _pick(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        if key in params and params[key] is not None:
            result[key] = params[key]
    return result


def _page(
    params: dict[str, Any],
    *,
    cur_key: str = "cur_page",
    size_key: str = "page_size",
    size_out_key: str = "page_size",
) -> dict[str, int]:
    return {
        "cur_page": int(params.get(cur_key, 1)),
        size_out_key: int(params.get(size_key, 20)),
    }


def _page_legacy(params: dict[str, Any]) -> dict[str, int]:
    return {
        "pagenum": int(params.get("cur_page", 1)),
        "pagesize": int(params.get("page_size", 20)),
    }


def _dns_page(params: dict[str, Any]) -> dict[str, int]:
    size = params.get("page_items_num", params.get("pageitemsnum", 20))
    return {
        "cur_page": int(params.get("cur_page", 1)),
        "page_items_num": int(size),
    }


def _sort_items(
    params: dict[str, Any],
    *,
    default_by: str,
    default_order: str = "desc",
) -> list[dict[str, Any]]:
    return [
        {
            "sort_by": params.get("sort_by", default_by),
            "sort_order": params.get("sort_order", default_order),
        }
    ]


def _sort_object(
    params: dict[str, Any],
    *,
    default_by: str,
    default_order: str = "desc",
) -> dict[str, Any]:
    sort = params.get("sort")
    if isinstance(sort, dict):
        result = dict(sort)
        result.setdefault("sort_by", params.get("sort_by", default_by))
        result.setdefault("sort_order", params.get("sort_order", default_order))
        return result
    return {
        "sort_by": params.get("sort_by", default_by),
        "sort_order": params.get("sort_order", default_order),
    }


def _agent_list(params: dict[str, Any]) -> list[str]:
    return params.get("agent_list") or params.get("umid_list") or []


def _task_scope(params: dict[str, Any]) -> dict[str, Any]:
    return {"agent_list": _agent_list(params)}


def _dns_search_queries_payload(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "condition": _pick(params, "time_from", "time_to", "domain", "ip", "qType", "rcode"),
        "page": _dns_page(params),
    }


def _dns_search_threatened_endpoint_payload(params: dict[str, Any]) -> dict[str, Any]:
    condition = _pick(
        params,
        "time_from",
        "time_to",
        "fuzzythreatname",
        "fuzzyhostip",
        "fuzzy_domain",
        "evidence_success",
        "threattypelist",
    )
    asc = params.get("asc")
    if asc is None:
        asc = str(params.get("sort_order", "desc")).lower() == "asc"
    return {
        "condition": condition,
        "page": _dns_page(params),
        "sort": {
            "sort_by": params.get("sort_by", "query_time"),
            "asc": bool(asc),
        },
    }


def _software_sort_payload(params: dict[str, Any], *, default_by: str = "install_time") -> dict[str, Any]:
    if isinstance(params.get("sort"), dict):
        sort = dict(params["sort"])
        if sort.get("sort_order") is None:
            sort["sort_order"] = params.get("sort_order", "desc")
        if sort.get("sort_by") is None:
            sort["sort_by"] = params.get("sort_by", default_by)
        return sort
    return {
        "sort_by": params.get("sort_by", params.get("sort", default_by)),
        "sort_order": params.get("sort_order", "desc"),
    }


def _software_query_page_list_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(
        params,
        "fuzzy",
        "install_begin",
        "install_end",
        "software_group_name",
        "condition",
    )
    agent_group_list = params.get("agent_group_list")
    if agent_group_list is None and params.get("group_list") is not None:
        agent_group_list = params.get("group_list")
    if agent_group_list is not None:
        payload["agent_group_list"] = agent_group_list
    payload["page"] = _page(params)
    payload["sort"] = _software_sort_payload(params)
    return payload


def _software_query_agent_list_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(
        params,
        "name",
        "publisher",
        "version_min",
        "version_max",
        "include_empty_version",
        "fuzzy",
        "os_version",
        "agent_version",
        "software_version",
        "install_begin",
        "install_end",
    )
    if params.get("software_id") is not None:
        payload["software_id"] = params["software_id"]
    payload["page"] = _page(params)
    return payload


def _ops_query_agent_page_list_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(
        params,
        "begin_time",
        "end_time",
        "fuzzy",
        "group_list",
        "condition",
        "time_type",
    )
    payload["page"] = _page(params)
    payload["sort"] = _sort_object(params, default_by="create_time")
    return payload


def _ops_edit_agent_info_payload(params: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        params,
        "umid",
        "mac",
        "update_type",
        "name",
        "department",
        "job_number",
        "phone_number",
        "mail",
        "organization_user_id",
        "is_virtual",
        "pc_id",
        "group_path",
    )


def _ops_query_task_page_list_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(
        params,
        "time_type",
        "begin_time",
        "end_time",
        "auto",
        "task_type_list",
        "task_status_list",
        "group_name",
        "agent_host_name",
        "agent_host_ip",
        "agent_host_mac",
        "agent_umid",
    )
    if params.get("group_list") is not None:
        payload["group_list"] = params["group_list"]
    if params.get("group_id") is not None:
        payload["group_id"] = params["group_id"]
    payload["page"] = _page(params)
    payload["sort"] = _sort_object(params, default_by="create_time")
    return payload


def _edr_ioc_severity_filter(params: dict[str, Any]) -> list[int] | None:
    severity = params.get("ioc_severity_list", params.get("severity"))
    if severity is None:
        return None
    if isinstance(severity, list):
        return [int(item) for item in severity]
    return [int(severity)]


def _edr_get_ioc_list_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = {}
    if params.get("fuzzy") is not None:
        payload["fuzzy"] = params["fuzzy"]
    severity = _edr_ioc_severity_filter(params)
    if severity is not None:
        payload["severity"] = severity
    payload["page"] = _page(params)
    payload["sort"] = _sort_object(params, default_by="updateTime")
    return payload


def _edr_get_threat_disposals_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(params, "incident_id", "umid")
    payload["page"] = _page(params)
    payload["sort"] = _sort_items(params, default_by="update_time")
    return payload


def _edr_get_recent_threat_disposals_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(params, "incident_id", "umid")
    payload["sort"] = _sort_items(params, default_by="update_time")
    return payload


def _edr_files_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(
        params,
        "time_from",
        "time_to",
        "group_list",
        "umid_list",
        "threat_severity",
        "process_result",
        "threat_file_type",
        "search_field",
    )
    payload["page"] = _page(params)
    return payload


def _edr_recent_files_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(
        params,
        "time_from",
        "time_to",
        "group_list",
        "threat_severity",
        "process_result",
        "threat_file_type",
        "search_field",
    )
    payload["sort"] = _sort_items(params, default_by="last_detected_time")
    return payload


def _edr_activities_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(
        params,
        "time_from",
        "time_to",
        "group_list",
        "threat_severity",
        "threat_phase_list",
        "search_field",
        "os_list",
        "umid",
    )
    payload["page"] = _page(params)
    return payload


def _edr_recent_activities_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(
        params,
        "time_from",
        "time_to",
        "group_list",
        "threat_severity",
        "threat_phase_list",
        "search_field",
        "os_list",
        "umid",
    )
    payload["sort"] = _sort_items(params, default_by="last_detected_time")
    return payload


def _edr_incidents_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(params, "time_from", "time_to", "params")
    payload["page"] = _page(params)
    payload["sort"] = _sort_items(params, default_by="incident.lastUpdateTime")
    return payload


def _edr_recent_incidents_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(params, "time_from", "time_to", "params")
    payload["sort"] = _sort_items(params, default_by="incident.lastUpdateTime")
    return payload


def _edr_endpoint_alerts_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(params, "time_from", "time_to", "sql", "search_fields")
    payload["page"] = _page(params)
    if params.get("sort_by") is not None:
        payload["sort"] = _sort_items(params, default_by=params["sort_by"])
    return payload


def _edr_recent_endpoint_alerts_payload(params: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(params, "time_from", "time_to", "sql", "search_fields")
    if params.get("sort_by") is not None:
        payload["sort"] = _sort_items(params, default_by=params["sort_by"])
    return payload


def _action_task_content(
    params: dict[str, Any],
    *keys: str,
    required: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    content = _pick(params, *keys)
    if required:
        content.update(required)
    return [content]


def _normalize_dns_search_blocked_queries(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    items = payload.get("items")
    if not isinstance(items, list):
        return payload

    normalized_items: list[Any] = []
    changed = False
    for item in items:
        if not isinstance(item, dict):
            normalized_items.append(item)
            continue

        normalized_item = dict(item)
        if "result" not in normalized_item:
            normalized_item["result"] = "block"
            changed = True
        if "is_blocked" not in normalized_item:
            normalized_item["is_blocked"] = True
            changed = True
        normalized_items.append(normalized_item)

    if not changed:
        return payload

    return {**payload, "items": normalized_items}


def _normalize_output(action: str, payload: Any) -> Any:
    if action in {"searchBlockedQueries", "dns_search_blocked_queries"}:
        return _normalize_dns_search_blocked_queries(payload)
    return payload


def _json_result(action: str, payload: Any) -> ToolResult:
    metadata = {"source": "OneSEC", "api": action}
    if isinstance(payload, dict):
        response_code = payload.get("response_code")
        if response_code not in (None, 0, 200):
            error_msg = payload.get("verbose_msg") or payload.get("msg") or "Unknown error"
            return ToolResult(success=False, error=f"OneSEC API error: {error_msg}", metadata=metadata)
        return ToolResult(
            success=True,
            output=_normalize_output(action, payload.get("data", payload)),
            metadata=metadata,
        )
    return ToolResult(success=True, output=payload, metadata=metadata)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _require_fields(params: dict[str, Any], *fields: str) -> list[str]:
    return [field for field in fields if not _has_value(params.get(field))]


def _normalize_unix_seconds(value: Any, field_name: str) -> tuple[Optional[int], Optional[str]]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"{field_name} ({value}) 必须是 Unix 秒级时间戳。"
    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        if value.is_integer():
            return int(value), None
        return None, f"{field_name} ({value}) 必须是 Unix 秒级时间戳。"
    if not isinstance(value, str):
        return None, f"{field_name} ({value}) 必须是 Unix 秒级时间戳。"

    stripped = value.strip()
    if not stripped:
        return None, None
    if stripped.lstrip("-").isdigit():
        return int(stripped), None

    try:
        parsed = dt.datetime.fromisoformat(stripped)
    except ValueError:
        return (
            None,
            f"{field_name} ({value}) 必须是 Unix 秒级时间戳。"
            " 当前工具支持自动转换常见日期时间格式，如 `YYYY-MM-DD HH:MM:SS`。",
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return int(parsed.timestamp()), None


def _normalize_action_params(action: str, params: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
    normalized = dict(params)

    for field_name in ("time_from", "time_to", "begin_time", "end_time"):
        if not _has_value(normalized.get(field_name)):
            continue
        normalized_value, error = _normalize_unix_seconds(normalized.get(field_name), field_name)
        if error:
            return params, error
        normalized[field_name] = normalized_value

    if action in {"dns_search_blocked_queries", "dns_get_recent_blocked_queries"}:
        public_ip = normalized.get("public_ip")
        if isinstance(public_ip, str) and public_ip.strip():
            normalized["public_ip"] = [public_ip.strip()]
    if action == "dns_search_blocked_queries":
        if not _has_value(normalized.get("keyword")) and _has_value(normalized.get("domain")):
            normalized["keyword"] = normalized["domain"]
    elif action == "dns_search_queries":
        if isinstance(normalized.get("qType"), str):
            normalized["qType"] = normalized["qType"].strip().upper()
        if isinstance(normalized.get("rcode"), str):
            normalized["rcode"] = normalized["rcode"].strip().upper()
    elif action in {"dns_search_blocked_queries", "dns_get_recent_blocked_queries"}:
        if isinstance(normalized.get("block_reason"), str):
            normalized["block_reason"] = normalized["block_reason"].strip().lower()
    elif action == "dns_get_all_destination_list":
        if isinstance(normalized.get("policy_type"), str):
            normalized["policy_type"] = normalized["policy_type"].strip().lower()
    elif action == "threat_virus_scan":
        for field_name in ("task_type", "scan_type", "scanmode"):
            value = normalized.get(field_name)
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                normalized[field_name] = int(value.strip())
    elif action == "threat_upgrade_bd_version_task":
        value = normalized.get("bd_upgrade_type")
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            normalized["bd_upgrade_type"] = int(value.strip())
    elif action == "threat_update_bd_version":
        if isinstance(normalized.get("os_platform"), str):
            normalized["os_platform"] = normalized["os_platform"].strip().lower()
        if isinstance(normalized.get("os_arch"), str):
            arch_map = {
                "apple silicon": "Apple Silicon",
                "intel chip": "Intel Chip",
            }
            normalized["os_arch"] = arch_map.get(normalized["os_arch"].strip().lower(), normalized["os_arch"])
    elif action == "ops_query_task_page_list":
        if isinstance(normalized.get("time_type"), str):
            normalized["time_type"] = normalized["time_type"].strip()
        auto_value = normalized.get("auto")
        if isinstance(auto_value, str) and auto_value.strip().lstrip("-").isdigit():
            normalized["auto"] = int(auto_value.strip())

    return normalized, None


def _reject_present_fields(params: dict[str, Any], *fields: str) -> list[str]:
    return [field for field in fields if _has_value(params.get(field))]


def _validate_non_empty_aliases(params: dict[str, Any], aliases: tuple[str, ...], label: str) -> list[str]:
    if any(_has_value(params.get(alias)) for alias in aliases):
        return []
    return [label]


_RECENT_ACTIONS = frozenset({
    "dns_get_recent_blocked_queries",
    "edr_get_recent_threat_files",
    "edr_get_recent_threat_activities",
    "edr_get_recent_incidents",
    "edr_get_recent_endpoint_alerts",
    "edr_get_recent_threat_disposals",
    "edr_get_recent_threat_timeline",
})

_PAGINATED_ALTERNATIVES: dict[str, str] = {
    "edr_get_recent_endpoint_alerts": "edr_get_endpoint_alerts",
    "edr_get_recent_threat_files": "edr_get_threat_files",
    "edr_get_recent_threat_activities": "edr_get_threat_activities",
    "edr_get_recent_incidents": "edr_get_incidents",
    "edr_get_recent_threat_timeline": "edr_get_threat_timeline",
    "dns_get_recent_blocked_queries": "dns_search_blocked_queries",
}

_ONE_DAY_SECS = 86400
_THIRTY_DAY_SECS = 30 * _ONE_DAY_SECS
_THREE_MONTH_SECS = 90 * _ONE_DAY_SECS

_SPAN_LIMIT_RULES: dict[str, tuple[str, str, int, str]] = {
    "dns_search_blocked_queries": (
        "time_from",
        "time_to",
        _ONE_DAY_SECS,
        "按 OneSEC API 文档，`dns_search_blocked_queries` 的时间窗口最多 24 小时。请缩小 time_from/time_to 范围。",
    ),
    "dns_search_queries": (
        "time_from",
        "time_to",
        _ONE_DAY_SECS,
        "按 OneSEC API 文档，`dns_search_queries` 的时间窗口最多 24 小时。",
    ),
    "edr_get_threat_files": (
        "time_from",
        "time_to",
        _THREE_MONTH_SECS,
        "按 OneSEC API 文档，`edr_get_threat_files` 的时间窗口最长三个月。请缩小 time_from/time_to 范围。",
    ),
    "edr_get_threat_activities": (
        "time_from",
        "time_to",
        _THREE_MONTH_SECS,
        "按 OneSEC API 文档，`edr_get_threat_activities` 的时间窗口最长三个月。请缩小 time_from/time_to 范围。",
    ),
    "edr_get_incidents": (
        "time_from",
        "time_to",
        _THREE_MONTH_SECS,
        "按 OneSEC API 文档，`edr_get_incidents` 的时间窗口最长三个月。请缩小 time_from/time_to 范围。",
    ),
    "edr_get_endpoint_alerts": (
        "time_from",
        "time_to",
        _THREE_MONTH_SECS,
        "按 OneSEC API 文档，`edr_get_endpoint_alerts` 的时间窗口最长三个月。请缩小 time_from/time_to 范围。",
    ),
    "ops_query_audit_log": (
        "begin_time",
        "end_time",
        _THIRTY_DAY_SECS,
        "按 OneSEC API 文档，`ops_query_audit_log` 的查询窗口最多 30 天。",
    ),
}

_AGE_LIMIT_RULES: dict[str, tuple[str, int, str]] = {
    "dns_search_queries": (
        "time_from",
        _ONE_DAY_SECS,
        "按 OneSEC API 文档，`dns_search_queries` 仅支持最近 24 小时内的数据。请将 time_from 设置在最近 24 小时内。",
    ),
    "ops_query_audit_log": (
        "begin_time",
        _THIRTY_DAY_SECS,
        "按 OneSEC API 文档，`ops_query_audit_log` 仅支持最近 30 天内的审计日志。请调整 begin_time。",
    ),
}

_DNS_QTYPE_VALUES = {"A", "AAAA", "CNAME", "MX", "TXT", "PTR", "NS", "CERT", "SRV", "SOA", "DS"}
_DNS_RCODE_VALUES = {"NOERROR", "NXDOMAIN", "FORMERR", "SERVFAIL", "YXDOMAIN"}
_DNS_BLOCK_REASON_VALUES = {"threat", "custom"}
_DNS_POLICY_TYPE_VALUES = {"block", "pass"}
_THREAT_SCAN_TASK_TYPES = {10110, 10120, 10130}
_THREAT_SCANMODES = {1, 2, 3}
_THREAT_BD_UPGRADE_TYPES = {1, 2}
_THREAT_OS_PLATFORMS = {"windows", "macos"}
_THREAT_MAC_ARCHES = {"Apple Silicon", "Intel Chip"}
_OPS_TASK_TIME_TYPES = {"create_time", "update_time"}
_OPS_TASK_AUTO_VALUES = {0, 1}


def _validate_time_params(action: str, params: dict[str, Any]) -> Optional[str]:
    """Check time order and documented time-window limits."""
    for start_field, end_field in (("time_from", "time_to"), ("begin_time", "end_time")):
        start_value = params.get(start_field)
        end_value = params.get(end_field)
        if start_value is None or end_value is None:
            continue
        try:
            start_int, end_int = int(start_value), int(end_value)
        except (TypeError, ValueError):
            continue
        if start_int >= end_int:
            return (
                f"{start_field} ({start_int}) 必须小于 {end_field} ({end_int})。"
                f" 请确认时间范围：`{start_field}` 为开始时间，`{end_field}` 为结束时间。"
            )

    tf = params.get("time_from")
    tt = params.get("time_to")
    if tf is not None and tt is not None:
        try:
            tf_int, tt_int = int(tf), int(tt)
        except (TypeError, ValueError):
            return None
        if action in _RECENT_ACTIONS:
            span = tt_int - tf_int
            if span > _ONE_DAY_SECS:
                alt = _PAGINATED_ALTERNATIVES.get(action, "")
                alt_hint = f" 如需查询超过 24 小时的历史数据，请改用 `{alt}`。" if alt else ""
                return (
                    f"{action} 属于 recent 接口，仅支持最近 24 小时的数据，"
                    f"但传入的时间跨度为 {span // 3600} 小时。{alt_hint}"
                )

    if action in _RECENT_ACTIONS and tf is not None:
        try:
            tf_int = int(tf)
        except (TypeError, ValueError):
            return None
        now = int(time.time())
        age = now - tf_int
        if age > _ONE_DAY_SECS + 3600:
            alt = _PAGINATED_ALTERNATIVES.get(action, "")
            alt_hint = f" 请改用 `{alt}` 查询历史数据，或将 time_from 设置为最近 24 小时内的时间戳。" if alt else ""
            return (
                f"{action} 属于 recent 接口，仅支持最近 24 小时的数据。"
                f" 传入的 time_from ({tf_int}) 距当前时间已超过 {age // 3600} 小时。{alt_hint}"
            )

    span_rule = _SPAN_LIMIT_RULES.get(action)
    if span_rule is not None:
        start_field, end_field, max_span, message = span_rule
        start_value = params.get(start_field)
        end_value = params.get(end_field)
        if start_value is not None and end_value is not None:
            try:
                start_int, end_int = int(start_value), int(end_value)
            except (TypeError, ValueError):
                return None
            if end_int - start_int > max_span:
                return message

    age_rule = _AGE_LIMIT_RULES.get(action)
    if age_rule is not None:
        field_name, max_age, message = age_rule
        field_value = params.get(field_name)
        if field_value is not None:
            try:
                field_int = int(field_value)
            except (TypeError, ValueError):
                return None
            if int(time.time()) - field_int > max_age + 3600:
                return message
    return None


def _validate_enum_params(action: str, params: dict[str, Any]) -> Optional[str]:
    if action == "dns_search_queries":
        qtype = params.get("qType")
        if _has_value(qtype) and str(qtype) not in _DNS_QTYPE_VALUES:
            allowed = ", ".join(sorted(_DNS_QTYPE_VALUES))
            return f"`qType` 取值无效：{qtype}。按 OneSEC API 文档仅支持：{allowed}。"
        rcode = params.get("rcode")
        if _has_value(rcode) and str(rcode) not in _DNS_RCODE_VALUES:
            allowed = ", ".join(sorted(_DNS_RCODE_VALUES))
            return f"`rcode` 取值无效：{rcode}。按 OneSEC API 文档仅支持：{allowed}。"

    if action in {"dns_search_blocked_queries", "dns_get_recent_blocked_queries"}:
        block_reason = params.get("block_reason")
        if _has_value(block_reason) and str(block_reason) not in _DNS_BLOCK_REASON_VALUES:
            allowed = ", ".join(sorted(_DNS_BLOCK_REASON_VALUES))
            return f"`block_reason` 取值无效：{block_reason}。按 OneSEC API 文档仅支持：{allowed}。"

    if action == "dns_get_all_destination_list":
        policy_type = params.get("policy_type")
        if _has_value(policy_type) and str(policy_type) not in _DNS_POLICY_TYPE_VALUES:
            allowed = ", ".join(sorted(_DNS_POLICY_TYPE_VALUES))
            return f"`policy_type` 取值无效：{policy_type}。按 OneSEC API 文档仅支持：{allowed}。"

    if action == "threat_virus_scan":
        task_type = params.get("task_type", params.get("scan_type"))
        if _has_value(task_type):
            try:
                task_type_int = int(task_type)
            except (TypeError, ValueError):
                return f"`task_type`/`scan_type` 取值无效：{task_type}。按 OneSEC API 文档应为整数枚举值。"
            if task_type_int not in _THREAT_SCAN_TASK_TYPES:
                allowed = ", ".join(str(item) for item in sorted(_THREAT_SCAN_TASK_TYPES))
                return f"`task_type`/`scan_type` 取值无效：{task_type_int}。按 OneSEC API 文档仅支持：{allowed}。"
        scanmode = params.get("scanmode")
        if _has_value(scanmode):
            try:
                scanmode_int = int(scanmode)
            except (TypeError, ValueError):
                return f"`scanmode` 取值无效：{scanmode}。按 OneSEC API 文档应为整数枚举值。"
            if scanmode_int not in _THREAT_SCANMODES:
                allowed = ", ".join(str(item) for item in sorted(_THREAT_SCANMODES))
                return f"`scanmode` 取值无效：{scanmode_int}。按 OneSEC API 文档仅支持：{allowed}。"

    if action == "threat_upgrade_bd_version_task":
        bd_upgrade_type = params.get("bd_upgrade_type")
        if _has_value(bd_upgrade_type):
            try:
                upgrade_int = int(bd_upgrade_type)
            except (TypeError, ValueError):
                return f"`bd_upgrade_type` 取值无效：{bd_upgrade_type}。按 OneSEC API 文档应为整数枚举值。"
            if upgrade_int not in _THREAT_BD_UPGRADE_TYPES:
                allowed = ", ".join(str(item) for item in sorted(_THREAT_BD_UPGRADE_TYPES))
                return f"`bd_upgrade_type` 取值无效：{upgrade_int}。按 OneSEC API 文档仅支持：{allowed}。"

    if action == "threat_update_bd_version":
        os_platform = params.get("os_platform")
        if _has_value(os_platform) and str(os_platform) not in _THREAT_OS_PLATFORMS:
            allowed = ", ".join(sorted(_THREAT_OS_PLATFORMS))
            return f"`os_platform` 取值无效：{os_platform}。按 OneSEC API 文档仅支持：{allowed}。"
        if str(os_platform) == "macos":
            os_arch = params.get("os_arch")
            if _has_value(os_arch) and str(os_arch) not in _THREAT_MAC_ARCHES:
                allowed = ", ".join(sorted(_THREAT_MAC_ARCHES))
                return f"`os_arch` 取值无效：{os_arch}。当 `os_platform=macos` 时仅支持：{allowed}。"

    if action == "ops_query_task_page_list":
        time_type = params.get("time_type")
        if _has_value(time_type) and str(time_type) not in _OPS_TASK_TIME_TYPES:
            allowed = ", ".join(sorted(_OPS_TASK_TIME_TYPES))
            return f"`time_type` 取值无效：{time_type}。按 OneSEC API 文档仅支持：{allowed}。"
        auto = params.get("auto")
        if _has_value(auto):
            try:
                auto_int = int(auto)
            except (TypeError, ValueError):
                return f"`auto` 取值无效：{auto}。按 OneSEC API 文档应为整数枚举值。"
            if auto_int not in _OPS_TASK_AUTO_VALUES:
                allowed = ", ".join(str(item) for item in sorted(_OPS_TASK_AUTO_VALUES))
                return f"`auto` 取值无效：{auto_int}。按 OneSEC API 文档仅支持：{allowed}。"

    return None


def _validate_action_params(action: str, params: dict[str, Any]) -> Optional[str]:
    time_err = _validate_time_params(action, params)
    if time_err:
        return time_err
    enum_err = _validate_enum_params(action, params)
    if enum_err:
        return enum_err

    missing: list[str] = []
    unsupported: list[str] = []

    if action == "dns_search_blocked_queries":
        missing.extend(_require_fields(params, "time_from", "time_to", "domain", "keyword"))
        if {
            "domain",
            "keyword",
        }.issubset(set(missing)) and _has_value(params.get("public_ip")):
            return (
                "`dns_search_blocked_queries` 按 OneSEC API 文档要求必须传 `domain` 和 `keyword`。"
                " 如果你当前只有 `public_ip` + 时间范围，且要查询最近 24 小时拦截记录，"
                " 请改用 `dns_get_recent_blocked_queries`。"
            )
    elif action == "dns_get_recent_blocked_queries":
        missing.extend(_require_fields(params, "time_from", "time_to"))
        unsupported.extend(
            _reject_present_fields(
                params,
                "domain",
                "keyword",
                "private_ip",
                "threat_type",
                "cur_page",
                "pageitemsnum",
                "page_items_num",
            )
        )
    elif action == "dns_search_queries":
        missing.extend(_require_fields(params, "time_from", "time_to"))
    elif action == "dns_search_threatened_endpoint":
        missing.extend(_require_fields(params, "time_from", "time_to"))
    elif action in {
        "dns_add_domains_to_destination_list",
        "dns_delete_domains_from_destination_list",
        "dns_replace_destination_list",
    }:
        missing.extend(_validate_non_empty_aliases(params, ("domains", "domain_list"), "domains/domain_list"))
        missing.extend(_require_fields(params, "target_list"))
    elif action in {
        "edr_isolate_endpoints",
        "edr_unisolate_endpoints",
        "ops_uninstall_agent",
        "threat_stop_virus_scan",
        "threat_upgrade_bd_version_task",
    }:
        missing.extend(_validate_non_empty_aliases(params, ("agent_list", "umid_list"), "agent_list"))
        if action == "threat_upgrade_bd_version_task":
            missing.extend(_require_fields(params, "bd_upgrade_type"))
    elif action in {
        "edr_quarantine_files",
        "edr_quarantine_proc_files",
        "edr_restore_quarantined_files",
    }:
        missing.extend(_validate_non_empty_aliases(params, ("agent_list", "umid_list"), "agent_list"))
        missing.extend(_require_fields(params, "file_path"))
    elif action in {"edr_block_network_connections", "edr_unblock_network_connections"}:
        missing.extend(_validate_non_empty_aliases(params, ("agent_list", "umid_list"), "agent_list"))
        if not _has_value(params.get("ip_port")) and not _has_value(params.get("domain_name")):
            missing.append("ip_port/domain_name")
    elif action in {"edr_disable_service", "edr_restore_disabled_service"}:
        missing.extend(_validate_non_empty_aliases(params, ("agent_list", "umid_list"), "agent_list"))
        missing.extend(_require_fields(params, "service_name"))
    elif action == "edr_get_action_status":
        missing.extend(_require_fields(params, "task_id"))
    elif action == "edr_delete_registry_startup":
        missing.extend(_validate_non_empty_aliases(params, ("agent_list", "umid_list"), "agent_list"))
        missing.extend(_require_fields(params, "registry_path", "registry_type"))
    elif action == "edr_delete_ioc":
        missing.extend(_require_fields(params, "iocs"))
    elif action == "edr_add_ioc":
        missing.extend(_require_fields(params, "iocs", "severity", "threatName"))
    elif action in {"edr_get_threat_disposals", "edr_get_recent_threat_disposals"}:
        missing.extend(_require_fields(params, "incident_id", "umid"))
    elif action in {"edr_get_threat_timeline", "edr_get_recent_threat_timeline"}:
        missing.extend(_require_fields(params, "incident_id"))
    elif action == "ops_edit_agent_info":
        if not _has_value(params.get("umid")) and not _has_value(params.get("mac")):
            missing.append("umid/mac")
    elif action == "ops_query_audit_log":
        missing.extend(_require_fields(params, "begin_time", "end_time"))
    elif action == "ops_query_task_page_list":
        missing.extend(_require_fields(params, "time_type", "begin_time", "end_time", "auto"))
    elif action == "ops_query_task_execute_list":
        missing.extend(_require_fields(params, "task_id"))
    elif action == "ops_edit_strategy_scope":
        missing.extend(_require_fields(params, "strategy_id"))
    elif action == "threat_virus_scan":
        missing.extend(_validate_non_empty_aliases(params, ("agent_list", "umid_list"), "agent_list"))
        task_type = params.get("task_type", params.get("scan_type"))
        if not _has_value(task_type):
            missing.append("task_type/scan_type")
        missing.extend(_require_fields(params, "scanmode"))
        if task_type == 10130 and not _has_value(params.get("scan_paths")):
            missing.append("scan_paths")
    elif action == "threat_update_bd_version":
        missing.extend(_require_fields(params, "os_platform"))
        if str(params.get("os_platform", "")).lower() == "macos":
            missing.extend(_require_fields(params, "os_arch"))
    elif action == "software_query_agent_list":
        missing.extend(_require_fields(params, "name", "publisher"))

    deduped: list[str] = []
    for item in missing:
        if item not in deduped:
            deduped.append(item)
    if deduped:
        return f"Missing required parameters for {action}: {', '.join(deduped)}"

    deduped_unsupported: list[str] = []
    for item in unsupported:
        if item not in deduped_unsupported:
            deduped_unsupported.append(item)
    if deduped_unsupported:
        fields = ", ".join(deduped_unsupported)
        return (
            f"{action} 按 OneSEC API 文档不支持以下参数: {fields}。"
            " 若需要按域名或关键字筛选 DNS 拦截记录，请改用 `dns_search_blocked_queries`。"
        )
    return None


class ActionSpec:
    def __init__(
        self,
        method: str,
        path: str,
        payload_builder: Callable[[dict[str, Any]], Optional[dict[str, Any]]],
    ) -> None:
        self.method = method
        self.path = path
        self.payload_builder = payload_builder


ACTION_SPECS: dict[str, ActionSpec] = {
    "dns_search_blocked_queries": ActionSpec(
        "POST",
        "/open/api/client/searchBlockedQueries",
        lambda p: _pick(
            p,
            "time_from",
            "time_to",
            "public_ip",
            "private_ip",
            "domain",
            "keyword",
            "block_reason",
            "show_unblocked_threat",
            "threat_level",
            "threat_type",
            "cur_page",
        ),
    ),
    "dns_get_recent_blocked_queries": ActionSpec(
        "POST",
        "/open/api/client/getRecentBlockedQueries",
        lambda p: _pick(
            p,
            "time_from",
            "time_to",
            "public_ip",
            "block_reason",
            "show_unblocked_threat",
            "threat_level",
        ),
    ),
    "dns_search_queries": ActionSpec("POST", "/open/api/client/searchQueries", _dns_search_queries_payload),
    "dns_search_threatened_endpoint": ActionSpec(
        "POST",
        "/open/api/client/searchThreatenedEndpoint",
        _dns_search_threatened_endpoint_payload,
    ),
    "dns_get_public_ip_list": ActionSpec("GET", "/open/api/client/getPublicIPList", lambda p: None),
    "dns_add_domains_to_destination_list": ActionSpec(
        "POST",
        "/open/api/client/addDomainsToDestinationList",
        lambda p: {
            "domains": p.get("domains") or p.get("domain_list") or [],
            "target_list": p.get("target_list"),
        },
    ),
    "dns_delete_domains_from_destination_list": ActionSpec(
        "POST",
        "/open/api/client/deleteDomainsFromDestinationList",
        lambda p: {
            "domains": p.get("domains") or p.get("domain_list") or [],
            "target_list": p.get("target_list"),
        },
    ),
    "dns_replace_destination_list": ActionSpec(
        "POST",
        "/open/api/client/replaceDestinationList",
        lambda p: {
            "domains": p.get("domains") or p.get("domain_list") or [],
            "target_list": p.get("target_list"),
        },
    ),
    "dns_get_all_destination_list": ActionSpec(
        "POST",
        "/open/api/client/getAllDestinationList",
        lambda p: _pick(p, "policy_type"),
    ),
    "edr_get_threat_files": ActionSpec("POST", "/api/saasedr/api/client/v1/getThreatFiles", _edr_files_payload),
    "edr_get_recent_threat_files": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getRecentThreatFiles",
        _edr_recent_files_payload,
    ),
    "edr_get_threat_activities": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getThreatActivities",
        _edr_activities_payload,
    ),
    "edr_get_recent_threat_activities": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getRecentThreatActivities",
        _edr_recent_activities_payload,
    ),
    "edr_get_incidents": ActionSpec("POST", "/api/saasedr/api/client/v1/getIncidents", _edr_incidents_payload),
    "edr_get_recent_incidents": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getRecentIncidents",
        _edr_recent_incidents_payload,
    ),
    "edr_get_endpoint_alerts": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getEndpointAlerts",
        _edr_endpoint_alerts_payload,
    ),
    "edr_get_recent_endpoint_alerts": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getRecentEndpointAlerts",
        _edr_recent_endpoint_alerts_payload,
    ),
    "edr_get_threat_disposals": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getThreatDisposals",
        _edr_get_threat_disposals_payload,
    ),
    "edr_get_recent_threat_disposals": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getRecentThreatDisposals",
        _edr_get_recent_threat_disposals_payload,
    ),
    "edr_get_threat_timeline": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getThreatTimeline",
        lambda p: {**_pick(p, "incident_id", "time_from", "time_to"), "page": _page(p)},
    ),
    "edr_get_recent_threat_timeline": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getRecentThreatTimeline",
        lambda p: _pick(p, "incident_id", "time_from", "time_to"),
    ),
    "edr_get_ioc_list": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getIOCList",
        _edr_get_ioc_list_payload,
    ),
    "edr_isolate_endpoints": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/isolateEndpoints",
        lambda p: {"task_scope": _task_scope(p), "task_content_req": []},
    ),
    "edr_unisolate_endpoints": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/unisolateEndpoints",
        lambda p: {"task_scope": _task_scope(p), "task_content_req": []},
    ),
    "edr_quarantine_files": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/quarantineFiles",
        lambda p: {
            "task_scope": _task_scope(p),
            "task_content_req": _action_task_content(p, "file_path", "file_sha256", "file_md5"),
        },
    ),
    "edr_quarantine_proc_files": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/quarantineProcFiles",
        lambda p: {
            "task_scope": _task_scope(p),
            "task_content_req": _action_task_content(
                p, "file_path", "file_sha256", "file_md5", "pid"
            ),
        },
    ),
    "edr_restore_quarantined_files": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/resotoreQuarantinedFiles",
        lambda p: {
            "task_scope": _task_scope(p),
            "task_content_req": _action_task_content(p, "file_path", "file_sha256", "file_md5"),
        },
    ),
    "edr_block_network_connections": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/blockNetworkConnections",
        lambda p: {
            "task_scope": _task_scope(p),
            "task_content_req": _action_task_content(p, "ip_port", "domain_name"),
        },
    ),
    "edr_unblock_network_connections": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/unblockNetworkConnections",
        lambda p: {
            "task_scope": _task_scope(p),
            "task_content_req": _action_task_content(p, "ip_port", "domain_name"),
        },
    ),
    "edr_disable_service": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/disableService",
        lambda p: {
            "task_scope": _task_scope(p),
            "task_content_req": _action_task_content(
                p, "service_name", "is_quarant_file"
            ),
        },
    ),
    "edr_restore_disabled_service": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/restoreDisabledService",
        lambda p: {
            "task_scope": _task_scope(p),
            "task_content_req": _action_task_content(p, "service_name"),
        },
    ),
    "edr_get_action_status": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/getActionStatus",
        lambda p: {
            "task_id": p.get("task_id"),
            "time_sort": int(p.get("time_sort", 0)),
            "page": _page(p),
        },
    ),
    "edr_delete_registry_startup": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/deleteRegistryStartup",
        lambda p: {
            "task_scope": _task_scope(p),
            "task_content_req": _action_task_content(p, "registry_path", "registry_type"),
        },
    ),
    "edr_delete_ioc": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/deleteIOC",
        lambda p: _pick(p, "iocs"),
    ),
    "edr_add_ioc": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/addIOC",
        lambda p: _pick(p, "iocs", "severity", "threatName", "remark"),
    ),
    "threat_query_bd_version": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/queryBdVersion",
        lambda p: {},
    ),
    "threat_virus_scan": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/virusScan",
        lambda p: {
            "task_scope": _task_scope(p),
            "task_type": p.get("task_type", p.get("scan_type")),
            "task_content": _pick(p, "scan_paths", "scanmode"),
        },
    ),
    "threat_stop_virus_scan": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/stopVirusScan",
        lambda p: {"task_scope": _task_scope(p)},
    ),
    "threat_upgrade_bd_version_task": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/upgradeBdVersionTask",
        lambda p: {
            **({"issue_time": p["issue_time"]} if p.get("issue_time") is not None else {}),
            "task_type": 10095,
            "task_scope": _task_scope(p),
            "task_content": {"bd_upgrade_type": p.get("bd_upgrade_type")},
        },
    ),
    "threat_update_bd_version": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/updateBdVersion",
        lambda p: _pick(p, "os_platform", "os_arch"),
    ),
    "ops_query_agent_page_list": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/QueryAgentPageList",
        _ops_query_agent_page_list_payload,
    ),
    "ops_edit_agent_info": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/EditAgentInfo",
        _ops_edit_agent_info_payload,
    ),
    "ops_query_audit_log": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/QueryAuditLog",
        lambda p: {
            **_pick(
                p,
                "begin_time",
                "end_time",
                "operate_list",
                "role_list",
                "group_list",
                "fuzzy",
                "api_access_type_list",
            ),
            "page": _page(p),
        },
    ),
    "ops_query_task_page_list": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/QueryTaskPageList",
        _ops_query_task_page_list_payload,
    ),
    "ops_query_task_execute_list": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/QueryTaskExecuteList",
        lambda p: {"task_id": p.get("task_id"), "page": _page(p)},
    ),
    "ops_uninstall_agent": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/uninstallAgent",
        lambda p: {"agent_list": _agent_list(p)},
    ),
    "ops_edit_strategy_scope": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/EditStrategyScope",
        lambda p: {
            "strategy_id": p.get("strategy_id"),
            "strategy_scope": _pick(p, "agent_list", "group_list"),
        },
    ),
    "software_query_page_list": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/querySoftwarePageList",
        _software_query_page_list_payload,
    ),
    "software_query_agent_list": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/actions/querySoftwareAgentList",
        _software_query_agent_list_payload,
    ),
}


async def _call_onesec_api(method: str, path: str, payload: Optional[dict[str, Any]]) -> ToolResult:
    try:
        base_url, timeout, api_key, secret, verify_ssl = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    auth_params = _build_auth_params(api_key, secret)
    url = f"{base_url}{path}"
    request_headers = {"Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            if method.upper() == "GET":
                async with session.get(
                    url,
                    params={**auth_params, **(payload or {})},
                    headers=request_headers,
                    ssl=verify_ssl,
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        return ToolResult(success=False, error=f"HTTP {resp.status}: {text[:500]}")
                    data = await resp.json(content_type=None)
            else:
                async with session.post(
                    url,
                    params=auth_params,
                    json=payload or {},
                    headers=request_headers,
                    ssl=verify_ssl,
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        return ToolResult(success=False, error=f"HTTP {resp.status}: {text[:500]}")
                    data = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        return ToolResult(success=False, error=f"Request failed: {exc}")
    except Exception as exc:
        return ToolResult(success=False, error=f"Unexpected error: {exc}")

    return _json_result(path.rsplit("/", 1)[-1], data)


async def unified_ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    del ctx
    spec = ACTION_SPECS.get(action)
    if spec is None:
        available = ", ".join(sorted(ACTION_SPECS))
        return ToolResult(
            success=False,
            error=f"Unknown action: {action}. Available actions: {available}",
        )
    normalized_params, normalize_error = _normalize_action_params(action, params)
    if normalize_error:
        return ToolResult(success=False, error=normalize_error)
    validation_error = _validate_action_params(action, normalized_params)
    if validation_error:
        return ToolResult(success=False, error=validation_error)
    result = await _call_onesec_api(spec.method, spec.path, spec.payload_builder(normalized_params))
    if result.success:
        metadata = dict(result.metadata or {})
        metadata["api"] = action
        result.metadata = metadata
    return result


GROUP_ACTIONS = {
    "dns": {name for name in ACTION_SPECS if name.startswith("dns_")},
    "edr": {name for name in ACTION_SPECS if name.startswith("edr_")},
    "ops": {name for name in ACTION_SPECS if name.startswith("ops_")},
    "software": {name for name in ACTION_SPECS if name.startswith("software_")},
    "threat": {name for name in ACTION_SPECS if name.startswith("threat_")},
}

# Lightweight actions used when action="test" is passed (connectivity check only)
_CONNECTIVITY_TEST_ACTIONS: dict[str, str] = {
    "dns": "dns_get_public_ip_list",
    "edr": "edr_get_ioc_list",
    "ops": "ops_query_agent_page_list",
    "software": "software_query_page_list",
    "threat": "threat_query_bd_version",
}


async def _dispatch_group(ctx: ToolContext, group: str, action: str, **params: Any) -> ToolResult:
    if action == "test":
        test_action = _CONNECTIVITY_TEST_ACTIONS.get(group)
        if test_action:
            return await unified_ops(ctx, action=test_action, **params)
    if action not in GROUP_ACTIONS[group]:
        available = ", ".join(sorted(GROUP_ACTIONS[group]))
        return ToolResult(
            success=False,
            error=f"Unsupported {group} action: {action}. Available actions: {available}",
        )
    return await unified_ops(ctx, action=action, **params)


async def dns(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "dns", action, **params)


async def edr(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "edr", action, **params)


async def ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "ops", action, **params)


async def software(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "software", action, **params)


async def threat(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "threat", action, **params)


def _make_action_function(action: str):
    async def _tool(ctx: ToolContext, **kwargs: Any) -> ToolResult:
        return await unified_ops(ctx, action=action, **kwargs)

    _tool.__name__ = action
    return _tool


for _action_name in ACTION_SPECS:
    globals()[_action_name] = _make_action_function(_action_name)

del _action_name
