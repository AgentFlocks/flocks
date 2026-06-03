from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
from typing import Any, Optional
from urllib.parse import urlencode, quote

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult


DEFAULT_REGION = "cn-north-4"
DEFAULT_TIMEOUT = 60
SERVICE_ID = "huaweicloud_waf_api"
WAF_SERVICE_NAME = "waf"


def _get_secret_manager():
    from flocks.security import get_secret_manager

    return get_secret_manager()


def _resolve_ref(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    if value.startswith("{secret:") and value.endswith("}"):
        return _get_secret_manager().get(value[len("{secret:"):-1])
    if value.startswith("{env:") and value.endswith("}"):
        return os.getenv(value[len("{env:"):-1])
    return value


def _service_config() -> dict[str, Any]:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    return raw if isinstance(raw, dict) else {}


def _resolve_verify_ssl(raw: dict[str, Any]) -> bool:
    value = raw.get("verify_ssl") or raw.get("ssl_verify")
    if value is None:
        value = raw.get("custom_settings", {}).get("verify_ssl", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class _WAFConfig:
    def __init__(
        self,
        ak: Optional[str],
        sk: Optional[str],
        token: Optional[str],
        region: str,
        project_id: str,
        enterprise_project_id: Optional[str],
        timeout: int,
        verify_ssl: bool,
    ) -> None:
        self.ak = ak
        self.sk = sk
        self.token = token
        self.region = region
        self.project_id = project_id
        self.enterprise_project_id = enterprise_project_id
        self.timeout = timeout
        self.verify_ssl = verify_ssl

    @property
    def endpoint(self) -> str:
        return f"https://waf.{self.region}.myhuaweicloud.com"

    def use_aksk(self) -> bool:
        return bool(self.ak and self.sk)


def _load_config(param_epid: Optional[str] = None) -> _WAFConfig:
    raw = _service_config()
    secret_manager = _get_secret_manager()

    ak = (
        _resolve_ref(raw.get("ak"))
        or secret_manager.get("huaweicloud_waf_ak")
        or os.getenv("HUAWEICLOUD_WAF_AK")
    )
    sk = (
        _resolve_ref(raw.get("sk"))
        or secret_manager.get("huaweicloud_waf_sk")
        or os.getenv("HUAWEICLOUD_WAF_SK")
    )
    token = (
        _resolve_ref(raw.get("token"))
        or secret_manager.get("huaweicloud_waf_token")
        or os.getenv("HUAWEICLOUD_WAF_TOKEN")
    )
    region = _resolve_ref(raw.get("region")) or DEFAULT_REGION
    project_id = _resolve_ref(raw.get("project_id")) or os.getenv("HUAWEICLOUD_PROJECT_ID", "")
    if not project_id:
        raise ValueError(
            "Huawei Cloud WAF: project_id is required. "
            "Configure it in the huaweicloud_waf_api service settings."
        )
    if not ak and not token:
        raise ValueError(
            "Huawei Cloud WAF credentials not found. Configure ak/sk or token "
            "in the huaweicloud_waf_api service settings."
        )
    enterprise_project_id = (
        param_epid
        or _resolve_ref(raw.get("enterprise_project_id"))
        or os.getenv("HUAWEICLOUD_ENTERPRISE_PROJECT_ID")
    )
    timeout = int(raw.get("timeout", DEFAULT_TIMEOUT))
    return _WAFConfig(
        ak=ak,
        sk=sk,
        token=token,
        region=region,
        project_id=project_id,
        enterprise_project_id=enterprise_project_id,
        timeout=timeout,
        verify_ssl=_resolve_verify_ssl(raw),
    )


# ---------------------------------------------------------------------------
# Huawei Cloud SDK-HMAC-SHA256 signing (AK/SK auth)
# Reference: https://support.huaweicloud.com/api-dew/dew_02_0008.html
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _build_aksk_headers(
    ak: str,
    sk: str,
    method: str,
    host: str,
    uri: str,
    query_string: str,
    body: bytes,
) -> dict[str, str]:
    """
    Build Huawei Cloud SDK-HMAC-SHA256 signed headers.

    Authorization = SDK-HMAC-SHA256 Access={ak},
                    SignedHeaders={signed_headers}, Signature={signature}
    """
    now = datetime.datetime.utcnow()
    x_sdk_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    payload_hash = _sha256_hex(body)
    headers_to_sign = {
        "content-type": "application/json;charset=utf8",
        "host": host,
        "x-sdk-date": x_sdk_date,
    }
    signed_headers = ";".join(sorted(headers_to_sign.keys()))
    canonical_headers = "".join(
        f"{k}:{v}\n" for k, v in sorted(headers_to_sign.items())
    )
    canonical_uri = uri if uri else "/"
    canonical_request = "\n".join([
        method.upper(),
        canonical_uri,
        query_string,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    credential_scope = f"{date_stamp}/{WAF_SERVICE_NAME}/sdk_request"
    string_to_sign = "\n".join([
        "SDK-HMAC-SHA256",
        x_sdk_date,
        credential_scope,
        _sha256_hex(canonical_request.encode("utf-8")),
    ])
    signing_key = _hmac_sha256(
        _hmac_sha256(
            _hmac_sha256(
                _hmac_sha256(
                    ("SDK" + sk).encode("utf-8"),
                    date_stamp,
                ),
                WAF_SERVICE_NAME,
            ),
            "sdk_request",
        ),
        "sdk_signing",
    )
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"SDK-HMAC-SHA256 Access={ak}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return {
        "Content-Type": "application/json;charset=utf8",
        "X-Sdk-Date": x_sdk_date,
        "Authorization": authorization,
    }


async def _request(
    cfg: _WAFConfig,
    method: str,
    path: str,
    query: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
) -> ToolResult:
    query = query or {}
    if cfg.enterprise_project_id:
        query.setdefault("enterprise_project_id", cfg.enterprise_project_id)
    qs = urlencode({k: v for k, v in query.items() if v is not None})
    url = f"{cfg.endpoint}{path}"
    if qs:
        url = f"{url}?{qs}"

    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else b""
    host = f"waf.{cfg.region}.myhuaweicloud.com"

    if cfg.use_aksk():
        headers = _build_aksk_headers(
            cfg.ak,
            cfg.sk,
            method,
            host,
            path,
            qs,
            body_bytes,
        )
    else:
        headers = {
            "Content-Type": "application/json;charset=utf8",
            "X-Auth-Token": cfg.token,
        }

    connector = aiohttp.TCPConnector(ssl=cfg.verify_ssl)
    async with aiohttp.ClientSession(connector=connector) as session:
        req_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": aiohttp.ClientTimeout(total=cfg.timeout),
        }
        if body_bytes:
            req_kwargs["data"] = body_bytes
        async with session.request(method, url, **req_kwargs) as resp:
            resp_text = await resp.text()
            try:
                resp_json = json.loads(resp_text)
            except Exception:
                resp_json = {"raw": resp_text}
            if resp.status >= 400:
                return ToolResult(
                    success=False,
                    data=resp_json,
                    error=f"HTTP {resp.status}: {resp_text[:300]}",
                )
            return ToolResult(success=True, data=resp_json)


def _pick(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: params[k] for k in keys if k in params and params[k] is not None}


def _page_query(params: dict[str, Any]) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if "page" in params and params["page"] is not None:
        q["page"] = params["page"]
    if "pagesize" in params and params["pagesize"] is not None:
        q["pagesize"] = params["pagesize"]
    return q


# ---------------------------------------------------------------------------
# Tool handler functions
# ---------------------------------------------------------------------------


async def host(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    cfg = _load_config(params.get("enterprise_project_id"))
    pid = cfg.project_id
    action = params.get("action", "")

    if action == "host_list":
        q = _page_query(params)
        if params.get("hostname"):
            q["hostname"] = params["hostname"]
        if params.get("policyname"):
            q["policyname"] = params["policyname"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/instance", query=q)

    if action == "host_show":
        iid = params["instance_id"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/instance/{iid}")

    if action == "host_create":
        body = _pick(params, "hostname", "proxy", "server", "certificateid")
        return await _request(cfg, "POST", f"/v1/{pid}/waf/instance", body=body)

    if action == "host_update":
        iid = params["instance_id"]
        body = _pick(params, "proxy", "server", "certificateid", "protect_status")
        return await _request(cfg, "PATCH", f"/v1/{pid}/waf/instance/{iid}", body=body)

    if action == "host_delete":
        iid = params["instance_id"]
        return await _request(cfg, "DELETE", f"/v1/{pid}/waf/instance/{iid}")

    if action == "host_update_protect_status":
        iid = params["instance_id"]
        body = _pick(params, "protect_status")
        return await _request(cfg, "PUT", f"/v1/{pid}/waf/instance/{iid}/protect-status", body=body)

    if action == "premium_host_list":
        q = _page_query(params)
        if params.get("hostname"):
            q["hostname"] = params["hostname"]
        if params.get("policyname"):
            q["policyname"] = params["policyname"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/premium-host", query=q)

    if action == "premium_host_show":
        iid = params["instance_id"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/premium-host/{iid}")

    if action == "premium_host_create":
        body = _pick(params, "hostname", "proxy", "server", "web_tag", "certificateid")
        return await _request(cfg, "POST", f"/v1/{pid}/waf/premium-host", body=body)

    if action == "premium_host_update":
        iid = params["instance_id"]
        body = _pick(params, "proxy", "server", "protect_status", "certificateid")
        return await _request(cfg, "PATCH", f"/v1/{pid}/waf/premium-host/{iid}", body=body)

    if action == "premium_host_delete":
        iid = params["instance_id"]
        return await _request(cfg, "DELETE", f"/v1/{pid}/waf/premium-host/{iid}")

    if action == "composite_host_list":
        q = _page_query(params)
        if params.get("hostname"):
            q["hostname"] = params["hostname"]
        return await _request(cfg, "GET", f"/v1/{pid}/composite-waf/host", query=q)

    return ToolResult(success=False, error=f"Unknown action: {action}")


async def policy(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    cfg = _load_config(params.get("enterprise_project_id"))
    pid = cfg.project_id
    action = params.get("action", "")

    if action == "policy_list":
        q = _page_query(params)
        if params.get("name"):
            q["name"] = params["name"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/policy", query=q)

    if action == "policy_show":
        pol_id = params["policy_id"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/policy/{pol_id}")

    if action == "policy_create":
        body = _pick(params, "name", "level", "full_detection")
        return await _request(cfg, "POST", f"/v1/{pid}/waf/policy", body=body)

    if action == "policy_update":
        pol_id = params["policy_id"]
        body = _pick(params, "name", "level", "full_detection", "options")
        return await _request(cfg, "PATCH", f"/v1/{pid}/waf/policy/{pol_id}", body=body)

    if action == "policy_delete":
        pol_id = params["policy_id"]
        return await _request(cfg, "DELETE", f"/v1/{pid}/waf/policy/{pol_id}")

    if action == "policy_update_hosts":
        pol_id = params["policy_id"]
        body = _pick(params, "hosts")
        return await _request(cfg, "PUT", f"/v1/{pid}/waf/policy/{pol_id}/hosts", body=body)

    if action == "cc_rule_list":
        pol_id = params["policy_id"]
        q = _page_query(params)
        return await _request(cfg, "GET", f"/v1/{pid}/waf/policy/{pol_id}/cc", query=q)

    if action == "cc_rule_create":
        pol_id = params["policy_id"]
        body = _pick(params, "url", "limit_num", "limit_period", "lock_time", "tag_type", "action")
        return await _request(cfg, "POST", f"/v1/{pid}/waf/policy/{pol_id}/cc", body=body)

    if action == "cc_rule_delete":
        pol_id = params["policy_id"]
        rule_id = params["rule_id"]
        return await _request(cfg, "DELETE", f"/v1/{pid}/waf/policy/{pol_id}/cc/{rule_id}")

    if action == "custom_rule_list":
        pol_id = params["policy_id"]
        q = _page_query(params)
        return await _request(cfg, "GET", f"/v1/{pid}/waf/policy/{pol_id}/custom", query=q)

    if action == "custom_rule_create":
        pol_id = params["policy_id"]
        body = _pick(params, "name", "conditions", "action", "priority", "description")
        return await _request(cfg, "POST", f"/v1/{pid}/waf/policy/{pol_id}/custom", body=body)

    if action == "custom_rule_delete":
        pol_id = params["policy_id"]
        rule_id = params["rule_id"]
        return await _request(cfg, "DELETE", f"/v1/{pid}/waf/policy/{pol_id}/custom/{rule_id}")

    if action == "whiteblackip_rule_list":
        pol_id = params["policy_id"]
        q = _page_query(params)
        return await _request(cfg, "GET", f"/v1/{pid}/waf/policy/{pol_id}/whiteblackip", query=q)

    if action == "whiteblackip_rule_create":
        pol_id = params["policy_id"]
        body = _pick(params, "addr", "white", "description")
        return await _request(cfg, "POST", f"/v1/{pid}/waf/policy/{pol_id}/whiteblackip", body=body)

    if action == "whiteblackip_rule_delete":
        pol_id = params["policy_id"]
        rule_id = params["rule_id"]
        return await _request(cfg, "DELETE", f"/v1/{pid}/waf/policy/{pol_id}/whiteblackip/{rule_id}")

    if action == "geoip_rule_list":
        pol_id = params["policy_id"]
        q = _page_query(params)
        return await _request(cfg, "GET", f"/v1/{pid}/waf/policy/{pol_id}/geoip", query=q)

    return ToolResult(success=False, error=f"Unknown action: {action}")


async def event(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    cfg = _load_config(params.get("enterprise_project_id"))
    pid = cfg.project_id
    action = params.get("action", "")

    if action == "event_list":
        q = _page_query(params)
        for k in ("from", "to", "hosts", "attacks", "action"):
            if params.get(k) is not None:
                q[k] = params[k]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/event/attack/logs", query=q)

    if action == "event_show":
        eid = params["eventid"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/event/attack/logs/{eid}")

    if action == "event_log_download":
        q: dict[str, Any] = {}
        for k in ("from", "to"):
            if params.get(k) is not None:
                q[k] = params[k]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/event/attack/log/download", query=q)

    if action == "event_export_job":
        body = {}
        for k in ("from", "to", "hosts", "attacks", "action"):
            if params.get(k) is not None:
                body[k] = params[k]
        return await _request(cfg, "POST", f"/v1/{pid}/waf/event/attack/log/job", body=body)

    if action == "threat_distribution":
        q = {}
        for k in ("from", "to", "hosts"):
            if params.get(k) is not None:
                q[k] = params[k]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/attack/types", query=q)

    if action == "top_url":
        q = {}
        for k in ("from", "to", "hosts", "top"):
            if params.get(k) is not None:
                q[k] = params[k]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/attack/top/url", query=q)

    if action == "top_source_ip":
        q = {}
        for k in ("from", "to", "hosts", "top"):
            if params.get(k) is not None:
                q[k] = params[k]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/attack/top/source", query=q)

    return ToolResult(success=False, error=f"Unknown action: {action}")


async def overview(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    cfg = _load_config(params.get("enterprise_project_id"))
    pid = cfg.project_id
    action = params.get("action", "")

    def _time_query() -> dict[str, Any]:
        q: dict[str, Any] = {}
        for k in ("from", "to", "hosts"):
            if params.get(k) is not None:
                q[k] = params[k]
        return q

    if action == "overview_statistics":
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/statistics", query=_time_query())

    if action == "overview_qps":
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/statistics/qps", query=_time_query())

    if action == "overview_bandwidth":
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/statistics/bandwidth", query=_time_query())

    if action == "overview_top_domains":
        q = _time_query()
        if params.get("top"):
            q["top"] = params["top"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/attack/top/host", query=q)

    if action == "overview_attack_types":
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/attack/types", query=_time_query())

    if action == "overview_top_ip":
        q = _time_query()
        if params.get("top"):
            q["top"] = params["top"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/attack/top/source", query=q)

    if action == "overview_top_url":
        q = _time_query()
        if params.get("top"):
            q["top"] = params["top"]
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/attack/top/url", query=q)

    if action == "overview_response_code":
        return await _request(cfg, "GET", f"/v1/{pid}/waf/overviews/statistics/response_code", query=_time_query())

    if action == "console_config":
        return await _request(cfg, "GET", f"/v1/{pid}/waf/config/console")

    return ToolResult(success=False, error=f"Unknown action: {action}")
