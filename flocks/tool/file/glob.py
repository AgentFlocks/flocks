"""
Glob Tool - File pattern matching

Fast file pattern matching tool that:
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Uses ripgrep for fast searching when available
"""

import os
import asyncio
import shutil
from typing import Optional, List, Dict, Any, AsyncIterator

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.tool.path_utils import resolve_tool_path
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
        "--follow",
        "--no-messages"
    ]
    
    for pattern in glob_patterns:
        args.extend(["--glob", pattern])
    
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd
    )
    
    async for line in proc.stdout:
        filepath = line.decode('utf-8', errors='replace').strip()
        if filepath:
            yield filepath
    
    await proc.wait()


def fallback_glob(
    cwd: str,
    pattern: str
) -> List[str]:
    """
    Fallback glob implementation using Python's glob module
    
    Args:
        cwd: Working directory
        pattern: Glob pattern
        
    Returns:
        List of matching file paths
    """
    import glob as python_glob
    
    # Normalize pattern
    if not pattern.startswith("**/"):
        pattern = "**/" + pattern
    
    full_pattern = os.path.join(cwd, pattern)
    
    matches = []
    for filepath in python_glob.glob(full_pattern, recursive=True):
        if os.path.isfile(filepath):
            rel_path = os.path.relpath(filepath, cwd)
            matches.append(rel_path)
    
    return matches


@ToolRegistry.register_function(
    name="glob",
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
        resolution = await resolve_tool_path(ctx, path or ".")
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc), title=path or pattern)

    search_path = resolution.resolved_path
    title = resolution.display_path

    # Request permission
    await ctx.ask(
        permission="glob",
        patterns=[resolution.permission_pattern],
        always=["*"],
        metadata={
            "pattern": pattern,
            "path": search_path,
        }
    )
    
    # Find files
    rg_path = find_ripgrep()
    files: List[Dict[str, Any]] = []
    truncated = False
    
    try:
        if rg_path:
            async for filepath in ripgrep_files(rg_path, search_path, [pattern]):
                if len(files) >= MAX_FILES:
                    truncated = True
                    break
                
                full_path = os.path.join(search_path, filepath)
                try:
                    stat = os.stat(full_path)
                    mtime = stat.st_mtime
                except OSError:
                    mtime = 0
                
                files.append({
                    'path': full_path,
                    'mtime': mtime
                })
        else:
            global _ripgrep_fallback_logged
            if not _ripgrep_fallback_logged:
                _ripgrep_fallback_logged = True
                log.info("glob.ripgrep_not_found", {"fallback": "python_glob"})
            
            for filepath in fallback_glob(search_path, pattern):
                if len(files) >= MAX_FILES:
                    truncated = True
                    break
                
                full_path = os.path.join(search_path, filepath)
                try:
                    stat = os.stat(full_path)
                    mtime = stat.st_mtime
                except OSError:
                    mtime = 0
                
                files.append({
                    'path': full_path,
                    'mtime': mtime
                })
                
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
    
    return ToolResult(
        success=True,
        output="\n".join(output_lines),
        title=title,
        truncated=truncated,
        metadata={
            "count": len(files),
            "truncated": truncated
        }
    )
