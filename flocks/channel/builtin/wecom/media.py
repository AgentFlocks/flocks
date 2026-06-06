"""
WeCom outbound media helpers.
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

_DEFAULT_MAX_MEDIA_BYTES = 50 * 1024 * 1024
WeComOutboundMediaType = Literal["file", "image", "voice", "video"]


@dataclass
class PreparedWeComMedia:
    data: bytes
    filename: str
    media_type: WeComOutboundMediaType


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return cleaned[:120] or "attachment"


def _media_type_from_filename(filename: str) -> WeComOutboundMediaType:
    mime = mimetypes.guess_type(filename)[0] or ""
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "voice"
    if mime.startswith("video/"):
        return "video"
    return "file"


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    basename = os.path.basename(parsed.path)
    return _sanitize_filename(unquote(basename)) if basename else "attachment"


def _path_from_media_url(media_url: str) -> Path | None:
    parsed = urlparse(media_url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme:
        return None
    return Path(media_url)


def _read_local_file_limited(path: Path, max_bytes: int) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"WeCom media file not found: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"WeCom outbound media too large: >{max_bytes // (1024 * 1024)}MB")
    return path.read_bytes()


async def _fetch_http_file_limited(url: str, max_bytes: int) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("GET", url, follow_redirects=True) as resp:
            resp.raise_for_status()
            content_length = resp.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError(f"WeCom outbound media too large: >{max_bytes // (1024 * 1024)}MB")

            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(8192):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"WeCom outbound media too large: >{max_bytes // (1024 * 1024)}MB")
                chunks.append(chunk)

    return b"".join(chunks), _filename_from_url(url)


async def prepare_wecom_media(
    media_url: str,
    *,
    max_bytes: int = _DEFAULT_MAX_MEDIA_BYTES,
) -> PreparedWeComMedia:
    parsed = urlparse(media_url)
    if parsed.scheme in ("http", "https"):
        data, filename = await _fetch_http_file_limited(media_url, max_bytes)
    else:
        path = _path_from_media_url(media_url)
        if path is None:
            raise ValueError(f"Unsupported WeCom media URL scheme: {parsed.scheme}")
        data = _read_local_file_limited(path, max_bytes)
        filename = _sanitize_filename(path.name)

    return PreparedWeComMedia(
        data=data,
        filename=filename,
        media_type=_media_type_from_filename(filename),
    )
