from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

from flocks.config.config_writer import ConfigWriter
from flocks.security import get_secret_manager
from flocks.tool.registry import ToolContext, ToolResult


SERVICE_ID = "sangfor_atrust"
STORAGE_KEY = "sangfor_atrust_v3"
PRODUCT_VERSION = "3"
DEFAULT_TIMEOUT = 30
DEFAULT_VERIFY_SSL = False
DEFAULT_LOCALE = "zh-cn"
DEFAULT_QUERY_LANG = "zh-CN"
CATALOG_FILE = Path(__file__).with_name("sangfor_atrust_api_catalog.json")
DEFAULT_LANG_PATHS = {
    "/api/v3/group/assignResourceByExternalId",
    "/api/v3/group/assignResourceByFullPath",
    "/api/v3/group/assignRoleByExternalId",
    "/api/v3/group/assignRoleByFullPath",
    "/api/v3/group/assignRoleById",
    "/api/v3/group/bulkDeleteByExternalIdList",
    "/api/v3/group/bulkDeleteByFullPathList",
    "/api/v3/group/bulkDeleteByIdList",
    "/api/v3/group/create",
    "/api/v3/group/queryAll",
    "/api/v3/group/updateByExternalId",
    "/api/v3/group/updateByFullPath",
    "/api/v3/group/updateById",
    "/api/v3/role/assignGroupByExternalId",
    "/api/v3/role/assignGroupById",
    "/api/v3/role/assignGroupByName",
    "/api/v3/role/assignResourceByExternalId",
    "/api/v3/role/assignResourceById",
    "/api/v3/role/assignResourceByName",
    "/api/v3/role/assignUserByExternalId",
    "/api/v3/role/assignUserById",
    "/api/v3/role/assignUserByName",
    "/api/v3/role/bulkDeleteByExternalIdList",
    "/api/v3/role/bulkDeleteByIdList",
    "/api/v3/role/bulkDeleteByNameList",
    "/api/v3/role/create",
    "/api/v3/role/queryAll",
    "/api/v3/role/updateByExternalId",
    "/api/v3/role/updateById",
    "/api/v3/role/updateByName",
    "/api/v3/user/assignResourceByExternalId",
    "/api/v3/user/assignResourceById",
    "/api/v3/user/assignResourceByName",
    "/api/v3/user/assignRoleByExternalId",
    "/api/v3/user/assignRoleById",
    "/api/v3/user/assignRoleByName",
    "/api/v3/user/bulkDeleteByExternalIdList",
    "/api/v3/user/bulkDeleteByIdList",
    "/api/v3/user/bulkDeleteByNameList",
    "/api/v3/user/bulkUpdateByExternalIdList",
    "/api/v3/user/bulkUpdateByIdList",
    "/api/v3/user/bulkUpdateByNameList",
    "/api/v3/user/create",
    "/api/v3/user/queryAll",
    "/api/v3/user/queryById",
    "/api/v3/user/updateByExternalId",
    "/api/v3/user/updateById",
    "/api/v3/user/updateByName",
}


class ATrustError(RuntimeError):
    pass


class RuntimeConfig:
    def __init__(
        self,
        *,
        base_url: str,
        app_id: str,
        app_secret: str,
        verify_ssl: bool,
        timeout: int,
        locale: str,
        default_lang: str,
    ) -> None:
        self.base_url = base_url
        self.app_id = app_id
        self.app_secret = app_secret
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.locale = locale
        self.default_lang = default_lang


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
    for suffix in ("/api/v3", "/api/v1", "/api"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.rstrip("/")


def resolve_config() -> RuntimeConfig:
    raw = _raw_service_config()
    base_url = (
        _resolve_ref(_config_value(raw, "base_url", "baseUrl"))
        or os.getenv("SANGFOR_ATRUST_BASE_URL", "")
    )
    if not base_url:
        raise ATrustError("Sangfor aTrust base_url is not configured")

    app_id = (
        _resolve_ref(_config_value(raw, "app_id", "appId", "api_id", "apiId"))
        or get_secret_manager().get("sangfor_atrust_app_id")
        or get_secret_manager().get(f"{SERVICE_ID}_app_id")
        or os.getenv("SANGFOR_ATRUST_APP_ID", "")
    )
    app_secret = (
        _resolve_ref(_config_value(raw, "app_secret", "appSecret", "api_secret", "apiSecret"))
        or get_secret_manager().get("sangfor_atrust_app_secret")
        or get_secret_manager().get(f"{SERVICE_ID}_app_secret")
        or os.getenv("SANGFOR_ATRUST_APP_SECRET", "")
    )
    if not app_id or not app_secret:
        raise ATrustError("Sangfor aTrust API ID/API Secret is not configured")

    try:
        timeout = int(_config_value(raw, "timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    verify_ssl = _as_bool(
        _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl")
        if _config_value(raw, "verify_ssl", "ssl_verify", "verifySsl") is not None
        else os.getenv("SANGFOR_ATRUST_VERIFY_SSL"),
        DEFAULT_VERIFY_SSL,
    )
    locale = (
        _resolve_ref(_config_value(raw, "locale", "locale_cookie"))
        or os.getenv("SANGFOR_ATRUST_LOCALE", DEFAULT_LOCALE)
    )
    default_lang = (
        _resolve_ref(_config_value(raw, "lang", "language", "default_lang"))
        or os.getenv("SANGFOR_ATRUST_LANG", DEFAULT_QUERY_LANG)
    )
    return RuntimeConfig(
        base_url=_normalize_base_url(base_url),
        app_id=app_id,
        app_secret=app_secret,
        verify_ssl=verify_ssl,
        timeout=timeout,
        locale=locale,
        default_lang=default_lang,
    )


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _query_pairs(query: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not query:
        return []
    pairs: list[tuple[str, str]] = []
    for key in sorted(query, key=lambda item: str(item)):
        value = query[key]
        if value is None:
            continue
        key_text = str(key)
        if isinstance(value, (list, tuple)):
            for item in value:
                if item is not None:
                    pairs.append((key_text, _scalar(item)))
        else:
            pairs.append((key_text, _scalar(value)))
    return pairs


def _query_string(query: dict[str, Any] | None) -> str:
    return "&".join(f"{key}={value}" for key, value in _query_pairs(query))


def _url_query_string(query: dict[str, Any] | None) -> str:
    return "&".join(
        f"{quote(key, safe='')}={quote(value, safe='/')}"
        for key, value in _query_pairs(query)
    )


def _body_text(body: Any) -> str:
    if body is None:
        return ""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _signature_string(path: str, query_string: str, body_text: str) -> str:
    if query_string and body_text:
        return f"{path}?{query_string}&{body_text}"
    if query_string:
        return f"{path}?{query_string}"
    if body_text:
        return f"{path}?{body_text}"
    return path


def _signature_headers(config: RuntimeConfig, path: str, query_string: str, body_text: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    sign_key = (
        f"appId={config.app_id}&appSecret={config.app_secret}"
        f"&timestamp={timestamp}&nonce={nonce}"
    )
    sign_text = _signature_string(path, query_string, body_text)
    signature = hmac.new(
        sign_key.encode("utf-8"),
        sign_text.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Ca-Key": config.app_id,
        "X-Ca-Nonce": nonce,
        "X-Ca-TimeStamp": timestamp,
        "X-Ca-Sign": signature,
    }


def _with_default_query_params(
    path: str,
    query: dict[str, Any] | None,
    default_lang: str,
) -> dict[str, Any]:
    merged = dict(query or {})
    if default_lang and path in DEFAULT_LANG_PATHS and "lang" not in merged:
        merged["lang"] = default_lang
    return merged


def _load_api_catalog() -> list[dict[str, Any]]:
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    entries = data.get("entries")
    return entries if isinstance(entries, list) else []


def _catalog_keys(kind: str) -> set[tuple[str, str]]:
    return {
        (str(entry.get("method", "")).upper(), str(entry.get("path", "")))
        for entry in _load_api_catalog()
        if entry.get("kind") == kind and entry.get("method") and entry.get("path")
    }


def _ok(data: Any, *, action: str) -> ToolResult:
    return ToolResult(
        success=True,
        output=data,
        metadata={"source": "Sangfor aTrust", "version": PRODUCT_VERSION, "action": action},
    )


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        message = payload.get("msg") or payload.get("message") or payload.get("error")
        code = payload.get("code")
        if message and code is not None:
            return f"aTrust API error (code={code}): {message}"
        if message:
            return str(message)
        if code not in (None, "OK", 0, "0"):
            return f"aTrust API error (code={code})"
    return fallback


def _is_success(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    code = payload.get("code")
    return code in (None, "OK", 0, "0")


def _result(action: str, response: requests.Response) -> ToolResult:
    try:
        payload = response.json()
    except ValueError:
        text = response.text[:500]
        if response.status_code >= 400:
            return ToolResult(success=False, error=f"HTTP {response.status_code}: {text}")
        return _ok(text, action=action)

    metadata = {
        "source": "Sangfor aTrust",
        "version": PRODUCT_VERSION,
        "action": action,
    }
    if isinstance(payload, dict) and payload.get("traceId"):
        metadata["traceId"] = payload["traceId"]
    if response.status_code >= 400 or not _is_success(payload):
        return ToolResult(
            success=False,
            error=_error_message(payload, fallback=f"HTTP {response.status_code}: {payload}"),
            metadata=metadata,
        )
    output = payload.get("data", payload) if isinstance(payload, dict) else payload
    return ToolResult(success=True, output=output, metadata=metadata)


class ATrustClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.session = requests.Session()

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        action: str = "",
    ) -> ToolResult:
        if not path.startswith("/"):
            raise ATrustError("path must start with /")
        query = _with_default_query_params(path, query, self.config.default_lang)
        query_string = _query_string(query)
        url_query_string = _url_query_string(query)
        body_text = _body_text(body)
        url = f"{self.config.base_url}{path}"
        if url_query_string:
            url = f"{url}?{url_query_string}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            **_signature_headers(self.config, path, query_string, body_text),
        }
        if self.config.locale:
            headers["Cookie"] = f"locale={self.config.locale}"
        response = self.session.request(
            method.upper(),
            url,
            data=body_text.encode("utf-8") if body_text else None,
            headers=headers,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
        )
        return _result(action or path.rsplit("/", 1)[-1], response)


def get_client() -> ATrustClient:
    return ATrustClient(resolve_config())


def _nested_dict(args: dict[str, Any], key: str) -> dict[str, Any]:
    value = args.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _extras(args: dict[str, Any]) -> dict[str, Any]:
    ignored = {"action", "method", "path", "query", "body"}
    return {key: value for key, value in args.items() if key not in ignored and value is not None}


def _query_from_args(args: dict[str, Any]) -> dict[str, Any]:
    query = _nested_dict(args, "query")
    for key, value in _extras(args).items():
        query.setdefault(key, value)
    return query


def _body_from_args(args: dict[str, Any]) -> Any:
    if isinstance(args.get("body"), dict):
        return dict(args["body"])
    extras = _extras(args)
    return extras or None


def _call_rest(method: str, path: str, args: dict[str, Any], *, action: str) -> ToolResult:
    method = method.upper()
    explicit_query = _nested_dict(args, "query")
    if method == "GET":
        query = explicit_query
        for key, value in _extras(args).items():
            query.setdefault(key, value)
        body = args.get("body") if args.get("body") is not None else None
    else:
        query = explicit_query
        body = _body_from_args(args)
    return get_client().request(method, path, query=query, body=body, action=action)


READONLY_ACTIONS: dict[str, tuple[str, str]] = {
    "online_users": ("GET", "/api/v1/monitor/getUserStatus"),
    "user_list": ("POST", "/api/v3/user/queryAll"),
    "user_by_id": ("GET", "/api/v3/user/queryById"),
    "user_by_name": ("GET", "/api/v3/user/queryByName"),
    "user_by_external_id": ("GET", "/api/v3/user/queryByExternalId"),
    "group_list": ("POST", "/api/v3/group/queryAll"),
    "group_by_id": ("GET", "/api/v3/group/queryById"),
    "group_by_full_path": ("GET", "/api/v3/group/queryByFullPath"),
    "group_by_external_id": ("GET", "/api/v3/group/queryByExternalId"),
    "role_list": ("POST", "/api/v3/role/queryAll"),
    "role_by_id": ("GET", "/api/v3/role/queryById"),
    "role_by_name": ("GET", "/api/v3/role/queryByName"),
    "role_by_external_id": ("GET", "/api/v3/role/queryByExternalId"),
    "resource_list": ("GET", "/api/v3/resource/queryAll"),
    "resource_by_id": ("GET", "/api/v3/resource/queryById"),
    "resource_by_name": ("GET", "/api/v3/resource/queryByName"),
    "resource_group_list": ("GET", "/api/v3/resourceGroup/queryAll"),
    "resource_assignment_by_id": ("POST", "/api/v3/resourceAssign/queryById"),
    "resource_assignment_by_name": ("POST", "/api/v3/resourceAssign/queryByName"),
    "resource_group_assignment_by_id": ("POST", "/api/v3/resourceGroupAssign/queryById"),
    "resource_group_assignment_by_name": ("POST", "/api/v3/resourceGroupAssign/queryByName"),
    "node_group_list": ("GET", "/api/v1/nodeGroup/queryAll"),
    "auth_server_list": ("GET", "/api/v1/authServer/queryAll"),
    "auth_server_detail": ("GET", "/api/v1/authServer/query"),
    "user_directory_list": ("GET", "/api/v1/userDirectory/queryAll"),
    "user_directory_detail": ("GET", "/api/v1/userDirectory/query"),
    "endpoint_list": ("POST", "/api/v1/device/queryAll"),
    "endpoint_detail": ("GET", "/api/v1/device/query"),
}


MONITOR_ACTIONS = {"online_users"}
IDENTITY_ACTIONS = {
    "user_list",
    "user_by_id",
    "user_by_name",
    "user_by_external_id",
    "group_list",
    "group_by_id",
    "group_by_full_path",
    "group_by_external_id",
    "role_list",
    "role_by_id",
    "role_by_name",
    "role_by_external_id",
    "auth_server_list",
    "auth_server_detail",
    "user_directory_list",
    "user_directory_detail",
}
RESOURCE_ACTIONS = {
    "resource_list",
    "resource_by_id",
    "resource_by_name",
    "resource_group_list",
    "resource_assignment_by_id",
    "resource_assignment_by_name",
    "resource_group_assignment_by_id",
    "resource_group_assignment_by_name",
    "node_group_list",
}
ENDPOINT_ACTIONS = {"endpoint_list", "endpoint_detail"}


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
                "monitor": sorted(MONITOR_ACTIONS),
                "identity": sorted(IDENTITY_ACTIONS),
                "resource": sorted(RESOURCE_ACTIONS),
                "endpoint": sorted(ENDPOINT_ACTIONS),
            },
        },
        action="api_catalog",
    )


def rest_call_readonly(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method") or "GET").upper()
    path = str(args.get("path") or "").strip()
    if not path:
        raise ATrustError("path is required")
    if (method, path) not in _catalog_keys("readonly"):
        raise ATrustError("Only documented read-only aTrust OpenAPI paths are allowed")
    return _call_rest(method, path, args, action="rest_call_readonly")


def rest_call_mutation(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method") or "POST").upper()
    path = str(args.get("path") or "").strip()
    if not path:
        raise ATrustError("path is required")
    if (method, path) not in _catalog_keys("mutation"):
        raise ATrustError("Only documented mutation aTrust OpenAPI paths are allowed")
    return _call_rest(method, path, args, action="rest_call_mutation")


ACTION_HANDLERS: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
    "api_catalog": api_catalog,
    "rest_call_readonly": rest_call_readonly,
    "rest_call_mutation": rest_call_mutation,
}
for _action, (_method, _path) in READONLY_ACTIONS.items():
    ACTION_HANDLERS[_action] = (
        lambda args, method=_method, path=_path, action=_action: _call_rest(method, path, args, action=action)
    )


async def _dispatch(ctx: ToolContext, allowed: set[str], action: str, **params: Any) -> ToolResult:
    del ctx
    if action == "test":
        for fallback in ("auth_server_list", "online_users", "resource_list", "endpoint_list"):
            if fallback in allowed:
                action = fallback
                break
    if action not in allowed:
        return ToolResult(
            success=False,
            error=f"Unsupported Sangfor aTrust action: {action}. Available: {', '.join(sorted(allowed))}",
        )
    try:
        return await asyncio.to_thread(ACTION_HANDLERS[action], params)
    except ATrustError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"source": "Sangfor aTrust", "version": PRODUCT_VERSION, "action": action},
        )
    except requests.RequestException as exc:
        return ToolResult(
            success=False,
            error=f"aTrust request failed: {exc}",
            metadata={"source": "Sangfor aTrust", "version": PRODUCT_VERSION, "action": action},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Unexpected Sangfor aTrust error: {exc}",
            metadata={"source": "Sangfor aTrust", "version": PRODUCT_VERSION, "action": action},
        )


async def monitor(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, MONITOR_ACTIONS | {"test"}, action, **params)


async def identity(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, IDENTITY_ACTIONS | {"test"}, action, **params)


async def resource(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, RESOURCE_ACTIONS | {"test"}, action, **params)


async def endpoint(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, ENDPOINT_ACTIONS | {"test"}, action, **params)


async def api_readonly(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(
        ctx,
        {"api_catalog", "rest_call_readonly", *READONLY_ACTIONS.keys(), "test"},
        action,
        **params,
    )


async def api_mutation(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch(ctx, {"api_catalog", "rest_call_mutation"}, action, **params)
