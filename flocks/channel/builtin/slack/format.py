"""Markdown to Slack mrkdwn helpers."""

from __future__ import annotations

import re
from html import escape
from urllib.parse import quote


_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_SAFE_ENTITY_RE = re.compile(r"&(amp|lt|gt);")


def _escape_slack_text(value: str) -> str:
    placeholders: list[str] = []

    def protect_entity(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\x02SLACK_ENTITY_{len(placeholders) - 1}\x02"

    escaped = _SAFE_ENTITY_RE.sub(protect_entity, value)
    escaped = escape(escaped, quote=False)
    for index, entity in enumerate(placeholders):
        escaped = escaped.replace(f"\x02SLACK_ENTITY_{index}\x02", entity)
    return escaped


def _escape_slack_link_url(value: str) -> str:
    return quote(value, safe=":/?#[]@!$&'()*+,;=%")


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Convert common Markdown to Slack mrkdwn without touching code fences."""
    if not text:
        return ""

    code_placeholders: list[str] = []

    def protect(match: re.Match[str]) -> str:
        code_placeholders.append(match.group(0))
        return f"\x01SLACK_CODE_{len(code_placeholders) - 1}\x01"

    link_placeholders: list[str] = []

    def convert_link(match: re.Match[str]) -> str:
        label = _escape_slack_text(match.group(1))
        url = _escape_slack_link_url(match.group(2))
        link_placeholders.append(f"<{url}|{label}>")
        return f"\x01SLACK_LINK_{len(link_placeholders) - 1}\x01"

    result = re.sub(r"```.*?```", protect, text, flags=re.DOTALL)
    result = re.sub(r"`[^`\n]+`", protect, result)

    result = _LINK_RE.sub(convert_link, result)
    result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result, flags=re.DOTALL)
    result = re.sub(r"__([^_\n]+)__", r"*\1*", result)
    result = re.sub(r"~~(.+?)~~", r"~\1~", result, flags=re.DOTALL)
    result = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", result, flags=re.MULTILINE)
    result = _escape_slack_text(result)

    for index, fragment in enumerate(link_placeholders):
        result = result.replace(f"\x01SLACK_LINK_{index}\x01", fragment)
    for index, fragment in enumerate(code_placeholders):
        result = result.replace(f"\x01SLACK_CODE_{index}\x01", fragment)

    return result


def strip_bot_mention(text: str, bot_user_id: str) -> str:
    if not bot_user_id:
        return text.strip()
    pattern = re.compile(rf"<@{re.escape(bot_user_id)}(?:\|[^>]+)?>")
    cleaned = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip()
