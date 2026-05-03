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
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from flocks.channel.base import InboundMessage
from flocks.utils.log import Log

log = Log.create(service="channel.wecom.media")

_DEFAULT_MAX_INBOUND_MEDIA_BYTES = 30 * 1024 * 1024


class WeComInboundMediaTooLarge(ValueError):
    """企微入站媒体超过允许大小。"""


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


def _filename_from_content_disposition(value: str) -> Optional[str]:
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', value, re.I)
    if not match:
        return None
    return unquote(match.group(1).strip())


def _max_size_error(max_bytes: int) -> ValueError:
    return WeComInboundMediaTooLarge(
        f"WeCom inbound media too large: >{max_bytes // (1024 * 1024)}MB"
    )


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

    if msg_type == "mixed":
        for item in raw_body.get("mixed", {}).get("msg_item", []):
            item_type = item.get("msgtype", "")
            if item_type == "file":
                raw_name = str(item.get("file", {}).get("filename", "") or "").strip()
                if raw_name:
                    return _sanitize_filename(raw_name)
            if item_type == "image":
                raw_name = str(item.get("image", {}).get("filename", "") or "").strip()
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
    if msg_type == "mixed":
        for item in raw_body.get("mixed", {}).get("msg_item", []):
            item_type = item.get("msgtype", "")
            if item_type == "file":
                key = str(item.get("file", {}).get("aeskey", "") or "").strip()
                if key:
                    return key
            if item_type == "image":
                key = str(item.get("image", {}).get("aeskey", "") or "").strip()
                if key:
                    return key
    return None


async def _close_api_client(api_client: Any) -> None:
    client = getattr(api_client, "_client", None)
    close = getattr(client, "aclose", None)
    if close:
        try:
            await close()
        except Exception as e:
            log.warning("wecom.media.client_close_failed", {"error": str(e)})


async def _download_file_limited(
    api_client: Any,
    media_url: str,
    max_bytes: int,
) -> tuple[bytes, Optional[str]]:
    client = getattr(api_client, "_client", None)
    stream = getattr(client, "stream", None)
    if callable(stream):
        chunks: list[bytes] = []
        total = 0
        filename: Optional[str] = None
        async with stream("GET", media_url) as resp:
            if hasattr(resp, "raise_for_status"):
                resp.raise_for_status()
            headers = getattr(resp, "headers", {}) or {}
            content_length = headers.get("content-length") or headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise _max_size_error(max_bytes)
                except ValueError as e:
                    if "invalid literal" not in str(e):
                        raise
            content_disposition = (
                headers.get("content-disposition")
                or headers.get("Content-Disposition")
                or ""
            )
            filename = _filename_from_content_disposition(content_disposition)
            async for chunk in resp.aiter_bytes(8192):
                total += len(chunk)
                if total > max_bytes:
                    raise _max_size_error(max_bytes)
                chunks.append(chunk)
        return b"".join(chunks), filename

    result = await api_client.download_file_raw(media_url)
    buffer: bytes = result["buffer"]
    if len(buffer) > max_bytes:
        raise _max_size_error(max_bytes)
    return buffer, result.get("filename")


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

    api_client = None
    try:
        sdk = importlib.import_module("wecom_aibot_sdk")
        api_client = sdk.WeComApiClient(log, timeout=30000)
        buffer, cd_filename = await _download_file_limited(
            api_client,
            media_url,
            max_bytes,
        )

        if aes_key:
            try:
                buffer = sdk.decrypt_file(buffer, aes_key)
            except Exception as e:
                log.warning("wecom.media.decrypt_failed", {
                    "url": media_url[:200],
                    "message_id": msg.message_id,
                    "error": str(e),
                })
                return None
            if len(buffer) > max_bytes:
                raise _max_size_error(max_bytes)

    except ImportError:
        log.warning("wecom.media.sdk_not_available")
        return None

    except WeComInboundMediaTooLarge as e:
        log.warning("wecom.media.file_too_large", {
            "url": media_url[:200],
            "message_id": msg.message_id,
            "error": str(e),
        })
        return None

    except Exception as e:
        log.warning("wecom.media.download_failed", {
            "url": media_url[:200],
            "message_id": msg.message_id,
            "error": str(e),
        })
        return None

    finally:
        if api_client is not None:
            await _close_api_client(api_client)

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
