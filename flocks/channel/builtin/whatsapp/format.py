"""WhatsApp text formatting helpers."""

from __future__ import annotations

import re


_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def format_for_whatsapp(text: str) -> str:
    """Convert generic Markdown into WhatsApp-friendly plain markdown.

    WhatsApp supports lightweight emphasis but not Markdown links. Keeping
    code fences intact gives readable monospace blocks on most clients.
    """
    if not text:
        return ""
    out = _LINK_RE.sub(r"\1: \2", text)
    out = re.sub(r"(?<!\*)\*\*([^*\n][\s\S]*?[^*\n])\*\*(?!\*)", r"*\1*", out)
    out = re.sub(r"(?<!_)__([^_\n][\s\S]*?[^_\n])__(?!_)", r"*\1*", out)
    return out

