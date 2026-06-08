"""
DingTalk outbound media helpers.

Uploads a local or remote media file via the DingTalk OAPI robot
``/v1.0/robot/messageFiles/upload`` endpoint and returns a
``PreparedDingTalkMedia`` with the ``media_id`` + ``downloadCode``
the channel's :meth:`send_media` needs to construct a ``file`` /
``image`` message.
"""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

import httpx

from flocks.channel.media_filename import sanitize_filename
from flocks.channel.builtin.dingtalk.client import (
    DingTalkApiError,
    api_request_for_account,
)
from flocks.channel.builtin.dingtalk.config import resolve_account_credentials
from flocks.utils.log import Log

log = Log.create(service="channel.dingtalk.send_media")

_DEFAULT_MAX_MEDIA_BYTES = 20 * 1024 * 1024
UPLOAD_PATH = "/v1.0/robot/messageFiles/upload"
DingTalkOutboundMediaType = Literal["image", "file", "voice", "video"]


@dataclass
class PreparedDingTalkMedia:
    data: bytes
    filename: str
    mime: str
    media_type: DingTalkOutboundMediaType
    media_id: str
    download_code: str


def _sanitize_filename(name: str) -> str:
    return sanitize_filename(name)


def _media_type_from_filename(filename: str) -> DingTalkOutboundMediaType:
    mime = mimetypes.guess_type(filename)[0] or ""
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "voice"
    if mime.startswith("video/"):
        return "video"
    return "file"


def _path_from_media_url(media_url: str) -> Path | None:
    parsed = urlparse(media_url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme:
        return None
    return Path(media_url)


def _read_local_file_limited(path: Path, max_bytes: int) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"DingTalk media file not found: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"DingTalk outbound media too large: >{max_bytes // (1024 * 1024)}MB"
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
                    f"DingTalk outbound media too large: >{max_bytes // (1024 * 1024)}MB"
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(8192):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"DingTalk outbound media too large: >{max_bytes // (1024 * 1024)}MB"
                    )
                chunks.append(chunk)
    basename = os.path.basename(urlparse(url).path) or "attachment"
    return b"".join(chunks), _sanitize_filename(unquote(basename))


async def _read_payload(
    media_url: str, max_bytes: int,
) -> tuple[bytes, str]:
    parsed = urlparse(media_url)
    if parsed.scheme in ("http", "https"):
        return await _fetch_http_file_limited(media_url, max_bytes)
    path = _path_from_media_url(media_url)
    if path is None:
        raise ValueError(f"Unsupported DingTalk media URL scheme: {parsed.scheme}")
    data = _read_local_file_limited(path, max_bytes)
    return data, _sanitize_filename(path.name)


async def upload_dingtalk_media(
    *,
    config: dict,
    account_id: str | None,
    data: bytes,
    filename: str,
) -> tuple[str, str]:
    """Upload *data* via the OAPI and return ``(media_id, download_code)``."""
    app_key, app_secret, robot_code = resolve_account_credentials(config, account_id)
    if not app_key or not app_secret:
        raise DingTalkApiError(
            "DingTalk appKey/appSecret not configured"
            + (f" for account '{account_id}'" if account_id else ""),
        )
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    files = {"media": (filename, data, mime)}
    form: dict[str, str] = {"robotCode": robot_code}
    # The OAPI endpoint accepts a multipart/form-data upload. We invoke it
    # via the raw client so we can pass through the file payload — the
    # generic ``api_request`` helper is JSON-only.
    from flocks.channel.builtin.dingtalk.client import (
        _get_http_client,
        get_access_token,
    )
    token = await get_access_token(app_key, app_secret)
    client = await _get_http_client()
    resp = await client.post(
        f"https://api.dingtalk.com{UPLOAD_PATH}",
        headers={"x-acs-dingtalk-access-token": token},
        data=form,
        files=files,
        timeout=60.0,
    )
    if resp.status_code >= 400:
        try:
            err = resp.json()
        except Exception:
            err = {}
        raise DingTalkApiError(
            f"DingTalk media upload failed: HTTP {resp.status_code}",
            http_status=resp.status_code,
            response=err,
        )
    data_obj = resp.json() if resp.content else {}
    media_id = (
        data_obj.get("mediaId")
        or data_obj.get("media_id")
        or ""
    )
    download_code = (
        data_obj.get("downloadCode")
        or data_obj.get("download_code")
        or ""
    )
    if not media_id or not download_code:
        raise DingTalkApiError(
            "DingTalk media upload returned no mediaId/downloadCode",
            response=data_obj,
        )
    return str(media_id), str(download_code)


async def prepare_dingtalk_media(
    *,
    config: dict,
    account_id: str | None,
    media_url: str,
    max_bytes: int = _DEFAULT_MAX_MEDIA_BYTES,
) -> PreparedDingTalkMedia:
    """Read *media_url*, upload it, and return the metadata needed for send."""
    data, filename = await _read_payload(media_url, max_bytes)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    media_type = _media_type_from_filename(filename)
    media_id, download_code = await upload_dingtalk_media(
        config=config, account_id=account_id,
        data=data, filename=filename,
    )
    return PreparedDingTalkMedia(
        data=data,
        filename=filename,
        mime=mime,
        media_type=media_type,
        media_id=media_id,
        download_code=download_code,
    )
