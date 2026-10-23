"""
Feishu media (image / file) upload and sending helpers.

Supports:
- Image upload via POST /im/v1/images  → image_key
- File upload via POST /im/v1/files    → file_key
- Sending the resulting key as an image/file message
- Fallback to text+URL when the media_url is an HTTP(S) link
"""

from __future__ import annotations

import mimetypes
import os
from typing import Optional

import httpx

from flocks.channel.builtin.feishu.client import (
    api_request_for_account,
    _get_http_client,
    ensure_api_success,
    get_tenant_token,
    _resolve_account_credentials,
    _resolve_account_config,
)
from flocks.channel.builtin.feishu.config import resolve_api_base, resolve_token_url
from flocks.utils.log import Log

log = Log.create(service="channel.feishu.media")

# Feishu lark:// URI schemes emitted by monitor._extract_content
_LARK_IMAGE_PREFIX = "lark://image/"
_LARK_FILE_PREFIX = "lark://file/"


async def _upload_image(
    data: bytes,
    filename: str,
    config: dict,
    account_id: Optional[str],
) -> str:
    """Upload image bytes and return the ``image_key``."""
    app_id, app_secret = _resolve_account_credentials(config, account_id)
    acc_config = _resolve_account_config(config, account_id)
    token = await get_tenant_token(app_id, app_secret, resolve_token_url(acc_config))
    api_base = resolve_api_base(acc_config)
    url = f"{api_base}/im/v1/images"

    client = await _get_http_client()
    resp = await client.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        files={"image": (filename, data)},
        data={"image_type": "message"},
    )
    resp.raise_for_status()
    result = ensure_api_success(
        resp.json(),
        context="Feishu image upload failed",
        http_status=resp.status_code,
    )
    image_key = (result.get("data") or {}).get("image_key", "")
    if not image_key:
        raise RuntimeError(f"Feishu image upload failed: {result}")
    return image_key


async def _upload_file(
    data: bytes,
    filename: str,
    config: dict,
    account_id: Optional[str],
) -> str:
    """Upload file bytes and return the ``file_key``."""
    app_id, app_secret = _resolve_account_credentials(config, account_id)
    acc_config = _resolve_account_config(config, account_id)
    token = await get_tenant_token(app_id, app_secret, resolve_token_url(acc_config))
    api_base = resolve_api_base(acc_config)
    url = f"{api_base}/im/v1/files"

    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    file_type = "stream"
    if mime.startswith("audio/"):
        file_type = "opus"
    elif mime.startswith("video/"):
        file_type = "mp4"
    elif filename.lower().endswith(".pdf"):
        file_type = "pdf"
    elif filename.lower().endswith((".doc", ".docx")):
        file_type = "doc"

    client = await _get_http_client()
    resp = await client.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, data, mime)},
        data={"file_type": file_type, "file_name": filename},
    )
    resp.raise_for_status()
    result = ensure_api_success(
        resp.json(),
        context="Feishu file upload failed",
        http_status=resp.status_code,
    )
    file_key = (result.get("data") or {}).get("file_key", "")
    if not file_key:
        raise RuntimeError(f"Feishu file upload failed: {result}")
    return file_key


_DEFAULT_MAX_MEDIA_BYTES = 30 * 1024 * 1024  # 30 MB


async def _fetch_url_bytes(url: str, max_bytes: int = _DEFAULT_MAX_MEDIA_BYTES) -> tuple[bytes, str]:
    """Fetch bytes from a URL; return (data, filename).

    Raises ``ValueError`` if the response exceeds *max_bytes*.
    """
    import httpx as _httpx
    async with _httpx.AsyncClient(timeout=60) as client:
        async with client.stream("GET", url, follow_redirects=True) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(8192):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"Media too large: >{max_bytes // (1024 * 1024)}MB (url={url[:120]})"
                    )
                chunks.append(chunk)
        filename = url.split("/")[-1].split("?")[0] or "file"
        return b"".join(chunks), filename


async def send_media_feishu(
    *,
    config: dict,
    to: str,
    text: str = "",
    media_url: str = "",
    reply_to_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> dict:
    """Upload and send a media message via the Feishu IM API.

    Supports the following ``media_url`` formats:
    - ``lark://image/<image_key>`` — already uploaded, send directly
    - ``lark://file/<file_key>``  — already uploaded, send directly
    - ``http(s)://...``           — fetch and upload, then send
    - Empty or unsupported        — falls back to send_text with URL text

    Returns ``{"message_id": "...", "chat_id": "..."}`` on success.
    """
    from flocks.channel.builtin.feishu.send import send_message_feishu
    import json

    if not media_url:
        return await send_message_feishu(
            config=config,
            to=to,
            text=text,
            reply_to_id=reply_to_id,
            account_id=account_id,
        )

    # Already-uploaded image key
    if media_url.startswith(_LARK_IMAGE_PREFIX):
        image_key = media_url[len(_LARK_IMAGE_PREFIX):]
        msg_type = "image"
        content = json.dumps({"image_key": image_key})
        return await _send_media_content(
            config=config, to=to, content=content, msg_type=msg_type,
            reply_to_id=reply_to_id,
            account_id=account_id,
        )

    # Already-uploaded file key
    if media_url.startswith(_LARK_FILE_PREFIX):
        file_key = media_url[len(_LARK_FILE_PREFIX):]
        msg_type = "file"
        content = json.dumps({"file_key": file_key})
        return await _send_media_content(
            config=config, to=to, content=content, msg_type=msg_type,
            reply_to_id=reply_to_id,
            account_id=account_id,
        )

    # Remote HTTP(S) URL — fetch, detect type, upload, then send
    if media_url.startswith("http://") or media_url.startswith("https://"):
        try:
            data, filename = await _fetch_url_bytes(media_url)
            mime = mimetypes.guess_type(filename)[0] or ""
            if mime.startswith("image/"):
                image_key = await _upload_image(data, filename, config, account_id)
                content = json.dumps({"image_key": image_key})
                msg_type = "image"
            else:
                file_key = await _upload_file(data, filename, config, account_id)
                content = json.dumps({"file_key": file_key})
                msg_type = "file"
            return await _send_media_content(
                config=config, to=to, content=content, msg_type=msg_type,
                reply_to_id=reply_to_id,
                account_id=account_id,
            )
        except Exception as exc:
            log.warning("feishu.media.upload_failed", {
                "url": media_url, "error": str(exc),
            })
            # Graceful fallback: send the URL as text
            fallback_text = f"{text}\n{media_url}".strip() if text else media_url
            return await send_message_feishu(
                config=config,
                to=to,
                text=fallback_text,
                reply_to_id=reply_to_id,
                account_id=account_id,
            )

    # Unsupported scheme — fall back to text
    fallback_text = f"{text}\n{media_url}".strip() if text else media_url
    return await send_message_feishu(
        config=config,
        to=to,
        text=fallback_text,
        reply_to_id=reply_to_id,
        account_id=account_id,
    )


async def _send_media_content(
    *,
    config: dict,
    to: str,
    content: str,
    msg_type: str,
    reply_to_id: Optional[str],
    account_id: Optional[str],
) -> dict:
    """Send a pre-built media content payload."""
    from flocks.channel.builtin.feishu.send import send_payload_feishu

    return await send_payload_feishu(
        config=config,
        to=to,
        content=content,
        msg_type=msg_type,
        reply_to_id=reply_to_id,
        account_id=account_id,
    )
