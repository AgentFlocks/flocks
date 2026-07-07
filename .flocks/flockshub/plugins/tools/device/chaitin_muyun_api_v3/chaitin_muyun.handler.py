from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

import requests

from flocks.config.config_writer import ConfigWriter
from flocks.security import get_secret_manager
from flocks.tool.registry import ToolContext, ToolResult


SERVICE_ID = "chaitin_muyun_api"
STORAGE_KEY = "chaitin_muyun_api_v3"
PRODUCT_VERSION = "API 3.0"
DEFAULT_TIMEOUT = 30
DEFAULT_VERIFY_SSL = False
CATALOG_FILE = Path(__file__).with_name("chaitin_muyun_api_catalog.json")


class ChaitinMuyunError(RuntimeError):
    pass


class RuntimeConfig:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        org_id: str,
        verify_ssl: bool,
        timeout: int,
    ) -> None:
        self.base_url = base_url
        self.api_token = api_token
        self.org_id = org_id
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
    if text.endswith("/rpc"):
        text = text[:-4]
    return text.rstrip("/")


def resolve_config() -> RuntimeConfig:
    raw = _raw_service_config()
    base_url = (
        _resolve_ref(_config_value(raw, "base_url", "baseUrl"))
        or os.getenv("CHAITIN_MUYUN_BASE_URL", "")
    )
    if not base_url:
        raise ChaitinMuyunError("Chaitin Muyun base_url is not configured")

    api_token = (
        _resolve_ref(_config_value(raw, "api_token", "apiToken", "token"))
        or get_secret_manager().get("chaitin_muyun_api_token")
        or get_secret_manager().get(f"{SERVICE_ID}_token")
        or os.getenv("CHAITIN_MUYUN_API_TOKEN", "")
    )
    if not api_token:
        raise ChaitinMuyunError("Chaitin Muyun API token is not configured")

    org_id = (
        _resolve_ref(_config_value(raw, "org_id", "oid"))
        or os.getenv("CHAITIN_MUYUN_ORG_ID", "")
    )
    try:
        timeout = int(_config_value(raw, "timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    verify_ssl = _as_bool(
        _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl")
        if _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl") is not None
        else os.getenv("CHAITIN_MUYUN_VERIFY_SSL"),
        DEFAULT_VERIFY_SSL,
    )
    return RuntimeConfig(
        base_url=_normalize_base_url(base_url),
        api_token=api_token,
        org_id=org_id,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )


class MuyunClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    @property
    def rpc_url(self) -> str:
        return f"{self.config.base_url}/rpc"

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        body = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": str(uuid.uuid4()),
        }
        headers = {
            "Content-Type": "application/json",
            "Cookie": f"API-Token={self.config.api_token}",
        }
        if self.config.org_id:
            headers["X-CW-OID"] = self.config.org_id
        response = requests.post(
            self.rpc_url,
            json=body,
            headers=headers,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
        )
        return _json_rpc_response(response)


def _json_rpc_response(response: requests.Response) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ChaitinMuyunError(f"Invalid JSON response: HTTP {response.status_code}") from exc
    if not isinstance(payload, dict):
        raise ChaitinMuyunError("Unexpected JSON-RPC response shape: expected object")
    if response.status_code >= 400:
        raise ChaitinMuyunError(f"HTTP {response.status_code}: {payload}")
    if payload.get("error"):
        error = payload["error"]
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or error
        else:
            message = error
        raise ChaitinMuyunError(str(message))
    return payload.get("result", payload)


def _ok(data: Any, *, action: str) -> ToolResult:
    return ToolResult(
        success=True,
        output=data,
        metadata={"source": "Chaitin Muyun", "version": PRODUCT_VERSION, "action": action},
    )


def get_client() -> MuyunClient:
    return MuyunClient(resolve_config())


def _params(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("params")
    if isinstance(raw, dict):
        return dict(raw)
    ignored = {"action", "method"}
    return {k: v for k, v in args.items() if k not in ignored and v is not None}


def _load_api_catalog() -> list[dict[str, Any]]:
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    entries = data.get("entries")
    return entries if isinstance(entries, list) else []


def _catalog_methods(kind: str) -> set[str]:
    return {
        str(entry.get("method"))
        for entry in _load_api_catalog()
        if entry.get("kind") == kind and entry.get("method")
    }


READONLY_ACTIONS: dict[str, str] = {
    "product_info": "CloudwalkerSettingService.GetProductInfo",
    "current_user": "AccountAuthService.GetCurrentUserInfo",
    "host_count": "HostAssetService.CountHost",
    "host_list": "HostAssetService.GetHostAssetList",
    "host_detail": "HostAssetService.GetHostInfoDetail",
    "application_list": "ApplicationAssetService.GetApplicationAssetList",
    "website_list": "WebsiteAssetService.GetWebsiteList",
    "process_list": "ProcessAssetService.GetProcessList",
    "webshell_events": "WebshellEventService.GetEventList",
    "malware_events": "MalwareEventService.GetEventList",
    "bruteforce_events": "BruteForceService.GetEventList",
    "abnormal_login_events": "AbnormalLoginEventService.GetEventList",
    "realtime_events": "ThreatOverviewService.ListRealTimeEvents",
    "vuln_list": "VulnService.GetVulnList",
    "vuln_detail": "VulnService.GetVuln",
    "security_check_events": "SecurityCheckService.GetEventList",
    "baseline_tasks": "BaselineV2Service.GetTaskList",
    "emergency_vulns": "EmergencyVulnService.ListVuln",
}


ASSET_ACTIONS = {
    "product_info",
    "current_user",
    "host_count",
    "host_list",
    "host_detail",
    "application_list",
    "website_list",
    "process_list",
}
EVENT_ACTIONS = {
    "webshell_events",
    "malware_events",
    "bruteforce_events",
    "abnormal_login_events",
    "realtime_events",
}
RISK_ACTIONS = {
    "vuln_list",
    "vuln_detail",
    "security_check_events",
    "baseline_tasks",
    "emergency_vulns",
}


def call_method(method: str, args: dict[str, Any], *, action: str) -> ToolResult:
    return _ok(get_client().rpc(method, _params(args)), action=action)


def api_catalog(args: dict[str, Any]) -> ToolResult:
    del args
    catalog = _load_api_catalog()
    return _ok(
        {
            "rpc": "/rpc",
            "catalog_counts": {
                "total": len(catalog),
                "readonly": sum(1 for entry in catalog if entry.get("kind") == "readonly"),
                "mutation": sum(1 for entry in catalog if entry.get("kind") == "mutation"),
            },
            "documented_api_catalog": catalog,
            "common_actions": {
                "assets": sorted(ASSET_ACTIONS),
                "events": sorted(EVENT_ACTIONS),
                "risk": sorted(RISK_ACTIONS),
            },
        },
        action="api_catalog",
    )


def rpc_call_readonly(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method") or "").strip()
    if not method:
        raise ChaitinMuyunError("method is required")
    allowed = _catalog_methods("readonly") | set(READONLY_ACTIONS.values())
    if method not in allowed:
        raise ChaitinMuyunError("Only documented read-only JSON-RPC methods are allowed")
    return call_method(method, args, action="rpc_call_readonly")


def rpc_call_mutation(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method") or "").strip()
    if not method:
        raise ChaitinMuyunError("method is required")
    if method not in _catalog_methods("mutation"):
        raise ChaitinMuyunError("Only documented mutation JSON-RPC methods are allowed")
    return call_method(method, args, action="rpc_call_mutation")


ACTION_HANDLERS: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
    "api_catalog": api_catalog,
    "rpc_call_readonly": rpc_call_readonly,
    "rpc_call_mutation": rpc_call_mutation,
}
for _action, _method in READONLY_ACTIONS.items():
    ACTION_HANDLERS[_action] = lambda args, method=_method, action=_action: call_method(method, args, action=action)


async def _dispatch(ctx: ToolContext, allowed: set[str], action: str, **params: Any) -> ToolResult:
    del ctx
    if action == "test":
        action = "product_info"
    if action not in allowed:
        return ToolResult(
            success=False,
            error=f"Unsupported Chaitin Muyun action: {action}. Available: {', '.join(sorted(allowed))}",
        )
    try:
        return await asyncio.to_thread(ACTION_HANDLERS[action], params)
    except ChaitinMuyunError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"source": "Chaitin Muyun", "version": PRODUCT_VERSION, "action": action},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Unexpected Chaitin Muyun error: {exc}",
            metadata={"source": "Chaitin Muyun", "version": PRODUCT_VERSION, "action": action},
        )


async def assets(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, ASSET_ACTIONS | {"test"}, action, **params)


async def events(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, EVENT_ACTIONS | {"test"}, action, **params)


async def risk(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, RISK_ACTIONS | {"test"}, action, **params)


async def api_readonly(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, {"api_catalog", "rpc_call_readonly", *READONLY_ACTIONS.keys(), "test"}, action, **params)


async def api_mutation(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, {"api_catalog", "rpc_call_mutation"}, action, **params)
