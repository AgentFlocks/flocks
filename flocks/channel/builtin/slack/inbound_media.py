"""Slack inbound media download helpers."""

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
from flocks.channel.media_filename import sanitize_filename
from flocks.utils.log import Log

from .config import resolve_bot_token

log = Log.create(service="channel.slack.media")

_DEFAULT_MAX_INBOUND_MEDIA_BYTES = 30 * 1024 * 1024


class SlackInboundMediaTooLarge(ValueError):
    """Slack 入站媒体超过允许大小。"""


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
        / "slack"
        / account_id
        / datetime.date.today().isoformat()
    )


def _sanitize_filename(name: str) -> str:
    return sanitize_filename(name)


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


def _max_size_error(max_bytes: int) -> SlackInboundMediaTooLarge:
    return SlackInboundMediaTooLarge(
        f"Slack inbound media too large: >{max_bytes // (1024 * 1024)}MB"
    )


def _iter_raw_files(msg: InboundMessage) -> list[dict[str, Any]]:
    raw = msg.raw if isinstance(msg.raw, dict) else {}
    files = raw.get("files")
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def _find_file_object(msg: InboundMessage, media_url: str) -> dict[str, Any]:
    parsed = urlparse(media_url)
    ref_file_id = parsed.path.lstrip("/") if parsed.scheme == "slack" else ""
    for item in _iter_raw_files(msg):
        if ref_file_id and str(item.get("id") or "") == ref_file_id:
            return item
        for key in ("url_private_download", "url_private"):
            if media_url and str(item.get(key) or "") == media_url:
                return item
    return {}


def _guess_filename(
    msg: InboundMessage,
    media_url: str,
    file_obj: dict[str, Any],
    cd_filename: Optional[str] = None,
) -> str:
    for key in ("name", "title"):
        candidate = str(file_obj.get(key) or "").strip()
        if candidate:
            return _sanitize_filename(candidate)
    if cd_filename:
        return _sanitize_filename(cd_filename)
    basename = os.path.basename(urlparse(media_url).path)
    if basename and "." in basename:
        return _sanitize_filename(unquote(basename))
    file_id = str(file_obj.get("id") or "").strip()
    suffix = file_id or msg.message_id or "unknown"
    return _sanitize_filename(f"slack_file_{suffix[:16]}")


async def _resolve_slack_file_url(
    *,
    media_url: str,
    bot_token: str,
) -> tuple[str, dict[str, Any]]:
    parsed = urlparse(media_url)
    if parsed.scheme != "slack":
        return media_url, {}
    file_id = parsed.path.lstrip("/")
    if not file_id:
        return "", {}
    try:
        from slack_sdk.web.async_client import AsyncWebClient
    except Exception as exc:
        raise RuntimeError("slack-sdk is required to resolve Slack file metadata") from exc

    response = await AsyncWebClient(token=bot_token).files_info(file=file_id)
    file_obj = response.get("file") if hasattr(response, "get") else None
    if not isinstance(file_obj, dict):
        raise RuntimeError("Slack files.info returned no file object")
    url = str(file_obj.get("url_private_download") or file_obj.get("url_private") or "").strip()
    if not url:
        raise RuntimeError("Slack files.info returned no private download URL")
    return url, file_obj


async def _download_file_limited(
    *,
    media_url: str,
    bot_token: str,
    max_bytes: int,
) -> tuple[bytes, Optional[str], Optional[str]]:
    chunks: list[bytes] = []
    total = 0
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        async with client.stream(
            "GET",
            media_url,
            headers={"Authorization": f"Bearer {bot_token}"},
        ) as resp:
            resp.raise_for_status()
            headers = resp.headers
            content_type = headers.get("content-type") or headers.get("Content-Type")
            if content_type and "text/html" in content_type.lower():
                raise RuntimeError(
                    "Slack returned HTML instead of media; check bot token scopes and file permissions"
                )
            content_length = headers.get("content-length") or headers.get("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise _max_size_error(max_bytes)
            content_disposition = (
                headers.get("content-disposition")
                or headers.get("Content-Disposition")
                or ""
            )
            cd_filename = _filename_from_content_disposition(content_disposition)
            async for chunk in resp.aiter_bytes(8192):
                total += len(chunk)
                if total > max_bytes:
                    raise _max_size_error(max_bytes)
                chunks.append(chunk)
    return b"".join(chunks), cd_filename, content_type


async def download_inbound_media(
    msg: InboundMessage,
    config: dict,
    *,
    max_bytes: int = _DEFAULT_MAX_INBOUND_MEDIA_BYTES,
) -> Optional[DownloadedInboundMedia]:
    media_url = msg.media_url or ""
    if not media_url:
        return None

    bot_token = resolve_bot_token(config)
    if not bot_token:
        log.warning("slack.media.no_token", {
            "account_id": msg.account_id,
            "message_id": msg.message_id,
        })
        return None

    file_obj = _find_file_object(msg, media_url)
    try:
        resolved_url, resolved_file = await _resolve_slack_file_url(
            media_url=media_url,
            bot_token=bot_token,
        )
        if resolved_file:
            file_obj = {**file_obj, **resolved_file}
        if not resolved_url:
            return None
        buffer, cd_filename, response_mime = await _download_file_limited(
            media_url=resolved_url,
            bot_token=bot_token,
            max_bytes=max_bytes,
        )
    except SlackInboundMediaTooLarge as exc:
        log.warning("slack.media.file_too_large", {
            "message_id": msg.message_id,
            "error": str(exc),
        })
        return None
    except Exception as exc:
        log.warning("slack.media.download_failed", {
            "message_id": msg.message_id,
            "media_url": media_url[:200],
            "error": str(exc),
        })
        return None

    filename = _guess_filename(msg, media_url, file_obj, cd_filename)
    mime = (
        msg.media_mime
        or str(file_obj.get("mimetype") or "").strip()
        or response_mime
        or _guess_mime_from_ext(filename)
        or "application/octet-stream"
    )
    if "." not in filename:
        ext = mimetypes.guess_extension(mime.split(";", 1)[0].strip()) or ""
        if ext:
            filename = f"{filename}{ext}"

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
            "channel": "slack",
            "account_id": msg.account_id,
            "message_id": msg.message_id,
            "media_url": msg.media_url,
            "file_id": str(file_obj.get("id") or "") or None,
        },
    )
