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

    raw_host = (_resolve_ref(raw.get("host")) or os.getenv("SANGFOR_XDR_HOST") or "").strip()
    # Tolerate users pasting any URL form into the WebUI ``host`` field —
    # ``10.0.0.1``, ``https://10.0.0.1``, ``https://10.0.0.1:8443/api/?x=1``
    # all collapse to a clean scheme://host[:port] base.  Without this we
    # produced things like ``https://https://10.0.0.1`` (double scheme) or
    # ``https://10.0.0.1/api/api/xdr/v1/...`` (leaked path component) and
    # every signed request silently routed to nowhere.
    candidate = raw_host if "://" in raw_host else f"https://{raw_host}"
    parsed_host = urlparse(candidate)
    hostname = (parsed_host.hostname or "").strip()
    inline_port: Optional[int] = parsed_host.port

    # Preserve IPv6 literal brackets when re-assembling the URL.
    if hostname and ":" in hostname and not hostname.startswith("["):
        hostname_for_url = f"[{hostname}]"
    else:
        hostname_for_url = hostname

    port_raw = raw.get("port") or inline_port or DEFAULT_PORT
    try:
        port = int(str(port_raw).strip())
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    base_url = (
        f"https://{hostname_for_url}:{port}"
        if port != 443
        else f"https://{hostname_for_url}"
    )

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

    if not hostname:
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
    data: Optional[Any] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    ak, sk = _decode_auth_code(cfg.auth_code)
    url = f"{cfg.base_url}{path}"
    # IMPORTANT: ``data`` may legitimately be an empty container (``{}`` or
    # ``[]``) — many XDR ``/list`` endpoints accept an empty filter object
    # but still require a *parsable* JSON body, otherwise return
    # "参数解析异常" / "参数不合法".  Using ``if data`` would treat an empty
    # dict as falsy and send an empty string body, which both breaks JSON
    # parsing and changes the signed payload hash from
    # ``SHA256("{}")`` to ``SHA256("")``.
    if data is None:
        payload = ""
    else:
        payload = json.dumps(data, ensure_ascii=False)
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
    data: Optional[Any] = None,
    params: Optional[dict[str, Any]] = None,
) -> ToolResult:
    try:
        cfg = _resolve_runtime_config()
        ssl_ctx = False if not cfg.verify_ssl else None
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        timeout_obj = aiohttp.ClientTimeout(total=cfg.timeout)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout_obj) as session:
            result = await _request(cfg, session, method, path, data=data, params=params)
        # CRITICAL: ``ToolResult`` declares ``output`` (not ``data``) as the
        # payload field.  Earlier versions of this handler returned
        # ``ToolResult(success=True, data=result)`` — pydantic silently
        # dropped the unknown kwarg, so callers (LLM agents) received
        # ``output=None`` and reported "返回内容都是空的" even though
        # authentication and the API call had both succeeded.
        return ToolResult(success=True, output=result)
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


# ── Time helpers ─────────────────────────────────────────────────────────────

def _to_ts(v: Any) -> int:
    """Coerce ``v`` to a Unix-second timestamp.

    Accepts ints/floats (already epoch seconds — milliseconds are *not*
    auto-detected; callers should normalise first), all-digit strings,
    and a wide variety of human-readable timestamp formats.  In
    particular ISO-8601 strings produced by JS ``Date.toISOString()``
    (``"2026-04-21T00:00:00Z"`` / ``"2026-04-21T00:00:00.000Z"``) used
    to crash with ``Cannot parse time value`` because the trailing
    ``Z`` was not declared in any of the strptime formats — and the
    LLM agent has been observed handing exactly that shape over from
    the user's natural-language window ("过去 24 小时").
    """
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    # ``datetime.fromisoformat`` (Py3.11+) handles trailing ``Z`` natively;
    # for broader compatibility we strip ``Z`` and force UTC ourselves.
    iso_candidate = s
    if iso_candidate.endswith("Z"):
        iso_candidate = iso_candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso_candidate)
    except ValueError:
        dt = None
    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
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


# ── Action alias normalisation ───────────────────────────────────────────────

# LLM agents observed to emit synonyms like ``query`` / ``search`` / ``get``
# instead of the canonical ``list`` declared in the YAML schema's ``enum``
# (the 各工具功能测试 session shows the agent's first attempt across all
# six tools used ``action="query"`` / ``"query_baseline"``).  Returning a
# hard ``Unknown action`` error wastes round-trips and makes the agent give
# up before recovering.  We map the most common misnomers to their canonical
# action so the request still goes through.
_ACTION_ALIASES: dict[str, str] = {
    "query": "list",
    "search": "list",
    "get": "list",
    "fetch": "list",
    "find": "list",
    "query_list": "list",
    "query_baseline": "baseline",
    "fetch_baseline": "baseline",
    "get_baseline": "baseline",
    "query_vuln": "vuln_list",
    "query_vulns": "vuln_list",
    "list_vulns": "vuln_list",
    "vuls_list": "vuln_list",
    "fix_status": "update_status",
    "update_fix_status": "update_status",
    "isolate": "isolate_list",
    "list_isolate": "isolate_list",
    "host_isolate_list": "isolate_list",
}


def _normalise_action(value: Any, default: str) -> str:
    """Return a canonical action name, mapping common LLM aliases.

    ``None`` / empty / non-string values fall back to ``default`` so a
    missing ``action`` key always picks the most useful default for the
    tool (typically ``list``).
    """
    if value is None:
        return default
    s = str(value).strip().lower()
    if not s:
        return default
    return _ACTION_ALIASES.get(s, s)


# ═════════════════════════════════════════════════════════════════════════════
# Tool entry points
# ═════════════════════════════════════════════════════════════════════════════

# ── Alerts ───────────────────────────────────────────────────────────────────

async def run_alerts(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = _normalise_action(params.pop("action", None), default="list")

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
        # Spec: POST /api/xdr/v1/alerts/dealstatus/list with a JSON *array*
        # body (apiRequestParamType=1, demo body: ``["alert-uuid"]``).  The
        # endpoint returns the current dealStatus for the supplied alert
        # UUIDs; previously we sent ``{}`` which made the appliance reply
        # with "参数解析异常" because a list was expected.
        uuids = params.get("uuids") or []
        if isinstance(uuids, str):
            uuids = [u.strip() for u in uuids.split(",") if u.strip()]
        return await _run_request(
            "POST", "/api/xdr/v1/alerts/dealstatus/list", data=list(uuids)
        )

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
    action = _normalise_action(params.pop("action", None), default="list")

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
        # Spec: POST /api/xdr/v1/incidents/dealstatus/list — like the alerts
        # counterpart this expects a JSON *array* of incident UUIDs and
        # returns ``[{"uuId": ..., "dealStatus": ...}]``.  Sending ``{}``
        # produced "参数解析异常".
        uuids = params.get("uuids") or []
        if isinstance(uuids, str):
            uuids = [u.strip() for u in uuids.split(",") if u.strip()]
        return await _run_request(
            "POST", "/api/xdr/v1/incidents/dealstatus/list", data=list(uuids)
        )

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
    action = _normalise_action(params.pop("action", None), default="isolate_list")

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
    action = _normalise_action(params.pop("action", None), default="list")

    if action == "list":
        # Spec (开放接口列表 → POST /api/xdr/v1/whitelists/list) defines the
        # paging keys as ``page`` and ``pageSize`` (NOT ``pageNum``).  This
        # XDR build hard-rejects ``pageNum`` with
        # ``param page cannot be null`` because it never finds the expected
        # key.  Always default page=1 / pageSize=20 so the WebUI
        # connectivity probe (which sends no params) still satisfies the
        # appliance's strict validator.
        body: dict[str, Any] = {
            "page": int(params.get("page_num") or params.get("page") or 1),
            "pageSize": int(params.get("page_size") or 20),
        }
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
    action = _normalise_action(params.pop("action", "list"), default="list")

    if action == "list":
        # Like ``whitelists/list``, the assets list endpoint validates
        # paging keys as ``page`` / ``pageSize`` (NOT ``pageNum`` /
        # ``pageSize``).  Sending the legacy pair triggers
        # ``参数: {'pageSize': 5, 'pageNum': 1} 不合法，请确认后重新添加``
        # observed in the 各工具功能测试 session.  Provide defaults so
        # callers that omit paging still satisfy the strict validator.
        body: dict[str, Any] = {
            "page": int(params.get("page_num") or params.get("page") or 1),
            "pageSize": int(params.get("page_size") or params.get("pageSize") or 20),
        }
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
    action = _normalise_action(params.pop("action", None), default="baseline")

    if action == "baseline":
        # Spec uses ``page`` / ``pageSize``; we tolerate the legacy
        # ``page_num`` alias for backwards compatibility.
        body: dict[str, Any] = {
            "page": int(params.get("page_num") or params.get("page") or 1),
            "pageSize": int(params.get("page_size") or 20),
        }
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
        # Spec: POST /api/xdr/v1/vuls/risk/list (获取漏洞、弱密码数据).
        # The 开放接口列表 marks ``dataType`` as the *only* paramNotNull=0
        # (i.e. required) request field; the appliance returns
        # "请求参数校验失败" if it is missing.  Default to ``loophole``
        # (vulnerabilities) and let callers override with ``weakpwd`` when
        # they want weak-password records instead.
        data_type = params.get("data_type") or "loophole"
        body: dict[str, Any] = {
            "dataType": data_type,
            "page": int(params.get("page_num") or params.get("page") or 1),
            "pageSize": int(params.get("page_size") or 20),
        }
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
