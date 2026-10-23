"""
Low-level iLink Bot HTTP API helpers.

Each function maps 1:1 to an iLink endpoint and returns the parsed JSON dict.
Higher-level retry/backoff is handled by the channel itself.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import ssl
import struct
from typing import TYPE_CHECKING, Optional

from .config import (
    API_TIMEOUT_MS,
    CHANNEL_VERSION,
    EP_GET_UPDATES,
    EP_GET_UPLOAD_URL,
    EP_SEND_MESSAGE,
    ILINK_APP_CLIENT_VERSION,
    ILINK_APP_ID,
    ITEM_TEXT,
    MSG_STATE_FINISH,
    MSG_TYPE_BOT,
    RATE_LIMIT_ERRCODE,
)

if TYPE_CHECKING:
    import aiohttp


def make_ssl_connector() -> "Optional[aiohttp.TCPConnector]":
    """Return a TCPConnector with certifi CA bundle for iLink TLS verification.

    Tencent's ``ilinkai.weixin.qq.com`` is not always verifiable against
    Homebrew OpenSSL on macOS; certifi's Mozilla bundle is the reliable choice.
    Returns ``None`` if certifi or aiohttp is unavailable; caller falls back
    to aiohttp defaults.
    """
    try:
        import aiohttp  # local import keeps module importable without aiohttp
        import certifi
    except ImportError:
        return None
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    return aiohttp.TCPConnector(ssl=ssl_ctx)


def random_wechat_uin() -> str:
    value = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def base_info() -> dict:
    return {"channel_version": CHANNEL_VERSION}


def make_headers(token: Optional[str], body: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def is_stale_session(
    ret: Optional[int], errcode: Optional[int], errmsg: Optional[str]
) -> bool:
    """Detect the iLink "stale session" disguise of errcode -2.

    iLink occasionally returns ret/errcode = -2 with errmsg "unknown error"
    for an expired session, rather than the documented errcode -14.
    """
    if ret != RATE_LIMIT_ERRCODE and errcode != RATE_LIMIT_ERRCODE:
        return False
    return (errmsg or "").lower() == "unknown error"


def _json_dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def api_post(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    endpoint: str,
    payload: dict,
    token: Optional[str],
    timeout_ms: int,
) -> dict:
    """POST *payload* + ``base_info`` to ``{base_url}/{endpoint}``."""
    import aiohttp

    body = _json_dumps({**payload, "base_info": base_info()})
    url = f"{base_url.rstrip('/')}/{endpoint}"
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    async with session.post(url, data=body, headers=make_headers(token, body), timeout=timeout) as resp:
        raw = await resp.text()
        if not resp.ok:
            raise RuntimeError(f"iLink POST {endpoint} HTTP {resp.status}: {raw[:200]}")
        return json.loads(raw)


async def get_updates(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    sync_buf: str,
    timeout_ms: int,
) -> dict:
    try:
        return await api_post(
            session,
            base_url=base_url,
            endpoint=EP_GET_UPDATES,
            payload={"get_updates_buf": sync_buf},
            token=token,
            timeout_ms=timeout_ms,
        )
    except asyncio.TimeoutError:
        return {"ret": 0, "msgs": [], "get_updates_buf": sync_buf}


async def send_text_message(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    to: str,
    text: str,
    context_token: Optional[str],
    client_id: str,
) -> dict:
    if not text or not text.strip():
        raise ValueError("send_text_message: text must not be empty")
    message: dict = {
        "from_user_id": "",
        "to_user_id": to,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
    }
    if context_token:
        message["context_token"] = context_token
    return await api_post(
        session,
        base_url=base_url,
        endpoint=EP_SEND_MESSAGE,
        payload={"msg": message},
        token=token,
        timeout_ms=API_TIMEOUT_MS,
    )


async def send_media_message(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    to: str,
    item: dict,
    context_token: Optional[str],
    client_id: str,
) -> dict:
    """Send a single pre-built media item (image/video/voice/file)."""
    message: dict = {
        "from_user_id": "",
        "to_user_id": to,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "item_list": [item],
    }
    if context_token:
        message["context_token"] = context_token
    return await api_post(
        session,
        base_url=base_url,
        endpoint=EP_SEND_MESSAGE,
        payload={"msg": message},
        token=token,
        timeout_ms=API_TIMEOUT_MS,
    )


async def get_upload_url(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    media_type: int,
    filekey: str,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey_hex: str,
) -> dict:
    """Request a CDN upload slot for an outbound media file."""
    return await api_post(
        session,
        base_url=base_url,
        endpoint=EP_GET_UPLOAD_URL,
        payload={
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey_hex,
        },
        token=token,
        timeout_ms=API_TIMEOUT_MS,
    )
