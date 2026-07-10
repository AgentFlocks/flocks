"""Inbound LINE webhook event parsing."""

from __future__ import annotations

import re
from typing import Any, Optional

from flocks.channel.base import ChatType, InboundMessage

from .config import coerce_str, resolve_source


_LINE_MEDIA_TYPES = {"image", "video", "audio", "file"}


def build_inbound_message(
    event: dict[str, Any],
    *,
    account_id: str,
    bot_user_id: Optional[str] = None,
    bot_mention_text: Optional[str] = None,
) -> Optional[InboundMessage]:
    """Convert a LINE webhook event into a Flocks ``InboundMessage``."""
    if event.get("type") != "message":
        return None
    source = event.get("source") or {}
    if not isinstance(source, dict):
        return None
    chat_id, chat_type = resolve_source(source)
    if not chat_id:
        return None

    sender_id = coerce_str(source.get("userId")) or chat_id
    msg = event.get("message") or {}
    if not isinstance(msg, dict):
        return None
    msg_type = coerce_str(msg.get("type"))
    raw_message_id = coerce_str(msg.get("id"))
    webhook_event_id = coerce_str(event.get("webhookEventId"))
    if not raw_message_id and not webhook_event_id:
        return None

    text = ""
    media_url = None
    media_mime = None

    if msg_type == "text":
        text = coerce_str(msg.get("text"))
    elif msg_type in _LINE_MEDIA_TYPES:
        media_url = f"line://{msg_type}/{raw_message_id}"
        text = _media_placeholder(msg)
    elif msg_type == "sticker":
        keywords = msg.get("keywords") or []
        if isinstance(keywords, list) and keywords:
            text = f"[贴纸: {', '.join(str(k) for k in keywords)}]"
        else:
            text = "[贴纸]"
    elif msg_type == "location":
        title = coerce_str(msg.get("title"))
        address = coerce_str(msg.get("address"))
        lat = msg.get("latitude")
        lon = msg.get("longitude")
        parts = [p for p in [title, address, f"{lat},{lon}" if lat is not None and lon is not None else ""] if p]
        text = f"[位置: {' '.join(parts)}]" if parts else "[位置]"
    else:
        text = f"[unsupported LINE message type: {msg_type or 'unknown'}]"

    if not text and not media_url:
        return None

    mentioned, mention_text = resolve_mention_state(
        text=text,
        message=msg,
        chat_type=chat_type,
        bot_user_id=bot_user_id,
        bot_mention_text=bot_mention_text,
    )

    return InboundMessage(
        channel_id="line",
        account_id=account_id,
        message_id=webhook_event_id or f"{chat_id}:{raw_message_id}",
        sender_id=sender_id,
        sender_name=sender_id,
        chat_id=chat_id,
        chat_type=chat_type,
        text=text.strip(),
        media_url=media_url,
        media_mime=media_mime,
        reply_to_id=raw_message_id or None,
        mentioned=mentioned,
        mention_text=mention_text,
        raw=event,
    )


def resolve_mention_state(
    *,
    text: str,
    message: dict[str, Any],
    chat_type: ChatType,
    bot_user_id: Optional[str],
    bot_mention_text: Optional[str],
) -> tuple[bool, str]:
    if chat_type == ChatType.DIRECT:
        return False, ""

    mention = message.get("mention")
    if isinstance(mention, dict) and bot_user_id:
        mentionees = mention.get("mentionees") or []
        if isinstance(mentionees, list):
            for item in mentionees:
                if isinstance(item, dict) and coerce_str(item.get("userId")) == bot_user_id:
                    return True, _remove_line_mention_ranges(text, mentionees)

    marker = coerce_str(bot_mention_text)
    if marker and marker in text:
        cleaned = text.replace(marker, " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return True, cleaned

    return False, ""


def _remove_line_mention_ranges(text: str, mentionees: list[Any]) -> str:
    ranges: list[tuple[int, int]] = []
    for item in mentionees:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
            length = int(item.get("length"))
        except (TypeError, ValueError):
            continue
        if index >= 0 and length > 0:
            ranges.append((index, index + length))
    if not ranges:
        return text
    result = []
    cursor = 0
    for start, end in sorted(ranges):
        result.append(text[cursor:start])
        cursor = max(cursor, end)
    result.append(text[cursor:])
    return re.sub(r"\s+", " ", "".join(result)).strip()


def _media_placeholder(message: dict[str, Any]) -> str:
    msg_type = coerce_str(message.get("type"))
    if msg_type == "image":
        return "[图片]"
    if msg_type == "audio":
        return "[音频]"
    if msg_type == "video":
        return "[视频]"
    if msg_type == "file":
        filename = coerce_str(message.get("fileName") or message.get("file_name"))
        return f"[文件: {filename}]" if filename else "[文件]"
    return f"[{msg_type}]"

