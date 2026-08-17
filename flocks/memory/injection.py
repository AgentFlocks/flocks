"""Budgeted Memory snapshot rendering for system-prompt injection."""

from collections.abc import Callable
import re
from typing import Any

from flocks.utils.log import Log


log = Log.create(service="memory.injection")

USER_MEMORY_INJECTION_TOKENS = 1000
CURATED_MEMORY_INJECTION_TOKENS = 2000


def render_memory_snapshot(
    memory_file: dict[str, Any],
    *,
    session_id: str,
    token_budget: int,
    count_tokens: Callable[[str], int],
) -> str:
    """Render a bounded Memory snapshot while preserving Markdown structure.

    Args:
        memory_file: Bootstrap record containing path, content, and optional
            absolute path.
        session_id: Session receiving the snapshot.
        token_budget: Maximum estimated tokens for the complete prompt block.
        count_tokens: Token estimator used by the Session prompt layer.

    Returns:
        Complete or section-aware truncated Memory prompt block.
    """
    path = str(memory_file["path"])
    content = str(memory_file.get("content", ""))
    prefix = f"## {path}\n\n"
    full_prompt = prefix + content
    if count_tokens(full_prompt) <= token_budget:
        return full_prompt

    source_path = str(memory_file.get("abs_path") or path)
    hint = (
        "\n\n> Memory snapshot truncated. Use `read` to open the complete "
        f"file as needed: `{source_path}`."
    )
    excerpt = _fit_memory_markdown(
        content,
        prefix=prefix,
        hint=hint,
        token_budget=token_budget,
        count_tokens=count_tokens,
    )
    bounded = prefix + excerpt + hint
    log.info(
        "memory.injection.truncated",
        {
            "session_id": session_id,
            "path": path,
            "source_tokens": count_tokens(full_prompt),
            "injected_tokens": count_tokens(bounded),
            "token_budget": token_budget,
        },
    )
    return bounded


def _fit_memory_markdown(
    content: str,
    *,
    prefix: str,
    hint: str,
    token_budget: int,
    count_tokens: Callable[[str], int],
) -> str:
    """Find the largest structural excerpt that fits the token budget."""
    low = 0
    high = len(content)
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        excerpt = _truncate_memory_markdown(content, midpoint)
        if count_tokens(prefix + excerpt + hint) <= token_budget:
            best = excerpt
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _truncate_memory_markdown(content: str, max_chars: int) -> str:
    """Fit Markdown to a character budget, retaining headings and indexes."""
    if len(content) <= max_chars:
        return content
    if max_chars <= 0:
        return ""

    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {"header": "", "body": []}
    for line in content.splitlines():
        if line.lstrip().startswith("#"):
            if current["header"] or current["body"]:
                sections.append(current)
            current = {"header": line, "body": []}
        else:
            current["body"].append(line)
    if current["header"] or current["body"]:
        sections.append(current)

    prepared: list[dict[str, str]] = []
    structural_lines: list[str] = []
    for section in sections:
        header = str(section["header"])
        body_lines = list(section["body"])
        index_lines = [
            line for line in body_lines if _is_memory_index_line(line, header)
        ]
        body = "\n".join(
            line for line in body_lines if line not in index_lines
        ).strip("\n")
        structure = "\n".join(
            line for line in [header, *index_lines] if line
        )
        prepared.append({"structure": structure, "body": body})
        structural_lines.extend(structure.splitlines())

    blocks = [section for section in prepared if any(section.values())]
    separator_chars = 2 * max(len(blocks) - 1, 0)
    structure_chars = sum(len(section["structure"]) for section in blocks)
    body_separator_chars = sum(
        bool(section["structure"] and section["body"])
        for section in blocks
    )
    available_body_chars = (
        max_chars - separator_chars - structure_chars - body_separator_chars
    )
    if available_body_chars < 0:
        return _truncate_prefix("\n".join(structural_lines), max_chars)

    bodies_left = sum(bool(section["body"]) for section in blocks)
    output: list[str] = []
    for section in blocks:
        excerpt = ""
        if section["body"] and bodies_left:
            quota = available_body_chars // bodies_left
            excerpt = _truncate_prefix(section["body"], quota)
            available_body_chars -= len(excerpt)
            bodies_left -= 1
        block = "\n".join(
            part for part in (section["structure"], excerpt) if part
        )
        if block:
            output.append(block)
    return "\n\n".join(output)


def _is_memory_index_line(line: str, header: str) -> bool:
    """Return whether a Markdown line is navigational index content."""
    stripped = line.strip()
    if not stripped:
        return False
    list_item = r"^(?:[-*+] |\d+[.)] )"
    linked_item = bool(re.match(list_item + r".*\[[^]]+\]\([^)]+\)", stripped))
    see_item = bool(re.match(list_item + r"see\s+\S+", stripped, re.IGNORECASE))
    reference_item = (
        header.lstrip("#").strip().casefold()
        in {"references", "index", "table of contents", "contents"}
        and bool(re.match(list_item, stripped))
    )
    return linked_item or see_item or reference_item


def _truncate_prefix(content: str, max_chars: int) -> str:
    """Truncate text at a line boundary when practical."""
    if len(content) <= max_chars:
        return content
    if max_chars <= 0:
        return ""
    excerpt = content[:max_chars]
    boundary = excerpt.rfind("\n")
    if boundary >= max_chars // 2:
        excerpt = excerpt[:boundary]
    return excerpt.rstrip()
