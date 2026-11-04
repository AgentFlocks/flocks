"""
Glob Tool - File pattern matching

Fast file pattern matching tool that:
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Uses ripgrep for fast searching when available
"""

import os
import asyncio
import contextlib
import fnmatch
import functools
import shutil
from typing import Optional, List, Dict, Any, AsyncIterator, Callable, Iterator, Tuple

from flocks.tool import nofollow, search_walk
from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.tool.path_utils import changed_before_asking, resolve_tool_path
from flocks.utils.log import Log


log = Log.create(service="tool.glob")


# Constants
MAX_FILES = 100
_ripgrep_fallback_logged = False


# Description matching Flocks' glob.txt
DESCRIPTION = """- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open-ended search that may require multiple rounds of globbing and grepping, prefer delegating that exploration or use a more specialized search workflow
- You may call multiple independent tools in the same response. Prefer separate parallel Glob calls when multiple searches are likely to be useful."""


def find_ripgrep() -> Optional[str]:
    """Find ripgrep executable"""
    for name in ['rg', 'ripgrep']:
        path = shutil.which(name)
        if path:
            return path
    return None


async def ripgrep_files(
    rg_path: str,
    cwd: str,
    glob_patterns: List[str]
) -> AsyncIterator[str]:
    """
    Find files using ripgrep
    
    Links are not followed: what one points at may be anyone's.

    Args:
        rg_path: Path to ripgrep
        cwd: Working directory
        glob_patterns: Glob patterns to match
        
    Yields:
        Matching file paths
    """
    args = [
        rg_path,
        "--files",
        "--hidden",
        "--no-messages"
    ]
    
    for pattern in glob_patterns:
        args.extend(["--glob", pattern])
    
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd
    )
    
    try:
        async for line in proc.stdout:
            filepath = line.decode('utf-8', errors='replace').strip()
            if filepath:
                yield filepath
    finally:
        # The caller may stop reading early: ripgrep is not left running,
        # and what is left in its pipes is read so it can be reaped.
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.communicate()


def fallback_glob(
    cwd: str,
    pattern: str,
    *,
    stop: Callable[[], bool] = lambda: False,
) -> List[str]:
    """
    Fallback glob implementation matching like Python's glob module
    
    Matches ``glob(recursive=True, include_hidden=True)`` would make --
    hidden files included, like ripgrep's --hidden -- over regular files
    reached without following a link, like ripgrep.

    Args:
        cwd: Working directory
        pattern: Glob pattern
        stop: Checked as the walk goes; once true, the matches so far are returned
        
    Returns:
        List of matching file paths
    """
    with nofollow.directory(cwd) as root:
        return list(_glob_below(root, pattern, stop))


def _glob_below(root: nofollow.Directory, pattern: str, stop: Callable[[], bool]) -> Iterator[str]:
    """Paths of the regular files below ``root`` that ``pattern`` matches, as the walk finds them."""
    # Normalize pattern; Windows takes either separator, as glob does there.
    if os.altsep:
        pattern = pattern.replace(os.sep, os.altsep)
    if not pattern.startswith("**/"):
        pattern = "**/" + pattern
    pattern_parts = pattern.split("/")
    
    for rel_path, _holder, _name in nofollow.walk_files(root, stop=stop):
        if _glob_matches(pattern_parts, rel_path.split(os.sep)):
            yield rel_path
    

def _add_file(files: List[Dict[str, Any]], root: nofollow.Directory, search_path: str, filepath: str) -> bool:
    """Keep ``filepath`` when it is a regular file below ``root``, reached without a link.

    False, with nothing kept, once MAX_FILES are in: there are more.
    """
    status = nofollow.file_status(root, filepath)
    if status is None:
        return True
    if len(files) >= MAX_FILES:
        return False
    files.append({
        'path': os.path.join(search_path, filepath),
        'mtime': status.st_mtime
    })
    return True


def _fallback_files(
    search_path: str,
    expected: nofollow.Identity,
    pattern: str,
    stop: Callable[[], bool],
) -> Tuple[List[Dict[str, Any]], bool]:
    """The files to show, and whether there were more, found walking in Python.

    Run in a worker thread, which opens the search directory itself (no
    descriptor is shared with the event loop, which closes its own when the
    search is given up) and goes no further unless it is the directory the
    tool opened, identified by ``expected``. Each match is checked as those
    ripgrep prints are, and the walk ends at the first one past MAX_FILES.
    """
    files: List[Dict[str, Any]] = []
    with nofollow.directory(search_path) as root:
        if nofollow.identity(root.status()) != expected:
            raise nofollow.StaleFile(search_path)
        for filepath in _glob_below(root, pattern, stop):
            if not _add_file(files, root, search_path, filepath):
                return files, True
    return files, False


def _glob_matches(pattern_parts: List[str], path_parts: List[str]) -> bool:
    """Whether a path matches a pattern, both split at "/", the way recursive glob does."""

    @functools.cache
    def matches_from(pattern_index: int, path_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            # Any number of directories, none included.
            return any(
                matches_from(pattern_index + 1, index) for index in range(path_index, len(path_parts) + 1)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatch(path_parts[path_index], part)
            and matches_from(pattern_index + 1, path_index + 1)
        )

    return matches_from(0, 0)


@ToolRegistry.register_function(
    name="glob",
    group="检索",
    description=DESCRIPTION,
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter(
            name="pattern",
            type=ParameterType.STRING,
            description="The glob pattern to match files against",
            required=True
        ),
        ToolParameter(
            name="path",
            type=ParameterType.STRING,
            description="The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter \"undefined\" or \"null\" - simply omit it for the default behavior.",
            required=False
        ),
    ]
)
async def glob_tool(
    ctx: ToolContext,
    pattern: str,
    path: Optional[str] = None,
) -> ToolResult:
    """
    Find files matching a glob pattern
    
    Args:
        ctx: Tool context
        pattern: Glob pattern to match
        path: Directory to search in
        
    Returns:
        ToolResult with matching files
    """
    try:
        resolution = await resolve_tool_path(
            ctx,
            path or ".",
            allow_host_memory=True,
            allow_host_skills=True,
        )
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc), title=path or pattern)

    search_path = resolution.resolved_path
    title = resolution.display_path

    # Find files
    rg_path = find_ripgrep()
    files: List[Dict[str, Any]] = []
    truncated = False
    cut_short = False
    
    try:
        # The search directory is opened once without following a link, and
        # every name found is checked against it the same way: ripgrep walks
        # by path, and a link put on the way while it runs would bring names
        # of someone else's files.
        with nofollow.directory(search_path) as root:
            if rg_path:
                found = ripgrep_files(rg_path, root.child_cwd(), [pattern])
                async with contextlib.aclosing(found):
                    async for filepath in found:
                        if not _add_file(files, root, search_path, filepath):
                            truncated = True
                            break
            else:
                global _ripgrep_fallback_logged
                if not _ripgrep_fallback_logged:
                    _ripgrep_fallback_logged = True
                    log.info("glob.ripgrep_not_found", {"fallback": "python_glob"})
                # Off the event loop: walking a home directory takes minutes,
                # and every other request would wait for it.
                expected = nofollow.identity(root.status())
                (files, truncated), cut_short = await search_walk.run_in_thread(
                    lambda stop: _fallback_files(search_path, expected, pattern, stop)
                )
            
    except (nofollow.LinkOnTheWay, nofollow.StaleFile):
        return ToolResult(success=False, error=changed_before_asking(search_path), title=title)
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Glob search failed: {str(e)}",
            title=title
        )
    
    # Sort by modification time (most recent first)
    files.sort(key=lambda x: x['mtime'], reverse=True)
    
    # Build output
    output_lines = []
    
    if not files:
        output_lines.append("No files found")
    else:
        output_lines.extend(f['path'] for f in files)
        
        if truncated:
            output_lines.append("")
            output_lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
    
    if cut_short:
        output_lines.append("")
        output_lines.append(search_walk.stopped_note())
        truncated = True

    return ToolResult(
        success=True,
        output="\n".join(output_lines),
        title=title,
        truncated=truncated,
        metadata={
            "count": len(files),
            "truncated": truncated,
            "timed_out": cut_short,
        }
    )
