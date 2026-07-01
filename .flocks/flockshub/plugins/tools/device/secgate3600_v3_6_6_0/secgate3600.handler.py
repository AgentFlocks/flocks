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


SERVICE_ID = "secgate3600_api"
STORAGE_KEY = "secgate3600_api_v3_6_6_0"
PRODUCT_VERSION = "V3.6.6.0"
DEFAULT_TIMEOUT = 30
DEFAULT_VERIFY_SSL = False
CATALOG_FILE = Path(__file__).with_name("secgate3600_api_catalog.json")


class SecGateError(RuntimeError):
    pass


class RuntimeConfig:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool,
        timeout: int,
    ) -> None:
        self.base_url = base_url
        self.username = username
        self.password = password
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


def _normalize_base_url(base_url: str) -> str:
    text = base_url.strip().rstrip("/")
    for suffix in ("/v1.0/rest", "/v1.0/login", "/v1.0/out", "/v1.0"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.rstrip("/")


def resolve_config() -> RuntimeConfig:
    raw = _raw_service_config()
    base_url = _resolve_ref(_config_value(raw, "base_url", "baseUrl")) or os.getenv("SECGATE3600_BASE_URL", "")
    if not base_url:
        raise SecGateError("SecGate 3600 base_url is not configured")

    username = (
        _resolve_ref(_config_value(raw, "username"))
        or get_secret_manager().get("secgate3600_username")
        or get_secret_manager().get(f"{SERVICE_ID}_username")
        or os.getenv("SECGATE3600_USERNAME", "")
    )
    password = (
        _resolve_ref(_config_value(raw, "password"))
        or get_secret_manager().get("secgate3600_password")
        or get_secret_manager().get(f"{SERVICE_ID}_password")
        or os.getenv("SECGATE3600_PASSWORD", "")
    )
    if not username or not password:
        raise SecGateError("SecGate 3600 username/password is not configured")

    verify_ssl = _as_bool(
        _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl")
        if _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl") is not None
        else os.getenv("SECGATE3600_VERIFY_SSL"),
        DEFAULT_VERIFY_SSL,
    )
    try:
        timeout = int(_config_value(raw, "timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    return RuntimeConfig(
        base_url=_normalize_base_url(base_url),
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )


class SecGateClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.token = ""

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    def login(self) -> dict[str, Any]:
        response = self.session.post(
            self._url("/v1.0/login"),
            json={"username": self.config.username, "password": self.config.password},
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
            headers={"Content-type": "application/json"},
        )
        payload = _json_response(response)
        if not _is_success(payload):
            raise SecGateError(_error_message(payload, fallback=f"Login failed with HTTP {response.status_code}"))
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        token = str(result.get("token") or payload.get("token") or "").strip()
        if not token:
            raise SecGateError("Login succeeded but response did not include token")
        self.token = token
        self.session.cookies.set("token", token)
        return {
            "success": True,
            "username": result.get("username") or self.config.username,
            "base_url": self.config.base_url,
            "token_present": True,
        }

    def rest(self, *, module: str, function: str, body: dict[str, Any] | None = None, page_index: int = 1, page_size: int = 20) -> dict[str, Any]:
        if not self.token:
            self.login()
        request_body = [
            {
                "head": {
                    "module": module,
                    "function": function,
                    "page_index": page_index,
                    "page_size": page_size,
                },
                "body": body or {},
            }
        ]
        response = self.session.post(
            self._url("/v1.0/rest/"),
            json=request_body,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
            headers={"Content-type": "application/json"},
        )
        payload = _json_response(response)
        if not _is_success(payload):
            raise SecGateError(_error_message(payload, fallback=f"REST call failed with HTTP {response.status_code}"))
        return payload


def _json_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SecGateError(f"Invalid JSON response: HTTP {response.status_code}") from exc
    if not isinstance(payload, dict):
        raise SecGateError("Unexpected response shape: expected object")
    return payload


def _is_success(payload: dict[str, Any]) -> bool:
    if payload.get("success") is True:
        return True
    head = payload.get("head")
    if isinstance(head, dict) and str(head.get("error_code")) in {"0", "success"}:
        return True
    if str(payload.get("error_code")) in {"0", "success"}:
        return True
    result = payload.get("result")
    if isinstance(result, dict) and str(result.get("error_code")) in {"0", "success"}:
        return True
    return False


def _error_message(payload: dict[str, Any], *, fallback: str) -> str:
    for container in (payload, payload.get("head"), payload.get("result")):
        if isinstance(container, dict):
            for key in ("error_string", "error_msg", "message", "error_code"):
                value = container.get(key)
                if value not in (None, ""):
                    return str(value)
    return fallback


def _ok(data: Any, *, action: str) -> ToolResult:
    return ToolResult(success=True, output=data, metadata={"source": "SecGate 3600", "version": PRODUCT_VERSION, "action": action})


def get_client() -> SecGateClient:
    return SecGateClient(resolve_config())


def check_login(args: dict[str, Any]) -> ToolResult:
    del args
    return _ok(get_client().login(), action="check_login")


def _load_api_catalog() -> list[dict[str, str]]:
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    entries = data.get("entries")
    if not isinstance(entries, list):
        return []
    catalog: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        module = str(entry.get("module") or "").strip()
        function = str(entry.get("function") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        if module and function and kind in {"readonly", "mutation"}:
            catalog.append({"module": module, "function": function, "kind": kind})
    return catalog


def _catalog_pairs(kind: str) -> set[tuple[str, str]]:
    return {
        (entry["module"], entry["function"])
        for entry in _load_api_catalog()
        if entry.get("kind") == kind
    }


READONLY_ACTIONS: dict[str, dict[str, Any]] = {
    "notice_num_day": {"module": "notice", "function": "get_notice_num_day", "body": {}, "page_size": 20},
    "threats_last_day": {
        "module": "statistics",
        "function": "get_threat_threats",
        "body": {"data": {"time": "last-1-day"}},
        "page_size": 20,
    },
    "focus_last_day": {
        "module": "statistics",
        "function": "get_focus_focus",
        "body": {"data": {"time": "last-1-day"}},
        "page_size": 20,
    },
    "cpu_usage": {
        "module": "statistics",
        "function": "get_cpu_usage",
        "body": {"data": {"time": "last-1-hours", "group_by": "cpu"}},
        "page_size": 2000,
    },
    "memory_usage": {
        "module": "statistics",
        "function": "get_memory_usage",
        "body": {"data": {"time": "last-1-hours", "group_by": "memory"}},
        "page_size": 2000,
    },
    "disk_usage": {
        "module": "statistics",
        "function": "get_disk_usage",
        "body": {"data": {"time": "last-1-hours", "group_by": "disk"}},
        "page_size": 2000,
    },
    "connection_monitor": {
        "module": "statistics",
        "function": "get_connection_monitor",
        "body": {"data": {"time": "last-1-hours"}},
        "page_size": 2000,
    },
    "system_info": {"module": "dashboard", "function": "get_system_info", "body": {}, "page_size": 20},
    "system_resource": {"module": "dashboard", "function": "get_system_resource", "body": {}, "page_size": 20},
    "interface_info": {"module": "dashboard", "function": "get_interface_info", "body": {}, "page_size": 20},
    "interface_list": {
        "module": "inter_face",
        "function": "show_all_interface_web",
        "body": {"info": {"interface": {}, "filter": {"inf_type": "physical", "inf_desc": "", "inf_name": "", "inf_zone": ""}}},
        "page_size": 50,
    },
    "security_policy_list": {
        "module": "sec_policy",
        "function": "get_sec_policy",
        "body": {"sec_policy": [{"name": "", "is_detail": False}]},
        "page_size": 20,
    },
}


SYSTEM_ACTIONS = {"check_login", "system_info", "system_resource", "cpu_usage", "memory_usage", "disk_usage"}
DASHBOARD_ACTIONS = {"notice_num_day", "threats_last_day", "focus_last_day", "connection_monitor", "interface_info"}
NETWORK_ACTIONS = {"interface_list"}
POLICY_ACTIONS = {"security_policy_list"}


def call_readonly(action: str, args: dict[str, Any]) -> ToolResult:
    spec = READONLY_ACTIONS[action]
    body = args.get("body") if isinstance(args.get("body"), dict) else spec.get("body", {})
    page_index = int(args.get("page_index") or 1)
    page_size = int(args.get("page_size") or spec.get("page_size") or 20)
    return _ok(
        get_client().rest(
            module=spec["module"],
            function=spec["function"],
            body=body,
            page_index=page_index,
            page_size=page_size,
        ),
        action=action,
    )


def api_catalog(args: dict[str, Any]) -> ToolResult:
    del args
    catalog = _load_api_catalog()
    readonly_entries = [entry for entry in catalog if entry.get("kind") == "readonly"]
    mutation_entries = [entry for entry in catalog if entry.get("kind") == "mutation"]
    return _ok(
        {
            "login": "/v1.0/login",
            "rest": "/v1.0/rest/",
            "catalog_counts": {
                "total": len(catalog),
                "readonly": len(readonly_entries),
                "mutation": len(mutation_entries),
            },
            "documented_api_catalog": catalog,
            "readonly_actions": READONLY_ACTIONS,
            "groups": {
                "system": sorted(SYSTEM_ACTIONS),
                "dashboard": sorted(DASHBOARD_ACTIONS),
                "network": sorted(NETWORK_ACTIONS),
                "policy": sorted(POLICY_ACTIONS),
            },
        },
        action="api_catalog",
    )


def rest_call_readonly(args: dict[str, Any]) -> ToolResult:
    module = str(args.get("module") or "").strip()
    function = str(args.get("function") or "").strip()
    if not module or not function:
        raise SecGateError("module and function are required")
    allowed = _catalog_pairs("readonly") | {
        (spec["module"], spec["function"]) for spec in READONLY_ACTIONS.values()
    }
    if (module, function) not in allowed:
        raise SecGateError("Only documented read-only module/function pairs in secgate3600_api_catalog are allowed")
    body = args.get("body") if isinstance(args.get("body"), dict) else {}
    page_index = int(args.get("page_index") or 1)
    page_size = int(args.get("page_size") or 20)
    return _ok(get_client().rest(module=module, function=function, body=body, page_index=page_index, page_size=page_size), action="rest_call_readonly")


def rest_call_mutation(args: dict[str, Any]) -> ToolResult:
    module = str(args.get("module") or "").strip()
    function = str(args.get("function") or "").strip()
    if not module or not function:
        raise SecGateError("module and function are required")
    if (module, function) not in _catalog_pairs("mutation"):
        raise SecGateError("Only documented mutation module/function pairs in secgate3600_api_catalog are allowed")
    body = args.get("body") if isinstance(args.get("body"), dict) else {}
    page_index = int(args.get("page_index") or 1)
    page_size = int(args.get("page_size") or 20)
    return _ok(get_client().rest(module=module, function=function, body=body, page_index=page_index, page_size=page_size), action="rest_call_mutation")


ACTION_HANDLERS: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
    "check_login": check_login,
    "api_catalog": api_catalog,
    "rest_call_readonly": rest_call_readonly,
    "rest_call_mutation": rest_call_mutation,
}
for _action in READONLY_ACTIONS:
    ACTION_HANDLERS[_action] = lambda args, action=_action: call_readonly(action, args)


async def _dispatch(ctx: ToolContext, allowed: set[str], action: str, **params: Any) -> ToolResult:
    del ctx
    if action == "test":
        try:
            return await asyncio.to_thread(check_login, params)
        except SecGateError as exc:
            return ToolResult(success=False, error=str(exc), metadata={"source": "SecGate 3600", "version": PRODUCT_VERSION, "action": action})
        except Exception as exc:
            return ToolResult(success=False, error=f"Unexpected SecGate 3600 error: {exc}", metadata={"source": "SecGate 3600", "version": PRODUCT_VERSION, "action": action})
    if action not in allowed:
        return ToolResult(success=False, error=f"Unsupported SecGate 3600 action: {action}. Available: {', '.join(sorted(allowed))}")
    try:
        return await asyncio.to_thread(ACTION_HANDLERS[action], params)
    except SecGateError as exc:
        return ToolResult(success=False, error=str(exc), metadata={"source": "SecGate 3600", "version": PRODUCT_VERSION, "action": action})
    except Exception as exc:
        return ToolResult(success=False, error=f"Unexpected SecGate 3600 error: {exc}", metadata={"source": "SecGate 3600", "version": PRODUCT_VERSION, "action": action})


async def system(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, SYSTEM_ACTIONS, action, **params)


async def dashboard(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, DASHBOARD_ACTIONS, action, **params)


async def network(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, NETWORK_ACTIONS, action, **params)


async def policy(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, POLICY_ACTIONS, action, **params)


async def api_readonly(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, {"api_catalog", "rest_call_readonly", *READONLY_ACTIONS.keys()}, action, **params)


async def api_mutation(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, {"api_catalog", "rest_call_mutation"}, action, **params)
