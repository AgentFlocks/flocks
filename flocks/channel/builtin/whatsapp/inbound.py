"""Inbound event normalization for the WhatsApp bridge."""

from __future__ import annotations

from typing import Any, Optional

from flocks.channel.base import ChatType, InboundMessage

from .config import coerce_str, identifier_aliases, normalize_jid, strip_jid


def _first_media(event: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    urls = event.get("mediaUrls")
    if isinstance(urls, list) and urls:
        url = coerce_str(urls[0])
        if url:
            return url, coerce_str(event.get("mime")) or None
    url = coerce_str(event.get("mediaUrl"))
    if url:
        return url, coerce_str(event.get("mime")) or None
    return None, None


def _is_group_jid(jid: str) -> bool:
    return jid.endswith("@g.us")


def _resolve_mentioned(event: dict[str, Any], text: str) -> tuple[bool, str]:
    if event.get("mentioned") is True:
        mention_text = coerce_str(event.get("mentionText")) or text
        return True, mention_text.strip()
    if event.get("isGroup") and event.get("isReplyToBot"):
        return True, text.strip()
    return False, ""


def build_inbound_message(event: dict[str, Any], account_id: str = "default") -> Optional[InboundMessage]:
    chat_id = normalize_jid(coerce_str(event.get("chatId")))
    sender_id = normalize_jid(coerce_str(event.get("senderId")) or chat_id)
    sender_alt_id = normalize_jid(coerce_str(event.get("senderAltId")))
    chat_alt_id = normalize_jid(coerce_str(event.get("chatAltId")))
    message_id = coerce_str(event.get("messageId"))
    if not chat_id or not message_id:
        return None

    text = coerce_str(event.get("body") or event.get("text"))
    media_url, media_mime = _first_media(event)
    if not text and not media_url:
        return None

    is_group = bool(event.get("isGroup")) or _is_group_jid(chat_id)
    chat_type = ChatType.GROUP if is_group else ChatType.DIRECT
    sender_name = coerce_str(event.get("senderName")) or None

    mentioned, mention_text = _resolve_mentioned(event, text)
    sender_aliases = identifier_aliases(sender_id, sender_alt_id, event.get("senderAliases"))
    chat_aliases = identifier_aliases(chat_id, chat_alt_id, event.get("chatAliases"))
    raw = {**event, "senderAliases": sender_aliases, "chatAliases": chat_aliases}

    return InboundMessage(
        channel_id="whatsapp",
        account_id=account_id,
        message_id=f"{chat_id}:{message_id}",
        sender_id=strip_jid(sender_id) or sender_id,
        sender_name=sender_name,
        chat_id=chat_id,
        chat_type=chat_type,
        text=text,
        media_url=media_url,
        media_mime=media_mime,
        reply_to_id=coerce_str(event.get("quotedMessageId")) or None,
        thread_id=None,
        mentioned=mentioned,
        mention_text=mention_text,
        raw=raw,
    )
