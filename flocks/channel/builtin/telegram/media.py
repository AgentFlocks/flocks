"""
Telegram outbound media helpers.

Prepares a local or remote media file for delivery through the Bot API
and exposes a small ``PreparedTelegramMedia`` dataclass carrying the
bytes, filename, and inferred ``kind`` (photo / document / video / etc.)
so :meth:`TelegramChannel.send_media` can pick the right endpoint.
"""

from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

import httpx

DingTalkOutboundMediaType = Literal[  # type: ignore[misc]
    "photo", "document", "video", "audio", "voice", "animation",
]
TelegramOutboundMediaType = Literal[
    "photo", "document", "video", "audio", "voice", "animation",
]

_DEFAULT_MAX_MEDIA_BYTES = 50 * 1024 * 1024  # 50MB — Telegram Bot API cap


@dataclass
class PreparedTelegramMedia:
    data: bytes
    filename: str
    mime: str
    kind: TelegramOutboundMediaType


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return cleaned[:120] or "attachment"


def _media_kind_from_filename(filename: str) -> TelegramOutboundMediaType:
    mime = mimetypes.guess_type(filename)[0] or ""
    if mime.startswith("image/"):
        # Telegram distinguishes photo (jpeg-only, no animation) from
        # animation (GIF / H.264).  Re-use the file name + mime to pick.
        if mime in {"image/gif"} or filename.lower().endswith(".gif"):
            return "animation"
        return "photo"
    if mime.startswith("video/"):
        return "video"
    if mime == "audio/ogg" or filename.lower().endswith(".ogg"):
        return "voice"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


def _path_from_media_url(media_url: str) -> Path | None:
    parsed = urlparse(media_url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme:
        return None
    return Path(media_url)


def _read_local_file_limited(path: Path, max_bytes: int) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"Telegram media file not found: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"Telegram outbound media too large: >{max_bytes // (1024 * 1024)}MB"
        )
    return path.read_bytes()


async def _fetch_http_file_limited(
    url: str, max_bytes: int,
) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise ValueError(
                    f"Telegram outbound media too large: >{max_bytes // (1024 * 1024)}MB"
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(8192):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"Telegram outbound media too large: >{max_bytes // (1024 * 1024)}MB"
                    )
                chunks.append(chunk)
    basename = os.path.basename(urlparse(url).path) or "attachment"
    return b"".join(chunks), _sanitize_filename(unquote(basename))


async def prepare_telegram_media(
    media_url: str,
    *,
    kind_override: TelegramOutboundMediaType | None = None,
    max_bytes: int = _DEFAULT_MAX_MEDIA_BYTES,
) -> PreparedTelegramMedia:
    """Read *media_url* (local or remote) and infer its Telegram kind.

    *kind_override* lets the caller force ``document`` (e.g. for an image
    that should bypass the photo dimension limits and use the generic
    file-upload path).
    """
    parsed = urlparse(media_url)
    if parsed.scheme in ("http", "https"):
        data, filename = await _fetch_http_file_limited(media_url, max_bytes)
    else:
        path = _path_from_media_url(media_url)
        if path is None:
            raise ValueError(f"Unsupported Telegram media URL scheme: {parsed.scheme}")
        data = _read_local_file_limited(path, max_bytes)
        filename = _sanitize_filename(path.name)

    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    kind = kind_override or _media_kind_from_filename(filename)
    return PreparedTelegramMedia(
        data=data, filename=filename, mime=mime, kind=kind,
    )
