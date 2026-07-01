from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

SERVICE_ID = "onesandbox_api"
DEFAULT_TIMEOUT = 120


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


def _ensure_scheme(value: str) -> str:
    if value and not value.startswith(("http://", "https://")):
        return "https://" + value
    return value


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _runtime_config() -> tuple[str, str, int, bool]:
    raw = _service_config()
    base_url = _ensure_scheme(
        (
            _resolve_ref(raw.get("base_url"))
            or _resolve_ref(raw.get("baseUrl"))
            or os.getenv("ONESANDBOX_BASE_URL")
            or ""
        ).rstrip("/")
    )
    apikey = (
        _resolve_ref(raw.get("apikey"))
        or _resolve_ref(raw.get("api_key"))
        or _resolve_ref(raw.get("apiKey"))
        or _get_secret_manager().get("onesandbox_apikey")
        or os.getenv("ONESANDBOX_APIKEY")
        or ""
    )
    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    verify_ssl = _bool_value(raw.get("verify_ssl"), False)
    custom_settings = raw.get("custom_settings")
    if isinstance(custom_settings, dict):
        verify_ssl = _bool_value(custom_settings.get("verify_ssl"), verify_ssl)
    return base_url, apikey, timeout, verify_ssl


def _output_dir() -> Path:
    today = dt.datetime.now().strftime("%Y-%m-%d")
    path = Path.home() / ".flocks" / "workspace" / "outputs" / today
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(value: str) -> str:
    keep = []
    for char in value:
        keep.append(char if char.isalnum() or char in {".", "-", "_"} else "_")
    return "".join(keep).strip("._") or "onesandbox_download"


def _query_params(apikey: str, params: dict[str, Any], *keys: str) -> dict[str, Any]:
    query: dict[str, Any] = {"apikey": apikey}
    for key in keys:
        value = params.get(key)
        if value is not None and value != "":
            query[key] = value
    return query


def _body_from_params(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    body = params.get("body")
    if isinstance(body, dict):
        return body
    return {
        key: params[key]
        for key in keys
        if key in params and params[key] is not None
    }


def _json_result(action: str, data: Any) -> ToolResult:
    metadata = {"source": "OneSandbox", "action": action}
    if isinstance(data, dict):
        code = data.get("response_code")
        if code is not None and code not in (0, "0"):
            return ToolResult(
                success=False,
                error=f"OneSandbox API error (code={code}): {data.get('verbose_msg') or data}",
                metadata=metadata,
            )
        return ToolResult(success=True, output=data.get("data", data), metadata=metadata)
    return ToolResult(success=True, output=data, metadata=metadata)


async def _json_request(
    action: str,
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    auth_required: bool = True,
) -> ToolResult:
    base_url, apikey, timeout, verify_ssl = _runtime_config()
    if not base_url:
        return ToolResult(success=False, error="OneSandbox base_url is not configured.")
    if auth_required and not apikey:
        return ToolResult(success=False, error="OneSandbox apikey is required.")
    url = f"{base_url}{path}"
    params = dict(query or {})
    if auth_required:
        params.setdefault("apikey", apikey)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.request(
                method,
                url,
                params=params,
                json=body if method not in {"GET", "DELETE"} else None,
                headers={"Content-Type": "application/json"},
                ssl=verify_ssl,
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    return ToolResult(success=False, error=f"HTTP {resp.status}: {text[:500]}")
                if text.strip() == "OK":
                    return ToolResult(success=True, output={"status": "OK"}, metadata={"source": "OneSandbox", "action": action})
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    return ToolResult(success=True, output=text, metadata={"source": "OneSandbox", "action": action})
    except aiohttp.ClientError as exc:
        return ToolResult(success=False, error=f"Request failed: {exc}")
    return _json_result(action, data)


async def _download_request(action: str, params: dict[str, Any]) -> ToolResult:
    base_url, apikey, timeout, verify_ssl = _runtime_config()
    if not base_url:
        return ToolResult(success=False, error="OneSandbox base_url is not configured.")
    if not apikey:
        return ToolResult(success=False, error="OneSandbox apikey is required.")
    query = _query_params(apikey, params, "sha256", "md5", "sha1", "sandbox_type", "type")
    if len(query) == 1:
        return ToolResult(success=False, error="download_file_report requires at least one of sha256, md5, or sha1.")
    report_type = str(params.get("type") or "report")
    target_name = _safe_filename(str(params.get("output_filename") or f"onesandbox_{report_type}_{query.get('sha256') or query.get('md5') or query.get('sha1')}.bin"))
    target = _output_dir() / target_name
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(f"{base_url}/v3/file/report", params=query, ssl=verify_ssl) as resp:
                content = await resp.read()
                if resp.status >= 400:
                    return ToolResult(success=False, error=f"HTTP {resp.status}: {content[:500].decode(errors='replace')}")
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type.lower():
                    try:
                        return _json_result(action, await resp.json(content_type=None))
                    except Exception:
                        pass
        target.write_bytes(content)
    except aiohttp.ClientError as exc:
        return ToolResult(success=False, error=f"Request failed: {exc}")
    return ToolResult(
        success=True,
        output={"file_path": str(target), "bytes": target.stat().st_size},
        metadata={"source": "OneSandbox", "action": action},
    )


async def _upload_file(params: dict[str, Any]) -> ToolResult:
    base_url, apikey, timeout, verify_ssl = _runtime_config()
    if not base_url:
        return ToolResult(success=False, error="OneSandbox base_url is not configured.")
    if not apikey:
        return ToolResult(success=False, error="OneSandbox apikey is required.")
    file_path = params.get("file_path")
    if not file_path:
        return ToolResult(success=False, error="upload_file requires file_path.")
    path = Path(str(file_path)).expanduser()
    if not path.exists() or not path.is_file():
        return ToolResult(success=False, error=f"file_path does not exist: {path}")

    query = _query_params(apikey, params, "filename", "password", "run_time", "mode")
    form = aiohttp.FormData()
    form.add_field(
        "file",
        path.open("rb"),
        filename=str(params.get("filename") or path.name),
        content_type="application/octet-stream",
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(f"{base_url}/v3/file/upload", params=query, data=form, ssl=verify_ssl) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    return ToolResult(success=False, error=f"HTTP {resp.status}: {text[:500]}")
                data = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        return ToolResult(success=False, error=f"Request failed: {exc}")
    finally:
        for field in getattr(form, "_fields", []):
            value = field[2]
            if hasattr(value, "close"):
                value.close()
    return _json_result("upload_file", data)


async def query(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    del ctx
    _, apikey, _, _ = _runtime_config()
    if action == "readyz":
        return await _json_request(action, "GET", "/health/readyz", auth_required=False)
    if action == "livez":
        return await _json_request(action, "GET", "/health/livez", auth_required=False)
    if action == "api_version":
        return await _json_request(action, "GET", "/api/version", auth_required=False)
    if action == "file_report":
        return await _json_request(action, "GET", "/v3/file/report", _query_params(apikey, params, "sha256", "md5", "sha1", "query_fields"))
    if action == "file_queue":
        return await _json_request(action, "GET", "/v3/file/queue", _query_params(apikey, params))
    if action == "download_file_report":
        return await _download_request(action, params)
    if action == "hash_reputation":
        return await _json_request(action, "GET", "/v3/hash/reputation", _query_params(apikey, params, "sha256", "md5", "sha1"))
    if action == "sdk_policy_get":
        return await _json_request(action, "GET", "/v3/sdk/policy", _query_params(apikey, params))
    if action == "safeskill_report":
        return await _json_request(action, "GET", "/v3/safeskill/report", _query_params(apikey, params, "sha256", "md5", "sha1", "task_id"))
    if action == "checkurl_result":
        task_id = params.get("task_id")
        if not task_id:
            return ToolResult(success=False, error="checkurl_result requires task_id.")
        return await _json_request(action, "GET", f"/v3/checkurl/result/{task_id}", _query_params(apikey, params))
    if action in {"checkurl_report", "checkurl_summary"}:
        uuid = params.get("uuid")
        if not uuid:
            return ToolResult(success=False, error=f"{action} requires uuid.")
        path = f"/v3/checkurl/{'summary' if action == 'checkurl_summary' else 'report'}/{uuid}"
        return await _json_request(action, "GET", path, _query_params(apikey, params, "summary"))
    return ToolResult(success=False, error=f"Unsupported OneSandbox query action: {action}")


async def ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    del ctx
    _, apikey, _, _ = _runtime_config()
    if action == "upload_file":
        return await _upload_file(params)
    if action == "delete_file_report":
        return await _json_request(action, "DELETE", "/v3/file/delete", _query_params(apikey, params, "sha256", "md5", "sha1"))
    if action == "upload_event":
        body = _body_from_params(params, "events", "file_name", "file_size", "sha256", "md5", "sha1", "source", "event_time")
        return await _json_request(action, "POST", "/v3/event/upload", _query_params(apikey, params), body)
    if action == "hash_reputation_set":
        body = _body_from_params(params, "hash", "white", "threat", "classify", "description")
        return await _json_request(action, "POST", "/v3/hash/reputation", _query_params(apikey, params), body)
    if action == "hash_reputation_delete":
        return await _json_request(action, "DELETE", "/v3/hash/reputation", _query_params(apikey, params, "sha256", "md5", "sha1"))
    if action == "safeskill_scan":
        body = _body_from_params(params, "sha256", "md5", "sha1", "url", "file_name", "file_type")
        return await _json_request(action, "POST", "/v3/safeskill/scan", _query_params(apikey, params), body)
    if action == "checkurl_scan":
        body = _body_from_params(params, "url")
        return await _json_request(action, "POST", "/v3/checkurl/scan", _query_params(apikey, params), body)
    return ToolResult(success=False, error=f"Unsupported OneSandbox ops action: {action}")
