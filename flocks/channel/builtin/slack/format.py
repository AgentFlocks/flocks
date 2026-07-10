"""Markdown to Slack mrkdwn helpers."""

from __future__ import annotations

import html
import re


_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Convert common Markdown to Slack mrkdwn without touching code fences."""
    if not text:
        return ""

    placeholders: list[str] = []

    def protect(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\x01SLACK_CODE_{len(placeholders) - 1}\x01"

    result = re.sub(r"```.*?```", protect, text, flags=re.DOTALL)
    result = re.sub(r"`[^`\n]+`", protect, result)

    result = html.unescape(result)
    result = _LINK_RE.sub(r"<\2|\1>", result)
    result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result, flags=re.DOTALL)
    result = re.sub(r"__([^_\n]+)__", r"*\1*", result)
    result = re.sub(r"~~(.+?)~~", r"~\1~", result, flags=re.DOTALL)
    result = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", result, flags=re.MULTILINE)

    for index, fragment in enumerate(placeholders):
        result = result.replace(f"\x01SLACK_CODE_{index}\x01", fragment)

    return result


def strip_bot_mention(text: str, bot_user_id: str) -> str:
    if not bot_user_id:
        return text.strip()
    pattern = re.compile(rf"<@{re.escape(bot_user_id)}(?:\|[^>]+)?>")
    cleaned = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip()
