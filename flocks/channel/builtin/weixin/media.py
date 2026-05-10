"""
High-level media orchestration for the Weixin channel.

- ``MediaCache`` writes decrypted inbound bytes to a content-addressed disk
  cache and returns local ``file://`` URIs that can travel through the rest
  of the Flocks pipeline.
- ``download_inbound_item`` dispatches on iLink item type to fetch + decrypt
  + cache an image / video / file / voice payload, returning ``(local_uri,
  mime_type)``.
- ``send_outbound_file`` encrypts a local file, requests a CDN upload slot,
  uploads the ciphertext, and posts the media item via ``send_media_message``.
- ``fetch_remote_to_temp`` resolves remote URLs to local temp files (used
  when ``OutboundContext.media_url`` is an http(s) URL rather than a path).
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import secrets
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from flocks.utils.log import Log

from .cdn import (
    cdn_upload_url,
    download_and_decrypt_media,
    download_bytes,
    media_reference,
    upload_ciphertext,
)
from .client import get_upload_url, send_media_message
from .config import (
    ITEM_FILE,
    ITEM_IMAGE,
    ITEM_VIDEO,
    ITEM_VOICE,
    MEDIA_DOWNLOAD_FILE_TIMEOUT_S,
    MEDIA_DOWNLOAD_IMAGE_TIMEOUT_S,
    MEDIA_DOWNLOAD_VIDEO_TIMEOUT_S,
    MEDIA_DOWNLOAD_VOICE_TIMEOUT_S,
    MEDIA_FILE,
    MEDIA_IMAGE,
    MEDIA_REMOTE_FETCH_TIMEOUT_S,
    MEDIA_UPLOAD_TIMEOUT_S,
    MEDIA_VIDEO,
    MEDIA_VOICE,
)
from .crypto import aes128_ecb_encrypt, aes_padded_size
from .inbound import safe_id
from .store import ensure_state_dir

if TYPE_CHECKING:
    import aiohttp

log = Log.create(service="channel.weixin.media")


# ---------------------------------------------------------------------------
# Local content-addressed cache for inbound media
# ---------------------------------------------------------------------------

class MediaCache:
    """Write decrypted inbound bytes to ``<state_dir>/media/`` and yield URIs.

    Content-addressed by sha256 of the plaintext to deduplicate re-deliveries
    of the same image / file across restarts.
    """

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self._root = ensure_state_dir(data_dir) / "media"
        self._root.mkdir(parents=True, exist_ok=True)

    def write(self, data: bytes, suffix: str, original_name: Optional[str] = None) -> str:
        """Cache *data* under sha256(data) + *suffix* and return a ``file://`` URI."""
        digest = hashlib.sha256(data).hexdigest()
        if original_name:
            stem = Path(original_name).stem.replace("/", "_") or "media"
            name = f"{stem}-{digest[:16]}{suffix}"
        else:
            name = f"{digest}{suffix}"
        path = self._root / name
        if not path.exists():
            try:
                path.write_bytes(data)
            except Exception as exc:
                log.warning("weixin.media.cache_write_error", {"error": str(exc)})
                return ""
        return path.resolve().as_uri()


# ---------------------------------------------------------------------------
# Inbound dispatch
# ---------------------------------------------------------------------------

def is_downloadable_media_item(item: dict) -> bool:
    """Return True iff *item* is a media item that ``download_inbound_item``
    would actually fetch (i.e. would produce bytes, not text-only fallback).
    """
    item_type = item.get("type")
    if item_type in (ITEM_IMAGE, ITEM_VIDEO, ITEM_FILE):
        return True
    if item_type == ITEM_VOICE:
        # Voice items already transcribed to text are not downloaded as media.
        voice_item = item.get("voice_item") or {}
        return not voice_item.get("text")
    return False


async def download_inbound_item(
    session: "aiohttp.ClientSession",
    *,
    item: dict,
    cdn_base_url: str,
    cache: MediaCache,
    sender_log_id: str = "?",
) -> Optional[tuple[str, str]]:
    """Download + decrypt + cache a single ``item_list`` entry.

    Returns ``(local_file_uri, mime_type)`` on success, or ``None`` for non-media
    items / failures (logged at WARN).
    """
    item_type = item.get("type")
    try:
        if item_type == ITEM_IMAGE:
            return await _download_image(session, item, cdn_base_url, cache)
        if item_type == ITEM_VIDEO:
            return await _download_video(session, item, cdn_base_url, cache)
        if item_type == ITEM_FILE:
            return await _download_file(session, item, cdn_base_url, cache)
        if item_type == ITEM_VOICE:
            return await _download_voice(session, item, cdn_base_url, cache)
    except Exception as exc:
        log.warning("weixin.media.download_failed", {
            "type": item_type, "from": sender_log_id, "error": str(exc),
        })
    return None


async def _download_image(
    session: "aiohttp.ClientSession",
    item: dict,
    cdn_base_url: str,
    cache: MediaCache,
) -> Optional[tuple[str, str]]:
    image_item = item.get("image_item") or {}
    media = image_item.get("media") or {}
    aes_key = _normalize_image_aes_key(image_item, media)
    data = await download_and_decrypt_media(
        session,
        cdn_base_url=cdn_base_url,
        encrypted_query_param=media.get("encrypt_query_param"),
        aes_key_b64=aes_key,
        full_url=media.get("full_url"),
        timeout_seconds=MEDIA_DOWNLOAD_IMAGE_TIMEOUT_S,
    )
    uri = cache.write(data, ".jpg")
    return (uri, "image/jpeg") if uri else None


async def _download_video(
    session: "aiohttp.ClientSession",
    item: dict,
    cdn_base_url: str,
    cache: MediaCache,
) -> Optional[tuple[str, str]]:
    media = media_reference(item, "video_item")
    data = await download_and_decrypt_media(
        session,
        cdn_base_url=cdn_base_url,
        encrypted_query_param=media.get("encrypt_query_param"),
        aes_key_b64=media.get("aes_key"),
        full_url=media.get("full_url"),
        timeout_seconds=MEDIA_DOWNLOAD_VIDEO_TIMEOUT_S,
    )
    uri = cache.write(data, ".mp4")
    return (uri, "video/mp4") if uri else None


async def _download_file(
    session: "aiohttp.ClientSession",
    item: dict,
    cdn_base_url: str,
    cache: MediaCache,
) -> Optional[tuple[str, str]]:
    file_item = item.get("file_item") or {}
    media = file_item.get("media") or {}
    filename = str(file_item.get("file_name") or "document.bin")
    mime = mime_from_filename(filename)
    data = await download_and_decrypt_media(
        session,
        cdn_base_url=cdn_base_url,
        encrypted_query_param=media.get("encrypt_query_param"),
        aes_key_b64=media.get("aes_key"),
        full_url=media.get("full_url"),
        timeout_seconds=MEDIA_DOWNLOAD_FILE_TIMEOUT_S,
    )
    suffix = Path(filename).suffix or ".bin"
    uri = cache.write(data, suffix, original_name=filename)
    return (uri, mime) if uri else None


async def _download_voice(
    session: "aiohttp.ClientSession",
    item: dict,
    cdn_base_url: str,
    cache: MediaCache,
) -> Optional[tuple[str, str]]:
    voice_item = item.get("voice_item") or {}
    if voice_item.get("text"):
        # Voice already transcribed by iLink; treat as text, no media to cache.
        return None
    media = voice_item.get("media") or {}
    data = await download_and_decrypt_media(
        session,
        cdn_base_url=cdn_base_url,
        encrypted_query_param=media.get("encrypt_query_param"),
        aes_key_b64=media.get("aes_key"),
        full_url=media.get("full_url"),
        timeout_seconds=MEDIA_DOWNLOAD_VOICE_TIMEOUT_S,
    )
    uri = cache.write(data, ".silk")
    return (uri, "audio/silk") if uri else None


def _normalize_image_aes_key(image_item: dict, media: dict) -> Optional[str]:
    """iLink image frames may stash the AES key under ``image_item.aeskey`` (hex)
    instead of ``media.aes_key`` (b64). Reconcile both into a base64 string.
    """
    if media.get("aes_key"):
        return media["aes_key"]
    aeskey_hex = image_item.get("aeskey")
    if isinstance(aeskey_hex, str) and aeskey_hex:
        try:
            return base64.b64encode(bytes.fromhex(aeskey_hex)).decode("ascii")
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Outbound dispatch
# ---------------------------------------------------------------------------

OutboundItemBuilder = Callable[..., dict]


def select_outbound_media(
    path: str, force_file_attachment: bool = False
) -> tuple[int, OutboundItemBuilder]:
    """Pick the right ``media_type`` + ``item`` constructor for *path*'s mime."""
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"

    if mime.startswith("image/"):
        return MEDIA_IMAGE, _build_image_item
    if mime.startswith("video/"):
        return MEDIA_VIDEO, _build_video_item
    if path.endswith(".silk") and not force_file_attachment:
        return MEDIA_VOICE, _build_voice_item
    if mime.startswith("audio/"):
        # Non-silk audio: send as file attachment (silk is required for native voice bubble).
        return MEDIA_FILE, _build_file_item
    return MEDIA_FILE, _build_file_item


def _build_image_item(**kw) -> dict:
    return {
        "type": ITEM_IMAGE,
        "image_item": {
            "media": {
                "encrypt_query_param": kw["encrypt_query_param"],
                "aes_key": kw["aes_key_for_api"],
                "encrypt_type": 1,
            },
            "mid_size": kw["ciphertext_size"],
        },
    }


def _build_video_item(**kw) -> dict:
    return {
        "type": ITEM_VIDEO,
        "video_item": {
            "media": {
                "encrypt_query_param": kw["encrypt_query_param"],
                "aes_key": kw["aes_key_for_api"],
                "encrypt_type": 1,
            },
            "video_size": kw["ciphertext_size"],
            "play_length": kw.get("play_length", 0),
            "video_md5": kw.get("rawfilemd5", ""),
        },
    }


def _build_voice_item(**kw) -> dict:
    return {
        "type": ITEM_VOICE,
        "voice_item": {
            "media": {
                "encrypt_query_param": kw["encrypt_query_param"],
                "aes_key": kw["aes_key_for_api"],
                "encrypt_type": 1,
            },
            "encode_type": kw.get("encode_type", 6),
            "bits_per_sample": kw.get("bits_per_sample", 16),
            "sample_rate": kw.get("sample_rate", 24000),
            "playtime": kw.get("playtime", 0),
        },
    }


def _build_file_item(**kw) -> dict:
    return {
        "type": ITEM_FILE,
        "file_item": {
            "media": {
                "encrypt_query_param": kw["encrypt_query_param"],
                "aes_key": kw["aes_key_for_api"],
                "encrypt_type": 1,
            },
            "file_name": kw["filename"],
            "len": str(kw["plaintext_size"]),
        },
    }


async def send_outbound_file(
    session: "aiohttp.ClientSession",
    *,
    base_url: str,
    cdn_base_url: str,
    token: str,
    chat_id: str,
    path: str,
    context_token: Optional[str],
    context_token_setter: Optional[Callable[[str, Optional[str]], None]] = None,
    force_file_attachment: bool = False,
) -> str:
    """Encrypt + upload + send a single local file. Returns the client_id used."""
    plaintext = Path(path).read_bytes()
    media_type, item_builder = select_outbound_media(
        path, force_file_attachment=force_file_attachment,
    )
    filekey = secrets.token_hex(16)
    aes_key = secrets.token_bytes(16)
    rawsize = len(plaintext)
    rawfilemd5 = hashlib.md5(plaintext).hexdigest()

    upload_resp = await get_upload_url(
        session,
        base_url=base_url,
        token=token,
        to_user_id=chat_id,
        media_type=media_type,
        filekey=filekey,
        rawsize=rawsize,
        rawfilemd5=rawfilemd5,
        filesize=aes_padded_size(rawsize),
        aeskey_hex=aes_key.hex(),
    )

    upload_param = str(upload_resp.get("upload_param") or "")
    upload_full_url = str(upload_resp.get("upload_full_url") or "")
    if upload_full_url:
        upload_url = upload_full_url
    elif upload_param:
        upload_url = cdn_upload_url(cdn_base_url, upload_param, filekey)
    else:
        raise RuntimeError(
            "getUploadUrl returned neither upload_param nor upload_full_url: "
            f"{upload_resp}"
        )

    ciphertext = aes128_ecb_encrypt(plaintext, aes_key)
    encrypted_query_param = await upload_ciphertext(
        session,
        ciphertext=ciphertext,
        upload_url=upload_url,
        timeout_seconds=MEDIA_UPLOAD_TIMEOUT_S,
    )

    # iLink expects aes_key as base64(hex_string), not base64(raw_bytes).
    aes_key_for_api = base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii")

    media_item_kwargs = {
        "encrypt_query_param": encrypted_query_param,
        "aes_key_for_api": aes_key_for_api,
        "ciphertext_size": len(ciphertext),
        "plaintext_size": rawsize,
        "filename": Path(path).name,
        "rawfilemd5": rawfilemd5,
    }
    media_item = item_builder(**media_item_kwargs)

    client_id = f"flocks-weixin-{uuid.uuid4().hex}"
    await send_media_message(
        session,
        base_url=base_url,
        token=token,
        to=chat_id,
        item=media_item,
        context_token=context_token,
        client_id=client_id,
    )
    log.info("weixin.media.sent", {
        "to": safe_id(chat_id),
        "media_type": media_type,
        "size": rawsize,
    })
    return client_id


async def fetch_remote_to_temp(
    session: "aiohttp.ClientSession",
    *,
    url: str,
    timeout_seconds: float = MEDIA_REMOTE_FETCH_TIMEOUT_S,
) -> str:
    """Download an http(s) URL into a temp file, return the local path.

    Caller is responsible for unlinking the temp file when done.
    Only use after validating the URL belongs to the WeChat CDN.
    """
    data = await download_bytes(session, url=url, timeout_seconds=timeout_seconds)
    suffix = Path(url.split("?", 1)[0]).suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(data)
        return handle.name


def mime_from_filename(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


# Re-exported for the channel to schedule async tasks without a circular import.
__all__ = [
    "MediaCache",
    "download_inbound_item",
    "is_downloadable_media_item",
    "send_outbound_file",
    "fetch_remote_to_temp",
    "mime_from_filename",
    "select_outbound_media",
]
