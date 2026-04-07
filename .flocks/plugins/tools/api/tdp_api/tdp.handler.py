from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, Callable
from urllib.parse import urljoin

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

SERVICE_ID = "tdp_api"
DEFAULT_TIMEOUT = 30
DEFAULT_BASE_URL = ""
DEFAULT_NET_DATA_TYPES = ["attack", "risk", "action"]
SYSTEM_STATUS_ENDPOINTS = {
    "core": "/api/v1/core-status",
    "ioc_update": "/api/v1/ioc-update-status",
    "hardware": "/api/v1/hardware-status",
    "input": "/api/v1/input-status",
    "database": "/api/v1/db-status",
    "timezone": "/api/v1/timezone-status",
    "service": "/api/v1/service-status",
    "cloud_connectivity": "/api/v1/cloud-connectivity-status",
}


class RuntimeConfig:
    def __init__(self, base_url: str, timeout: int, api_key: str, secret: str, verify_ssl: bool):
        self.base_url = base_url
        self.timeout = timeout
        self.api_key = api_key
        self.secret = secret
        self.verify_ssl = verify_ssl


def _get_secret_manager():
    from flocks.security import get_secret_manager

    return get_secret_manager()


def _resolve_ref(value: Any) -> str | None:
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
    value = raw.get("verify_ssl")
    if value is None:
        custom_settings = raw.get("custom_settings", {})
        if isinstance(custom_settings, dict):
            value = custom_settings.get("verify_ssl", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_runtime_config() -> RuntimeConfig:
    raw = _service_config()
    base_url = (
        _resolve_ref(raw.get("base_url"))
        or _resolve_ref(raw.get("baseUrl"))
        or _get_secret_manager().get("tdp_host")
        or os.getenv("TDP_HOST")
        or DEFAULT_BASE_URL
    )
    if base_url:
        base_url = base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            base_url = f"https://{base_url}"

    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    api_key_ref = raw.get("apiKey") or raw.get("authentication", {}).get("key")
    secret_ref = raw.get("secret") or raw.get("authentication", {}).get("secret")
    combined = _resolve_ref(api_key_ref)

    api_key = ""
    secret = ""
    resolved_secret = _resolve_ref(secret_ref)
    if combined and "|" in combined:
        api_key, secret = combined.split("|", 1)
        api_key = api_key.strip()
        secret = secret.strip()
    else:
        secret_manager = _get_secret_manager()
        combined_candidates = [
            combined,
            secret_manager.get("tdp_credentials"),
            secret_manager.get(f"{SERVICE_ID}_credentials"),
            os.getenv("TDP_CREDENTIALS"),
        ]
        for candidate in combined_candidates:
            if candidate and "|" in candidate:
                api_key, secret = candidate.split("|", 1)
                api_key = api_key.strip()
                secret = secret.strip()
                break
        if not api_key:
            api_key = (
                combined
                or secret_manager.get("tdp_api_key")
                or secret_manager.get(f"{SERVICE_ID}_api_key")
                or secret_manager.get("tdp_credentials")
                or secret_manager.get(f"{SERVICE_ID}_credentials")
                or os.getenv("TDP_API_KEY")
                or ""
            )
            if isinstance(api_key, str) and "|" in api_key:
                api_key, secret = [part.strip() for part in api_key.split("|", 1)]
        if not secret:
            secret = (
                resolved_secret
                or secret_manager.get("tdp_secret")
                or secret_manager.get(f"{SERVICE_ID}_secret")
                or secret_manager.get("tdp_api_secret")
                or os.getenv("TDP_SECRET")
                or ""
            )
            if isinstance(secret, str) and "|" in secret and not api_key:
                api_key, secret = [part.strip() for part in secret.split("|", 1)]

    if not base_url:
        raise ValueError(
            "TDP base URL not found. Configure tdp_host secret or api_services.tdp_api.base_url."
        )
    if not api_key or not secret:
        raise ValueError(
            "TDP credentials not found. Configure API Key and Secret, or set tdp_api_key / tdp_secret."
        )

    return RuntimeConfig(
        base_url=base_url,
        timeout=timeout,
        api_key=api_key,
        secret=secret,
        verify_ssl=_resolve_verify_ssl(raw),
    )


def _default_time_range(days: int = 7) -> tuple[int, int]:
    end_time = int(time.time())
    start_time = end_time - days * 24 * 60 * 60
    return start_time, end_time


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return override


def _normalize_body(body: Any) -> Any:
    if isinstance(body, dict):
        return dict(body)
    if isinstance(body, list):
        return list(body)
    return {}


def _dict_body(body: Any) -> dict[str, Any]:
    if isinstance(body, dict):
        return dict(body)
    return {}


def _extract_list_body(body: Any, *keys: str, wrap_scalar: bool = True) -> list[Any]:
    if isinstance(body, list):
        return list(body)
    if not isinstance(body, dict):
        return []

    for key in keys:
        if key not in body:
            continue
        value = body[key]
        if isinstance(value, list):
            return list(value)
        if wrap_scalar and value is not None:
            return [value]
    return []


def _clean_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = _clean_payload(item)
            if cleaned is None:
                continue
            if cleaned == {} or cleaned == []:
                result[key] = cleaned
            elif cleaned != "":
                result[key] = cleaned
        return result
    if isinstance(value, list):
        return [_clean_payload(item) for item in value if item is not None]
    return value


def _build_auth_params(api_key: str, secret: str) -> dict[str, str]:
    auth_timestamp = str(int(time.time()))
    sign_data = f"{api_key}{auth_timestamp}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), sign_data, hashlib.sha256).digest()
    sign = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return {
        "api_key": api_key,
        "auth_timestamp": auth_timestamp,
        "sign": sign,
    }


def _extract_filename(headers: Any, fallback: str) -> str:
    if not headers:
        return fallback
    disposition = None
    if hasattr(headers, "get"):
        disposition = headers.get("Content-Disposition") or headers.get("content-disposition")
    if not disposition:
        return fallback
    match = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?", disposition)
    if not match:
        return fallback
    return match.group(1) or match.group(2) or fallback


def _error_message(payload: Any, default: str) -> str:
    if isinstance(payload, dict):
        for key in ("verbose_msg", "message", "msg", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    return default


def _json_result(action: str, path: str, payload: Any) -> ToolResult:
    metadata = {"source": "TDP", "api": action, "path": path}
    if isinstance(payload, dict):
        response_code = payload.get("response_code")
        if response_code not in (None, 0, 200):
            return ToolResult(
                success=False,
                error=f"TDP API error: {_error_message(payload, 'Unknown error')}",
                output=payload,
                metadata=metadata,
            )
        return ToolResult(success=True, output=payload.get("data", payload), metadata=metadata)
    return ToolResult(success=True, output=payload, metadata=metadata)


def _binary_result(action: str, path: str, *, content: bytes, headers: Any, fallback_name: str) -> ToolResult:
    metadata = {"source": "TDP", "api": action, "path": path}
    content_type = "application/octet-stream"
    if hasattr(headers, "get"):
        content_type = headers.get("Content-Type") or headers.get("content-type") or content_type
    return ToolResult(
        success=True,
        output={
            "filename": _extract_filename(headers, fallback_name),
            "content_type": content_type,
            "encoding": "base64",
            "content_base64": base64.b64encode(content).decode("ascii"),
        },
        metadata=metadata,
    )


async def _decode_error_response(response: Any) -> Any:
    try:
        return await response.json(content_type=None)
    except Exception:
        pass
    try:
        text = await response.text()
        if text:
            return text
    except Exception:
        pass
    return None


async def _post_json(
    session: aiohttp.ClientSession,
    config: RuntimeConfig,
    path: str,
    body: Any = None,
) -> Any:
    url = urljoin(f"{config.base_url}/", path.lstrip("/"))
    params = _build_auth_params(config.api_key, config.secret)
    payload = _clean_payload(body if body is not None else {})
    async with session.post(
        url,
        params=params,
        json=payload,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        ssl=config.verify_ssl,
    ) as response:
        try:
            data = await response.json(content_type=None)
        except Exception:
            text = await response.text()
            data = {"response_code": response.status, "verbose_msg": text or f"HTTP {response.status}"}
        if response.status != 200 and isinstance(data, dict) and data.get("response_code") in (None, 0, 200):
            data["response_code"] = response.status
        return data


async def _get_binary(
    session: aiohttp.ClientSession,
    config: RuntimeConfig,
    path: str,
    *,
    body_query: dict[str, Any] | None = None,
) -> tuple[bool, Any, Any]:
    url = urljoin(f"{config.base_url}/", path.lstrip("/"))
    params = _build_auth_params(config.api_key, config.secret)
    if body_query:
        params["body"] = json.dumps(_clean_payload(body_query), ensure_ascii=False, separators=(",", ":"))
    async with session.get(
        url,
        params=params,
        headers={"Content-Type": "application/octet-stream"},
        ssl=config.verify_ssl,
    ) as response:
        if response.status != 200:
            return False, await _decode_error_response(response), response.headers
        content = await response.read()
        content_type = ""
        if hasattr(response.headers, "get"):
            content_type = response.headers.get("Content-Type") or response.headers.get("content-type") or ""
        if "json" in content_type:
            try:
                return False, json.loads(content.decode("utf-8")), response.headers
            except Exception:
                return False, await _decode_error_response(response), response.headers
        return True, content, response.headers


def _body_with_condition_time(defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    time_from, time_to = _default_time_range()
    body = {"condition": {"time_from": time_from, "time_to": time_to}}
    if defaults:
        body = _deep_merge(body, defaults)
    return body


def _body_with_root_time(defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    time_from, time_to = _default_time_range()
    body = {"time_from": time_from, "time_to": time_to}
    if defaults:
        body = _deep_merge(body, defaults)
    return body


def _dashboard_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge({}, body)


def _threat_host_summary_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "condition": {"threat_characters": ["is_compromised"]},
            "page": {"cur_page": 1, "page_size": 20, "sort_by": "severity", "sort_flag": "desc"},
        }
    )
    return _deep_merge(defaults, body)


def _threat_host_event_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "max_severity", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _condition_time_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(_body_with_condition_time(), body)


def _incident_search_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "condition": {"duration": {"begin_duration": 0, "end_duration": 24}},
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "last_time", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _paged_asset_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge({"condition": {}, "page": {"cur_page": 1, "page_size": 20}}, body)


def _web_app_framework_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "condition": {"af_class": "web_application"},
        "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "last_occ_time", "sort_order": "desc"}]},
    }
    return _deep_merge(defaults, body)


def _domain_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(_body_with_condition_time({"page": {"cur_page": 1, "page_size": 20}}), body)


def _api_risk_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "last_occ_time", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _api_list_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "condition": {"is_encrypted": True},
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "url_pattern", "sort_order": "asc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _weak_password_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time({"condition": {"is_plaintext": False}, "page": {"cur_page": 1, "page_size": 20}})
    return _deep_merge(defaults, body)


def _privacy_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(_body_with_condition_time(), body)


def _inbound_attack_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(
        _body_with_condition_time(
            {
                "condition": {
                    "fuzzy": {
                        "keyword": "",
                        "fieldlist": ["threat.name", "external_ip", "machine", "assets.name", "data"],
                    }
                }
            }
        ),
        body,
    )


def _login_entry_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "threat_tag_count", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _login_entry_summary_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(_body_with_root_time(), body)


def _login_entry_category_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(_body_with_root_time(), body)


def _log_search_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(_body_with_root_time({"net_data_type": DEFAULT_NET_DATA_TYPES, "size": 10}), body)


def _log_terms_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(
        _body_with_root_time({"term": "threat.name", "net_data_type": DEFAULT_NET_DATA_TYPES, "size": 10}),
        body,
    )


def _upload_api_summary_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(_body_with_root_time({"search_for_upload": True}), body)


def _upload_api_host_list_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(_body_with_root_time(), body)


def _upload_api_interface_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "condition": {"search_for_upload": True},
            "page": {
                "cur_page": 1,
                "page_size": 20,
                "sort": [
                    {"sort_by": "last_upload_time", "sort_order": "desc"},
                    {"sort_by": "url_pattern", "sort_order": "asc"},
                ],
            },
        }
    )
    return _deep_merge(defaults, body)


def _vulnerability_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "severity", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _cloud_access_source_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "connect_times", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _cloud_assets_info_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "connect_times", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _cloud_instance_list_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "connect_times", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _cloud_instance_access_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "connect_times", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _mdr_list_body(body: dict[str, Any]) -> dict[str, Any]:
    defaults = _body_with_condition_time(
        {
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "alert_judge_time", "sort_order": "desc"}]},
        }
    )
    return _deep_merge(defaults, body)


def _mdr_indicator_body(body: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(_body_with_condition_time(), body)


def _paged_body(
    body: Any,
    *,
    sort_by: str,
    sort_order: str = "desc",
    page_size: int = 20,
    condition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    defaults = {
        "condition": condition or {},
        "page": {
            "cur_page": 1,
            "page_size": page_size,
            "sort": [{"sort_by": sort_by, "sort_order": sort_order}],
        },
    }
    return _deep_merge(defaults, _dict_body(body))


def _asset_config_list_body(body: Any) -> dict[str, Any]:
    return _paged_body(body, sort_by="updated_time")


def _asset_config_delete_body(body: Any) -> list[Any]:
    return _extract_list_body(body, "ips", "items", "id", "values")


def _white_rule_search_body(body: Any) -> dict[str, Any]:
    return _paged_body(body, sort_by="create_time")


def _cascade_children_body(body: Any) -> dict[str, Any]:
    return _dict_body(body)


def _custom_intel_list_body(body: Any) -> dict[str, Any]:
    return _deep_merge(
        {
            "condition": {
                "ioc_type": [],
                "source_name_desc": [],
                "fuzzy_ioc": "",
                "threat_name": [],
                "severity": [],
                "status": [],
            },
            "page": {"cur_page": 1, "page_size": 20, "sort_by": "alert_count", "sort_flag": "desc"},
        },
        _dict_body(body),
    )


def _custom_intel_edit_body(body: Any) -> dict[str, Any]:
    return _deep_merge({"action": "edit", "data": {}}, _dict_body(body))


def _custom_intel_delete_body(body: Any) -> dict[str, Any]:
    return _deep_merge({"action": "delete", "data": {}}, _dict_body(body))


def _ip_reputation_list_body(body: Any) -> dict[str, Any]:
    return _paged_body(body, sort_by="updated_at")


def _ip_reputation_delete_body(body: Any) -> list[Any]:
    return _extract_list_body(body, "ids", "items", "id", wrap_scalar=True)


def _bypass_block_list_body(body: Any) -> dict[str, Any]:
    time_from, time_to = _default_time_range()
    return _deep_merge(
        {
            "condition": {
                "keyword": "",
                "time_from": time_from,
                "time_to": time_to,
            },
            "page": {"cur_page": 1, "page_size": 20, "sort": []},
        },
        _dict_body(body),
    )


def _block_filter_list_body(body: Any) -> dict[str, Any]:
    return _deep_merge(
        {
            "condition": {"keyword": ""},
            "page": {
                "cur_page": 1,
                "page_size": 20,
                "sort": [
                    {"sort_by": "create_time", "sort_order": "desc"},
                    {"sort_by": "id", "sort_order": "desc"},
                ],
            },
        },
        _dict_body(body),
    )


def _linkage_block_ioc_list_body(body: Any) -> dict[str, Any]:
    return _paged_body(body, sort_by="created_time")


def _linkage_deny_list_list_body(body: Any) -> dict[str, Any]:
    return _paged_body(body, sort_by="block_start_time")


def _linkage_pass_list_list_body(body: Any) -> dict[str, Any]:
    return _paged_body(body, sort_by="pass_start_time")


def _disposal_log_list_body(body: Any) -> dict[str, Any]:
    time_from, time_to = _default_time_range()
    return _deep_merge(
        {
            "condition": {"time_from": time_from, "time_to": time_to},
            "page": {"cur_page": 1, "page_size": 20, "sort": [{"sort_by": "cts", "sort_order": "desc"}]},
        },
        _dict_body(body),
    )


def _passthrough_body(body: Any) -> Any:
    if isinstance(body, list):
        return list(body)
    return _dict_body(body)


JsonActionMap = dict[str, tuple[str, Callable[[Any], Any], str]]

DASHBOARD_ACTIONS: JsonActionMap = {
    "status": ("/api/v1/dashboard/status", _dashboard_body, "dashboard_status"),
    "block": ("/api/v1/dashboard/block", _dashboard_body, "dashboard_block"),
    "security": ("/api/v1/dashboard/security", _dashboard_body, "dashboard_security"),
    "threat_event": ("/api/v1/dashboard/threaten_event", _dashboard_body, "dashboard_threat_event"),
    "threat_topic": ("/api/v1/dashboard/threat-topic", _dashboard_body, "dashboard_threat_topic"),
    "attack_assets_all": ("/api/v1/dashboard/attack_assets/all", _dashboard_body, "dashboard_attack_assets_all"),
    "attack_assets_public": ("/api/v1/dashboard/attack_assets/public", _dashboard_body, "dashboard_attack_assets_public"),
    "attack_assets_new": ("/api/v1/dashboard/attack_assets/new", _dashboard_body, "dashboard_attack_assets_new"),
    "file_check": ("/api/v1/dashboard/fileCheck", _dashboard_body, "dashboard_file_check"),
    "phase_sum": ("/api/v1/dashboard/phaseSum", _dashboard_body, "dashboard_phase_sum"),
    "login_api": ("/api/v1/dashboard/loginApi", _dashboard_body, "dashboard_login_api"),
    "vulnerability": ("/api/v1/dashboard/vulnerability", _dashboard_body, "dashboard_vulnerability"),
    "alert_sum": ("/api/v1/alert/getSumList", _dashboard_body, "dashboard_alert_sum"),
    "track": ("/api/v1/dashboard/track", _dashboard_body, "dashboard_track"),
    "app_frame": ("/api/v1/dashboard/appFrame", _dashboard_body, "dashboard_app_frame"),
    "unhandled_host_list": ("/api/v1/dashboard/unhandledHostList", _dashboard_body, "dashboard_unhandled_host_list"),
    "service_class": ("/api/v1/dashboard/serviceClass", _dashboard_body, "dashboard_service_class"),
    "privacy_info": ("/api/v1/dashboard/privacy-info", _dashboard_body, "dashboard_privacy_info"),
    "alert_level_trend": ("/api/v1/dashboard/alertLevelTrend", _dashboard_body, "dashboard_alert_level_trend"),
}

SERVICE_ASSET_ACTIONS: JsonActionMap = {
    "service_list": ("/api/v1/machine/list", _paged_asset_body, "machine_list"),
    "host_asset_list": ("/api/v1/machine/list", _paged_asset_body, "host_asset_list"),
    "web_app_framework_list": (
        "/api/v1/machine/appFrame/detailList",
        _web_app_framework_body,
        "machine_app_frame_detail_list",
    ),
}

THREAT_HOST_ACTIONS: JsonActionMap = {
    "summary": ("/api/v1/host/getFallHostSumList", _threat_host_summary_body, "host_get_fall_host_sum_list"),
    "events": ("/api/v1/host/threat/list", _threat_host_event_body, "host_threat_list"),
}

INCIDENT_ACTIONS: JsonActionMap = {
    "search": ("/api/v1/incident/search", _incident_search_body, "incident_search"),
    "top_attacked_entity": ("/api/v1/incident/topAttackedEntity", _condition_time_body, "incident_top_attacked_entity"),
    "result": ("/api/v1/incident/result", _dashboard_body, "incident_result"),
    "timeline": ("/api/v1/incident/timeline", _dashboard_body, "incident_timeline"),
    "alert_search": ("/api/v1/alert/search", _condition_time_body, "incident_alert_search"),
    "result_distribution": (
        "/api/v1/incident/result/distribution",
        _condition_time_body,
        "incident_result_distribution",
    ),
    "attacker_ip_list": ("/api/v1/incident/attackerIpList", _dashboard_body, "incident_attacker_ip_list"),
    "attacker_ip_detail": ("/api/v1/incident/attackerIpDetail", _dashboard_body, "incident_attacker_ip_detail"),
}

LOGIN_ENTRY_ACTIONS: JsonActionMap = {
    "summary": ("/api/v1/loginApi/countOfAppClass", _login_entry_summary_body, "login_api_count"),
    "category": ("/api/v1/loginApi/rightTopScreen", _login_entry_category_body, "login_api_category"),
    "list": ("/api/v1/loginApi/list", _login_entry_body, "login_api_list"),
}

LOG_ACTIONS: JsonActionMap = {
    "search": ("/api/v1/log/searchBySql", _log_search_body, "log_search_by_sql"),
    "terms": ("/api/v1/log/terms", _log_terms_body, "log_terms"),
}

UPLOAD_API_ACTIONS: JsonActionMap = {
    "summary": ("/api/v1/asset/uploadApi/head", _upload_api_summary_body, "upload_api_head"),
    "host_list": ("/api/v1/asset/uploadApi/host/list", _upload_api_host_list_body, "upload_api_host_list"),
    "interface_list": (
        "/api/v1/asset/uploadApi/interface/list",
        _upload_api_interface_body,
        "upload_api_interface_list",
    ),
}

CLOUD_SERVICE_ACTIONS: JsonActionMap = {
    "access_source": (
        "/api/v1/cloud-facilities/access-source",
        _cloud_access_source_body,
        "cloud_facilities_access_source",
    ),
    "assets_info": ("/api/v1/cloud-facilities/assets-info", _cloud_assets_info_body, "cloud_facilities_assets_info"),
    "instance_list": (
        "/api/v1/cloud-facilities/instance-info-list",
        _cloud_instance_list_body,
        "cloud_facilities_instance_info_list",
    ),
    "instance_access_list": (
        "/api/v1/cloud-facilities/instance-access-list",
        _cloud_instance_access_body,
        "cloud_facilities_instance_access_list",
    ),
}

MDR_ACTIONS: JsonActionMap = {
    "indicator": ("/api/v1/mdr/alertExpert/indicator", _mdr_indicator_body, "mdr_alert_indicator"),
    "list": ("/api/v1/mdr/alertExpert/list", _mdr_list_body, "mdr_alert_list"),
}

PLATFORM_CONFIG_ACTIONS: JsonActionMap = {
    "asset_list": ("/api/v1/assets/getList", _asset_config_list_body, "assets_get_list"),
    "asset_add": ("/api/v1/assets/import/interface", _passthrough_body, "assets_import_interface"),
    "asset_update": ("/api/v1/assets/update", _passthrough_body, "assets_update"),
    "asset_delete": ("/api/v1/assets/delete", _asset_config_delete_body, "assets_delete"),
    "white_rule_search": ("/api/v1/whiteRule/search", _white_rule_search_body, "white_rule_search"),
    "white_rule_add": ("/api/v1/whiteRule/add", _passthrough_body, "white_rule_add"),
    "white_rule_update": ("/api/v1/whiteRule/update", _passthrough_body, "white_rule_update"),
    "white_rule_delete": ("/api/v1/whiteRule/delete", _passthrough_body, "white_rule_delete"),
    "cascade_children": (
        "/api/v1/device/cascade_platform/children",
        _cascade_children_body,
        "device_cascade_platform_children",
    ),
}

POLICY_SETTINGS_ACTIONS: JsonActionMap = {
    "custom_intel_list": ("/api/v1/intel/getList", _custom_intel_list_body, "intel_get_list"),
    "custom_intel_add": ("/api/v1/intel/bulkAdd", _passthrough_body, "intel_bulk_add"),
    "custom_intel_edit": ("/api/v1/intel/action", _custom_intel_edit_body, "intel_action_edit"),
    "custom_intel_delete": ("/api/v1/intel/action", _custom_intel_delete_body, "intel_action_delete"),
    "ip_reputation_list": ("/api/v1/ipReputation/getList", _ip_reputation_list_body, "ip_reputation_get_list"),
    "ip_reputation_add": ("/api/v1/ipReputation/bulkAdd", _passthrough_body, "ip_reputation_bulk_add"),
    "ip_reputation_update": ("/api/v1/ipReputation/update", _passthrough_body, "ip_reputation_update"),
    "ip_reputation_delete": ("/api/v1/ipReputation/delete", _ip_reputation_delete_body, "ip_reputation_delete"),
    "bypass_block_list": ("/api/v1/block/list", _bypass_block_list_body, "block_list"),
    "bypass_block_add": ("/api/v1/block/import/interface", _passthrough_body, "block_import_interface"),
    "bypass_block_update": ("/api/v1/block/update", _passthrough_body, "block_update"),
    "bypass_block_delete": ("/api/v1/block/delete/interface", _passthrough_body, "block_delete_interface"),
    "block_filter_list": ("/api/v1/block/filter/getList", _block_filter_list_body, "block_filter_get_list"),
    "block_filter_add": ("/api/v1/block/filter/add", _passthrough_body, "block_filter_add"),
    "block_filter_update": ("/api/v1/block/filter/updateNew", _passthrough_body, "block_filter_update_new"),
    "block_filter_delete": ("/api/v1/block/filter/delete", _passthrough_body, "block_filter_delete"),
    "linkage_block_ioc_list": ("/api/v1/firewall/block/ioc/list", _linkage_block_ioc_list_body, "firewall_block_ioc_list"),
    "linkage_deny_list_add": (
        "/api/v1/firewall/block/deny-list/add",
        _passthrough_body,
        "firewall_block_deny_list_add",
    ),
    "linkage_deny_list_edit": (
        "/api/v1/firewall/block/deny-list/edit",
        _passthrough_body,
        "firewall_block_deny_list_edit",
    ),
    "linkage_deny_list_delete": (
        "/api/v1/firewall/block/deny-list/delete",
        _passthrough_body,
        "firewall_block_deny_list_delete",
    ),
    "linkage_pass_list_add": (
        "/api/v1/firewall/block/pass-list/add",
        _passthrough_body,
        "firewall_block_pass_list_add",
    ),
    "linkage_pass_list_edit": (
        "/api/v1/firewall/block/pass-list/edit",
        _passthrough_body,
        "firewall_block_pass_list_edit",
    ),
    "linkage_pass_list_delete": (
        "/api/v1/firewall/block/pass-list/delete",
        _passthrough_body,
        "firewall_block_pass_list_delete",
    ),
    "linkage_deny_list_list": (
        "/api/v1/firewall/block/deny-list/list",
        _linkage_deny_list_list_body,
        "firewall_block_deny_list_list",
    ),
    "linkage_pass_list_list": (
        "/api/v1/firewall/block/pass-list/list",
        _linkage_pass_list_list_body,
        "firewall_block_pass_list_list",
    ),
    "resolve_host": ("/api/v1/response/resolve-host", _passthrough_body, "response_resolve_host"),
    "disposal_log_list": ("/api/v1/disposal/log/list", _disposal_log_list_body, "disposal_log_list"),
}


def _normalize_action(action: str | None, default: str) -> str:
    if not isinstance(action, str) or not action.strip():
        return default
    return action.strip().lower()


def _invalid_action_result(action: str, action_map: JsonActionMap) -> ToolResult:
    available = ", ".join(sorted(action_map.keys()))
    return ToolResult(success=False, error=f"Unsupported action '{action}'. Available actions: {available}")


def _body_value(body: Any, path: str) -> Any:
    current = body
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def _missing_fields_result(action: str, missing_fields: list[str]) -> ToolResult:
    fields = ", ".join(missing_fields)
    return ToolResult(
        success=False,
        error=f"Action '{action}' requires the following body fields: {fields}.",
    )


def _validate_required_body_fields(action: str, body: Any, *paths: str) -> ToolResult | None:
    normalized = _normalize_body(body)
    missing = [path for path in paths if not _has_value(_body_value(normalized, path))]
    if missing:
        return _missing_fields_result(action, missing)
    return None


def _validate_non_empty_list_body(
    action: str,
    body: Any,
    *,
    fallback_keys: tuple[str, ...] = (),
    label: str,
) -> ToolResult | None:
    values: list[Any] = []
    if isinstance(body, list):
        values = list(body)
    elif isinstance(body, dict):
        values = _extract_list_body(body, *fallback_keys)
    if values:
        return None
    return ToolResult(
        success=False,
        error=(
            f"Action '{action}' requires a non-empty {label}. "
            f"You can pass it as a raw array or via one of these keys: {', '.join(fallback_keys)}."
        ),
    )


def _validate_resolve_host_body(action: str, body: Any) -> ToolResult | None:
    validation_error = _validate_required_body_fields(action, body, "assets_machine", "status")
    if validation_error:
        return validation_error
    if _body_value(_normalize_body(body), "status") == 3:
        return _validate_required_body_fields(action, body, "sub_status")
    return None


def _dict_copy(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _list_copy(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return list(value)
    return None


def _set_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _compose_payload(
    *,
    condition: Any = None,
    page: Any = None,
    root: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    condition_dict = _dict_copy(condition)
    page_dict = _dict_copy(page)
    if condition_dict:
        payload["condition"] = condition_dict
    if page_dict:
        payload["page"] = page_dict
    for key, value in (root or {}).items():
        _set_if_present(payload, key, value)
    return payload


async def _run_json_tool(action: str, path: str, body_builder, body: Any = None) -> ToolResult:
    try:
        config = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    timeout = aiohttp.ClientTimeout(total=config.timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        payload = await _post_json(session, config, path, body_builder(_normalize_body(body)))
    return _json_result(action, path, payload)


async def _run_action_json_tool(
    action_map: JsonActionMap,
    *,
    default_action: str,
    action: str | None,
    body: Any = None,
) -> ToolResult:
    selected_action = _normalize_action(action, default_action)
    spec = action_map.get(selected_action)
    if spec is None:
        return _invalid_action_result(selected_action, action_map)
    path, body_builder, api_name = spec
    return await _run_json_tool(api_name, path, body_builder, body)


async def dashboard_status(
    context: ToolContext,
    action: str = "status",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
    assets_group: list[Any] | None = None,
    is_new: bool | None = None,
) -> ToolResult:
    del context
    selected_action = _normalize_action(action, "status")
    body = _compose_payload(condition=condition, page=page)
    if selected_action in {"alert_sum", "unhandled_host_list"}:
        condition_dict = body.setdefault("condition", {})
        _set_if_present(condition_dict, "time_from", time_from)
        _set_if_present(condition_dict, "time_to", time_to)
        if assets_group is not None:
            condition_dict["assets_group"] = list(assets_group)
    else:
        _set_if_present(body, "time_from", time_from)
        _set_if_present(body, "time_to", time_to)
        if assets_group is not None:
            body["assets_group"] = list(assets_group)
        _set_if_present(body, "is_new", is_new)
    if selected_action in {
        "alert_sum",
        "unhandled_host_list",
    }:
        validation_error = _validate_required_body_fields(selected_action, body, "condition")
        if validation_error:
            return validation_error
    return await _run_action_json_tool(DASHBOARD_ACTIONS, default_action="status", action=selected_action, body=body)


async def threat_host_list(
    context: ToolContext,
    action: str = "summary",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
    asset_machine: str | None = None,
    device_id: str | None = None,
) -> ToolResult:
    del context
    selected_action = _normalize_action(action, "summary")
    body = _compose_payload(condition=condition, page=page)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    _set_if_present(condition_dict, "asset_machine", asset_machine)
    _set_if_present(condition_dict, "device_id", device_id)
    if selected_action == "events":
        validation_error = _validate_required_body_fields(selected_action, body, "condition.asset_machine")
        if validation_error:
            return validation_error
    return await _run_action_json_tool(THREAT_HOST_ACTIONS, default_action="summary", action=selected_action, body=body)


async def incident_list(
    context: ToolContext,
    action: str = "search",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    incident_id: str | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
    include_risk: bool | None = None,
    include_action: bool | None = None,
    alert_ids: list[Any] | None = None,
    attacker: list[Any] | None = None,
) -> ToolResult:
    del context
    selected_action = _normalize_action(action, "search")
    body = _compose_payload(condition=condition, page=page)
    if selected_action == "search":
        condition_dict = body.setdefault("condition", {})
        _set_if_present(condition_dict, "time_from", time_from)
        _set_if_present(condition_dict, "time_to", time_to)
    elif selected_action == "alert_search":
        condition_dict = body.setdefault("condition", {})
        _set_if_present(condition_dict, "time_from", time_from)
        _set_if_present(condition_dict, "time_to", time_to)
        if alert_ids is not None:
            condition_dict["id"] = list(alert_ids)
        _set_if_present(condition_dict, "include_risk", include_risk)
        _set_if_present(condition_dict, "include_action", include_action)
    elif selected_action == "attacker_ip_list":
        condition_dict = body.setdefault("condition", {})
        _set_if_present(condition_dict, "incident_id", incident_id)
        _set_if_present(condition_dict, "time_from", time_from)
        _set_if_present(condition_dict, "time_to", time_to)
    else:
        _set_if_present(body, "incident_id", incident_id)
        _set_if_present(body, "time_from", time_from)
        _set_if_present(body, "time_to", time_to)
        _set_if_present(body, "include_risk", include_risk)
        if attacker is not None:
            body["attacker"] = list(attacker)
    validation_map: dict[str, tuple[str, ...]] = {
        "top_attacked_entity": ("incident_id",),
        "result": ("incident_id",),
        "timeline": ("incident_id",),
        "alert_search": ("condition.id", "page"),
        "result_distribution": ("incident_id",),
        "attacker_ip_list": ("condition.incident_id",),
        "attacker_ip_detail": ("incident_id", "attacker"),
    }
    required_fields = validation_map.get(selected_action, ())
    if required_fields:
        validation_error = _validate_required_body_fields(selected_action, body, *required_fields)
        if validation_error:
            return validation_error
    return await _run_action_json_tool(INCIDENT_ACTIONS, default_action="search", action=selected_action, body=body)


async def service_asset_list(
    context: ToolContext,
    action: str = "service_list",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition, page=page)
    return await _run_action_json_tool(SERVICE_ASSET_ACTIONS, default_action="service_list", action=action, body=body)


async def domain_asset_list(
    context: ToolContext,
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition, page=page)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    return await _run_json_tool("domain_asset_search", "/api/v1/assets/domainName/search", _domain_body, body)


async def api_risk_list(
    context: ToolContext,
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition, page=page)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    return await _run_json_tool("interface_risk_list", "/api/v1/interface/risk/getApiList", _api_risk_body, body)


async def api_list(
    context: ToolContext,
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition, page=page)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    return await _run_json_tool("interface_list", "/api/v1/interface/list", _api_list_body, body)


async def weak_password_list(
    context: ToolContext,
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition, page=page)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    return await _run_json_tool("weak_password_list", "/api/v1/login/weakpwd/list", _weak_password_body, body)


async def privacy_overview(
    context: ToolContext,
    condition: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    return await _run_json_tool("privacy_diagram", "/api/v1/privacy/diagram", _privacy_body, body)


async def inbound_attack(
    context: ToolContext,
    condition: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    return await _run_json_tool(
        "inbound_attack_severity_distribution",
        "/api/v1/threat/inbound-attack/severity-distribution",
        _inbound_attack_body,
        body,
    )


async def login_entry_list(
    context: ToolContext,
    action: str = "list",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
    assets_group: list[Any] | None = None,
    app_class: str | None = None,
    category: str | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition, page=page)
    selected_action = _normalize_action(action, "list")
    if selected_action == "list":
        condition_dict = body.setdefault("condition", {})
        _set_if_present(condition_dict, "time_from", time_from)
        _set_if_present(condition_dict, "time_to", time_to)
        if assets_group is not None:
            condition_dict["assets_group"] = list(assets_group)
        _set_if_present(condition_dict, "app_class", app_class)
        _set_if_present(condition_dict, "category", category)
    else:
        _set_if_present(body, "time_from", time_from)
        _set_if_present(body, "time_to", time_to)
        if assets_group is not None:
            body["assets_group"] = list(assets_group)
        _set_if_present(body, "app_class", app_class)
        _set_if_present(body, "category", category)
    return await _run_action_json_tool(LOGIN_ENTRY_ACTIONS, default_action="list", action=selected_action, body=body)


async def log_search(
    context: ToolContext,
    action: str = "search",
    time_from: int | None = None,
    time_to: int | None = None,
    net_data_type: list[Any] | None = None,
    sql: str | None = "threat.level = 'attack'",
    columns: list[Any] | None = None,
    assets_group: list[Any] | None = None,
    cascade_asset_group: dict[str, Any] | None = None,
    term: str | None = None,
    size: int | None = None,
    log_ip: str | None = None,
) -> ToolResult:
    del context
    selected_action = _normalize_action(action, "search")
    body = {}
    _set_if_present(body, "time_from", time_from)
    _set_if_present(body, "time_to", time_to)
    if net_data_type is not None:
        body["net_data_type"] = list(net_data_type)
    _set_if_present(body, "sql", sql)
    if columns is not None:
        body["columns"] = list(columns)
    if assets_group is not None:
        body["assets_group"] = list(assets_group)
    if cascade_asset_group is not None:
        body["cascade_asset_group"] = dict(cascade_asset_group)
    _set_if_present(body, "term", term)
    _set_if_present(body, "size", size)
    _set_if_present(body, "log_ip", log_ip)
    if selected_action == "search":
        validation_error = _validate_required_body_fields(selected_action, body, "sql")
        if validation_error:
            return validation_error
    return await _run_action_json_tool(LOG_ACTIONS, default_action="search", action=selected_action, body=body)


async def upload_api(
    context: ToolContext,
    action: str = "summary",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
    search_for_upload: bool | None = None,
    host: str | None = None,
    fuzzy: Any = None,
) -> ToolResult:
    del context
    selected_action = _normalize_action(action, "summary")
    body = _compose_payload(condition=condition, page=page)
    if selected_action in {"summary", "host_list"}:
        _set_if_present(body, "time_from", time_from)
        _set_if_present(body, "time_to", time_to)
        _set_if_present(body, "search_for_upload", search_for_upload)
        if fuzzy is not None:
            body["fuzzy"] = fuzzy
    else:
        condition_dict = body.setdefault("condition", {})
        _set_if_present(condition_dict, "time_from", time_from)
        _set_if_present(condition_dict, "time_to", time_to)
        _set_if_present(condition_dict, "search_for_upload", search_for_upload)
        _set_if_present(condition_dict, "host", host)
        if fuzzy is not None:
            condition_dict["fuzzy"] = fuzzy
    return await _run_action_json_tool(UPLOAD_API_ACTIONS, default_action="summary", action=selected_action, body=body)


async def vulnerability_list(
    context: ToolContext,
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition, page=page)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    return await _run_json_tool(
        "vulnerability_list",
        "/api/v1/vulnerability/vulnerabilityList",
        _vulnerability_body,
        body,
    )


async def cloud_service(
    context: ToolContext,
    action: str = "access_source",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
    source_ip: str | None = None,
) -> ToolResult:
    del context
    selected_action = _normalize_action(action, "access_source")
    body = _compose_payload(condition=condition, page=page)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    _set_if_present(condition_dict, "source_ip", source_ip)
    if selected_action == "assets_info":
        validation_error = _validate_required_body_fields(selected_action, body, "condition.source_ip")
        if validation_error:
            return validation_error
    return await _run_action_json_tool(CLOUD_SERVICE_ACTIONS, default_action="access_source", action=selected_action, body=body)


async def download_pcap(context: ToolContext, alert_id: str, occ_time: int) -> ToolResult:
    del context
    try:
        config = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    timeout = aiohttp.ClientTimeout(total=config.timeout)
    path = "/api/v1/pcap/download"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ok, payload, headers = await _get_binary(
            session,
            config,
            path,
            body_query={"alert_id": alert_id, "occ_time": occ_time},
        )
    if not ok:
        return ToolResult(
            success=False,
            error=f"TDP API error: {_error_message(payload, 'PCAP download failed')}",
            output=payload,
            metadata={"source": "TDP", "api": "pcap_download", "path": path},
        )
    return _binary_result("pcap_download", path, content=payload, headers=headers, fallback_name=f"{alert_id}.pcap")


async def download_malware_file(context: ToolContext, hash: str) -> ToolResult:
    del context
    try:
        config = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    timeout = aiohttp.ClientTimeout(total=config.timeout)
    path = f"/api/v1/file/download/{hash}"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ok, payload, headers = await _get_binary(session, config, path)
    if not ok:
        return ToolResult(
            success=False,
            error=f"TDP API error: {_error_message(payload, 'Malware file download failed')}",
            output=payload,
            metadata={"source": "TDP", "api": "file_download", "path": path},
        )
    return _binary_result("file_download", path, content=payload, headers=headers, fallback_name=f"{hash}.bin")


async def mdr_alert_list(
    context: ToolContext,
    action: str = "list",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    time_from: int | None = None,
    time_to: int | None = None,
) -> ToolResult:
    del context
    body = _compose_payload(condition=condition, page=page)
    condition_dict = body.setdefault("condition", {})
    _set_if_present(condition_dict, "time_from", time_from)
    _set_if_present(condition_dict, "time_to", time_to)
    return await _run_action_json_tool(MDR_ACTIONS, default_action="list", action=action, body=body)


async def platform_config(
    context: ToolContext,
    action: str = "asset_list",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    asset: dict[str, Any] | None = None,
    lock: dict[str, Any] | None = None,
    rule: dict[str, Any] | None = None,
    ips: list[Any] | None = None,
    keyword: str | None = None,
) -> ToolResult:
    del context
    selected_action = _normalize_action(action, "asset_list")
    if selected_action == "asset_delete":
        body: Any = list(ips) if ips is not None else []
    elif selected_action == "asset_add":
        body = _dict_copy(asset)
    elif selected_action == "asset_update":
        body = {}
        if asset is not None:
            body["asset"] = dict(asset)
        if lock is not None:
            body["lock"] = dict(lock)
    elif selected_action.startswith("white_rule_") and selected_action != "white_rule_search":
        body = _dict_copy(rule)
    elif selected_action == "cascade_children":
        body = {}
        _set_if_present(body, "keyword", keyword)
    else:
        body = _compose_payload(condition=condition, page=page)
    if selected_action == "asset_delete":
        validation_error = _validate_non_empty_list_body(
            selected_action,
            body,
            fallback_keys=("ips", "items", "id", "values"),
            label="asset IP list",
        )
        if validation_error:
            return validation_error
    elif selected_action == "white_rule_delete":
        validation_error = _validate_required_body_fields(selected_action, body, "id")
        if validation_error:
            return validation_error
    return await _run_action_json_tool(
        PLATFORM_CONFIG_ACTIONS,
        default_action="asset_list",
        action=selected_action,
        body=body,
    )


async def policy_settings(
    context: ToolContext,
    action: str = "custom_intel_list",
    condition: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    entry: dict[str, Any] | None = None,
    rule: dict[str, Any] | None = None,
    ids: list[Any] | None = None,
    id: int | None = None,
    ioc_type: str | None = None,
    ioc_list: list[Any] | None = None,
    main_tag: str | None = None,
    severity: str | int | None = None,
    overwrite: bool | None = None,
    ip_list: list[Any] | None = None,
    tags: list[Any] | None = None,
    block_ip: list[Any] | None = None,
    block_expire_time: int | None = None,
    block_remark: str | None = None,
) -> ToolResult:
    del context
    selected_action = _normalize_action(action, "custom_intel_list")
    if selected_action.endswith("_list"):
        body: Any = _compose_payload(condition=condition, page=page)
    elif selected_action == "custom_intel_add":
        body = {}
        _set_if_present(body, "ioc_type", ioc_type)
        if ioc_list is not None:
            body["ioc_list"] = list(ioc_list)
        _set_if_present(body, "main_tag", main_tag)
        _set_if_present(body, "severity", severity)
        _set_if_present(body, "overwrite", overwrite)
    elif selected_action in {"custom_intel_edit", "custom_intel_delete"}:
        body = {"data": _dict_copy(data)}
    elif selected_action == "ip_reputation_add":
        body = {}
        if ip_list is not None:
            body["ip_list"] = list(ip_list)
        if tags is not None:
            body["tags"] = list(tags)
        _set_if_present(body, "overwrite", overwrite)
    elif selected_action == "ip_reputation_update":
        body = {}
        _set_if_present(body, "id", id)
        if tags is not None:
            body["tags"] = list(tags)
    elif selected_action == "ip_reputation_delete":
        body = list(ids) if ids is not None else []
    elif selected_action == "bypass_block_add":
        body = {}
        if block_ip is not None:
            body["block_ip"] = list(block_ip)
        _set_if_present(body, "block_expire_time", block_expire_time)
        _set_if_present(body, "block_remark", block_remark)
    elif selected_action.startswith("block_filter_") and selected_action != "block_filter_list":
        body = _dict_copy(rule)
    elif selected_action.startswith("linkage_") and not selected_action.endswith("_list"):
        body = _dict_copy(entry)
    elif selected_action == "resolve_host":
        body = _dict_copy(entry)
    else:
        body = _dict_copy(entry)
    if selected_action in {"custom_intel_edit", "custom_intel_delete"}:
        validation_error = _validate_required_body_fields(selected_action, body, "data.intel_uuid")
        if validation_error:
            return validation_error
    elif selected_action == "custom_intel_add":
        validation_error = _validate_required_body_fields(
            selected_action,
            body,
            "ioc_type",
            "ioc_list",
            "main_tag",
            "severity",
        )
        if validation_error:
            return validation_error
    elif selected_action == "ip_reputation_add":
        validation_error = _validate_non_empty_list_body(
            selected_action,
            body,
            fallback_keys=("ip_list", "items", "value", "values"),
            label="IP list",
        )
        if validation_error:
            return validation_error
    elif selected_action == "ip_reputation_delete":
        validation_error = _validate_non_empty_list_body(
            selected_action,
            body,
            fallback_keys=("ids", "items", "id"),
            label="IP reputation ID list",
        )
        if validation_error:
            return validation_error
    elif selected_action in {"bypass_block_add", "bypass_block_delete"}:
        validation_error = _validate_non_empty_list_body(
            selected_action,
            body,
            fallback_keys=("block_ip", "items", "value", "values"),
            label="block IP list",
        )
        if validation_error:
            return validation_error
    elif selected_action in {"block_filter_delete", "linkage_deny_list_delete", "linkage_pass_list_delete"}:
        validation_error = _validate_required_body_fields(selected_action, body, "id")
        if validation_error:
            return validation_error
    elif selected_action == "resolve_host":
        validation_error = _validate_resolve_host_body(selected_action, body)
        if validation_error:
            return validation_error
    return await _run_action_json_tool(
        POLICY_SETTINGS_ACTIONS,
        default_action="custom_intel_list",
        action=selected_action,
        body=body,
    )


async def system_status(context: ToolContext, action: str = "all") -> ToolResult:
    del context
    try:
        config = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    timeout = aiohttp.ClientTimeout(total=config.timeout)
    selected_action = _normalize_action(action, "all")
    if selected_action != "all":
        path = SYSTEM_STATUS_ENDPOINTS.get(selected_action)
        if path is None:
            available = ", ".join(["all", *sorted(SYSTEM_STATUS_ENDPOINTS.keys())])
            return ToolResult(success=False, error=f"Unsupported action '{selected_action}'. Available actions: {available}")
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = await _post_json(session, config, path, {})
        return _json_result(f"system_status_{selected_action}", path, payload)

    aggregated: dict[str, Any] = {}
    metadata = {"source": "TDP", "api": "system_status_all", "path": list(SYSTEM_STATUS_ENDPOINTS.values())}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for key, path in SYSTEM_STATUS_ENDPOINTS.items():
            payload = await _post_json(session, config, path, {})
            if isinstance(payload, dict) and payload.get("response_code") not in (None, 0, 200):
                return ToolResult(
                    success=False,
                    error=f"TDP API error: {_error_message(payload, f'{key} status failed')}",
                    output={"failed": key, "payload": payload, "partial": aggregated},
                    metadata=metadata,
                )
            aggregated[key] = payload.get("data", payload) if isinstance(payload, dict) else payload
    return ToolResult(success=True, output=aggregated, metadata=metadata)
