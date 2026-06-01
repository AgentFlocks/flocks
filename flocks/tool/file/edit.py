"""
Edit Tool - File editing with batch exact replacements

Supports both legacy single-edit arguments and pi-style edits[] batch edits.
All edits in one call are matched against the same original file snapshot.
"""

import os
import unicodedata
from dataclasses import dataclass
from difflib import unified_diff
from typing import Any, Dict, List, Optional

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.tool.path_utils import resolve_tool_path
from flocks.utils.log import Log


log = Log.create(service="tool.edit")


DESCRIPTION = """Edit a single file using exact text replacement.

Usage:
- Prefer `edits` for one or more disjoint replacements in the same file.
- Every `edits[].oldString` is matched against the original file content, not after earlier edits are applied.
- Do not use overlapping or nested edits. Merge nearby changes into one edit.
- Legacy `oldString`/`newString`/`replaceAll` is still supported for single-edit callers.
- Use `replaceAll` only with legacy single-edit arguments when you want to replace every occurrence in the file.
- CRITICAL: match text exactly including whitespace and newlines.
- The tool preserves the file's existing encoding and dominant line-ending style."""


def normalize_line_endings(text: str) -> str:
    """Normalize all line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def detect_line_ending(text: str) -> str:
    """Return the dominant line ending for the file."""
    crlf_idx = text.find("\r\n")
    lf_idx = text.find("\n")
    if lf_idx == -1:
        return "\n"
    if crlf_idx == -1:
        return "\n"
    return "\r\n" if crlf_idx < lf_idx else "\n"


def restore_line_endings(text: str, ending: str) -> str:
    """Restore LF-normalized text to the original line-ending style."""
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def strip_bom(text: str) -> tuple[str, str]:
    """Split a UTF-8 BOM prefix from the file body."""
    if text.startswith("\ufeff"):
        return "\ufeff", text[1:]
    return "", text


def normalize_for_fuzzy_match(text: str) -> str:
    """Normalize text for fuzzy matching, mirroring pi semantics."""
    return (
        unicodedata.normalize("NFKC", text)
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201a", "'")
        .replace("\u201b", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u201e", '"')
        .replace("\u201f", '"')
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2015", "-")
        .replace("\u2212", "-")
        .replace("\u00a0", " ")
        .replace("\u2002", " ")
        .replace("\u2003", " ")
        .replace("\u2004", " ")
        .replace("\u2005", " ")
        .replace("\u2006", " ")
        .replace("\u2007", " ")
        .replace("\u2008", " ")
        .replace("\u2009", " ")
        .replace("\u200a", " ")
        .replace("\u202f", " ")
        .replace("\u205f", " ")
        .replace("\u3000", " ")
    )


def _normalize_fuzzy_lines(text: str) -> str:
    """Apply fuzzy normalization plus trailing-space trimming per line."""
    normalized = normalize_for_fuzzy_match(text)
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


@dataclass(frozen=True)
class FuzzyTextIndex:
    """Normalized fuzzy-search view plus original-content span mapping."""

    normalized_text: str
    spans: List[tuple[int, int]]


def _build_fuzzy_text_index(text: str) -> FuzzyTextIndex:
    """Build a fuzzy-normalized shadow string mapped back to original offsets."""
    if not text:
        return FuzzyTextIndex(normalized_text="", spans=[])

    normalized_chars: List[str] = []
    spans: List[tuple[int, int]] = []
    line_start = 0
    text_length = len(text)

    while line_start < text_length:
        newline_index = text.find("\n", line_start)
        has_newline = newline_index != -1
        line_end = newline_index if has_newline else text_length

        line_chars: List[str] = []
        line_spans: List[tuple[int, int]] = []
        for absolute_index in range(line_start, line_end):
            normalized_char = normalize_for_fuzzy_match(text[absolute_index])
            for output_char in normalized_char:
                line_chars.append(output_char)
                line_spans.append((absolute_index, absolute_index + 1))

        keep_count = len("".join(line_chars).rstrip())
        normalized_chars.extend(line_chars[:keep_count])
        spans.extend(line_spans[:keep_count])

        if not has_newline:
            break

        normalized_chars.append("\n")
        spans.append((line_end, line_end + 1))
        line_start = line_end + 1

    return FuzzyTextIndex(
        normalized_text="".join(normalized_chars),
        spans=spans,
    )


def _find_fuzzy_spans(content_index: FuzzyTextIndex, old_string: str) -> List[tuple[int, int]]:
    """Return original-content spans for all fuzzy matches of old_string."""
    normalized_old = _normalize_fuzzy_lines(old_string)
    if not normalized_old:
        return []

    matches: List[tuple[int, int]] = []
    search_start = 0
    while True:
        normalized_index = content_index.normalized_text.find(normalized_old, search_start)
        if normalized_index == -1:
            break

        match_end_index = normalized_index + len(normalized_old) - 1
        matches.append(
            (
                content_index.spans[normalized_index][0],
                content_index.spans[match_end_index][1],
            )
        )
        search_start = normalized_index + len(normalized_old)

    return matches


def generate_diff(filepath: str, old_content: str, new_content: str) -> str:
    """Generate a unified diff between old and new content."""
    old_lines = normalize_line_endings(old_content).splitlines(keepends=True)
    new_lines = normalize_line_endings(new_content).splitlines(keepends=True)
    diff_lines = list(
        unified_diff(
            old_lines,
            new_lines,
            fromfile=filepath,
            tofile=filepath,
            lineterm="",
        )
    )
    return "".join(diff_lines)


def trim_diff(diff: str) -> str:
    """Trim common indentation from diff content lines."""
    if not diff:
        return diff

    lines = diff.split("\n")
    content_lines = [
        line
        for line in lines
        if (line.startswith("+") or line.startswith("-") or line.startswith(" "))
        and not line.startswith("---")
        and not line.startswith("+++")
    ]
    if not content_lines:
        return diff

    min_indent = float("inf")
    for line in content_lines:
        content = line[1:]
        if content.strip():
            indent = len(content) - len(content.lstrip())
            min_indent = min(min_indent, indent)

    if min_indent in (float("inf"), 0):
        return diff

    trimmed_lines = []
    for line in lines:
        if (
            (line.startswith("+") or line.startswith("-") or line.startswith(" "))
            and not line.startswith("---")
            and not line.startswith("+++")
        ):
            trimmed_lines.append(line[0] + line[1 + int(min_indent):])
        else:
            trimmed_lines.append(line)
    return "\n".join(trimmed_lines)


def _fuzzy_find_text(
    content: str,
    old_string: str,
    content_index: Optional[FuzzyTextIndex] = None,
) -> tuple[bool, int, int, bool]:
    """Find text using exact match first, then fuzzy-normalized match."""
    exact_index = content.find(old_string)
    if exact_index != -1:
        return True, exact_index, exact_index + len(old_string), False

    fuzzy_spans = _find_fuzzy_spans(content_index or _build_fuzzy_text_index(content), old_string)
    if not fuzzy_spans:
        return False, -1, 0, False
    return True, fuzzy_spans[0][0], fuzzy_spans[0][1], True


def _count_occurrences(
    content: str,
    old_string: str,
    *,
    used_fuzzy: bool,
    content_index: Optional[FuzzyTextIndex] = None,
) -> int:
    if not used_fuzzy:
        return content.count(old_string)

    fuzzy_old = _normalize_fuzzy_lines(old_string)
    if not fuzzy_old:
        return 0
    return (content_index or _build_fuzzy_text_index(content)).normalized_text.count(fuzzy_old)


def _get_not_found_error(filepath: str, edit_index: int, total_edits: int) -> str:
    if total_edits == 1:
        return (
            f"Could not find oldString in {filepath}. "
            "Re-read the file and provide a slightly larger unique snippet from the current file contents."
        )
    return (
        f"Could not find edits[{edit_index}] in {filepath}. "
        "Re-read the file and provide a slightly larger unique snippet from the current file contents."
    )


def _get_duplicate_error(filepath: str, edit_index: int, total_edits: int, occurrences: int) -> str:
    if total_edits == 1:
        return (
            f"Found {occurrences} occurrences of the text in {filepath}. "
            "The text must be unique. Please provide more context to make it unique."
        )
    return (
        f"Found {occurrences} occurrences of edits[{edit_index}] in {filepath}. "
        "Each oldString must be unique. Please provide more context to make it unique."
    )


def _get_empty_old_string_error(filepath: str, edit_index: int, total_edits: int) -> str:
    if total_edits == 1:
        return f"oldString must not be empty in {filepath}."
    return f"edits[{edit_index}].oldString must not be empty in {filepath}."


def _get_no_change_error(filepath: str, total_edits: int) -> str:
    if total_edits == 1:
        return (
            f"No changes made to {filepath}. "
            "The replacement produced identical content."
        )
    return f"No changes made to {filepath}. The replacements produced identical content."


def _prepare_batch_edits(
    filepath: str,
    edits: Optional[List[Dict[str, Any]]],
    old_string: Optional[str],
    new_string: Optional[str],
) -> tuple[Optional[List[Dict[str, str]]], Optional[str]]:
    """Return normalized batch edits or a validation error."""
    if edits is not None:
        if old_string is not None or new_string is not None:
            return None, "Use either edits or oldString/newString, not both."
        if not isinstance(edits, list) or not edits:
            return None, "edits must contain at least one replacement."

        prepared: List[Dict[str, str]] = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                return None, f"edits[{index}] must be an object."
            current_old = edit.get("oldString")
            current_new = edit.get("newString")
            if not isinstance(current_old, str) or not isinstance(current_new, str):
                return None, f"edits[{index}] must include string oldString and newString."
            if current_old == "":
                return None, _get_empty_old_string_error(filepath, index, len(edits))
            if current_old == current_new:
                return None, f"edits[{index}].oldString and newString must be different."
            prepared.append({"oldString": current_old, "newString": current_new})
        return prepared, None

    if old_string is None or new_string is None:
        return None, "Provide edits or legacy oldString/newString arguments."
    if old_string != "" and old_string == new_string:
        return None, "oldString and newString must be different"
    return [{"oldString": old_string, "newString": new_string}], None


def _apply_replace_all(
    normalized_content: str,
    old_string: str,
    new_string: str,
    filepath: str,
) -> tuple[str, str]:
    """Apply a legacy replaceAll operation without mutating untouched content."""
    normalized_old = normalize_line_endings(old_string)
    normalized_new = normalize_line_endings(new_string)
    content_index = _build_fuzzy_text_index(normalized_content)
    found, _, _, used_fuzzy = _fuzzy_find_text(
        normalized_content,
        normalized_old,
        content_index,
    )
    if not found:
        raise ValueError(_get_not_found_error(filepath, 0, 1))

    if not used_fuzzy:
        if normalized_old == "":
            raise ValueError(_get_empty_old_string_error(filepath, 0, 1))
        new_content = normalized_content.replace(normalized_old, normalized_new)
    else:
        fuzzy_spans = _find_fuzzy_spans(content_index, normalized_old)
        new_content = normalized_content
        for match_start, match_end in reversed(fuzzy_spans):
            new_content = new_content[:match_start] + normalized_new + new_content[match_end:]

    if new_content == normalized_content:
        raise ValueError(_get_no_change_error(filepath, 1))
    return normalized_content, new_content


def _apply_edits_to_normalized_content(
    normalized_content: str,
    edits: List[Dict[str, str]],
    filepath: str,
) -> tuple[str, str]:
    """Apply all edits against the same original file snapshot."""
    normalized_edits = [
        {
            "oldString": normalize_line_endings(edit["oldString"]),
            "newString": normalize_line_endings(edit["newString"]),
        }
        for edit in edits
    ]

    for index, edit in enumerate(normalized_edits):
        if edit["oldString"] == "":
            raise ValueError(_get_empty_old_string_error(filepath, index, len(normalized_edits)))

    content_index = _build_fuzzy_text_index(normalized_content)

    matched_edits = []
    for index, edit in enumerate(normalized_edits):
        found, match_start, match_end, used_fuzzy = _fuzzy_find_text(
            normalized_content,
            edit["oldString"],
            content_index,
        )
        if not found:
            raise ValueError(_get_not_found_error(filepath, index, len(normalized_edits)))

        occurrences = _count_occurrences(
            normalized_content,
            edit["oldString"],
            used_fuzzy=used_fuzzy,
            content_index=content_index,
        )
        if occurrences > 1:
            raise ValueError(_get_duplicate_error(filepath, index, len(normalized_edits), occurrences))

        matched_edits.append(
            {
                "editIndex": index,
                "matchStart": match_start,
                "matchEnd": match_end,
                "newString": edit["newString"],
            }
        )

    matched_edits.sort(key=lambda item: item["matchStart"])
    for index in range(1, len(matched_edits)):
        previous = matched_edits[index - 1]
        current = matched_edits[index]
        if previous["matchEnd"] > current["matchStart"]:
            raise ValueError(
                f"edits[{previous['editIndex']}] and edits[{current['editIndex']}] overlap in {filepath}. "
                "Merge them into one edit or target disjoint regions."
            )

    new_content = normalized_content
    for edit in reversed(matched_edits):
        start = edit["matchStart"]
        end = edit["matchEnd"]
        new_content = new_content[:start] + edit["newString"] + new_content[end:]

    if new_content == normalized_content:
        raise ValueError(_get_no_change_error(filepath, len(normalized_edits)))
    return normalized_content, new_content


@ToolRegistry.register_function(
    name="edit",
    description=DESCRIPTION,
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(
            name="filePath",
            type=ParameterType.STRING,
            description="The path to the file to modify. It may be absolute, use `~`, or be relative to the current project directory.",
            required=True,
        ),
        ToolParameter(
            name="edits",
            type=ParameterType.ARRAY,
            description=(
                "One or more targeted replacements. Each edits[].oldString is matched "
                "against the original file, not incrementally."
            ),
            required=False,
            json_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "oldString": {
                            "type": "string",
                            "description": (
                                "Exact text for one targeted replacement. It must be "
                                "unique in the original file and must not overlap with "
                                "any other edits[].oldString in the same call."
                            ),
                        },
                        "newString": {
                            "type": "string",
                            "description": "Replacement text for this targeted edit.",
                        },
                    },
                    "required": ["oldString", "newString"],
                    "additionalProperties": False,
                },
            },
        ),
        ToolParameter(
            name="oldString",
            type=ParameterType.STRING,
            description="Legacy single-edit old text. Use edits[] for new callers.",
            required=False,
        ),
        ToolParameter(
            name="newString",
            type=ParameterType.STRING,
            description="Legacy single-edit replacement text. Use edits[] for new callers.",
            required=False,
        ),
        ToolParameter(
            name="replaceAll",
            type=ParameterType.BOOLEAN,
            description="Legacy single-edit option to replace every occurrence of oldString.",
            required=False,
            default=False,
        ),
    ],
)
async def edit_tool(
    ctx: ToolContext,
    filePath: str,
    edits: Optional[List[Dict[str, Any]]] = None,
    oldString: Optional[str] = None,
    newString: Optional[str] = None,
    replaceAll: bool = False,
) -> ToolResult:
    """Edit a file with legacy single-edit or pi-style edits[] semantics."""
    if not filePath:
        return ToolResult(success=False, error="filePath is required")

    try:
        resolution = await resolve_tool_path(ctx, filePath)
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc), title=filePath)
    filepath = resolution.resolved_path

    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    if isinstance(sandbox, dict) and sandbox.get("workspace_access") == "ro":
        return ToolResult(
            success=False,
            error=(
                "Edit is blocked in sandbox read-only workspace mode. "
                "Set sandbox.workspace_access to 'rw' to allow edits."
            ),
            title=filePath,
        )

    title = resolution.display_path

    if oldString == "" and edits is None:
        if newString is None:
            return ToolResult(success=False, error="newString is required when oldString is empty", title=title)
        diff = trim_diff(generate_diff(filepath, "", newString))
        await ctx.ask(
            permission="edit",
            patterns=[resolution.permission_pattern],
            always=["*"],
            metadata={"filepath": filepath, "diff": diff},
        )

        parent_dir = os.path.dirname(filepath)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        try:
            with open(filepath, "w", encoding="utf-8", newline="") as file_handle:
                file_handle.write(newString)
        except Exception as error:
            return ToolResult(
                success=False,
                error=f"Failed to write file: {str(error)}",
                title=title,
            )

        return ToolResult(
            success=True,
            output="Edit applied successfully. If you need to make additional edits to this file, use the Read tool first to get the current file content.",
            title=title,
            metadata={"diff": diff, "diagnostics": {}},
        )

    prepared_edits, validation_error = _prepare_batch_edits(filepath, edits, oldString, newString)
    if validation_error:
        return ToolResult(success=False, error=validation_error, title=title)
    assert prepared_edits is not None

    if not os.path.exists(filepath):
        return ToolResult(success=False, error=f"File {filepath} not found", title=title)
    if os.path.isdir(filepath):
        return ToolResult(
            success=False,
            error=f"Path is a directory, not a file: {filepath}",
            title=title,
        )

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace", newline="") as file_handle:
            raw_content_old = file_handle.read()
    except Exception as error:
        return ToolResult(
            success=False,
            error=f"Failed to read file: {str(error)}",
            title=title,
        )

    bom, content_without_bom = strip_bom(raw_content_old)
    original_line_ending = detect_line_ending(content_without_bom)
    normalized_content_old = normalize_line_endings(content_without_bom)

    try:
        if replaceAll:
            if edits is not None:
                raise ValueError("replaceAll is only supported with legacy oldString/newString arguments.")
            assert oldString is not None and newString is not None
            base_content, normalized_content_new = _apply_replace_all(
                normalized_content_old,
                oldString,
                newString,
                filepath,
            )
        else:
            base_content, normalized_content_new = _apply_edits_to_normalized_content(
                normalized_content_old,
                prepared_edits,
                filepath,
            )
    except ValueError as error:
        return ToolResult(success=False, error=str(error), title=title)

    content_new = bom + restore_line_endings(normalized_content_new, original_line_ending)
    diff = trim_diff(generate_diff(filepath, base_content, normalized_content_new))

    await ctx.ask(
        permission="edit",
        patterns=[resolution.permission_pattern],
        always=["*"],
        metadata={"filepath": filepath, "diff": diff},
    )

    try:
        with open(filepath, "w", encoding="utf-8", newline="") as file_handle:
            file_handle.write(content_new)
    except Exception as error:
        return ToolResult(
            success=False,
            error=f"Failed to write file: {str(error)}",
            title=title,
        )

    old_lines = normalize_line_endings(raw_content_old).split("\n")
    new_lines = normalize_line_endings(content_new).split("\n")
    additions = sum(1 for line in set(new_lines) - set(old_lines) if line)
    deletions = sum(1 for line in set(old_lines) - set(new_lines) if line)

    ctx.metadata(
        {
            "metadata": {
                "diff": diff,
                "filediff": {
                    "file": filepath,
                    "before": raw_content_old,
                    "after": content_new,
                    "additions": additions,
                    "deletions": deletions,
                },
                "diagnostics": {},
            }
        }
    )

    return ToolResult(
        success=True,
        output=(
            "Edit applied successfully. If you need to make additional edits to this "
            "file, use the Read tool first to get the current file content."
        ),
        title=title,
        metadata={
            "diff": diff,
            "diagnostics": {},
            "filediff": {
                "file": filepath,
                "additions": additions,
                "deletions": deletions,
            },
        },
    )
