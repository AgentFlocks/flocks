from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable

import requests

from flocks.config.config_writer import ConfigWriter
from flocks.security import get_secret_manager
from flocks.tool.registry import ToolContext, ToolResult


SERVICE_ID = "chaitin_safeline_waf"
STORAGE_KEY = "chaitin_safeline_waf_api"
PRODUCT_VERSION = "OpenAPI"
DEFAULT_TIMEOUT = 30
DEFAULT_VERIFY_SSL = False
CATALOG_FILE = Path(__file__).with_name("chaitin_safeline_waf_api_catalog.json")


class ChaitinWafError(RuntimeError):
    pass


class RuntimeConfig:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        verify_ssl: bool,
        timeout: int,
    ) -> None:
        self.base_url = base_url
        self.api_token = api_token
        self.verify_ssl = verify_ssl
        self.timeout = timeout


def _resolve_ref(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    if value.startswith("{secret:") and value.endswith("}"):
        return get_secret_manager().get(value[len("{secret:") : -1]) or ""
    if value.startswith("{env:") and value.endswith("}"):
        return os.getenv(value[len("{env:") : -1], "")
    return value


def _raw_service_config() -> dict[str, Any]:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    if not isinstance(raw, dict):
        raw = ConfigWriter.get_api_service_raw(STORAGE_KEY)
    return raw if isinstance(raw, dict) else {}


def _config_value(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if raw.get(key) is not None:
            return raw[key]
    custom_settings = raw.get("custom_settings")
    if isinstance(custom_settings, dict):
        for key in keys:
            if custom_settings.get(key) is not None:
                return custom_settings[key]
    return None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def resolve_config() -> RuntimeConfig:
    raw = _raw_service_config()
    base_url = (
        _resolve_ref(_config_value(raw, "base_url", "baseUrl"))
        or os.getenv("CHAITIN_SAFELINE_WAF_BASE_URL", "")
    )
    if not base_url:
        raise ChaitinWafError("Chaitin SafeLine WAF base_url is not configured")

    api_token = (
        _resolve_ref(_config_value(raw, "api_token", "apiToken", "token"))
        or get_secret_manager().get("chaitin_safeline_waf_api_token")
        or get_secret_manager().get(f"{SERVICE_ID}_token")
        or os.getenv("CHAITIN_SAFELINE_WAF_API_TOKEN", "")
    )
    if not api_token:
        raise ChaitinWafError("Chaitin SafeLine WAF API token is not configured")

    try:
        timeout = int(_config_value(raw, "timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    verify_ssl = _as_bool(
        _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl")
        if _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl") is not None
        else os.getenv("CHAITIN_SAFELINE_WAF_VERIFY_SSL"),
        DEFAULT_VERIFY_SSL,
    )
    return RuntimeConfig(
        base_url=_normalize_base_url(base_url),
        api_token=api_token,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )


def _render_path(path: str, args: dict[str, Any]) -> str:
    rendered = path
    path_params = args.get("path_params") if isinstance(args.get("path_params"), dict) else {}
    for key, value in {**path_params, **args}.items():
        if isinstance(key, str):
            rendered = rendered.replace("{" + key + "}", str(value))
    if "{" in rendered or "}" in rendered:
        raise ChaitinWafError(f"Missing path parameter for {path}")
    return rendered


class WafClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        url = f"{self.config.base_url}{path}"
        headers = {
            "Accept": "application/json",
            "API-TOKEN": self.config.api_token,
        }
        if method.upper() in {"POST", "PUT", "DELETE", "PATCH"}:
            headers["Content-Type"] = "application/json"
        response = requests.request(
            method.upper(),
            url,
            params={k: v for k, v in (query or {}).items() if v is not None},
            json=body if body not in (None, "") else None,
            headers=headers,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
        )
        return _json_response(response)


def _json_response(response: requests.Response) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ChaitinWafError(f"Invalid JSON response: HTTP {response.status_code}") from exc
    if response.status_code >= 400:
        raise ChaitinWafError(f"HTTP {response.status_code}: {payload}")
    if isinstance(payload, dict) and payload.get("err") not in (None, ""):
        raise ChaitinWafError(str(payload.get("msg") or payload.get("err")))
    return payload


def _ok(data: Any, *, action: str) -> ToolResult:
    return ToolResult(
        success=True,
        output=data,
        metadata={"source": "Chaitin SafeLine WAF", "version": PRODUCT_VERSION, "action": action},
    )


def get_client() -> WafClient:
    return WafClient(resolve_config())


def _request_args(args: dict[str, Any], default_method: str, default_path: str) -> tuple[str, str, dict[str, Any], Any]:
    method = str(args.get("method") or default_method).upper()
    path = _render_path(str(args.get("path") or default_path), args)
    query = args.get("query") if isinstance(args.get("query"), dict) else {}
    body = args.get("body")
    if method == "GET" and not query:
        ignored = {"action", "method", "path", "query", "body", "path_params"}
        query = {k: v for k, v in args.items() if k not in ignored and v is not None}
    return method, path, dict(query), body


def _load_api_catalog() -> list[dict[str, Any]]:
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    entries = data.get("entries")
    return entries if isinstance(entries, list) else []


def _catalog_pairs(kind: str) -> set[tuple[str, str]]:
    return {
        (str(entry.get("method", "")).upper(), str(entry.get("path", "")))
        for entry in _load_api_catalog()
        if entry.get("kind") == kind and entry.get("method") and entry.get("path")
    }


READONLY_ACTIONS: dict[str, tuple[str, str]] = {
    "profile": ("GET", "/api/ProfileAPI"),
    "overview": ("GET", "/api/OverviewAPI"),
    "acl_rules": ("GET", "/api/ACLRuleAPI"),
    "acl_templates": ("GET", "/api/ACLRuleTemplateAPI"),
    "attack_logs": ("GET", "/api/FilterV2API"),
    "ip_groups": ("GET", "/api/IPGroupAPI"),
    "reverse_proxy_sites": ("GET", "/api/HardwareReverseProxyWebsiteAPI"),
    "traffic_detection_sites": ("GET", "/api/HardwareTrafficDetectionWebsiteAPI"),
    "certificates": ("GET", "/api/CertAPI"),
    "traffic_learning_overview": ("GET", "/api/traffic_learning/v1/Overview"),
}


SYSTEM_ACTIONS = {"profile", "overview"}
POLICY_ACTIONS = {"acl_rules", "acl_templates", "ip_groups"}
SITE_ACTIONS = {"reverse_proxy_sites", "traffic_detection_sites", "certificates"}
LOG_ACTIONS = {"attack_logs", "traffic_learning_overview"}


def call_rest(action: str, args: dict[str, Any]) -> ToolResult:
    method, path = READONLY_ACTIONS[action]
    req_method, req_path, query, body = _request_args(args, method, path)
    return _ok(get_client().request(req_method, req_path, query=query, body=body), action=action)


def api_catalog(args: dict[str, Any]) -> ToolResult:
    del args
    catalog = _load_api_catalog()
    return _ok(
        {
            "catalog_counts": {
                "total": len(catalog),
                "readonly": sum(1 for entry in catalog if entry.get("kind") == "readonly"),
                "mutation": sum(1 for entry in catalog if entry.get("kind") == "mutation"),
            },
            "documented_api_catalog": catalog,
            "common_actions": {
                "system": sorted(SYSTEM_ACTIONS),
                "policy": sorted(POLICY_ACTIONS),
                "site": sorted(SITE_ACTIONS),
                "logs": sorted(LOG_ACTIONS),
            },
        },
        action="api_catalog",
    )


def rest_call_readonly(args: dict[str, Any]) -> ToolResult:
    method, path, query, body = _request_args(args, "GET", "")
    if (method, path) not in _catalog_pairs("readonly"):
        raise ChaitinWafError("Only documented read-only REST method/path pairs are allowed")
    return _ok(get_client().request(method, path, query=query, body=body), action="rest_call_readonly")


def rest_call_mutation(args: dict[str, Any]) -> ToolResult:
    method, path, query, body = _request_args(args, "POST", "")
    if (method, path) not in _catalog_pairs("mutation"):
        raise ChaitinWafError("Only documented mutation REST method/path pairs are allowed")
    return _ok(get_client().request(method, path, query=query, body=body), action="rest_call_mutation")


ACTION_HANDLERS: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
    "api_catalog": api_catalog,
    "rest_call_readonly": rest_call_readonly,
    "rest_call_mutation": rest_call_mutation,
}
for _action in READONLY_ACTIONS:
    ACTION_HANDLERS[_action] = lambda args, action=_action: call_rest(action, args)


async def _dispatch(ctx: ToolContext, allowed: set[str], action: str, **params: Any) -> ToolResult:
    del ctx
    if action == "test":
        action = "profile"
    if action not in allowed:
        return ToolResult(
            success=False,
            error=f"Unsupported Chaitin SafeLine WAF action: {action}. Available: {', '.join(sorted(allowed))}",
        )
    try:
        return await asyncio.to_thread(ACTION_HANDLERS[action], params)
    except ChaitinWafError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"source": "Chaitin SafeLine WAF", "version": PRODUCT_VERSION, "action": action},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Unexpected Chaitin SafeLine WAF error: {exc}",
            metadata={"source": "Chaitin SafeLine WAF", "version": PRODUCT_VERSION, "action": action},
        )


async def system(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, SYSTEM_ACTIONS | {"test"}, action, **params)


async def policy(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, POLICY_ACTIONS | {"test"}, action, **params)


async def site(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, SITE_ACTIONS | {"test"}, action, **params)


async def logs(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, LOG_ACTIONS | {"test"}, action, **params)


async def api_readonly(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, {"api_catalog", "rest_call_readonly", *READONLY_ACTIONS.keys(), "test"}, action, **params)


async def api_mutation(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, {"api_catalog", "rest_call_mutation"}, action, **params)
