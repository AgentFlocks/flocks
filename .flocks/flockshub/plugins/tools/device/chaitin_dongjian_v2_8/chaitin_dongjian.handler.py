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


SERVICE_ID = "chaitin_dongjian_api"
STORAGE_KEY = "chaitin_dongjian_v2_8"
PRODUCT_VERSION = "OpenAPI V2.8"
DEFAULT_TIMEOUT = 30
DEFAULT_VERIFY_SSL = False
CATALOG_FILE = Path(__file__).with_name("chaitin_dongjian_api_catalog.json")


class ChaitinDongjianError(RuntimeError):
    pass


class RuntimeConfig:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        verify_ssl: bool,
        timeout: int,
    ) -> None:
        self.base_url = base_url
        self.token = token
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
    text = base_url.strip().rstrip("/")
    if not text.endswith("/api/v2"):
        text = f"{text}/api/v2"
    return text.rstrip("/")


def resolve_config() -> RuntimeConfig:
    raw = _raw_service_config()
    base_url = (
        _resolve_ref(_config_value(raw, "base_url", "baseUrl"))
        or os.getenv("CHAITIN_DONGJIAN_BASE_URL", "")
    )
    if not base_url:
        raise ChaitinDongjianError("Chaitin Dongjian base_url is not configured")

    token = (
        _resolve_ref(_config_value(raw, "token", "api_token", "apiToken"))
        or get_secret_manager().get("chaitin_dongjian_token")
        or get_secret_manager().get(f"{SERVICE_ID}_token")
        or os.getenv("CHAITIN_DONGJIAN_TOKEN", "")
    )
    if not token:
        raise ChaitinDongjianError("Chaitin Dongjian token is not configured")

    try:
        timeout = int(_config_value(raw, "timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    verify_ssl = _as_bool(
        _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl")
        if _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl") is not None
        else os.getenv("CHAITIN_DONGJIAN_VERIFY_SSL"),
        DEFAULT_VERIFY_SSL,
    )
    return RuntimeConfig(
        base_url=_normalize_base_url(base_url),
        token=token,
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
        raise ChaitinDongjianError(f"Missing path parameter for {path}")
    return rendered


class DongjianClient:
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
            "token": self.config.token,
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
        raise ChaitinDongjianError(f"Invalid JSON response: HTTP {response.status_code}") from exc
    if response.status_code >= 400:
        raise ChaitinDongjianError(f"HTTP {response.status_code}: {payload}")
    if isinstance(payload, dict) and payload.get("err") not in (None, ""):
        raise ChaitinDongjianError(str(payload.get("msg") or payload.get("err")))
    return payload


def _ok(data: Any, *, action: str) -> ToolResult:
    return ToolResult(
        success=True,
        output=data,
        metadata={"source": "Chaitin Dongjian", "version": PRODUCT_VERSION, "action": action},
    )


def get_client() -> DongjianClient:
    return DongjianClient(resolve_config())


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
    "project_list": ("GET", "/project/"),
    "project_filter": ("POST", "/project/filter/"),
    "template_list": ("GET", "/template/"),
    "plan_filter": ("POST", "/plan/filter/"),
    "plan_detail": ("GET", "/plan/{id}/"),
    "plugin_filter": ("POST", "/plugin/filter/"),
    "engine_filter": ("POST", "/engine/filter/"),
    "xprocess_filter": ("POST", "/xprocess/filter/"),
    "xprocess_detail": ("GET", "/xprocess/{id}/"),
    "xprocess_progress": ("GET", "/xprocess/{id}/progress/"),
    "result_filter": ("POST", "/result/filter/"),
    "result_detail": ("GET", "/result/{id}/"),
    "website_filter": ("POST", "/website/filter/"),
    "host_filter": ("POST", "/ip/filter/"),
    "service_filter": ("POST", "/service/filter/"),
    "domain_filter": ("POST", "/domain/filter/"),
    "vuln_filter": ("POST", "/vuln/filter/"),
    "vuln_detail": ("GET", "/vuln/{id}/"),
    "auditlog_filter": ("POST", "/auditlog/filter/"),
    "report_filter": ("POST", "/report/filter/"),
    "system_info_mgmt": ("GET", "/system/info/mgmt/"),
    "system_services": ("GET", "/system/info/services/"),
}


PROJECT_ACTIONS = {"project_list", "project_filter", "template_list"}
TASK_ACTIONS = {
    "plan_filter",
    "plan_detail",
    "plugin_filter",
    "engine_filter",
    "xprocess_filter",
    "xprocess_detail",
    "xprocess_progress",
}
ASSET_ACTIONS = {"website_filter", "host_filter", "service_filter", "domain_filter"}
RESULT_ACTIONS = {"result_filter", "result_detail", "vuln_filter", "vuln_detail", "auditlog_filter", "report_filter"}
SYSTEM_ACTIONS = {"system_info_mgmt", "system_services"}


def call_rest(action: str, args: dict[str, Any]) -> ToolResult:
    method, path = READONLY_ACTIONS[action]
    req_method, req_path, query, body = _request_args(args, method, path)
    return _ok(get_client().request(req_method, req_path, query=query, body=body), action=action)


def api_catalog(args: dict[str, Any]) -> ToolResult:
    del args
    catalog = _load_api_catalog()
    return _ok(
        {
            "base_path": "/api/v2",
            "catalog_counts": {
                "total": len(catalog),
                "readonly": sum(1 for entry in catalog if entry.get("kind") == "readonly"),
                "mutation": sum(1 for entry in catalog if entry.get("kind") == "mutation"),
            },
            "documented_api_catalog": catalog,
            "common_actions": {
                "projects": sorted(PROJECT_ACTIONS),
                "tasks": sorted(TASK_ACTIONS),
                "assets": sorted(ASSET_ACTIONS),
                "results": sorted(RESULT_ACTIONS),
                "system": sorted(SYSTEM_ACTIONS),
            },
        },
        action="api_catalog",
    )


def rest_call_readonly(args: dict[str, Any]) -> ToolResult:
    method, path, query, body = _request_args(args, "GET", "")
    if (method, path) not in _catalog_pairs("readonly"):
        raise ChaitinDongjianError("Only documented read-only REST method/path pairs are allowed")
    return _ok(get_client().request(method, path, query=query, body=body), action="rest_call_readonly")


def rest_call_mutation(args: dict[str, Any]) -> ToolResult:
    method, path, query, body = _request_args(args, "POST", "")
    if (method, path) not in _catalog_pairs("mutation"):
        raise ChaitinDongjianError("Only documented mutation REST method/path pairs are allowed")
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
        action = "project_list"
        params.setdefault("limit", 1)
        params.setdefault("offset", 0)
    if action not in allowed:
        return ToolResult(
            success=False,
            error=f"Unsupported Chaitin Dongjian action: {action}. Available: {', '.join(sorted(allowed))}",
        )
    try:
        return await asyncio.to_thread(ACTION_HANDLERS[action], params)
    except ChaitinDongjianError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"source": "Chaitin Dongjian", "version": PRODUCT_VERSION, "action": action},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Unexpected Chaitin Dongjian error: {exc}",
            metadata={"source": "Chaitin Dongjian", "version": PRODUCT_VERSION, "action": action},
        )


async def projects(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, PROJECT_ACTIONS | {"test"}, action, **params)


async def tasks(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, TASK_ACTIONS | {"test"}, action, **params)


async def assets(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, ASSET_ACTIONS | {"test"}, action, **params)


async def results(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, RESULT_ACTIONS | {"test"}, action, **params)


async def system(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, SYSTEM_ACTIONS | {"test"}, action, **params)


async def api_readonly(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, {"api_catalog", "rest_call_readonly", *READONLY_ACTIONS.keys(), "test"}, action, **params)


async def api_mutation(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, {"api_catalog", "rest_call_mutation"}, action, **params)
