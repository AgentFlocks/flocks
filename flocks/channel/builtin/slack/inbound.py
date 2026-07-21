"""Inbound Slack event parsing."""

from __future__ import annotations

import json
from typing import Any, Optional

from flocks.channel.base import ChatType, InboundMessage

from .config import allow_bot_messages
from .format import strip_bot_mention


def slack_thread_cache_key(account_id: str, channel_id: str, thread_ts: str) -> str:
    return f"{account_id or 'default'}:{channel_id}:{thread_ts}"


def _join_parts(parts: list[str]) -> str:
    return "\n\n".join(part for part in (p.strip() for p in parts) if part)


def _dedupe_part(part: str, existing_text: str) -> str:
    candidate = part.strip()
    existing = existing_text.strip()
    if not candidate:
        return ""
    if not existing:
        return candidate
    if candidate == existing or candidate in existing:
        return ""
    if candidate.startswith(existing):
        return candidate[len(existing):].strip()
    return candidate


def _slack_text_object(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text.strip()
    return ""


def _rich_text_element_text(element: Any) -> str:
    if not isinstance(element, dict):
        return ""

    element_type = str(element.get("type") or "")
    if element_type == "text":
        return str(element.get("text") or "")
    if element_type == "user":
        return f"<@{element.get('user_id')}>"
    if element_type == "channel":
        return f"<#{element.get('channel_id')}>"
    if element_type == "usergroup":
        return f"<!subteam^{element.get('usergroup_id')}>"
    if element_type == "broadcast":
        return f"<!{element.get('range')}>"
    if element_type == "emoji":
        return f":{element.get('name')}:"
    if element_type == "link":
        text = str(element.get("text") or "").strip()
        url = str(element.get("url") or "").strip()
        return f"{text} ({url})" if text and url else text or url
    if element_type == "date":
        return str(element.get("fallback") or element.get("timestamp") or "")

    children = element.get("elements")
    if isinstance(children, list):
        child_text = "".join(_rich_text_element_text(child) for child in children).strip()
        if element_type == "rich_text_preformatted" and child_text:
            return f"```\n{child_text}\n```"
        if element_type == "rich_text_quote" and child_text:
            return "\n".join(f"> {line}" for line in child_text.splitlines())
        return child_text
    return ""


def _extract_blocks_text(blocks: Any) -> str:
    if not isinstance(blocks, list):
        return ""

    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue

        block_type = str(block.get("type") or "")
        if block_type == "rich_text":
            elements = block.get("elements")
            if isinstance(elements, list):
                parts.append(_join_parts([
                    _rich_text_element_text(element)
                    for element in elements
                ]))
            continue

        for key in ("text", "title"):
            value = _slack_text_object(block.get(key))
            if value:
                parts.append(value)

        fields = block.get("fields")
        if isinstance(fields, list):
            parts.extend(_slack_text_object(field) for field in fields)

        elements = block.get("elements")
        if isinstance(elements, list):
            parts.extend(_slack_text_object(element) for element in elements)

    return _join_parts(parts)


def _extract_attachments_text(attachments: Any) -> str:
    if not isinstance(attachments, list):
        return ""

    parts: list[str] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue

        for key in ("pretext", "title", "text", "fallback"):
            value = _slack_text_object(attachment.get(key))
            if value:
                parts.append(value)

        fields = attachment.get("fields")
        if isinstance(fields, list):
            for field in fields:
                if not isinstance(field, dict):
                    continue
                title = _slack_text_object(field.get("title"))
                value = _slack_text_object(field.get("value"))
                if title and value:
                    parts.append(f"{title}: {value}")
                elif value:
                    parts.append(value)

        blocks = _extract_blocks_text(attachment.get("blocks"))
        if blocks:
            parts.append(blocks)

    return _join_parts(parts)


def _extract_files_text(files: Any) -> str:
    if not isinstance(files, list) or not files:
        return ""

    names = [
        str(item.get("name") or item.get("title") or item.get("id") or "file")
        for item in files
        if isinstance(item, dict)
    ]
    if names:
        return "[Slack files: " + ", ".join(names) + "]"
    return ""


def extract_text(event: dict[str, Any]) -> str:
    parts: list[str] = []
    combined = ""
    text = event.get("text")
    if isinstance(text, str) and text.strip():
        combined = text.strip()
        parts.append(combined)

    blocks = _dedupe_part(_extract_blocks_text(event.get("blocks")), combined)
    if blocks:
        parts.append(blocks)
        combined = _join_parts(parts)

    attachments = _dedupe_part(_extract_attachments_text(event.get("attachments")), combined)
    if attachments:
        parts.append(attachments)
        combined = _join_parts(parts)

    files = _dedupe_part(_extract_files_text(event.get("files")), combined)
    if files:
        parts.append(files)

    rendered = _join_parts(parts)
    if rendered:
        return rendered

    if event.get("blocks") or event.get("attachments"):
        try:
            return "[Slack structured message]\n" + json.dumps(
                {
                    "blocks": event.get("blocks"),
                    "attachments": event.get("attachments"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return "[Slack structured message]"

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
    account_id = str(event.get("team") or event.get("team_id") or "default")
    thread_ts = str(event.get("thread_ts") or "")
    is_thread_reply = bool(thread_ts and thread_ts != ts)
    mentioned = bool(bot_user_id and f"<@{bot_user_id}>" in text)
    thread_key = slack_thread_cache_key(account_id, channel_id, thread_ts) if thread_ts else ""
    continues_bot_thread = bool(
        is_thread_reply
        and (
            thread_key in known_thread_ids
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
        account_id=account_id,
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
