"""
Feishu inbound media download helpers.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flocks.channel.base import InboundMessage
from flocks.channel.builtin.feishu.client import (
    _get_http_client,
    _resolve_account_config,
    _resolve_account_credentials,
    get_tenant_token,
)
from flocks.channel.builtin.feishu.config import resolve_api_base, resolve_token_url

_DEFAULT_MAX_INBOUND_MEDIA_BYTES = 30 * 1024 * 1024


@dataclass
class DownloadedInboundMedia:
    filename: str
    mime: str
    url: str
    source: dict


def _parse_media_url(media_url: str) -> tuple[Optional[str], Optional[str]]:
    if media_url.startswith("lark://image/"):
        return "image", media_url[len("lark://image/"):].strip()
    if media_url.startswith("lark://file/"):
        return "file", media_url[len("lark://file/"):].strip()
    return None, None


def _media_storage_dir(account_id: str) -> Path:
    from flocks.config.config import _get_data_dir

    return _get_data_dir() / "channel_media" / "feishu" / account_id


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return cleaned[:120] or "attachment"


def _guess_filename(
    msg: InboundMessage,
    media_kind: str,
    resource_key: str,
    mime: str,
) -> str:
    raw_content = {}
    try:
        raw_message = ((msg.raw or {}).get("event") or {}).get("message") or {}
        import json as _json

        raw_content = _json.loads(raw_message.get("content", "{}"))
    except Exception:
        raw_content = {}

    filename = str(raw_content.get("file_name") or "").strip()
    if filename:
        return _sanitize_filename(filename)

    ext = mimetypes.guess_extension(mime) or ""
    prefix = "image" if media_kind == "image" else "file"
    return _sanitize_filename(f"{prefix}_{resource_key[:12]}{ext}")


async def download_inbound_media(
    msg: InboundMessage,
    config: dict,
    *,
    max_bytes: int = _DEFAULT_MAX_INBOUND_MEDIA_BYTES,
) -> Optional[DownloadedInboundMedia]:
    media_kind, resource_key = _parse_media_url(msg.media_url or "")
    if not media_kind or not resource_key or not msg.message_id:
        return None

    app_id, app_secret = _resolve_account_credentials(config, msg.account_id)
    acc_config = _resolve_account_config(config, msg.account_id)
    token = await get_tenant_token(app_id, app_secret, resolve_token_url(acc_config))
    api_base = resolve_api_base(acc_config)
    url = f"{api_base}/im/v1/messages/{msg.message_id}/resources/{resource_key}"

    client = await _get_http_client()
    async with client.stream(
        "GET",
        url,
        params={"type": media_kind},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        resp.raise_for_status()
        mime = (
            (resp.headers.get("content-type") or "application/octet-stream")
            .split(";", 1)[0]
            .strip()
            or "application/octet-stream"
        )
        filename = _guess_filename(msg, media_kind, resource_key, mime)
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes(8192):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"Feishu inbound media too large: >{max_bytes // (1024 * 1024)}MB"
                )
            chunks.append(chunk)

    storage_dir = _media_storage_dir(msg.account_id or "default")
    storage_dir.mkdir(parents=True, exist_ok=True)
    file_path = storage_dir / _sanitize_filename(
        f"{msg.message_id}_{resource_key[:8]}_{filename}"
    )
    file_path.write_bytes(b"".join(chunks))

    return DownloadedInboundMedia(
        filename=filename,
        mime=mime,
        url=file_path.resolve().as_uri(),
        source={
            "channel": "feishu",
            "account_id": msg.account_id,
            "message_id": msg.message_id,
            "media_url": msg.media_url,
        },
    )
