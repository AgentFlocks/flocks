"""LINE inbound media download helpers."""

from __future__ import annotations

import datetime
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from flocks.channel.base import InboundMessage
from flocks.channel.media_filename import sanitize_filename
from flocks.utils.log import Log

from .client import LineClient
from .config import coerce_int, resolve_api_roots, resolve_credentials

log = Log.create(service="channel.line.media")

_DEFAULT_MAX_INBOUND_MEDIA_BYTES = 20 * 1024 * 1024


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
        / "line"
        / account_id
        / datetime.date.today().isoformat()
    )


def _parse_line_uri(uri: str) -> tuple[Optional[str], Optional[str]]:
    if not uri.startswith("line://"):
        return None, None
    rest = uri[len("line://"):]
    if "/" not in rest:
        return None, None
    kind, _, message_id = rest.partition("/")
    return kind or None, message_id or None


def _extension_for(kind: str, mime: str, raw: dict[str, Any]) -> str:
    if kind == "file":
        name = str((raw.get("message") or {}).get("fileName") or "").strip()
        if "." in name:
            return os.path.splitext(name)[1]
    ext = mimetypes.guess_extension(mime or "") or ""
    if ext == ".jpe":
        return ".jpg"
    if ext:
        return ext
    if kind == "image":
        return ".jpg"
    if kind == "audio":
        return ".m4a"
    if kind == "video":
        return ".mp4"
    return ".bin"


def _filename(msg: InboundMessage, kind: str, mime: str) -> str:
    raw = msg.raw if isinstance(msg.raw, dict) else {}
    message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    if kind == "file":
        name = str(message.get("fileName") or message.get("file_name") or "").strip()
        if name:
            line_message_id = str(message.get("id") or "").strip()
            if not line_message_id:
                _, line_message_id = _parse_line_uri(msg.media_url or "")
            safe_line_id = sanitize_filename(line_message_id or msg.message_id, max_chars=48)
            return sanitize_filename(f"line_{safe_line_id}_{name}")
    ext = _extension_for(kind, mime, raw)
    safe_id = sanitize_filename(msg.message_id.replace(":", "_"))[:80]
    return sanitize_filename(f"line_{kind}_{safe_id}{ext}")


async def download_inbound_media(
    msg: InboundMessage,
    config: dict,
    *,
    max_bytes: int = _DEFAULT_MAX_INBOUND_MEDIA_BYTES,
) -> Optional[DownloadedInboundMedia]:
    kind, message_id = _parse_line_uri(msg.media_url or "")
    if not kind or not message_id:
        log.warning("line.media.invalid_uri", {"media_url": (msg.media_url or "")[:200]})
        return None

    token, _ = resolve_credentials(config)
    if not token:
        log.warning("line.media.no_token", {
            "channel_id": msg.channel_id,
            "account_id": msg.account_id,
        })
        return None

    api_root, data_api_root = resolve_api_roots(config)
    timeout = max(coerce_int(config.get("timeoutSeconds"), 30), 1)
    client = LineClient(
        token,
        api_root=api_root,
        data_api_root=data_api_root,
        timeout=float(timeout),
    )
    data, mime = await client.fetch_content(message_id, max_bytes=max_bytes)
    filename = _filename(msg, kind, mime)
    storage_dir = _media_storage_dir(msg.account_id or "default")
    storage_dir.mkdir(parents=True, exist_ok=True)
    path = storage_dir / filename
    path.write_bytes(data)

    return DownloadedInboundMedia(
        filename=filename,
        mime=mime or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        url=path.resolve().as_uri(),
        source={
            "channel": "line",
            "message_id": msg.message_id,
            "line_message_id": message_id,
            "kind": kind,
        },
    )
