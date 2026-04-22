"""
Sangfor XDR Open API handler.

Auth: HMAC-SHA256 signature derived from auth_code (联动码).
The auth_code is an AES-CBC encrypted bundle containing AK/SK.
Each request is signed with the SK and carries the AK in the Authorization header.

Endpoints covered (v2.0.21):
  /api/xdr/v1/alerts/*          - 安全告警
  /api/xdr/v1/incidents/*       - 安全事件
  /api/xdr/v1/responses/*       - 响应管理
  /api/xdr/v1/whitelists/*      - 白名单管理
  /api/xdr/v1/assets/*          - 资产管理
  /api/xdr/v1/vuls/*            - 脆弱性管理
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse, urlencode, quote

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

SERVICE_ID = "sangfor_xdr"
DEFAULT_PORT = 443
DEFAULT_TIMEOUT = 60

EXTEND_HEADER = "algorithm=HMAC-SHA256, Access=%s, SignedHeaders=%s, Signature=%s"
TOTAL_STR = "HMAC-SHA256\n%s\n%s"
AUTH_HEADER_KEY = "Authorization"
SDK_HOST_KEY = "sdk-host"
CONTENT_TYPE_KEY = "content-type"
SDK_CONTENT_TYPE_KEY = "sdk-content-type"
DEFAULT_CONTENT_TYPE = "application/json"
SIGN_DATE_KEY = "sign-date"
AUTH_CODE_PARAMS = "%s+%s+%s+%s+%s+%s+%s+%s"
AUTH_CODE_PARAMS_NUM = 14
AUTH_INFO_MAP_SIZE = 4
MAP_STRING_SIZE = 2

_AK_SK_CACHE: dict[str, tuple[str, str]] = {}


# ── AK/SK decode from auth_code ──────────────────────────────────────────────

def _reverse_hex(auth_code: str) -> bytes:
    return binascii.unhexlify(auth_code)


def _calculate_aes_secret(builders: list[str]) -> bytes:
    build_str = AUTH_CODE_PARAMS % (
        builders[0], builders[1], builders[2], builders[3],
        builders[4], builders[5], builders[6], builders[11],
    )
    return hashlib.sha256(build_str.encode("utf-8")).digest()


def _aes_cbc_decrypt(cipher_text: str, key: bytes) -> str:
    """Decrypt one AK/SK ciphertext slot from the auth_code.

    Mirrors the official Sangfor demo (``aksk_py3.Signature.__aes_cbc_decrypt``)
    which uses AES-CBC **decryption** with a zero IV.  An earlier version of
    this file accidentally used ``cipher.encryptor()`` which silently produced
    garbage AK/SK and made every signed request fail with
    ``access key not exist`` / ``Full ak/sk authentication is required``.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    backend = default_backend()
    cipher = Cipher(algorithms.AES(key), modes.CBC(bytearray(16)), backend=backend)
    decryptor = cipher.decryptor()
    pt = decryptor.update(bytes.fromhex(cipher_text)) + decryptor.finalize()
    # Sangfor SDK pads ciphertext with NUL bytes; the AK/SK plaintext is
    # always ASCII hex once decrypted correctly.
    return pt.rstrip(b"\x00").decode("utf-8")


def _decode_auth_code(auth_code: str) -> tuple[str, str]:
    cached = _AK_SK_CACHE.get(auth_code)
    if cached:
        return cached
    if not auth_code:
        raise ValueError("auth_code is empty")
    cleaned = auth_code.strip()
    # Reject obviously non-hex inputs early with a clear message instead of
    # the cryptic ``binascii.Error: Non-hexadecimal digit found``.
    try:
        builder_bytes = _reverse_hex(cleaned)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            "auth_code is not a valid hex string. Please copy the 联动码 "
            "from XDR (配置管理 → 系统设置 → 开放性 → 联动码管理)."
        ) from exc
    try:
        builder_str = builder_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "auth_code decoded bytes are not valid UTF-8 — likely a wrong or "
            "truncated 联动码."
        ) from exc
    builders = builder_str.split("|")
    if len(builders) != AUTH_CODE_PARAMS_NUM:
        raise ValueError(
            f"auth_code decode error: expected {AUTH_CODE_PARAMS_NUM} parts, "
            f"got {len(builders)}"
        )
    aes_secret = _calculate_aes_secret(builders)
    ak = _aes_cbc_decrypt(builders[9], aes_secret)
    sk = _aes_cbc_decrypt(builders[10], aes_secret)
    _AK_SK_CACHE[auth_code] = (ak, sk)
    return ak, sk


# ── HMAC-SHA256 signing ──────────────────────────────────────────────────────

def _sha256_hex_upper(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _hmac_sha256_hex(secret_key: str, data: str) -> str:
    mac = hmac.new(secret_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256)
    return mac.digest().hex().upper()


def _remove_spaces(b: bytearray) -> bytearray:
    j = 0
    for i in range(len(b)):
        if b[i] != 32:
            if i != j:
                b[j] = b[i]
            j += 1
    return b[:j]


def _payload_transform(payload: str) -> str:
    encoded = payload.encode("utf-8")
    byte_values = sorted(encoded)
    new_payload = bytearray(byte_values)
    new_payload = _remove_spaces(new_payload)
    return _sha256_hex_upper(bytes(new_payload))


def _url_transform(url_str: str) -> str:
    parsed = urlparse(url_str)
    path = parsed.path
    if not path.endswith("/"):
        path += "/"
    return quote(path)


def _sign_request(
    ak: str,
    sk: str,
    method: str,
    url: str,
    headers: dict[str, str],
    params: Optional[dict[str, Any]] = None,
    payload: str = "",
) -> dict[str, str]:
    parsed = urlparse(url)
    host = parsed.netloc

    if SDK_HOST_KEY not in headers:
        headers[SDK_HOST_KEY] = host
    if CONTENT_TYPE_KEY not in headers:
        headers[SDK_CONTENT_TYPE_KEY] = DEFAULT_CONTENT_TYPE
    else:
        headers[SDK_CONTENT_TYPE_KEY] = headers[CONTENT_TYPE_KEY]
    sign_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    headers[SIGN_DATE_KEY] = sign_date

    header_keys = sorted(headers.items(), key=lambda x: x[0].lower())
    header_str = "".join(f"{k}:{v}\n" for k, v in header_keys)
    sign_header_keys = ";".join(k for k, _ in header_keys)

    # Match the official demo's ``__query_str_transform``: keys are sorted
    # before urlencoding so the canonical request is deterministic regardless
    # of the dict iteration order on the client side.
    canonical_query = ""
    if params:
        sorted_items = sorted(params.items(), key=lambda kv: kv[0])
        canonical_query = urlencode(sorted_items)

    canonical_parts = [
        method.upper(),
        "\n",
        _url_transform(url),
        "\n",
        canonical_query,
        "\n",
        header_str,
        sign_header_keys,
        "\n",
        _payload_transform(payload),
    ]
    canonical_str = "".join(canonical_parts)
    hashed_canonical = _sha256_hex_upper(canonical_str.encode("utf-8"))
    total_str = TOTAL_STR % (sign_date, hashed_canonical)
    signature = _hmac_sha256_hex(sk, total_str)

    headers[AUTH_HEADER_KEY] = EXTEND_HEADER % (ak, sign_header_keys, signature)
    return headers


# ── Config helpers ───────────────────────────────────────────────────────────

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


class RuntimeConfig:
    def __init__(self, base_url: str, timeout: int, auth_code: str, verify_ssl: bool):
        self.base_url = base_url
        self.timeout = timeout
        self.auth_code = auth_code
        self.verify_ssl = verify_ssl


def _resolve_runtime_config() -> RuntimeConfig:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    raw = raw if isinstance(raw, dict) else {}

    host = (_resolve_ref(raw.get("host")) or os.getenv("SANGFOR_XDR_HOST") or "").strip()
    # Tolerate users pasting the full URL (with scheme and/or trailing slash)
    # in the WebUI ``host`` field — without this normalisation we would build
    # ``https://https://10.0.0.1`` which silently routes to nowhere.
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.strip("/").strip()
    # The host field may also contain an inline port (``10.0.0.1:8443``).
    inline_port: Optional[int] = None
    if ":" in host and not host.startswith("["):
        host_part, _, port_part = host.partition(":")
        if port_part.isdigit():
            host = host_part
            inline_port = int(port_part)

    port_raw = raw.get("port") or inline_port or DEFAULT_PORT
    try:
        port = int(str(port_raw).strip())
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    base_url = f"https://{host}:{port}" if port != 443 else f"https://{host}"

    timeout_raw = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    sm = _get_secret_manager()
    auth_code = (
        _resolve_ref(raw.get("auth_code"))
        or sm.get("sangfor_xdr_auth_code")
        or sm.get(f"{SERVICE_ID}_auth_code")
        or os.getenv("SANGFOR_XDR_AUTH_CODE")
        or ""
    ).strip()

    verify_ssl_raw = raw.get("verify_ssl", "false")
    if isinstance(verify_ssl_raw, bool):
        verify_ssl = verify_ssl_raw
    else:
        verify_ssl = str(verify_ssl_raw).strip().lower() in {"1", "true", "yes", "on"}

    if not host:
        raise ValueError("Sangfor XDR host not configured. Set api_services.sangfor_xdr.host or SANGFOR_XDR_HOST.")
    if not auth_code:
        raise ValueError(
            "Sangfor XDR auth_code not configured. "
            "Set sangfor_xdr_auth_code secret or SANGFOR_XDR_AUTH_CODE env var."
        )
    return RuntimeConfig(base_url=base_url, timeout=timeout, auth_code=auth_code, verify_ssl=verify_ssl)


# ── Generic request executor ─────────────────────────────────────────────────

async def _request(
    cfg: RuntimeConfig,
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    data: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    ak, sk = _decode_auth_code(cfg.auth_code)
    url = f"{cfg.base_url}{path}"
    payload = json.dumps(data) if data else ""
    headers = {CONTENT_TYPE_KEY: DEFAULT_CONTENT_TYPE}
    headers = _sign_request(ak, sk, method, url, headers, params=params, payload=payload)
    # Ask the server not to compress the response — some XDR appliances ignore
    # ``Accept-Encoding`` negotiation and ship gzip bytes that aiohttp cannot
    # transparently decode on every code path, surfacing as
    # ``'utf-8' codec can't decode byte 0x8d in position 0``.
    headers.setdefault("Accept-Encoding", "identity")

    kwargs: dict[str, Any] = {"headers": headers}
    if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        kwargs["data"] = payload
    if params:
        kwargs["params"] = params

    async with session.request(method, url, **kwargs) as resp:
        raw_bytes = await resp.read()
        result = _parse_response_body(raw_bytes, resp.status)

    code = result.get("code")
    if code == "Success" or code == 0:
        return result
    raise RuntimeError(f"XDR API error: code={code}, message={result.get('message', '')}")


def _parse_response_body(raw: bytes, status: int) -> dict[str, Any]:
    """Decode an XDR response body with broad encoding tolerance.

    The Sangfor XDR appliance has been observed returning JSON encoded as
    UTF-8, GBK or even raw bytes that fail strict UTF-8 validation
    (``0x8d`` in position 0).  ``aiohttp`` defaults to UTF-8, which made
    every connectivity probe surface a misleading
    ``'utf-8' codec can't decode byte 0x8d`` error.
    """
    if not raw:
        raise RuntimeError(f"XDR returned empty body (HTTP {status})")
    last_error: Optional[Exception] = None
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict):
            return parsed
        return {"code": "Success", "data": parsed}
    snippet = raw[:120].hex()
    raise RuntimeError(
        f"XDR response parse error (HTTP {status}): could not decode body "
        f"(first bytes hex={snippet}, last={last_error})"
    )


async def _run_request(
    method: str,
    path: str,
    data: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> ToolResult:
    try:
        cfg = _resolve_runtime_config()
        ssl_ctx = False if not cfg.verify_ssl else None
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        timeout_obj = aiohttp.ClientTimeout(total=cfg.timeout)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout_obj) as session:
            result = await _request(cfg, session, method, path, data=data, params=params)
        return ToolResult(success=True, data=result)
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


# ── Time helpers ─────────────────────────────────────────────────────────────

def _to_ts(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    raise ValueError(f"Cannot parse time value: {v!r}")


def _resolve_time_range(params: dict[str, Any], default_hours: int = 24) -> tuple[int, int]:
    now = int(time.time())
    from_ts = _to_ts(params.get("start_time")) or (now - default_hours * 3600)
    to_ts = _to_ts(params.get("end_time")) or now
    return from_ts, to_ts


# ═════════════════════════════════════════════════════════════════════════════
# Tool entry points
# ═════════════════════════════════════════════════════════════════════════════

# ── Alerts ───────────────────────────────────────────────────────────────────

async def run_alerts(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "list")

    if action == "list":
        from_ts, to_ts = _resolve_time_range(params)
        body: dict[str, Any] = {
            "startTimestamp": from_ts,
            "endTimestamp": to_ts,
        }
        if params.get("page_size"):
            body["pageSize"] = int(params["page_size"])
        if params.get("page_num"):
            body["pageNum"] = int(params["page_num"])
        return await _run_request("POST", "/api/xdr/v1/alerts/list", data=body)

    elif action == "update_status":
        body = {
            "uuIds": params.get("uuids", []),
            "dealStatus": int(params.get("deal_status", 1)),
        }
        if params.get("deal_comment"):
            body["dealComment"] = params["deal_comment"]
        return await _run_request("POST", "/api/xdr/v1/alerts/dealstatus", data=body)

    elif action == "status_list":
        return await _run_request("POST", "/api/xdr/v1/alerts/dealstatus/list", data={})

    elif action == "get_proof":
        uuid = params.get("uuid", "")
        if not uuid:
            return ToolResult(success=False, error="uuid is required for get_proof")
        # Spec: GET /api/xdr/v1/alerts/:uuid/proof  (开放接口列表 v1，
        # apiRequestType=1 即 GET).  Earlier versions sent POST and were
        # silently ignored / 404'd by the appliance.
        return await _run_request("GET", f"/api/xdr/v1/alerts/{uuid}/proof")

    else:
        return ToolResult(
            success=False,
            error=(
                f"Unknown alert action: {action}. Use: list, update_status, "
                "status_list, get_proof. (注：标准开放列表中没有 alerts/:uuid/detail "
                "接口，如需查看告警详情请使用 list 并按 uuId 过滤。)"
            ),
        )


# ── Incidents ────────────────────────────────────────────────────────────────

async def run_incidents(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "list")

    if action == "list":
        from_ts, to_ts = _resolve_time_range(params)
        body: dict[str, Any] = {
            "startTimestamp": from_ts,
            "endTimestamp": to_ts,
        }
        if params.get("page_size"):
            body["pageSize"] = int(params["page_size"])
        if params.get("page_num"):
            body["pageNum"] = int(params["page_num"])
        return await _run_request("POST", "/api/xdr/v1/incidents/list", data=body)

    elif action == "update_status":
        body = {
            "uuIds": params.get("uuids", []),
            "dealStatus": int(params.get("deal_status", 1)),
        }
        if params.get("deal_comment"):
            body["dealComment"] = params["deal_comment"]
        return await _run_request("POST", "/api/xdr/v1/incidents/dealstatus", data=body)

    elif action == "status_list":
        return await _run_request("POST", "/api/xdr/v1/incidents/dealstatus/list", data={})

    elif action == "get_proof":
        uuid = params.get("uuid", "")
        if not uuid:
            return ToolResult(success=False, error="uuid is required for get_proof")
        # Spec: GET /api/xdr/v1/incidents/:uuid/proof
        return await _run_request("GET", f"/api/xdr/v1/incidents/{uuid}/proof")

    elif action == "get_entities":
        uuid = params.get("uuid", "")
        entity_type = params.get("entity_type", "host")
        if not uuid:
            return ToolResult(success=False, error="uuid is required for get_entities")
        valid_types = ("host", "dns", "innerip", "ip", "file", "process")
        if entity_type not in valid_types:
            return ToolResult(success=False, error=f"entity_type must be one of {valid_types}")
        # Spec: GET /api/xdr/v1/incidents/:uuid/entities/{dns,file,host,
        # innerip,ip,process}.  All six entity sub-paths are GET in the
        # 开放接口列表; POST returns 405 / signature mismatch.
        return await _run_request(
            "GET", f"/api/xdr/v1/incidents/{uuid}/entities/{entity_type}"
        )

    else:
        return ToolResult(
            success=False,
            error=(
                f"Unknown incident action: {action}. Use: list, update_status, "
                "status_list, get_proof, get_entities. (注：标准开放列表中没有 "
                "incidents/:uuid/detail 接口，事件详情请通过 list 按 uuId 过滤获取。)"
            ),
        )


# ── Responses (Isolate / Unisolate) ─────────────────────────────────────────

async def run_responses(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "isolate_list")

    if action == "isolate_list":
        return await _run_request("POST", "/api/xdr/v1/responses/host/isolate/list", data={})

    elif action == "unisolate":
        body: dict[str, Any] = {}
        if params.get("host_ips"):
            body["hostIps"] = params["host_ips"]
        return await _run_request("POST", "/api/xdr/v1/responses/host/unisolate", data=body)

    else:
        return ToolResult(success=False, error=f"Unknown response action: {action}. Use: isolate_list, unisolate")


# ── Whitelists ───────────────────────────────────────────────────────────────

async def run_whitelists(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "list")

    if action == "list":
        body: dict[str, Any] = {}
        if params.get("page_size"):
            body["pageSize"] = int(params["page_size"])
        if params.get("page_num"):
            body["pageNum"] = int(params["page_num"])
        return await _run_request("POST", "/api/xdr/v1/whitelists/list", data=body)

    elif action == "create":
        body = {}
        for k in ("name", "type", "value", "description"):
            if params.get(k):
                body[k] = params[k]
        return await _run_request("POST", "/api/xdr/v1/whitelists", data=body)

    elif action == "update":
        wl_id = params.get("id", "")
        if not wl_id:
            return ToolResult(success=False, error="id is required for update")
        body = {}
        for k in ("name", "type", "value", "description"):
            if params.get(k):
                body[k] = params[k]
        return await _run_request("PUT", f"/api/xdr/v1/whitelists/{wl_id}", data=body)

    elif action == "delete":
        body = {}
        if params.get("ids"):
            body["ids"] = params["ids"]
        return await _run_request("DELETE", "/api/xdr/v1/whitelists", data=body)

    elif action == "toggle_status":
        wl_id = params.get("id", "")
        if not wl_id:
            return ToolResult(success=False, error="id is required for toggle_status")
        body = {}
        if "status" in params:
            body["status"] = params["status"]
        return await _run_request("PUT", f"/api/xdr/v1/whitelists/{wl_id}/status", data=body)

    else:
        return ToolResult(success=False, error=f"Unknown whitelist action: {action}. Use: list, create, update, delete, toggle_status")


# ── Assets ───────────────────────────────────────────────────────────────────

async def run_assets(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "list")

    if action == "list":
        body: dict[str, Any] = {}
        if params.get("page_size"):
            body["pageSize"] = int(params["page_size"])
        if params.get("page_num"):
            body["pageNum"] = int(params["page_num"])
        return await _run_request("POST", "/api/xdr/v1/assets/list", data=body)

    elif action == "ip_segment_tree":
        return await _run_request("POST", "/api/xdr/v1/assets/ipsegmenttree", data={})

    elif action == "asset_class":
        query_params: dict[str, Any] = {}
        if "is_filter" in params:
            query_params["isFilter"] = params["is_filter"]
        if "need_auto" in params:
            query_params["needAuto"] = params["need_auto"]
        return await _run_request("GET", "/api/xdr/v1/assets/assetclass", params=query_params)

    elif action == "device_list":
        query_params = {}
        if params.get("name"):
            query_params["name"] = params["name"]
        return await _run_request("GET", "/api/xdr/v1/assets/assetadapter", params=query_params)

    elif action == "department_tree":
        query_params = {}
        if "get_undistributed" in params:
            query_params["getUndistributed"] = params["get_undistributed"]
        return await _run_request("GET", "/api/xdr/v1/assets/department", params=query_params)

    elif action == "delete":
        body = {}
        if params.get("ids"):
            body["ids"] = params["ids"]
        return await _run_request("DELETE", "/api/xdr/v1/assets/list", data=body)

    else:
        return ToolResult(success=False, error=f"Unknown asset action: {action}. Use: list, ip_segment_tree, asset_class, device_list, department_tree, delete")


# ── Vulnerabilities ──────────────────────────────────────────────────────────

async def run_vulns(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "baseline")

    if action == "baseline":
        body: dict[str, Any] = {}
        if params.get("page_size"):
            body["pageSize"] = int(params["page_size"])
        if params.get("page_num"):
            body["pageNum"] = int(params["page_num"])
        return await _run_request("POST", "/api/xdr/v1/vuls/baseline/list", data=body)

    elif action == "update_status":
        body = {}
        for k in ("ids", "fixStatus"):
            if params.get(k):
                body[k] = params[k]
        return await _run_request("PATCH", "/api/xdr/v1/vuls/fixstatus", data=body)

    elif action == "source_device":
        return await _run_request("GET", "/api/xdr/v1/vuls/sourcedevice")

    elif action == "vuln_list":
        body = {}
        if params.get("page_size"):
            body["pageSize"] = int(params["page_size"])
        if params.get("page_num"):
            body["pageNum"] = int(params["page_num"])
        # Spec: POST /api/xdr/v1/vuls/risk/list (获取漏洞、弱密码数据).
        # The legacy ``/api/xdr/v1/vuls/list`` path does not exist in the
        # 开放接口列表 and was returning 404 / signature failures.
        return await _run_request("POST", "/api/xdr/v1/vuls/risk/list", data=body)

    else:
        return ToolResult(success=False, error=f"Unknown vuln action: {action}. Use: baseline, update_status, source_device, vuln_list")


# ── Registration ─────────────────────────────────────────────────────────────

def register(registry):
    registry.register("sangfor_xdr_alerts", run_alerts)
    registry.register("sangfor_xdr_incidents", run_incidents)
    registry.register("sangfor_xdr_responses", run_responses)
    registry.register("sangfor_xdr_whitelists", run_whitelists)
    registry.register("sangfor_xdr_assets", run_assets)
    registry.register("sangfor_xdr_vulns", run_vulns)
