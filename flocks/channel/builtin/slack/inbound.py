"""Inbound Slack event parsing."""

from __future__ import annotations

from typing import Any, Optional

from flocks.channel.base import ChatType, InboundMessage

from .config import allow_bot_messages
from .format import strip_bot_mention


def extract_text(event: dict[str, Any]) -> str:
    text = event.get("text")
    if isinstance(text, str) and text.strip():
        return text

    files = event.get("files")
    if isinstance(files, list) and files:
        names = [
            str(item.get("name") or item.get("title") or item.get("id") or "file")
            for item in files
            if isinstance(item, dict)
        ]
        if names:
            return "[Slack files: " + ", ".join(names) + "]"

    return ""


def is_own_or_ignored_bot_message(
    event: dict[str, Any],
    *,
    bot_user_id: Optional[str],
    config: dict[str, Any],
) -> bool:
    user_id = str(event.get("user") or event.get("bot_id") or "")
    if bot_user_id and user_id == bot_user_id:
        return True

    if not (event.get("bot_id") or event.get("subtype") == "bot_message"):
        return False

    policy = allow_bot_messages(config)
    if policy == "all":
        return False
    if policy == "mentions":
        return not bool(bot_user_id and f"<@{bot_user_id}>" in str(event.get("text") or ""))
    return True


def build_inbound_message(
    event: dict[str, Any],
    *,
    bot_user_id: Optional[str],
    config: dict[str, Any],
    known_thread_ids: set[str],
) -> Optional[InboundMessage]:
    """Convert a Slack message/app_mention event into a Flocks InboundMessage."""
    subtype = event.get("subtype")
    if subtype in {"message_changed", "message_deleted"}:
        return None

    if is_own_or_ignored_bot_message(event, bot_user_id=bot_user_id, config=config):
        return None

    channel_id = str(event.get("channel") or "")
    user_id = str(event.get("user") or event.get("bot_id") or "")
    ts = str(event.get("ts") or event.get("event_ts") or "")
    if not channel_id or not user_id or not ts:
        return None

    text = extract_text(event)
    if not text:
        return None

    channel_type = str(event.get("channel_type") or "").lower()
    if not channel_type and channel_id.startswith("D"):
        channel_type = "im"

    if channel_type == "im":
        chat_type = ChatType.DIRECT
    elif channel_type == "mpim":
        chat_type = ChatType.GROUP
    else:
        # Slack public/private channels are not one-to-one chats. Treat them
        # as channel surfaces while preserving group-trigger behavior in the
        # dispatcher (all non-DIRECT chats use the same trigger policy).
        chat_type = ChatType.CHANNEL
    thread_ts = str(event.get("thread_ts") or "")
    is_thread_reply = bool(thread_ts and thread_ts != ts)
    mentioned = bool(bot_user_id and f"<@{bot_user_id}>" in text)
    continues_bot_thread = bool(
        is_thread_reply
        and (
            thread_ts in known_thread_ids
            or (bot_user_id and str(event.get("parent_user_id") or "") == bot_user_id)
        )
    )
    mention_text = strip_bot_mention(text, bot_user_id or "") if mentioned else ""

    if chat_type == ChatType.DIRECT:
        mentioned = False
        mention_text = ""
    elif continues_bot_thread:
        mentioned = True
        if not mention_text:
            mention_text = text

    sender_name = str(event.get("username") or event.get("user_name") or "") or None

    return InboundMessage(
        channel_id="slack",
        account_id=str(event.get("team") or event.get("team_id") or "default"),
        message_id=ts,
        sender_id=user_id,
        sender_name=sender_name,
        chat_id=channel_id,
        chat_type=chat_type,
        text=text,
        reply_to_id=str(event.get("parent_user_id") or "") or None,
        thread_id=thread_ts or None,
        mentioned=mentioned,
        mention_text=mention_text,
        raw=event,
    )
