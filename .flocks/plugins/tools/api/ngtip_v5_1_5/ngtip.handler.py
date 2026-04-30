from __future__ import annotations

import os
from typing import Any, Callable, Optional

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

DEFAULT_PLATFORM_BASE_URL = "http://YOUR_NGTIP_IP"
DEFAULT_QUERY_BASE_URL = "http://YOUR_NGTIP_IP:8090"
DEFAULT_TIMEOUT = 60
SERVICE_ID = "ngtip_api"


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
    # "verify_ssl" is canonical; "ssl_verify" is accepted for backward compatibility.
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


def _ensure_scheme(url: str) -> str:
    """Auto-prepend https:// if the URL has no scheme."""
    if url and not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _resolve_runtime_config() -> tuple[str, str, int, str, bool]:
    """Returns (platform_base_url, query_base_url, timeout, apikey, verify_ssl)."""
    raw = _service_config()

    platform_base_url = _ensure_scheme(
        (
            _resolve_ref(raw.get("base_url"))
            or _resolve_ref(raw.get("baseUrl"))
            or os.getenv("NGTIP_BASE_URL")
            or DEFAULT_PLATFORM_BASE_URL
        ).rstrip("/")
    )

    query_base_url = _ensure_scheme(
        (
            _resolve_ref(raw.get("query_base_url"))
            or _resolve_ref(raw.get("queryBaseUrl"))
            or os.getenv("NGTIP_QUERY_BASE_URL")
            or DEFAULT_QUERY_BASE_URL
        ).rstrip("/")
    )

    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    apikey_ref = (
        raw.get("apiKey")
        or raw.get("apikey")
        or raw.get("authentication", {}).get("key")
    )
    apikey = (
        _resolve_ref(apikey_ref)
        or _get_secret_manager().get("ngtip_apikey")
        or _get_secret_manager().get(f"{SERVICE_ID}_apikey")
        or os.getenv("NGTIP_APIKEY")
    )
    if not apikey:
        raise ValueError(
            "NGTIP API key not found. Configure ngtip_api.apiKey in your service settings "
            "or set the NGTIP_APIKEY environment variable."
        )
    return platform_base_url, query_base_url, timeout, apikey, _resolve_verify_ssl(raw)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _pick(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: params[k] for k in keys if k in params and params[k] is not None}


def _json_result(action: str, data: Any) -> ToolResult:
    metadata = {"source": "NGTIP", "api": action}
    if isinstance(data, dict):
        response_code = data.get("response_code")
        if response_code not in (None, 0):
            error_msg = data.get("verbose_msg") or "Unknown error"
            return ToolResult(
                success=False,
                error=f"NGTIP API error (code={response_code}): {error_msg}",
                metadata=metadata,
            )
        output = data.get("data", data)
        return ToolResult(success=True, output=output, metadata=metadata)
    return ToolResult(success=True, output=data, metadata=metadata)


# ─── HTTP helpers ─────────────────────────────────────────────────────────────


async def _get_request(
    url: str,
    params: dict[str, Any],
    timeout: int,
    verify_ssl: bool,
    action: str,
) -> ToolResult:
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.get(url, params=params, ssl=verify_ssl) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    return ToolResult(success=False, error=f"HTTP {resp.status}: {text[:500]}")
                data = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        return ToolResult(success=False, error=f"Request failed: {exc}")
    except Exception as exc:
        return ToolResult(success=False, error=f"Unexpected error: {exc}")
    return _json_result(action, data)


async def _post_request(
    url: str,
    body: dict[str, Any],
    timeout: int,
    verify_ssl: bool,
    action: str,
) -> ToolResult:
    headers = {"Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.post(url, json=body, headers=headers, ssl=verify_ssl) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    return ToolResult(success=False, error=f"HTTP {resp.status}: {text[:500]}")
                data = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        return ToolResult(success=False, error=f"Request failed: {exc}")
    except Exception as exc:
        return ToolResult(success=False, error=f"Unexpected error: {exc}")
    return _json_result(action, data)


# ─── query action specs ───────────────────────────────────────────────────────


class QueryActionSpec:
    def __init__(
        self,
        path: str,
        param_builder: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.path = path
        self.param_builder = param_builder


def _dns_params(p: dict[str, Any]) -> dict[str, Any]:
    return _pick(p, "resource", "host", "lang")


def _ip_params(p: dict[str, Any]) -> dict[str, Any]:
    params = _pick(p, "resource", "host", "lang")
    if p.get("location") is not None:
        params["location"] = str(p["location"]).lower()
    return params


def _hash_params(p: dict[str, Any]) -> dict[str, Any]:
    return _pick(p, "resource")


def _vuln_params(p: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        p,
        "cursor",
        "limit",
        "vuln_id",
        "vendor",
        "product",
        "component_name",
        "component_package_manager",
        "version",
        "update_time",
        "threatbook_create_time",
        "is_highrisk",
    )


def _location_params(p: dict[str, Any]) -> dict[str, Any]:
    return _pick(p, "resource")


QUERY_SPECS: dict[str, QueryActionSpec] = {
    "query_dns": QueryActionSpec("/tip_api/v5/dns", _dns_params),
    "query_ip": QueryActionSpec("/tip_api/v5/ip", _ip_params),
    "query_hash": QueryActionSpec("/tip_api/v5/hash", _hash_params),
    "query_vuln": QueryActionSpec("/tip_api/v5/vuln", _vuln_params),
    "query_location": QueryActionSpec("/tip_api/v5/location", _location_params),
}

QUERY_REQUIRED: dict[str, list[str]] = {
    "query_dns": ["resource"],
    "query_ip": ["resource"],
    "query_hash": ["resource"],
    "query_vuln": [],
    "query_location": ["resource"],
}


# ─── platform action specs ────────────────────────────────────────────────────


class PlatformActionSpec:
    def __init__(
        self,
        method: str,
        path: str,
        payload_builder: Callable[[dict[str, Any]], dict[str, Any]],
        skip_apikey: bool = False,
    ) -> None:
        self.method = method
        self.path = path
        self.payload_builder = payload_builder
        self.skip_apikey = skip_apikey


def _add_intelligence_payload(p: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"source_name": p["source_name"]}
    if p.get("op") is not None:
        payload["op"] = p["op"]
    payload["data"] = p.get("intel_data") or []
    return payload


def _export_ioc_payload(p: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"source_name": p["source_name"]}
    payload.update(_pick(p, "page_size", "current_page", "start_time", "end_time"))
    return payload


def _intelligence_count_payload(p: dict[str, Any]) -> dict[str, Any]:
    payload = _pick(p, "start_time", "end_time")
    if p.get("intel_type") is not None:
        payload["type"] = p["intel_type"]
    payload.update(_pick(p, "org", "source", "content"))
    return payload


def _export_hw_data_params(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": p.get("hw_token", ""),
        "start_time": p.get("start_time"),
        "end_time": p.get("end_time"),
    }


def _add_asset_payload(p: dict[str, Any]) -> dict[str, Any]:
    return {"data": p.get("asset_data") or []}


def _update_asset_payload(p: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"operation": p.get("operation", 0)}
    payload["data"] = p.get("asset_data") or []
    return payload


def _add_user_payload(p: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "username": p["username"],
        "roles": p.get("roles") or [],
    }
    payload.update(_pick(p, "password", "describe"))
    return payload


def _login_with_token_params(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": p.get("username", ""),
        "timestamp": p.get("timestamp", ""),
        "token": p.get("login_token", ""),
    }


def _query_sample_params(p: dict[str, Any]) -> dict[str, Any]:
    return _pick(p, "resource", "format")


def _distribution_rules_params(p: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "operation": p.get("operation"),
        "rule": p.get("rule"),
    }
    params.update(_pick(p, "block_time", "direction", "device_id"))
    return {k: v for k, v in params.items() if v is not None}


def _vuln_alert_payload(p: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        p,
        "current_page",
        "page_size",
        "group_name",
        "start_time",
        "end_time",
        "threatbook_create_time",
        "publish_time",
    )


def _subscription_payload(p: dict[str, Any]) -> dict[str, Any]:
    return _pick(p, "current_page", "page_size", "report_time")


def _industry_attack_payload(p: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"update_time": p["update_time"]}
    payload.update(_pick(p, "confidence_level", "severity", "industry", "current_page", "page_size"))
    return payload


PLATFORM_SPECS: dict[str, PlatformActionSpec] = {
    "platform_add_intelligence": PlatformActionSpec(
        "POST", "/tip/v5/manually", _add_intelligence_payload
    ),
    "platform_export_ioc": PlatformActionSpec(
        "POST", "/tip/v5/ioc_list", _export_ioc_payload
    ),
    "platform_intelligence_count": PlatformActionSpec(
        "POST", "/tip/v5/intelligence_count", _intelligence_count_payload
    ),
    "platform_export_hw_data": PlatformActionSpec(
        "GET", "/tip/hw/data", _export_hw_data_params, skip_apikey=True
    ),
    "platform_add_asset": PlatformActionSpec(
        "POST", "/tip/v5/add_asset", _add_asset_payload
    ),
    "platform_update_asset": PlatformActionSpec(
        "POST", "/tip/v5/update_asset", _update_asset_payload
    ),
    "platform_add_user": PlatformActionSpec(
        "POST", "/tip/v5/add_user", _add_user_payload
    ),
    "platform_login_with_token": PlatformActionSpec(
        "GET", "/tip/v5/user/login_with_token", _login_with_token_params
    ),
    "platform_query_sample": PlatformActionSpec(
        "GET", "/tip/v5/sample_produce", _query_sample_params
    ),
    "platform_distribution_rules": PlatformActionSpec(
        "GET", "/tip/v5/distribution_rules", _distribution_rules_params
    ),
    "platform_vuln_alert": PlatformActionSpec(
        "POST", "/tip/v5/vuln_alert", _vuln_alert_payload
    ),
    "platform_subscription": PlatformActionSpec(
        "POST", "/tip/v5/human_intel_subscription", _subscription_payload
    ),
    "platform_industry_attack": PlatformActionSpec(
        "POST", "/tip/v5/industry_attack_share", _industry_attack_payload
    ),
}

PLATFORM_REQUIRED: dict[str, list[str]] = {
    "platform_add_intelligence": ["source_name", "intel_data"],
    "platform_export_ioc": ["source_name"],
    "platform_intelligence_count": [],
    "platform_export_hw_data": ["hw_token", "start_time", "end_time"],
    "platform_add_asset": ["asset_data"],
    "platform_update_asset": ["operation", "asset_data"],
    "platform_add_user": ["username", "roles"],
    "platform_login_with_token": ["username", "timestamp", "login_token"],
    "platform_query_sample": ["resource"],
    "platform_distribution_rules": ["operation", "rule"],
    "platform_vuln_alert": [],
    "platform_subscription": [],
    "platform_industry_attack": ["update_time"],
}


# ─── validation ───────────────────────────────────────────────────────────────


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _validate_required(
    action: str,
    params: dict[str, Any],
    required_map: dict[str, list[str]],
) -> Optional[str]:
    required = required_map.get(action, [])
    missing = [f for f in required if not _has_value(params.get(f))]
    if missing:
        return f"Action `{action}` 缺少必填参数：{', '.join(missing)}"

    if action == "platform_vuln_alert":
        has_time = _has_value(params.get("start_time")) and _has_value(params.get("end_time"))
        has_tb = _has_value(params.get("threatbook_create_time"))
        has_pub = _has_value(params.get("publish_time"))
        if not (has_time or has_tb or has_pub):
            return (
                "platform_vuln_alert 需满足以下三选一：\n"
                "  1. 同时传入 start_time + end_time\n"
                "  2. 传入 threatbook_create_time（如 '7d'）\n"
                "  3. 传入 publish_time（如 '7d'）"
            )
    return None


# ─── public entry points ──────────────────────────────────────────────────────


async def query(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    del ctx
    spec = QUERY_SPECS.get(action)
    if spec is None:
        available = ", ".join(sorted(QUERY_SPECS))
        return ToolResult(
            success=False,
            error=f"Unknown query action: `{action}`. Available: {available}",
        )
    err = _validate_required(action, params, QUERY_REQUIRED)
    if err:
        return ToolResult(success=False, error=err)

    try:
        _, query_base_url, timeout, apikey, verify_ssl = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    url = f"{query_base_url}{spec.path}"
    query_params = {"apikey": apikey, **spec.param_builder(params)}
    result = await _get_request(url, query_params, timeout, verify_ssl, action)
    if result.success:
        result.metadata = {**(result.metadata or {}), "api": action}
    return result


async def platform(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    del ctx
    spec = PLATFORM_SPECS.get(action)
    if spec is None:
        available = ", ".join(sorted(PLATFORM_SPECS))
        return ToolResult(
            success=False,
            error=f"Unknown platform action: `{action}`. Available: {available}",
        )
    err = _validate_required(action, params, PLATFORM_REQUIRED)
    if err:
        return ToolResult(success=False, error=err)

    try:
        platform_base_url, _, timeout, apikey, verify_ssl = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    url = f"{platform_base_url}{spec.path}"
    payload = spec.payload_builder(params)

    if spec.method.upper() == "GET":
        if not spec.skip_apikey:
            payload = {"apikey": apikey, **payload}
        result = await _get_request(url, payload, timeout, verify_ssl, action)
    else:
        body = {"apikey": apikey, **payload} if not spec.skip_apikey else payload
        result = await _post_request(url, body, timeout, verify_ssl, action)

    if result.success:
        result.metadata = {**(result.metadata or {}), "api": action}
    return result
