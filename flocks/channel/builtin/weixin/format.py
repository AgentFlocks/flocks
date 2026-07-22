"""
Markdown normalization and message-chunk splitting for WeChat delivery.

WeChat clients render most Markdown but truncate very long lines awkwardly.
We:
- Collapse runs of blank lines to at most one.
- Hard-wrap non-code, non-table lines longer than ``LINE_WRAP_WIDTH``.
- Pack content into messages under ``MAX_MESSAGE_LENGTH`` while keeping
  fenced code blocks (``` ``` ```) intact.
"""

from __future__ import annotations

import textwrap
from typing import Optional

from .config import FENCE_RE, TABLE_RULE_RE

LINE_WRAP_WIDTH = 120


def normalize_markdown(content: str) -> str:
    """Collapse multi-blank-line runs (outside code blocks) to a single blank."""
    lines = content.splitlines()
    result: list[str] = []
    in_code_block = False
    blank_run = 0

    for raw_line in lines:
        line = raw_line.rstrip()
        if FENCE_RE.match(line.strip()):
            in_code_block = not in_code_block
            result.append(line)
            blank_run = 0
            continue
        if in_code_block:
            result.append(line)
            continue
        if not line.strip():
            blank_run += 1
            if blank_run <= 1:
                result.append("")
            continue
        blank_run = 0
        result.append(line)

    return "\n".join(result).strip()


def wrap_long_lines(content: str, width: int = LINE_WRAP_WIDTH) -> str:
    """Soft-wrap copy-unfriendly long lines while preserving code/tables."""
    wrapped: list[str] = []
    in_code_block = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if FENCE_RE.match(stripped):
            in_code_block = not in_code_block
            wrapped.append(line)
            continue

        if (
            in_code_block
            or len(line) <= width
            or not stripped
            or stripped.startswith("|")
            or TABLE_RULE_RE.match(stripped)
        ):
            wrapped.append(line)
            continue

        wrapped_lines = textwrap.wrap(
            line, width=width,
            break_long_words=False, break_on_hyphens=False,
            replace_whitespace=False, drop_whitespace=True,
        )
        wrapped.extend(wrapped_lines or [line])

    return "\n".join(wrapped).strip()


def format_for_weixin(content: Optional[str]) -> str:
    """Top-level formatter: normalize whitespace + soft-wrap long lines."""
    if not content:
        return ""
    return wrap_long_lines(normalize_markdown(content))


def split_markdown_blocks(content: str) -> list[str]:
    """Split content into markdown-aware blocks, keeping fenced code intact."""
    if not content:
        return []
    blocks: list[str] = []
    current: list[str] = []
    in_code_block = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if FENCE_RE.match(line.strip()):
            if not in_code_block and current:
                blocks.append("\n".join(current).strip())
                current = []
            current.append(line)
            in_code_block = not in_code_block
            if not in_code_block:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        if in_code_block:
            current.append(line)
            continue
        if not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)

    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def split_chunks(content: str, max_length: int) -> list[str]:
    """Pack markdown blocks into chunks under *max_length*, preserving code fences.

    Long single blocks (e.g. a large code block) are force-split by line then
    by character as a last resort.
    """
    if not content:
        return []
    if len(content) <= max_length:
        return [content]

    chunks: list[str] = []
    current = ""
    for block in split_markdown_blocks(content):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(block) <= max_length:
            current = block
            continue
        # Block itself oversized — fall back to line-then-char split.
        line_buf = ""
        for line in block.splitlines():
            if len(line) > max_length:
                if line_buf:
                    chunks.append(line_buf)
                    line_buf = ""
                for i in range(0, len(line), max_length):
                    chunks.append(line[i:i + max_length])
                continue
            if len(line_buf) + len(line) + 1 > max_length:
                if line_buf:
                    chunks.append(line_buf)
                line_buf = line
            else:
                line_buf = f"{line_buf}\n{line}" if line_buf else line
        if line_buf:
            current = line_buf
    if current:
        chunks.append(current)
    return [c for c in chunks if c] or [content[:max_length]]
