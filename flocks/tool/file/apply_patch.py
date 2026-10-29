"""
Apply Patch Tool - Patch application

Applies unified diff patches to files.
Supports file add, update, delete, and move operations.
"""

import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from difflib import unified_diff

from flocks.tool import nofollow
from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.tool.path_utils import (
    changed_before_asking,
    checked_error,
    moved_since_resolved,
    paths_changed,
    resolve_tool_path,
    resolved_identities,
    safe_relpath as _safe_relpath,
)
from flocks.utils.log import Log


log = Log.create(service="tool.apply_patch")

DESCRIPTION = """Apply a patch to modify a single file.

Use this tool for a complex edit, creation, deletion, or move affecting one
file. Submit a separate call for each file.

Do not use this tool when a dedicated tool is a better fit:
- Change one existing file, including multiple disjoint replacements -> `edit`
- Create one new file -> `write`

`patchText` uses the Flocks patch format, not a standard git diff:

*** Begin Patch
*** Add File: path/to/new_file.py
new file content
*** Update File: path/to/existing_file.py
@@ -10,3 +10,3 @@
 context line
-old line
+new line
*** Delete File: path/to/delete.py
*** End Patch

Use the edit tool for simple string replacements.
Use apply_patch for complex single-file patch changes."""


@dataclass
class PatchChunk:
    """A chunk of changes within a file update"""
    start_line: int
    original_lines: List[str] = field(default_factory=list)
    new_lines: List[str] = field(default_factory=list)


@dataclass
class PatchHunk:
    """A hunk representing a single file operation"""
    path: str
    type: str  # "add", "update", "delete"
    contents: str = ""  # For add operations
    chunks: List[PatchChunk] = field(default_factory=list)  # For update operations
    move_path: Optional[str] = None  # For move operations


def parse_patch(patch_text: str) -> List[PatchHunk]:
    """
    Parse a patch text into hunks
    
    Args:
        patch_text: The patch text
        
    Returns:
        List of PatchHunk objects
    """
    hunks = []
    
    # Normalize line endings
    patch_text = patch_text.replace("\r\n", "\n").replace("\r", "\n")
    
    # Check for empty patch
    if "*** Begin Patch" in patch_text and "*** End Patch" in patch_text:
        begin_idx = patch_text.find("*** Begin Patch")
        end_idx = patch_text.find("*** End Patch")
        patch_content = patch_text[begin_idx + len("*** Begin Patch"):end_idx].strip()
        
        if not patch_content:
            return []
    
    lines = patch_text.split("\n")
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Skip begin/end markers
        if line.startswith("*** Begin Patch") or line.startswith("*** End Patch"):
            i += 1
            continue
        
        # Add file
        if line.startswith("*** Add File:"):
            path = line[len("*** Add File:"):].strip()
            i += 1
            
            # Collect content until next marker
            content_lines = []
            while i < len(lines) and not lines[i].startswith("***"):
                content_lines.append(lines[i])
                i += 1
            
            hunks.append(PatchHunk(
                path=path,
                type="add",
                contents="\n".join(content_lines)
            ))
            continue
        
        # Update file
        if line.startswith("*** Update File:"):
            path = line[len("*** Update File:"):].strip()
            move_path = None
            
            # Check for move
            if " -> " in path:
                parts = path.split(" -> ")
                path = parts[0].strip()
                move_path = parts[1].strip()
            
            i += 1
            
            # Collect chunks until next file marker
            chunks = []
            while i < len(lines) and not lines[i].startswith("*** "):
                chunk_line = lines[i]
                
                # Parse chunk header @@ ... @@
                if chunk_line.startswith("@@@") or chunk_line.startswith("@@"):
                    # Parse line numbers
                    match = re.match(r'@@+\s*-(\d+)(?:,\d+)?\s*\+(\d+)(?:,\d+)?\s*@@+', chunk_line)
                    if match:
                        start_line = int(match.group(1))
                    else:
                        start_line = 1
                    
                    i += 1
                    
                    # Collect lines until next chunk or file
                    original_lines = []
                    new_lines = []
                    
                    while i < len(lines):
                        if lines[i].startswith("@@") or lines[i].startswith("*** "):
                            break
                        
                        if lines[i].startswith("-"):
                            original_lines.append(lines[i][1:])
                        elif lines[i].startswith("+"):
                            new_lines.append(lines[i][1:])
                        elif lines[i].startswith(" "):
                            original_lines.append(lines[i][1:])
                            new_lines.append(lines[i][1:])
                        else:
                            # Context line without prefix
                            original_lines.append(lines[i])
                            new_lines.append(lines[i])
                        
                        i += 1
                    
                    chunks.append(PatchChunk(
                        start_line=start_line,
                        original_lines=original_lines,
                        new_lines=new_lines
                    ))
                else:
                    i += 1
            
            hunks.append(PatchHunk(
                path=path,
                type="update",
                chunks=chunks,
                move_path=move_path
            ))
            continue
        
        # Delete file
        if line.startswith("*** Delete File:"):
            path = line[len("*** Delete File:"):].strip()
            hunks.append(PatchHunk(
                path=path,
                type="delete"
            ))
            i += 1
            continue
        
        i += 1
    
    return hunks


def apply_chunks(content: str, chunks: List[PatchChunk]) -> str:
    """
    Apply chunks to file content
    
    Args:
        content: Original file content
        chunks: List of changes to apply
        
    Returns:
        Modified content
    """
    if not chunks:
        return content
    
    lines = content.split("\n")

    # Validate every chunk against the file before touching it: a hunk that
    # points past the end or whose context no longer matches must fail loudly
    # instead of deleting whatever happens to sit at that line number.
    for chunk in chunks:
        start_idx = chunk.start_line - 1
        if start_idx < 0 or start_idx > len(lines) or (
            start_idx == len(lines) and chunk.original_lines
        ):
            raise ValueError(
                f"Patch hunk starts at line {chunk.start_line} but the file only has {len(lines)} lines"
            )
        end_idx = start_idx + len(chunk.original_lines)
        if end_idx > len(lines):
            raise ValueError(
                f"Patch hunk at line {chunk.start_line} removes {len(chunk.original_lines)} lines "
                f"but only {len(lines) - start_idx} remain in the file"
            )
        for offset, expected in enumerate(chunk.original_lines):
            actual = lines[start_idx + offset]
            if expected.rstrip() != actual.rstrip():
                raise ValueError(
                    f"Patch context mismatch at line {chunk.start_line + offset}: "
                    f"expected {expected!r}, found {actual!r}. Re-read the file and regenerate the patch."
                )

    # Apply chunks in reverse order to preserve line numbers
    for chunk in reversed(chunks):
        start_idx = chunk.start_line - 1
        end_idx = start_idx + len(chunk.original_lines)
        del lines[start_idx:end_idx]
        for i, new_line in enumerate(chunk.new_lines):
            lines.insert(start_idx + i, new_line)

    return "\n".join(lines)


def _read_file_state(path: str) -> Optional[nofollow.FileText]:
    """Current text of ``path`` and which file it is (None when absent).

    Read without following a link on the way: one put there meanwhile
    raises :class:`nofollow.LinkOnTheWay` instead of reading elsewhere.
    """
    return nofollow.read_text(path)


def _unchanged(path: str, before: Optional[nofollow.FileText]) -> bool:
    try:
        return _read_file_state(path) == before
    except OSError:
        return False


def generate_diff(filepath: str, old_content: str, new_content: str) -> str:
    """Generate unified diff"""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    
    diff_lines = list(unified_diff(
        old_lines,
        new_lines,
        fromfile=filepath,
        tofile=filepath
    ))
    
    return "".join(diff_lines)


@ToolRegistry.register_function(
    name="apply_patch",
    group="文件操作",
    description=DESCRIPTION,
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(
            name="patchText",
            type=ParameterType.STRING,
            description="The full patch text that describes all changes to be made",
            required=True
        ),
    ]
)
async def apply_patch_tool(
    ctx: ToolContext,
    patchText: str,
) -> ToolResult:
    """
    Apply a patch to files
    
    Args:
        ctx: Tool context
        patchText: Patch text to apply
        
    Returns:
        ToolResult with applied changes
    """
    if not patchText:
        return ToolResult(
            success=False,
            error="patchText is required"
        )
    
    # Parse patch
    try:
        hunks = parse_patch(patchText)
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Failed to parse patch: {str(e)}"
        )
    
    if not hunks:
        return ToolResult(
            success=False,
            error="No valid hunks found in patch"
        )
    if len(hunks) != 1:
        return ToolResult(
            success=False,
            error=(
                "apply_patch currently supports exactly one file operation per call. "
                "Split multi-file patches into separate apply_patch calls."
            ),
        )
    
    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    sandbox_read_only = (
        isinstance(sandbox, dict)
        and sandbox.get("workspace_access") == "ro"
    )
    execution_mode = ctx.extra.get("execution_mode") if ctx.extra else None
    if sandbox_read_only and execution_mode != "plan":
        return ToolResult(
            success=False,
            error=(
                "Patch is blocked in sandbox read-only workspace mode. "
                "Set sandbox.workspace_access to 'rw' to allow edits."
            ),
        )
    
    # Process hunks and collect changes
    file_changes: List[Dict[str, Any]] = []
    total_diff = ""
    # What each touched path held when the patch was computed, to refuse
    # writing a stale result after a permission wait.
    states_before: Dict[str, Optional[nofollow.FileText]] = {}
    # Where the permission patterns are relative to; every hunk resolves
    # against the same session workspace.
    pattern_base: Optional[str] = None
    
    for hunk in hunks:
        # The paths of this hunk once resolved: an error quoting what was read
        # through one that moved is not passed on.
        hunk_paths: list[str] = []
        try:
            resolution = await resolve_tool_path(ctx, hunk.path)
            pattern_base = resolution.worktree
            filepath = resolution.resolved_path
            move_resolution = await resolve_tool_path(ctx, hunk.move_path) if hunk.move_path else None
            hunk_paths = [filepath, *([move_resolution.resolved_path] if move_resolution else [])]
            # Nothing is read through a path that moved since it was checked.
            moved = moved_since_resolved(*hunk_paths)
            if moved:
                return ToolResult(success=False, error=changed_before_asking(moved[0]))

            if hunk.type == "add":
                # Whatever stands there, looked at without opening it.
                if nofollow.kind(filepath) is not None:
                    return ToolResult(
                        success=False,
                        error=(
                            f"File already exists: {filepath}. "
                            "Use '*** Update File:' to change it or '*** Delete File:' first."
                        ),
                    )
                states_before[filepath] = None
                old_content = ""
                new_content = hunk.contents if hunk.contents.endswith("\n") or not hunk.contents else hunk.contents + "\n"
                diff = generate_diff(filepath, old_content, new_content)
                
                file_changes.append({
                    "displayPath": resolution.display_path,
                    "permissionPattern": resolution.permission_pattern,
                    "filePath": filepath,
                    "oldContent": old_content,
                    "newContent": new_content,
                    "type": "add",
                    "diff": diff,
                    "additions": new_content.count("\n") + 1,
                    "deletions": 0
                })
                total_diff += diff + "\n"
                
            elif hunk.type == "update":
                state = _read_file_state(filepath)
                if state is None:
                    return ToolResult(
                        success=False,
                        error=f"File not found for update: {filepath}"
                    )
                old_content = state.text
                
                new_content = apply_chunks(old_content, hunk.chunks)
                diff = generate_diff(filepath, old_content, new_content)
                
                change_type = "move" if hunk.move_path else "update"
                move_filepath = move_resolution.resolved_path if move_resolution else None
                states_before[filepath] = state
                if move_filepath:
                    states_before[move_filepath] = _read_file_state(move_filepath)
                
                file_changes.append({
                    "displayPath": resolution.display_path,
                    "permissionPattern": (
                        move_resolution.permission_pattern if move_resolution else resolution.permission_pattern
                    ),
                    "filePath": filepath,
                    "oldContent": old_content,
                    "newContent": new_content,
                    "type": change_type,
                    "movePath": move_filepath,
                    "diff": diff,
                    "additions": sum(1 for line in new_content.split("\n") if line not in old_content.split("\n")),
                    "deletions": sum(1 for line in old_content.split("\n") if line not in new_content.split("\n"))
                })
                total_diff += diff + "\n"
                
            elif hunk.type == "delete":
                state = _read_file_state(filepath)
                if state is None:
                    return ToolResult(
                        success=False,
                        error=f"File not found for deletion: {filepath}"
                    )
                old_content = state.text
                
                states_before[filepath] = state
                diff = generate_diff(filepath, old_content, "")
                
                file_changes.append({
                    "displayPath": resolution.display_path,
                    "permissionPattern": resolution.permission_pattern,
                    "filePath": filepath,
                    "oldContent": old_content,
                    "newContent": "",
                    "type": "delete",
                    "diff": diff,
                    "additions": 0,
                    "deletions": old_content.count("\n") + 1
                })
                total_diff += diff + "\n"
                
        except nofollow.LinkOnTheWay as e:
            return ToolResult(success=False, error=changed_before_asking(e.path))
        except ValueError as e:
            return ToolResult(
                success=False,
                error=checked_error(hunk_paths, f"Invalid patch path for {hunk.path}: {str(e)}")
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=checked_error(hunk_paths, f"Failed to process hunk for {hunk.path}: {str(e)}")
            )

    if sandbox_read_only:
        from flocks.session.execution_mode import is_plan_file_edit

        if not all(
            is_plan_file_edit(execution_mode, ctx, change["filePath"])
            and (
                not change.get("movePath")
                or is_plan_file_edit(execution_mode, ctx, change["movePath"])
            )
            for change in file_changes
        ):
            return ToolResult(
                success=False,
                error=(
                    "Patch is blocked in sandbox read-only workspace mode. "
                    "Only the current session plan file may be changed in Plan mode."
                ),
            )

    # Explicit edit rules (session, agent, delegating session) apply here.
    # A move changes its source (displayPath) as well as its destination.
    # The request carries the diff: none read after a path moved.
    moved = moved_since_resolved(*states_before)
    if moved:
        return ToolResult(success=False, error=changed_before_asking(moved[0]))
    identities = resolved_identities(*states_before)
    await ctx.ask(
        permission="edit",
        patterns=list(dict.fromkeys(
            pattern
            for c in file_changes
            for pattern in (c["displayPath"], c["permissionPattern"])
        )),
        always=["*"],
        metadata={"tool": "apply_patch", "diff": total_diff, "pattern_base": pattern_base},
    )
    # A path that now leads elsewhere is not read through to compare.
    changed_meanwhile = paths_changed(identities) or [
        path for path, before in states_before.items()
        if not _unchanged(path, before)
    ]
    if changed_meanwhile:
        return ToolResult(
            success=False,
            error=(
                f"{', '.join(changed_meanwhile)} changed while waiting for permission; "
                "read it again and redo the patch."
            ),
        )

    # Apply changes
    changed_files = []
    
    # Each file is written or removed through directories opened without
    # following a link, and only while it is still what was read above.
    for change in file_changes:
        filepath = change["filePath"]
        
        try:
            if change["type"] == "add":
                # Missing parent directories are made on the way.
                nofollow.write_text(filepath, change["newContent"], expect=None)
                changed_files.append(filepath)
                
            elif change["type"] == "update":
                nofollow.replace_text(filepath, change["newContent"], before=states_before[filepath])
                changed_files.append(filepath)
                
            elif change["type"] == "move":
                move_path = change["movePath"]
                landing = states_before.get(move_path)
                if landing is None:
                    nofollow.write_text(move_path, change["newContent"], expect=None)
                else:
                    nofollow.replace_text(move_path, change["newContent"], before=landing)
                nofollow.remove(filepath, expect=states_before[filepath].identity)
                changed_files.append(move_path)
                
            elif change["type"] == "delete":
                nofollow.remove(filepath, expect=states_before[filepath].identity)
                changed_files.append(filepath)
                
        except (nofollow.LinkOnTheWay, nofollow.StaleFile) as e:
            return ToolResult(
                success=False,
                error=f"{e.path} changed while waiting for permission; read it again and redo the patch.",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to apply change to {filepath}: {str(e)}"
            )
    
    # Build summary
    summary_lines = []
    for change in file_changes:
        rel_path = change.get("movePath") and change["permissionPattern"] or change["displayPath"]
        if change["type"] == "add":
            summary_lines.append(f"A {rel_path}")
        elif change["type"] == "delete":
            summary_lines.append(f"D {rel_path}")
        else:
            summary_lines.append(f"M {rel_path}")
    
    output = f"Success. Updated the following files:\n" + "\n".join(summary_lines)
    
    return ToolResult(
        success=True,
        output=output,
        title=output,
        metadata={
            "diff": total_diff,
            "files": file_changes
        }
    )
