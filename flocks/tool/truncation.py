"""
Tool output truncation module

Limits tool output size to prevent context window overflow.
Inspired by Flocks' ported src/tool/truncation.ts

When output exceeds limits, saves full content to a temporary file
and returns a truncated version with a pointer to the full output.

Supports:
- Static limits (line/byte based)
- Dynamic limits based on model context window
- Head+tail truncation to preserve error messages at the end
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from flocks.utils.log import Log
from flocks.workspace.manager import WorkspaceManager

log = Log.create(service="tool.truncation")

MAX_LINES = 200
MAX_BYTES = 10 * 1024  # 10 KB

MAX_TOOL_RESULT_CONTEXT_SHARE = 0.3
HARD_MAX_TOOL_RESULT_CHARS = 10_000
MIN_KEEP_CHARS = 1_000

_OUTPUT_DIR: Optional[Path] = None

# Cleanup: keep files for at most 4 hours
_CLEANUP_MAX_AGE_SECS = 4 * 3600
_CLEANUP_INTERVAL_SECS = 600  # run at most once per 10 min
_last_cleanup_ts: float = 0.0


def _ensure_output_dir() -> Path:
    global _OUTPUT_DIR
    if _OUTPUT_DIR is None:
        base = WorkspaceManager.get_instance().get_workspace_dir() / "tool-output"
        base.mkdir(parents=True, exist_ok=True)
        _OUTPUT_DIR = base
    return _OUTPUT_DIR


def _maybe_cleanup(output_dir: Path) -> None:
    """Remove stale temp files older than _CLEANUP_MAX_AGE_SECS."""
    global _last_cleanup_ts
    now = time.time()
    if now - _last_cleanup_ts < _CLEANUP_INTERVAL_SECS:
        return
    _last_cleanup_ts = now

    try:
        cutoff = now - _CLEANUP_MAX_AGE_SECS
        removed = 0
        for entry in output_dir.iterdir():
            if entry.is_file():
                try:
                    if entry.stat().st_mtime < cutoff:
                        entry.unlink()
                        removed += 1
                except OSError:
                    pass
        if removed:
            log.debug("truncation.cleanup", {"removed": removed})
    except Exception as e:
        log.debug("truncation.cleanup_error", {"error": str(e)})


@dataclass
class TruncateResult:
    content: str
    truncated: bool
    output_path: Optional[str] = None


def truncate_output(
    text: str,
    *,
    max_lines: int = MAX_LINES,
    max_bytes: int = MAX_BYTES,
    direction: str = "head",
    has_task_tool: bool = False,
) -> TruncateResult:
    """
    Truncate tool output that exceeds size limits.

    Args:
        text: Raw tool output text.
        max_lines: Maximum number of lines to keep.
        max_bytes: Maximum byte size to keep.
        direction: "head" keeps the first N lines, "tail" keeps the last N.
        has_task_tool: Whether the current agent can delegate via task tool.

    Returns:
        TruncateResult with (possibly truncated) content.
    """
    if not text:
        return TruncateResult(content=text, truncated=False)

    lines = text.split("\n")
    total_bytes = len(text.encode("utf-8"))

    if len(lines) <= max_lines and total_bytes <= max_bytes:
        return TruncateResult(content=text, truncated=False)

    out: list[str] = []
    byte_count = 0
    hit_bytes = False

    if direction == "head":
        for i, line in enumerate(lines):
            if i >= max_lines:
                break
            size = len(line.encode("utf-8")) + (1 if out else 0)
            if byte_count + size > max_bytes:
                hit_bytes = True
                break
            out.append(line)
            byte_count += size
    else:
        # Build in reverse, then flip — avoids O(n²) insert(0, …)
        rev: list[str] = []
        for i in range(len(lines) - 1, -1, -1):
            if len(rev) >= max_lines:
                break
            size = len(lines[i].encode("utf-8")) + (1 if rev else 0)
            if byte_count + size > max_bytes:
                hit_bytes = True
                break
            rev.append(lines[i])
            byte_count += size
        out = rev[::-1]

    removed = total_bytes - byte_count if hit_bytes else len(lines) - len(out)
    unit = "bytes" if hit_bytes else "lines"
    preview = "\n".join(out)

    output_dir = _ensure_output_dir()
    _maybe_cleanup(output_dir)

    filename = f"tool_{int(time.time() * 1000)}"
    filepath = output_dir / filename
    try:
        filepath.write_text(text, encoding="utf-8")
    except Exception as e:
        log.warn("truncation.save_error", {"error": str(e)})
        filepath_str = None
    else:
        filepath_str = str(filepath)

    if has_task_tool and filepath_str:
        hint = (
            f"The tool call succeeded but the output was truncated. "
            f"Full output saved to: {filepath_str}\n"
            f"Use the Task tool to have explore agent process this file with Grep and Read "
            f"(with offset/limit). Do NOT read the full file yourself - delegate to save context."
        )
    elif filepath_str:
        hint = (
            f"The tool call succeeded but the output was truncated. "
            f"Full output saved to: {filepath_str}\n"
            f"Use Grep to search the full content or Read with offset/limit to view specific sections."
        )
    else:
        hint = "The tool call succeeded but the output was truncated."

    if direction == "head":
        message = f"{preview}\n\n...{removed} {unit} truncated...\n\n{hint}"
    else:
        message = f"...{removed} {unit} truncated...\n\n{hint}\n\n{preview}"

    log.info("truncation.applied", {
        "original_lines": len(lines),
        "original_bytes": total_bytes,
        "kept_lines": len(out),
        "kept_bytes": byte_count,
        "direction": direction,
    })

    return TruncateResult(content=message, truncated=True, output_path=filepath_str)


# ---------------------------------------------------------------------------
# Dynamic context-window-aware truncation
# ---------------------------------------------------------------------------

_IMPORTANT_TAIL_RE = re.compile(
    r"\b(error|exception|failed|fatal|traceback|panic|stack trace|errno|exit code"
    r"|total|summary|result|complete|finished|done)\b",
    re.IGNORECASE,
)


def _has_important_tail(text: str) -> bool:
    """Check if the tail contains error/result patterns worth keeping.

    The detection window (2000 chars) matches the tail_budget cap in
    truncate_tool_result_text so we only flag content we can actually retain.
    """
    tail = text[-2000:]
    if _IMPORTANT_TAIL_RE.search(tail):
        return True
    stripped = tail.rstrip()
    if stripped.endswith("}") or stripped.endswith("]"):
        return True
    return False


def calculate_max_tool_result_chars(context_window_tokens: int) -> int:
    """Max chars for a single tool result based on the model's context window."""
    max_tokens = int(context_window_tokens * MAX_TOOL_RESULT_CONTEXT_SHARE)
    max_chars = max_tokens * 4  # ~4 chars/token heuristic
    return min(max_chars, HARD_MAX_TOOL_RESULT_CHARS)


def truncate_tool_result_text(
    text: str,
    max_chars: int,
    *,
    suffix: str = "",
    min_keep_chars: int = MIN_KEEP_CHARS,
) -> str:
    """Truncate text with head+tail strategy when tail has important content."""
    if len(text) <= max_chars:
        return text

    if not suffix:
        suffix = (
            "\n\n[Content truncated - original was too large for the model's context window. "
            "Use offset/limit parameters or request specific sections for large content.]"
        )

    budget = max(min_keep_chars, max_chars - len(suffix))

    if _has_important_tail(text) and budget > min_keep_chars * 2:
        tail_budget = min(int(budget * 0.3), 2_000)
        middle_marker = "\n\n[... middle content omitted - showing head and tail ...]\n\n"
        head_budget = budget - tail_budget - len(middle_marker)

        if head_budget > min_keep_chars:
            head_cut = head_budget
            head_nl = text.rfind("\n", 0, head_budget)
            if head_nl > head_budget * 0.8:
                head_cut = head_nl

            tail_start = len(text) - tail_budget
            tail_nl = text.find("\n", tail_start)
            if tail_nl != -1 and tail_nl < tail_start + tail_budget * 0.2:
                tail_start = tail_nl + 1

            return text[:head_cut] + middle_marker + text[tail_start:] + suffix

    cut_point = budget
    last_nl = text.rfind("\n", 0, budget)
    if last_nl > budget * 0.8:
        cut_point = last_nl
    return text[:cut_point] + suffix


def truncate_tool_result_dynamic(
    text: str,
    context_window_tokens: int,
) -> tuple[str, bool]:
    """Truncate a tool result string to fit within a share of the context window.

    Returns (possibly_truncated_text, was_truncated).
    """
    max_chars = calculate_max_tool_result_chars(context_window_tokens)
    if len(text) <= max_chars:
        return text, False
    return truncate_tool_result_text(text, max_chars), True
