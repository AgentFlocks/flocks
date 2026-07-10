"""Formatting helpers for LINE text messages."""

from __future__ import annotations

import re

from .config import LINE_MAX_MESSAGES_PER_CALL, LINE_SAFE_TEXT_LIMIT, LINE_TEXT_BUBBLE_LIMIT

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_MD_CODE_BLOCK_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_MD_CODE_INLINE_RE = re.compile(r"`([^`]+)`")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)


def strip_markdown_preserving_urls(text: str) -> str:
    """Convert generic Markdown to LINE-friendly plain text."""
    if not text:
        return text
    text = _MD_CODE_BLOCK_RE.sub(lambda m: m.group(1).rstrip("\n"), text)
    text = _MD_CODE_INLINE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BULLET_RE.sub("• ", text)
    return text


def split_for_line(
    text: str,
    max_chars: int = LINE_SAFE_TEXT_LIMIT,
    *,
    max_chunks: int = LINE_MAX_MESSAGES_PER_CALL,
) -> list[str]:
    """Split text into at most five LINE text bubbles."""
    if not text:
        return []
    max_chars = max(1, min(max_chars, LINE_TEXT_BUBBLE_LIMIT))
    max_chunks = max(1, min(max_chunks, LINE_MAX_MESSAGES_PER_CALL))
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining and len(chunks) < max_chunks:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            remaining = ""
            break
        cut = remaining.rfind("\n\n", 0, max_chars)
        if cut < int(max_chars * 0.5):
            cut = remaining.rfind("\n", 0, max_chars)
        if cut < int(max_chars * 0.5):
            cut = remaining.rfind(" ", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    if remaining and chunks:
        tail = chunks[-1]
        if len(tail) >= max_chars:
            tail = tail[: max_chars - 1]
        chunks[-1] = tail.rstrip() + "…"
    return chunks


def text_messages(
    text: str,
    *,
    max_chars: int = LINE_SAFE_TEXT_LIMIT,
    max_messages: int = LINE_MAX_MESSAGES_PER_CALL,
) -> list[dict]:
    return [
        {"type": "text", "text": chunk[:LINE_TEXT_BUBBLE_LIMIT]}
        for chunk in split_for_line(text, max_chars, max_chunks=max_messages)
    ]
