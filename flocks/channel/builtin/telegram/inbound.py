"""
Inbound message parsing for the Telegram channel.

Converts a raw Telegram ``message`` / ``channel_post`` dict into a Flocks
``InboundMessage``.

Supported content:
  - Text messages (with or without entities / captions)
  - Media messages: photo, video, audio, voice, document, animation,
    video_note, sticker, location → represented as human-readable placeholders
    in the ``text`` field so the AI always receives something meaningful.

Bot-identity resolution (getMe) is deferred to the first group message and
then cached to avoid unnecessary network round-trips.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from flocks.channel.base import ChatType, InboundMessage
from flocks.utils.log import Log

from .client import get_http_client
from .config import (
    clean_bot_username,
    coerce_int,
    coerce_str,
    extract_text,
    resolve_account_config,
    resolve_api_base,
    resolve_chat_type,
    resolve_mention_state,
)

log = Log.create(service="channel.telegram")


class BotIdentityResolver:
    """Lazy resolver for bot username / user-id via getMe.

    Results are cached in-instance; the lock prevents duplicate calls.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.bot_username: Optional[str] = None
        self.bot_user_id: Optional[int] = None

    def seed(self, username: Optional[str], user_id: Optional[int]) -> None:
        """Pre-populate from config (avoids a network round-trip)."""
        self.bot_username = username
        self.bot_user_id = user_id

    def clear(self) -> None:
        self.bot_username = None
        self.bot_user_id = None

    async def ensure(self, config: dict[str, Any]) -> None:
        if self.bot_username or self.bot_user_id is not None:
            return
        async with self._lock:
            if self.bot_username or self.bot_user_id is not None:
                return
            try:
                _, account = resolve_account_config(config)
            except ValueError:
                return
            token = coerce_str(account.get("botToken"))
            if not token:
                return
            base_url = resolve_api_base(account, token)
            timeout_seconds = max(coerce_int(account.get("timeoutSeconds")) or 30, 1)
            client = await get_http_client()
            try:
                response = await client.post(
                    f"{base_url}/getMe",
                    json={},
                    timeout=timeout_seconds,
                )
                data = response.json()
            except Exception as exc:
                log.warning("telegram.identity.resolve_failed", {"error": str(exc)})
                return
            if not response.is_success or not data.get("ok"):
                log.warning(
                    "telegram.identity.resolve_failed",
                    {"status_code": response.status_code},
                )
                return
            result = data.get("result") or {}
            self.bot_username = clean_bot_username(result.get("username"))
            self.bot_user_id = coerce_int(result.get("id"))


# ---------------------------------------------------------------------------
# Media description helpers
# ---------------------------------------------------------------------------

def extract_media_description(message: dict[str, Any]) -> Optional[str]:
    """Return a human-readable placeholder for non-text media content.

    When the message carries a caption it is appended after the placeholder
    so the AI can see both the media type and any accompanying text.

    Returns ``None`` for message types we don't recognise (e.g. polls, service
    messages) so the caller can choose to discard them.
    """
    caption = (message.get("caption") or "").strip()
    suffix = f": {caption}" if caption else ""

    if message.get("photo"):
        return f"[图片]{suffix}"

    doc = message.get("document")
    if doc:
        fname = coerce_str(doc.get("file_name")) or "文件"
        return f"[文件: {fname}]{suffix}"

    audio = message.get("audio")
    if audio:
        title = (
            coerce_str(audio.get("title"))
            or coerce_str(audio.get("file_name"))
            or "音频"
        )
        return f"[音频: {title}]{suffix}"

    if message.get("voice"):
        return f"[语音消息]{suffix}"

    if message.get("video"):
        return f"[视频]{suffix}"

    if message.get("video_note"):
        return "[圆形视频]"

    sticker = message.get("sticker")
    if sticker:
        emoji = coerce_str(sticker.get("emoji"))
        return f"[贴纸{' ' + emoji if emoji else ''}]"

    if message.get("animation"):
        return f"[动图]{suffix}"

    loc = message.get("location")
    if loc:
        lat = loc.get("latitude", "?")
        lon = loc.get("longitude", "?")
        return f"[位置: {lat}, {lon}]"

    contact = message.get("contact")
    if contact:
        name = coerce_str(contact.get("first_name"))
        phone = coerce_str(contact.get("phone_number"))
        parts = [p for p in [name, phone] if p]
        return f"[联系人: {', '.join(parts)}]" if parts else "[联系人]"

    return None


# ---------------------------------------------------------------------------
# Channel-post sender resolution
# ---------------------------------------------------------------------------

def _resolve_channel_post_sender(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    """For channel posts (no ``from`` field) synthesise a sender from the chat."""
    chat = message.get("chat") or {}
    chat_id = coerce_str(chat.get("id"))
    if not chat_id:
        return None
    return {
        "id": chat_id,
        "is_bot": False,
        "first_name": coerce_str(chat.get("title")) or f"channel:{chat_id}",
    }


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

async def build_inbound_message(
    message: dict[str, Any],
    account_id: str,
    identity: BotIdentityResolver,
    config: dict[str, Any],
) -> Optional[InboundMessage]:
    """Convert a raw Telegram message dict into an InboundMessage.

    Returns ``None`` only for:
      - Messages sent by other bots
      - Unsupported chat types (channels without sender info)
      - Messages with no recognisable content (no text, no media, no location…)
    """
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None

    # ``from`` is absent for channel posts; synthesise a sender in that case.
    sender = message.get("from")
    if not isinstance(sender, dict):
        sender = _resolve_channel_post_sender(message)
        if sender is None:
            return None

    if sender.get("is_bot"):
        return None

    chat_type = resolve_chat_type(chat)
    if chat_type is None:
        return None

    # ---- content ----
    # Check for media first so we always surface the media type to the AI,
    # even when the message also carries a text caption.
    media_desc = extract_media_description(message)
    media_url: Optional[str] = None
    if media_desc:
        text = media_desc
        # Surface the Telegram file_id as an opaque URI so the dispatcher
        # can route it to the per-channel downloader (Telegram getFile).
        file_id, media_kind = _extract_primary_file_id(message)
        if file_id:
            media_url = f"telegram://{media_kind}/{file_id}"
    else:
        text = extract_text(message)
        if not text:
            return None  # Unrecognised / unsupported message type

    if chat_type == ChatType.GROUP and not identity.bot_username and identity.bot_user_id is None:
        await identity.ensure(config)

    chat_id = coerce_str(chat.get("id"))
    sender_id = coerce_str(sender.get("id"))
    raw_message_id = coerce_str(message.get("message_id"))
    if not chat_id or not sender_id or not raw_message_id:
        return None

    mentioned, mention_text = resolve_mention_state(
        message=message,
        chat_type=chat_type,
        text=text,
        bot_username=identity.bot_username,
        bot_user_id=identity.bot_user_id,
    )

    sender_name = (
        coerce_str(sender.get("username"))
        or " ".join(
            part for part in [
                coerce_str(sender.get("first_name")),
                coerce_str(sender.get("last_name")),
            ]
            if part
        )
        or None
    )

    reply_to = message.get("reply_to_message")
    reply_to_id = None
    if isinstance(reply_to, dict):
        reply_to_id = coerce_str(reply_to.get("message_id")) or None

    thread_id = coerce_str(message.get("message_thread_id")) or None

    return InboundMessage(
        channel_id="telegram",
        account_id=account_id,
        message_id=f"{chat_id}:{raw_message_id}",
        sender_id=sender_id,
        sender_name=sender_name,
        chat_id=chat_id,
        chat_type=chat_type,
        text=text.strip(),
        media_url=media_url,
        reply_to_id=reply_to_id,
        thread_id=thread_id,
        mentioned=mentioned,
        mention_text=mention_text,
        raw=message,
    )


def _extract_primary_file_id(
    message: dict[str, Any],
) -> tuple[Optional[str], str]:
    """Return the first Telegram ``file_id`` in *message* + the media kind.

    Media kinds follow the Telegram Bot API parameter names so the
    outbound ``send_*`` methods can use them directly: ``photo``,
    ``document``, ``video``, ``audio``, ``voice``, ``animation``.
    Photos are special — Telegram exposes them as a list of size variants;
    we pick the largest one.
    """
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        largest = max(
            (p for p in photos if isinstance(p, dict)),
            key=lambda p: coerce_int(p.get("file_size")) or 0,
            default=None,
        )
        if largest is not None:
            return coerce_str(largest.get("file_id")), "photo"

    for kind in ("document", "video", "audio", "voice", "animation"):
        block = message.get(kind)
        if isinstance(block, dict):
            file_id = coerce_str(block.get("file_id"))
            if file_id:
                return file_id, kind
    return None, "document"
