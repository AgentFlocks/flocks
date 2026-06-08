"""
Telegram inbound media download helpers.

The Telegram Bot API gives the bot a stable ``file_id`` for every file
the bot can see; the actual bytes are fetched in two steps:

1. ``GET /getFile?file_id=<id>`` → ``{"file_path": "documents/file_0.pdf"}``
2. ``GET https://api.telegram.org/file/bot<token>/<file_path>``

This module wraps those two calls and returns a local file URI the
dispatcher can hand to the session pipeline as a
:class:`flocks.session.message.FilePart`.
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
from flocks.utils.log import Log
from .config import resolve_account_config, resolve_api_base

log = Log.create(service="channel.telegram.media")

_DEFAULT_MAX_INBOUND_MEDIA_BYTES = 20 * 1024 * 1024


class TelegramInboundMediaTooLarge(ValueError):
    """Telegram 入站媒体超过允许大小。"""


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
        / "telegram"
        / account_id
        / datetime.date.today().isoformat()
    )


def _guess_mime_from_ext(filename: str) -> Optional[str]:
    _, ext = os.path.splitext(filename)
    if ext:
        return mimetypes.guess_type(filename)[0]
    return None


def _parse_telegram_uri(uri: str) -> tuple[Optional[str], Optional[str]]:
    """Parse ``telegram://<kind>/<file_id>`` into ``(kind, file_id)``."""
    if not uri.startswith("telegram://"):
        return None, None
    rest = uri[len("telegram://"):]
    if "/" not in rest:
        return None, None
    kind, _, file_id = rest.partition("/")
    return kind or None, file_id or None


def _resolve_credentials(
    config: dict, account_id: Optional[str],
) -> Optional[str]:
    """Look up the bot token for the given account (helper for tests)."""
    if not isinstance(config, dict):
        return None
    if account_id and account_id in (config.get("accounts") or {}):
        return (
            config["accounts"][account_id].get("botToken")
            or config.get("botToken")
        )
    return config.get("botToken") or config.get("BOT_TOKEN")


async def _get_file_path(
    *,
    bot_token: str,
    api_base: str,
    file_id: str,
    timeout: float,
) -> tuple[str, str]:
    """Call ``/getFile`` and return ``(file_path, file_id)``."""
    url = f"{api_base.rstrip('/')}/getFile"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            url, params={"file_id": file_id},
        )
    data = resp.json() if resp.content else {}
    if not resp.is_success or not data.get("ok"):
        raise RuntimeError(
            f"Telegram getFile failed: {data.get('description') or resp.text}"
        )
    result = data.get("result") or {}
    file_path = coerce_str(result.get("file_path"))
    if not file_path:
        raise RuntimeError("Telegram getFile returned no file_path")
    return file_path, file_id


def coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


async def _download_file(
    *,
    download_base: str,
    file_path: str,
    max_bytes: int,
    timeout: float,
) -> bytes:
    url = f"{download_base.rstrip('/')}/{file_path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise TelegramInboundMediaTooLarge(
                    f"Telegram inbound media too large: >{max_bytes // (1024 * 1024)}MB"
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(8192):
                total += len(chunk)
                if total > max_bytes:
                    raise TelegramInboundMediaTooLarge(
                        f"Telegram inbound media too large: >{max_bytes // (1024 * 1024)}MB"
                    )
                chunks.append(chunk)
    return b"".join(chunks)


def _guess_filename(
    msg: InboundMessage,
    kind: str,
    file_id: str,
    file_path: str,
) -> str:
    raw = msg.raw if isinstance(msg.raw, dict) else {}

    # Document blocks carry a `file_name`.  Photos have no name.
    if kind in {"document", "audio", "video", "animation", "voice"}:
        block = raw.get(kind)
        if isinstance(block, dict):
            for key in ("file_name", "fileName"):
                candidate = str(block.get(key) or "").strip()
                if candidate:
                    return _sanitize_filename(candidate)
        for key in ("file_name", "fileName"):
            candidate = str(raw.get(key) or "").strip()
            if candidate:
                return _sanitize_filename(candidate)
    # file_path looks like ``documents/file_42.pdf`` — use the basename.
    url_basename = os.path.basename(file_path)
    if url_basename and "." in url_basename:
        return _sanitize_filename(unquote(url_basename))
    # photo kinds: synthesise a stable name from message_id + file_id hash.
    if kind == "photo":
        suffix = file_id[-6:] if file_id else "x"
        return _sanitize_filename(f"photo_{msg.message_id}_{suffix}.jpg")
    return _sanitize_filename(f"telegram_{kind}_{file_id[:12]}")


def _legacy_api_root(
    config: dict[str, Any],
    account_id: Optional[str],
    bot_token: str,
) -> Optional[str]:
    if not isinstance(config, dict):
        return None
    if account_id and isinstance(config.get("accounts"), dict):
        acc = config["accounts"].get(account_id) or {}
        base = acc.get("apiBase") or acc.get("api_base")
        if base:
            return _normalize_legacy_api_root(str(base), bot_token)
    base = config.get("apiBase") or config.get("api_base")
    if base:
        return _normalize_legacy_api_root(str(base), bot_token)
    return None


def _normalize_legacy_api_root(base: str, bot_token: str) -> str:
    normalized = base.rstrip("/")
    if "{token}" in normalized:
        normalized = normalized.replace("{token}", "").rstrip("/")
    if normalized.endswith(bot_token):
        normalized = normalized[: -len(bot_token)].rstrip("/")
    if normalized.endswith("/bot"):
        return normalized[: -len("/bot")]
    return normalized


def _resolve_api_roots(
    config: dict[str, Any],
    account_id: Optional[str],
    bot_token: str,
) -> tuple[str, str]:
    try:
        _, account = resolve_account_config(config, account_id)
        api_root = coerce_str(account.get("apiRoot"))
        if api_root:
            api_call_base = resolve_api_base(account, bot_token)
            api_root = api_call_base.rsplit("/bot", 1)[0]
        else:
            legacy_root = _legacy_api_root(config, account_id, bot_token)
            api_root = legacy_root or "https://api.telegram.org"
            api_call_base = f"{api_root.rstrip('/')}/bot{bot_token}"
    except ValueError:
        legacy_root = _legacy_api_root(config, account_id, bot_token)
        api_root = legacy_root or "https://api.telegram.org"
        api_call_base = f"{api_root.rstrip('/')}/bot{bot_token}"
    download_base = f"{api_root.rstrip('/')}/file/bot{bot_token}"
    return api_call_base, download_base


async def download_inbound_media(
    msg: InboundMessage,
    config: dict,
    *,
    max_bytes: int = _DEFAULT_MAX_INBOUND_MEDIA_BYTES,
) -> Optional[DownloadedInboundMedia]:
    media_ref = msg.media_url or ""
    if not media_ref:
        return None

    kind, file_id = _parse_telegram_uri(media_ref)
    if not file_id:
        log.warning("telegram.media.invalid_uri", {"media_url": media_ref[:200]})
        return None

    bot_token = _resolve_credentials(config, msg.account_id)
    if not bot_token:
        log.warning("telegram.media.no_token", {
            "channel_id": msg.channel_id,
            "account_id": msg.account_id,
        })
        return None

    api_call_base, download_base = _resolve_api_roots(
        config, msg.account_id, bot_token,
    )

    try:
        file_path, _ = await _get_file_path(
            bot_token=bot_token, api_base=api_call_base,
            file_id=file_id, timeout=30.0,
        )
        buffer = await _download_file(
            download_base=download_base, file_path=file_path,
            max_bytes=max_bytes, timeout=60.0,
        )
    except TelegramInboundMediaTooLarge as e:
        log.warning("telegram.media.file_too_large", {
            "message_id": msg.message_id, "error": str(e),
        })
        return None
    except Exception as e:
        log.warning("telegram.media.download_failed", {
            "message_id": msg.message_id, "error": str(e),
        })
        return None

    filename = _guess_filename(msg, kind or "document", file_id, file_path)
    if "." not in filename:
        guessed_mime = _guess_mime_from_ext(filename)
        ext = mimetypes.guess_extension(guessed_mime) if guessed_mime else ""
        if ext:
            filename = f"{filename}{ext}"
    mime = _guess_mime_from_ext(filename) or _guess_mime_for_kind(kind or "document")

    storage_dir = _media_storage_dir(msg.account_id or "default")
    storage_dir.mkdir(parents=True, exist_ok=True)
    msg_id = msg.message_id or "unknown"
    file_path_local = storage_dir / _sanitize_filename(f"{msg_id}_{filename}")
    file_path_local.write_bytes(buffer)

    return DownloadedInboundMedia(
        filename=filename,
        mime=mime,
        url=file_path_local.resolve().as_uri(),
        source={
            "channel": "telegram",
            "account_id": msg.account_id,
            "message_id": msg.message_id,
            "media_url": msg.media_url,
            "file_id": file_id,
            "kind": kind,
        },
    )


def _guess_mime_for_kind(kind: str) -> str:
    return {
        "photo": "image/jpeg",
        "voice": "audio/ogg",
        "audio": "audio/mpeg",
        "video": "video/mp4",
        "animation": "video/mp4",
        "document": "application/octet-stream",
    }.get(kind, "application/octet-stream")
