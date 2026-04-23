from __future__ import annotations

import base64
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


def _resolve_runtime_config() -> tuple[str, int, str, str]:
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
        return base_url, timeout, api_key.strip(), secret.strip()

    secret_manager = _get_secret_manager()
    combined_candidates = [
        combined,
        secret_manager.get("onesec_credentials"),
        os.getenv("ONESEC_CREDENTIALS"),
    ]
    for candidate in combined_candidates:
        if candidate and "|" in candidate:
            api_key, secret = candidate.split("|", 1)
            return base_url, timeout, api_key.strip(), secret.strip()

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
    return base_url, timeout, api_key, secret


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


def _json_result(action: str, payload: Any) -> ToolResult:
    metadata = {"source": "OneSEC", "api": action}
    if isinstance(payload, dict):
        response_code = payload.get("response_code")
        if response_code not in (None, 0, 200):
            error_msg = payload.get("verbose_msg") or payload.get("msg") or "Unknown error"
            return ToolResult(success=False, error=f"OneSEC API error: {error_msg}", metadata=metadata)
        return ToolResult(success=True, output=payload.get("data", payload), metadata=metadata)
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


def _validate_time_params(action: str, params: dict[str, Any]) -> Optional[str]:
    """Check time_from/time_to consistency and recent-API 24-hour window."""
    tf = params.get("time_from")
    tt = params.get("time_to")

    if tf is not None and tt is not None:
        try:
            tf_int, tt_int = int(tf), int(tt)
        except (TypeError, ValueError):
            return None
        if tf_int >= tt_int:
            return (
                f"time_from ({tf_int}) 必须小于 time_to ({tt_int})。"
                " 请确认时间范围：time_from 为开始时间，time_to 为结束时间。"
            )
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
    return None


def _validate_action_params(action: str, params: dict[str, Any]) -> Optional[str]:
    time_err = _validate_time_params(action, params)
    if time_err:
        return time_err

    missing: list[str] = []

    if action == "dns_search_blocked_queries":
        missing.extend(_require_fields(params, "time_from", "time_to", "domain", "keyword"))
    elif action == "dns_get_recent_blocked_queries":
        missing.extend(_require_fields(params, "time_from", "time_to"))
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

    if not missing:
        return None
    deduped: list[str] = []
    for item in missing:
        if item not in deduped:
            deduped.append(item)
    return f"Missing required parameters for {action}: {', '.join(deduped)}"


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
            "threat_level",
            "threat_type",
            "cur_page",
        ),
    ),
    "dns_get_recent_blocked_queries": ActionSpec(
        "POST",
        "/open/api/client/getRecentBlockedQueries",
        lambda p: _pick(p, "time_from", "time_to", "public_ip", "block_reason", "threat_level"),
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
        lambda p: {**_pick(p, "time_from", "time_to"), "page": _page(p)},
    ),
    "edr_get_recent_threat_timeline": ActionSpec(
        "POST",
        "/api/saasedr/api/client/v1/getRecentThreatTimeline",
        lambda p: _pick(p, "time_from", "time_to"),
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
        base_url, timeout, api_key, secret = _resolve_runtime_config()
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
    validation_error = _validate_action_params(action, params)
    if validation_error:
        return ToolResult(success=False, error=validation_error)
    result = await _call_onesec_api(spec.method, spec.path, spec.payload_builder(params))
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
