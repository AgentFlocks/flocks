"""
DingTalk inbound media download helpers.

DingTalk delivers media references as opaque ``download_code`` strings
that must be exchanged for a short-lived HTTPS URL via the OAPI before
they can be downloaded.  This module handles that exchange + the actual
download, returning a local file URI the dispatcher can hand to the
session pipeline as a :class:`FilePart`.
"""

from __future__ import annotations

import datetime
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import httpx

from flocks.channel.base import InboundMessage
from flocks.channel.builtin.dingtalk.client import (
    DingTalkApiError,
    api_request_for_account,
)
from flocks.channel.builtin.dingtalk.config import resolve_account_credentials
from flocks.utils.log import Log

log = Log.create(service="channel.dingtalk.media")

_DEFAULT_MAX_INBOUND_MEDIA_BYTES = 20 * 1024 * 1024  # DingTalk caps inbound files at 20MB
_DOWNLOAD_PATH = "/v1.0/robot/messageFiles/download"


class DingTalkInboundMediaTooLarge(ValueError):
    """DingTalk 入站媒体超过允许大小。"""


@dataclass
class DownloadedInboundMedia:
    filename: str
    mime: str
    url: str
    source: dict


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return cleaned[:120] or "attachment"


def _media_storage_dir(account_id: str) -> Path:
    return (
        Path.home()
        / ".flocks"
        / "data"
        / "channel_media"
        / "dingtalk"
        / account_id
        / datetime.date.today().isoformat()
    )


def _guess_mime_from_ext(filename: str) -> Optional[str]:
    _, ext = os.path.splitext(filename)
    if ext:
        return mimetypes.guess_type(filename)[0]
    return None


def _is_download_code(value: str) -> bool:
    """True if *value* looks like a raw ``download_code`` rather than a URL.

    DingTalk's ``download_code`` is a short opaque token — no scheme, no
    path separators.  Anything that parses as an absolute URL is treated
    as ready-to-fetch.
    """
    if not value:
        return False
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https", "file"):
        return False
    return bool(value.strip())


async def _exchange_download_code(
    *,
    config: dict,
    account_id: Optional[str],
    download_code: str,
) -> tuple[str, Optional[str]]:
    """Exchange *download_code* for a short-lived HTTPS URL.

    Returns ``(download_url, filename)``; ``filename`` is best-effort and
    may be ``None`` when the OAPI response omits it.
    """
    app_key, app_secret, robot_code = resolve_account_credentials(config, account_id)
    if not app_key or not app_secret:
        raise DingTalkApiError(
            "DingTalk appKey/appSecret not configured"
            + (f" for account '{account_id}'" if account_id else ""),
        )
    body = {
        "robotCode": robot_code,
        "downloadCode": download_code,
    }
    data = await api_request_for_account(
        "POST", _DOWNLOAD_PATH,
        config=config, account_id=account_id, json_body=body,
    )
    download_url = (
        data.get("downloadUrl")
        or data.get("download_url")
        or ""
    )
    if not download_url:
        raise DingTalkApiError(
            "DingTalk media download code exchange returned no URL",
            response=data,
        )
    filename = data.get("fileName") or data.get("filename") or None
    return str(download_url), (str(filename) if filename else None)


async def _download_remote_bytes_limited(
    url: str, max_bytes: int,
) -> tuple[bytes, Optional[str]]:
    """Stream *url* into bytes, aborting when the body exceeds *max_bytes*."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            content_length = headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise DingTalkInboundMediaTooLarge(
                    f"DingTalk inbound media too large: >{max_bytes // (1024 * 1024)}MB"
                )
            cd = headers.get("content-disposition") or ""
            cd_match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.I)
            cd_filename = unquote(cd_match.group(1).strip()) if cd_match else None

            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(8192):
                total += len(chunk)
                if total > max_bytes:
                    raise DingTalkInboundMediaTooLarge(
                        f"DingTalk inbound media too large: >{max_bytes // (1024 * 1024)}MB"
                    )
                chunks.append(chunk)
    return b"".join(chunks), cd_filename


def _guess_filename(
    msg: InboundMessage,
    media_ref: str,
    cd_filename: Optional[str],
) -> str:
    """Resolve a filename from message raw body / header / URL fallback."""
    raw = msg.raw if isinstance(msg.raw, dict) else {}
    for key in ("fileName", "filename", "name"):
        candidate = str(raw.get(key) or "").strip()
        if candidate:
            return _sanitize_filename(candidate)
    if raw.get("msgtype") == "richText" and isinstance(raw.get("rich_text_list"), list):
        for item in raw["rich_text_list"]:
            if isinstance(item, dict):
                for key in ("fileName", "filename", "name"):
                    candidate = str(item.get(key) or "").strip()
                    if candidate:
                        return _sanitize_filename(candidate)
    if cd_filename:
        return _sanitize_filename(cd_filename)
    url_path = urlparse(media_ref).path if not _is_download_code(media_ref) else ""
    url_basename = os.path.basename(url_path)
    if url_basename and "." in url_basename:
        return _sanitize_filename(url_basename)
    msg_id = msg.message_id or "unknown"
    return _sanitize_filename(f"dingtalk_{msg_id[:12]}")


async def download_inbound_media(
    msg: InboundMessage,
    config: dict,
    *,
    max_bytes: int = _DEFAULT_MAX_INBOUND_MEDIA_BYTES,
) -> Optional[DownloadedInboundMedia]:
    media_ref = msg.media_url or ""
    if not media_ref:
        return None

    try:
        if _is_download_code(media_ref):
            download_url, name_hint = await _exchange_download_code(
                config=config, account_id=msg.account_id,
                download_code=media_ref,
            )
        else:
            download_url = media_ref
            name_hint = None

        buffer, cd_filename = await _download_remote_bytes_limited(
            download_url, max_bytes,
        )
    except DingTalkInboundMediaTooLarge as e:
        log.warning("dingtalk.media.file_too_large", {
            "message_id": msg.message_id, "error": str(e),
        })
        return None
    except DingTalkApiError as e:
        log.warning("dingtalk.media.exchange_failed", {
            "message_id": msg.message_id, "error": str(e),
        })
        return None
    except Exception as e:
        log.warning("dingtalk.media.download_failed", {
            "message_id": msg.message_id, "error": str(e),
        })
        return None

    filename = _guess_filename(msg, media_ref, cd_filename or name_hint)
    if "." not in filename:
        guessed_mime = _guess_mime_from_ext(filename)
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
            "channel": "dingtalk",
            "account_id": msg.account_id,
            "message_id": msg.message_id,
            "media_url": msg.media_url,
            "download_code": media_ref if _is_download_code(media_ref) else None,
        },
    )
