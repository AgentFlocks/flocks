"""
Grep Tool - Code search using ripgrep

Fast content search tool that:
- Searches file contents using regular expressions
- Supports full regex syntax
- Filter files by pattern
- Returns results sorted by modification time
"""

import os
import asyncio
import contextlib
import shutil
import re
from typing import Optional, List, Dict, Any
from datetime import datetime

from flocks.tool import nofollow
from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.tool.path_utils import changed_before_asking, resolve_tool_path
from flocks.utils.log import Log


log = Log.create(service="tool.grep")


# Constants
MAX_LINE_LENGTH = 2000
MAX_MATCHES = 100
# Directories the Python fallback does not search.
SKIPPED_DIRECTORIES = ('node_modules', '__pycache__', '.git', 'dist', 'build', 'target')


# Description matching Flocks' grep.txt
DESCRIPTION = """- Fast content search tool that works with any codebase size
- Searches file contents using regular expressions
- Supports full regex syntax (eg. "log.*Error", "function\\s+\\w+", etc.)
- Filter files by pattern with the include parameter (eg. "*.js", "*.{ts,tsx}")
- Returns file paths and line numbers with at least one match sorted by modification time
- Use this tool when you need to find files containing specific patterns
- If you need to identify/count the number of matches within files, use the Bash tool with `rg` (ripgrep) directly. Do NOT use `grep`.
- When you are doing an open-ended search that may require multiple rounds of globbing and grepping, use delegate_task instead"""


def find_ripgrep() -> Optional[str]:
    """
    Find ripgrep executable
    
    Returns:
        Path to ripgrep or None
    """
    # Try common names
    for name in ['rg', 'ripgrep']:
        path = shutil.which(name)
        if path:
            return path
    return None


def fallback_grep(
    pattern: str,
    search_path: str,
    include: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fallback grep implementation using Python's re module
    
    Used when ripgrep is not available.
    
    Args:
        pattern: Regex pattern to search
        search_path: Directory to search
        include: File pattern to include
        
    Returns:
        List of matches
    """
    import fnmatch
    
    matches = []
    
    try:
        regex = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {e}")
    
    # Walked without following a link, every file opened through the
    # directory that holds it: what a link points at may be anyone's.
    try:
        root = nofollow.directory(search_path)
    except nofollow.LinkOnTheWay:
        raise
    except OSError:
        return matches

    def skipped(name: str) -> bool:
        # Skip hidden and common ignore directories
        return name.startswith('.') or name in SKIPPED_DIRECTORIES

    with root:
        for relative, holder, filename in nofollow.walk_files(root, skip_directory=skipped):
            # Apply include filter
            if include:
                # Handle glob patterns like "*.js" or "*.{ts,tsx}"
                patterns = []
                if '{' in include:
                    # Expand brace patterns
                    match = re.match(r'\*\.?\{([^}]+)\}', include)
                    if match:
                        exts = match.group(1).split(',')
                        patterns = [f"*.{ext.strip()}" for ext in exts]
                else:
                    patterns = [include]
                
                if not any(fnmatch.fnmatch(filename, p) for p in patterns):
                    continue
            
            filepath = os.path.join(search_path, relative)
            
            try:
                fd = holder.open(filename, os.O_RDONLY)
            except OSError:
                continue
            try:
                with open(fd, 'r', encoding='utf-8', errors='replace') as f:
                    mod_time = os.fstat(f.fileno()).st_mtime
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            matches.append({
                                'path': filepath,
                                'modTime': mod_time,
                                'lineNum': line_num,
                                'lineText': line.rstrip('\n\r')
                            })
                            
                            if len(matches) >= MAX_MATCHES * 10:
                                return matches
                                
            except (IOError, OSError):
                continue
    
    return matches


async def _communicate(proc) -> tuple:
    """``proc.communicate()``; a search given up (cancelled) does not leave ripgrep running.

    What is left in its pipes is read to the end as it is killed: with a pipe
    full, the process would not be reaped.
    """
    try:
        return await proc.communicate()
    except BaseException:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        with contextlib.suppress(Exception):
            await proc.communicate()
        raise


async def ripgrep_search(
    rg_path: str,
    pattern: str,
    search_path: str,
    include: Optional[str] = None,
    *,
    cwd: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search using ripgrep
    
    Links are not followed: what one points at may be anyone's. Paths in the
    matches are as ripgrep printed them, ``./`` taken off.

    Args:
        rg_path: Path to ripgrep executable
        pattern: Regex pattern
        search_path: Directory (or file) to search, relative to ``cwd``
        include: File pattern to include
        cwd: Where ripgrep runs
        
    Returns:
        List of matches
    """
    args = [
        rg_path,
        "-nH",              # Line numbers, filenames
        "--hidden",         # Search hidden files
        "--no-messages",    # Suppress error messages
        "--field-match-separator=|",
        "--regexp", pattern
    ]
    
    if include:
        args.extend(["--glob", include])
    
    args.extend(["--", search_path])
    
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    
    stdout, stderr = await _communicate(proc)
    output = stdout.decode('utf-8', errors='replace')
    
    # Exit codes: 0 = matches found, 1 = no matches, 2 = errors
    if proc.returncode == 1:
        return []
    if proc.returncode == 2 and not output.strip():
        # ``--no-messages`` silences per-file errors (unreadable paths) but a
        # regex parse error still lands on stderr; report it instead of
        # pretending nothing matched.
        err = stderr.decode('utf-8', errors='replace')
        if 'regex parse error' in err or 'error parsing' in err.lower() or 'unclosed' in err.lower():
            raise ValueError(f"Invalid regex pattern: {pattern} ({err.strip().splitlines()[-1] if err.strip() else 'ripgrep exit 2'})")
        return []
    
    matches = []
    # Lines are taken as printed: their text is compared with a second search.
    for line in output.split('\n'):
        if not line:
            continue
        
        parts = line.split('|', 2)
        if len(parts) < 3:
            continue
        
        filepath, line_num_str, line_text = parts
        
        try:
            line_num = int(line_num_str)
        except ValueError:
            continue
        
        matches.append({
            'path': filepath.removeprefix(os.curdir + os.sep),
            'modTime': 0,
            'lineNum': line_num,
            'lineText': line_text
        })
    
    return matches


def _open_search_root(search_path: str):
    """Where ripgrep runs, what it searches there, and the directory its paths are under.

    The directory is opened without following a link (the caller closes
    it). None when there is nothing to search.
    """
    try:
        return nofollow.directory(search_path), os.curdir, search_path
    except NotADirectoryError:
        pass
    except FileNotFoundError:
        return None
    try:
        holder, name = nofollow.parent(search_path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    try:
        kind = holder.kind(name)
    except BaseException:
        holder.close()
        raise
    if kind == "link":
        holder.close()
        raise nofollow.LinkOnTheWay(search_path)
    if kind != "file":
        holder.close()
        return None
    return holder, os.path.join(os.curdir, name), os.path.dirname(search_path)


async def _ripgrep_matches(
    rg_path: str,
    pattern: str,
    search_path: str,
    include: Optional[str],
) -> tuple[List[Dict[str, Any]], bool]:
    """Matches to show, most recently modified files first, and whether more were found.

    ripgrep walks and reads by path, so a link put on the way while it runs
    could bring someone else's names and lines. Every match is checked
    against the search directory opened here without following a link: its
    file must be reached that way, and each line shown must be found again
    in the file opened that way.
    """
    opened = _open_search_root(search_path)
    if opened is None:
        return [], False
    root, target, base = opened
    with root:
        matches = await ripgrep_search(rg_path, pattern, target, include, cwd=root.child_cwd())
        statuses: Dict[str, Any] = {}
        reached = []
        for match in matches:
            relative = match['path']
            if relative not in statuses:
                statuses[relative] = nofollow.file_status(root, relative)
            if statuses[relative] is not None:
                reached.append({**match, 'modTime': statuses[relative].st_mtime})
        # Sort by modification time (most recent first)
        reached.sort(key=lambda x: x['modTime'], reverse=True)
        shown = reached[:MAX_MATCHES]
        found_again = await _lines_found_again(rg_path, pattern, root, {match['path'] for match in shown})
    shown = [
        match for match in shown
        if found_again.get((match['path'], match['lineNum'])) == match['lineText']
    ]
    return [{**match, 'path': os.path.join(base, match['path'])} for match in shown], len(reached) > MAX_MATCHES


async def _lines_found_again(
    rg_path: str,
    pattern: str,
    root: "nofollow.Directory",
    relatives: set,
) -> Dict[tuple, str]:
    """(path, line) -> text of the matches in the files opened here."""
    if not nofollow.SUPPORTED:
        # No descriptor paths here (Windows): ripgrep reads each file, as
        # opened here, on its standard input.
        found: Dict[tuple, str] = {}
        for relative in sorted(relatives):
            try:
                fd = nofollow.open_below(root, relative)
            except (OSError, ValueError):
                continue
            try:
                for line_num, text in (await _search_input(rg_path, pattern, fd)).items():
                    found[(relative, line_num)] = text
            finally:
                os.close(fd)
        return found
    descriptors: Dict[str, int] = {}
    try:
        for relative in sorted(relatives):
            try:
                descriptors[relative] = nofollow.open_below(root, relative)
            except (OSError, ValueError):
                continue
        if not descriptors:
            return {}
        # ripgrep reads each file through its descriptor, not its path.
        names = {f"/dev/fd/{fd}": relative for relative, fd in descriptors.items()}
        return await _search_descriptors(rg_path, pattern, names, tuple(descriptors.values()))
    finally:
        for fd in descriptors.values():
            os.close(fd)


async def _search_input(rg_path: str, pattern: str, fd: int) -> Dict[int, str]:
    """line -> text of what ``pattern`` matches in the file open as ``fd``, read on standard input."""
    args = [rg_path, "-n", "--no-filename", "--no-messages", "--field-match-separator=|", "--regexp", pattern, "-"]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=fd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _stderr = await _communicate(proc)
    found: Dict[int, str] = {}
    for line in stdout.decode('utf-8', errors='replace').split('\n'):
        number, separator, text = line.partition('|')
        if not separator:
            continue
        try:
            found[int(number)] = text
        except ValueError:
            continue
    return found


async def _search_descriptors(rg_path: str, pattern: str, names: Dict[str, str], fds: tuple) -> Dict[tuple, str]:
    """(path, line) -> text of what ``pattern`` matches in the files open as ``fds``."""
    args = [rg_path, "-nH", "--no-messages", "--field-match-separator=|", "--regexp", pattern, "--", *names]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        pass_fds=fds,
    )
    stdout, _stderr = await _communicate(proc)
    found: Dict[tuple, str] = {}
    for line in stdout.decode('utf-8', errors='replace').split('\n'):
        parts = line.split('|', 2)
        if len(parts) < 3 or parts[0] not in names:
            continue
        try:
            found[(names[parts[0]], int(parts[1]))] = parts[2]
        except ValueError:
            continue
    return found


@ToolRegistry.register_function(
    name="grep",
    group="检索",
    description=DESCRIPTION,
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter(
            name="pattern",
            type=ParameterType.STRING,
            description="The regex pattern to search for in file contents",
            required=True
        ),
        ToolParameter(
            name="path",
            type=ParameterType.STRING,
            description="The directory to search in. Defaults to the current working directory.",
            required=False
        ),
        ToolParameter(
            name="include",
            type=ParameterType.STRING,
            description='File pattern to include in the search (e.g. "*.js", "*.{ts,tsx}")',
            required=False
        ),
    ]
)
async def grep_tool(
    ctx: ToolContext,
    pattern: str,
    path: Optional[str] = None,
    include: Optional[str] = None,
) -> ToolResult:
    """
    Search file contents using regex
    
    Args:
        ctx: Tool context
        pattern: Regex pattern to search
        path: Directory to search
        include: File pattern filter
        
    Returns:
        ToolResult with search results
    """
    if not pattern:
        return ToolResult(
            success=False,
            error="pattern is required"
        )
    
    try:
        resolution = await resolve_tool_path(
            ctx,
            path or ".",
            allow_host_memory=True,
            allow_host_skills=True,
        )
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc), title=pattern)

    search_path = resolution.resolved_path

    # Find ripgrep
    rg_path = find_ripgrep()
    
    try:
        if rg_path:
            final_matches, truncated = await _ripgrep_matches(rg_path, pattern, search_path, include)
        else:
            log.warn("grep.ripgrep_not_found", {"fallback": "python_regex"})
            matches = fallback_grep(pattern, search_path, include)
            # Sort by modification time (most recent first)
            matches.sort(key=lambda x: x['modTime'], reverse=True)
            # Truncate results
            truncated = len(matches) > MAX_MATCHES
            final_matches = matches[:MAX_MATCHES]
    except nofollow.LinkOnTheWay:
        return ToolResult(success=False, error=changed_before_asking(search_path), title=pattern)
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Search failed: {str(e)}",
            title=pattern
        )
    
    if not final_matches:
        return ToolResult(
            success=True,
            output="No files found",
            title=pattern,
            metadata={"matches": 0, "truncated": False}
        )
    
    # Build output
    output_lines = [f"Found {len(final_matches)} matches"]
    
    current_file = ""
    for match in final_matches:
        if current_file != match['path']:
            if current_file:
                output_lines.append("")
            current_file = match['path']
            output_lines.append(f"{match['path']}:")
        
        # Truncate long lines
        line_text = match['lineText']
        if len(line_text) > MAX_LINE_LENGTH:
            line_text = line_text[:MAX_LINE_LENGTH] + "..."
        
        output_lines.append(f"  Line {match['lineNum']}: {line_text}")
    
    if truncated:
        output_lines.append("")
        output_lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
    
    return ToolResult(
        success=True,
        output="\n".join(output_lines),
        title=pattern,
        truncated=truncated,
        metadata={
            "matches": len(final_matches),
            "truncated": truncated
        }
    )
