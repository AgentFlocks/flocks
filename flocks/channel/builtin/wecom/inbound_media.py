"""
WeCom inbound media download helpers.

Downloads and decrypts file/image media received via the WeCom AI Bot
WebSocket channel.  WeCom encrypts all media with AES-256-CBC; the
decryption key (``aeskey``) is provided in the message frame alongside
the download URL.
"""

from __future__ import annotations

import mimetypes
import os
import re
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from flocks.channel.base import InboundMessage
from flocks.utils.log import Log

log = Log.create(service="channel.wecom.media")

_DEFAULT_MAX_INBOUND_MEDIA_BYTES = 30 * 1024 * 1024


@dataclass
class DownloadedInboundMedia:
    filename: str
    mime: str
    url: str
    source: dict


def _media_storage_dir(account_id: str) -> Path:
    return (
        Path.home()
        / ".flocks"
        / "data"
        / "channel_media"
        / "wecom"
        / account_id
        / datetime.date.today().isoformat()
    )


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return cleaned[:120] or "attachment"


def _guess_mime_from_ext(filename: str) -> Optional[str]:
    _, ext = os.path.splitext(filename)
    if ext:
        return mimetypes.guess_type(filename)[0]
    return None


def _guess_filename(msg: InboundMessage, media_url: str, cd_filename: Optional[str] = None) -> str:
    raw_body = msg.raw if isinstance(msg.raw, dict) else {}
    msg_type = raw_body.get("msgtype", "")

    if msg_type == "file":
        raw_name = str(raw_body.get("file", {}).get("filename", "") or "").strip()
        if raw_name:
            return _sanitize_filename(raw_name)

    if msg_type == "image":
        raw_name = str(raw_body.get("image", {}).get("filename", "") or "").strip()
        if raw_name:
            return _sanitize_filename(raw_name)

    if cd_filename:
        return _sanitize_filename(cd_filename)

    url_path = urlparse(media_url).path
    url_filename = os.path.basename(url_path)
    if url_filename and "." in url_filename:
        return _sanitize_filename(url_filename)

    prefix = "image" if msg_type == "image" else "file"
    msg_id = msg.message_id or "unknown"
    return _sanitize_filename(f"{prefix}_{msg_id[:12]}")


def _extract_aes_key(msg: InboundMessage) -> Optional[str]:
    raw_body = msg.raw if isinstance(msg.raw, dict) else {}
    msg_type = raw_body.get("msgtype", "")

    if msg_type == "file":
        return str(raw_body.get("file", {}).get("aeskey", "") or "").strip() or None
    if msg_type == "image":
        return str(raw_body.get("image", {}).get("aeskey", "") or "").strip() or None
    return None


async def download_inbound_media(
    msg: InboundMessage,
    config: dict,
    *,
    max_bytes: int = _DEFAULT_MAX_INBOUND_MEDIA_BYTES,
) -> Optional[DownloadedInboundMedia]:
    media_url = msg.media_url
    if not media_url:
        return None

    aes_key = _extract_aes_key(msg)

    try:
        from wecom_aibot_sdk import WeComApiClient, decrypt_file
        api_client = WeComApiClient(log, timeout=30000)
        result = await api_client.download_file_raw(media_url)
        buffer: bytes = result["buffer"]
        cd_filename: Optional[str] = result.get("filename")
        await api_client._client.aclose()

        if aes_key:
            buffer = decrypt_file(buffer, aes_key)

    except ImportError:
        log.warning("wecom.media.sdk_not_available")
        return None

    except Exception as e:
        log.warning("wecom.media.download_failed", {
            "url": media_url[:200],
            "error": str(e),
        })
        return None

    if len(buffer) > max_bytes:
        raise ValueError(
            f"WeCom inbound media too large: >{max_bytes // (1024 * 1024)}MB"
        )

    filename = _guess_filename(msg, media_url, cd_filename)

    if not filename or "." not in filename:
        guessed_mime = _guess_mime_from_ext(filename or "")
        ext = mimetypes.guess_extension(guessed_mime) if guessed_mime else ""
        if ext:
            filename = f"{filename}{ext}"

    mime = _guess_mime_from_ext(filename) or "application/octet-stream"

    storage_dir = _media_storage_dir(msg.account_id or "default")
    storage_dir.mkdir(parents=True, exist_ok=True)
    msg_id = msg.message_id or "unknown"
    file_path = storage_dir / _sanitize_filename(f"{msg_id}_{filename}")
    file_path.write_bytes(buffer)

    return DownloadedInboundMedia(
        filename=filename,
        mime=mime,
        url=file_path.resolve().as_uri(),
        source={
            "channel": "wecom",
            "account_id": msg.account_id,
            "message_id": msg.message_id,
            "media_url": msg.media_url,
        },
    )
