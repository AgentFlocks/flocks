"""
Read Tool - File reading with support for text, images, and PDFs

Reads files from the local filesystem, supporting:
- Text files with line numbers
- Image files (returns base64 data)
- PDF files (returns base64 data)
- Binary file detection and rejection
"""

import os
import base64
import mimetypes
from pathlib import Path
from typing import Optional, List, Dict, Any

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.project.instance import Instance
from flocks.utils.log import Log
from flocks.utils.id import Identifier


log = Log.create(service="tool.read")

# Constants — keep output within the 10 K-char tool result budget
DEFAULT_READ_LIMIT = 200
MAX_LINE_LENGTH = 500
MAX_BYTES = 8 * 1024  # 8 KB

# Binary file extensions
BINARY_EXTENSIONS = {
    '.zip', '.tar', '.gz', '.exe', '.dll', '.so', '.class', '.jar', '.war',
    '.7z', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods',
    '.odp', '.bin', '.dat', '.obj', '.o', '.a', '.lib', '.wasm', '.pyc', '.pyo'
}

# Image MIME types that are supported
IMAGE_MIME_TYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp'
}

# Description matching Flocks' read.txt
DESCRIPTION = """Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The filePath parameter must be an absolute path, not a relative path
- By default, it reads up to 200 lines starting from the beginning of the file
- For files longer than 200 lines, you MUST use offset and limit to read in segments (e.g. offset=0 limit=200, then offset=200 limit=200, etc.)
- Any lines longer than 500 characters will be truncated
- Results are returned using cat -n format, with line numbers starting at 1
- You have the capability to call multiple tools in a single response. It is always better to speculatively read multiple files as a batch that are potentially useful.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.
- You can read image files using this tool."""


def is_binary_file(filepath: str) -> bool:
    """
    Check if a file is binary
    
    Uses extension check and content analysis.
    
    Args:
        filepath: Path to file
        
    Returns:
        True if file is binary
    """
    # Check extension first
    ext = Path(filepath).suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True
    
    # Check content for binary markers
    try:
        with open(filepath, 'rb') as f:
            # Read first 4KB
            chunk = f.read(4096)
            if not chunk:
                return False
            
            # Check for null bytes
            if b'\x00' in chunk:
                return True
            
            # Count non-printable characters
            non_printable = sum(
                1 for byte in chunk
                if byte < 9 or (byte > 13 and byte < 32)
            )
            
            # If >30% non-printable, consider binary
            return non_printable / len(chunk) > 0.3
            
    except Exception:
        return False


def get_mime_type(filepath: str) -> str:
    """
    Get MIME type for a file
    
    Args:
        filepath: Path to file
        
    Returns:
        MIME type string
    """
    mime_type, _ = mimetypes.guess_type(filepath)
    return mime_type or 'application/octet-stream'


def find_similar_files(directory: str, filename: str, max_suggestions: int = 3) -> List[str]:
    """
    Find similar files in directory for suggestions
    
    Args:
        directory: Directory to search
        filename: Target filename
        max_suggestions: Maximum suggestions to return
        
    Returns:
        List of similar file paths
    """
    try:
        entries = os.listdir(directory)
        filename_lower = filename.lower()
        
        suggestions = []
        for entry in entries:
            entry_lower = entry.lower()
            if filename_lower in entry_lower or entry_lower in filename_lower:
                suggestions.append(os.path.join(directory, entry))
        
        return suggestions[:max_suggestions]
    except Exception:
        return []


async def _resolve_sandbox_file_path(ctx: ToolContext, filepath: str) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve file path under sandbox workspace when sandbox is enabled.

    Returns:
        (resolved_path, error_message)
    """
    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    if not isinstance(sandbox, dict):
        return filepath, None

    workspace_root = sandbox.get("workspace_dir")
    if not workspace_root:
        return filepath, None

    if not os.path.isabs(filepath):
        filepath = os.path.join(workspace_root, filepath)

    try:
        from flocks.sandbox.paths import assert_sandbox_path

        resolved = await assert_sandbox_path(
            file_path=filepath,
            cwd=workspace_root,
            root=workspace_root,
        )
        return resolved.resolved, None
    except Exception:
        return None, (
            f"Path escapes sandbox workspace: {filepath}. "
            "Use paths inside sandbox workspace only."
        )


@ToolRegistry.register_function(
    name="read",
    description=DESCRIPTION,
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(
            name="filePath",
            type=ParameterType.STRING,
            description="The path to the file to read",
            required=True
        ),
        ToolParameter(
            name="offset",
            type=ParameterType.INTEGER,
            description="The line number to start reading from (0-based)",
            required=False,
            default=0
        ),
        ToolParameter(
            name="limit",
            type=ParameterType.INTEGER,
            description="The number of lines to read (defaults to 200)",
            required=False,
            default=DEFAULT_READ_LIMIT
        ),
    ]
)
async def read_tool(
    ctx: ToolContext,
    filePath: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None
) -> ToolResult:
    """
    Read a file from the local filesystem
    
    Supports text files, images, and PDFs.
    
    Args:
        ctx: Tool context
        filePath: Path to file
        offset: Starting line (0-based)
        limit: Number of lines to read
        
    Returns:
        ToolResult with file contents
    """
    # Resolve path
    filepath = filePath
    if not os.path.isabs(filepath):
        # Try to use Instance directory if available
        base_dir = Instance.get_directory() or os.getcwd()
        filepath = os.path.join(base_dir, filepath)

    filepath, sandbox_error = await _resolve_sandbox_file_path(ctx, filepath)
    if sandbox_error:
        return ToolResult(
            success=False,
            error=sandbox_error,
            title=filePath,
        )
    
    # Get relative title for display
    worktree = Instance.get_worktree() or os.getcwd()
    try:
        title = os.path.relpath(filepath, worktree)
    except ValueError:
        title = filepath
    
    # Request permission
    await ctx.ask(
        permission="read",
        patterns=[filepath],
        always=["*"],
        metadata={}
    )
    
    # Check file exists
    if not os.path.exists(filepath):
        directory = os.path.dirname(filepath)
        basename = os.path.basename(filepath)
        
        suggestions = find_similar_files(directory, basename)
        
        if suggestions:
            error_msg = f"File not found: {filepath}\n\nDid you mean one of these?\n" + "\n".join(suggestions)
        else:
            error_msg = f"File not found: {filepath}"
        
        return ToolResult(
            success=False,
            error=error_msg,
            title=title
        )
    
    # Get MIME type
    mime_type = get_mime_type(filepath)
    
    # Handle images (excluding SVG)
    is_image = (
        mime_type.startswith('image/') and 
        mime_type not in ('image/svg+xml', 'image/vnd.fastbidsheet')
    )
    
    if is_image:
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            
            data_url = f"data:{mime_type};base64,{base64.b64encode(content).decode('utf-8')}"
            
            return ToolResult(
                success=True,
                output="Image read successfully",
                title=title,
                metadata={
                    "preview": "Image read successfully",
                    "truncated": False
                },
                attachments=[{
                    "id": Identifier.ascending("part"),
                    "sessionID": ctx.session_id,
                    "messageID": ctx.message_id,
                    "type": "file",
                    "mime": mime_type,
                    "url": data_url
                }]
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to read image: {str(e)}",
                title=title
            )
    
    # Handle PDFs
    if mime_type == 'application/pdf':
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            
            data_url = f"data:{mime_type};base64,{base64.b64encode(content).decode('utf-8')}"
            
            return ToolResult(
                success=True,
                output="PDF read successfully",
                title=title,
                metadata={
                    "preview": "PDF read successfully",
                    "truncated": False
                },
                attachments=[{
                    "id": Identifier.ascending("part"),
                    "sessionID": ctx.session_id,
                    "messageID": ctx.message_id,
                    "type": "file",
                    "mime": mime_type,
                    "url": data_url
                }]
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to read PDF: {str(e)}",
                title=title
            )
    
    # Check if binary
    if is_binary_file(filepath):
        return ToolResult(
            success=False,
            error=f"Cannot read binary file: {filepath}",
            title=title
        )
    
    # Read text file
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.read().split('\n')
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Failed to read file: {str(e)}",
            title=title
        )
    
    # Apply offset and limit
    read_limit = limit if limit is not None else DEFAULT_READ_LIMIT
    read_offset = offset if offset is not None else 0
    
    # Read lines with byte limit
    raw_lines: List[str] = []
    total_bytes = 0
    truncated_by_bytes = False
    
    end_line = min(len(all_lines), read_offset + read_limit)
    
    for i in range(read_offset, end_line):
        line = all_lines[i]
        
        # Truncate long lines
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + "..."
        
        # Check byte limit
        line_bytes = len(line.encode('utf-8')) + (1 if raw_lines else 0)
        if total_bytes + line_bytes > MAX_BYTES:
            truncated_by_bytes = True
            break
        
        raw_lines.append(line)
        total_bytes += line_bytes
    
    # Format with line numbers (cat -n format)
    content_lines = [
        f"{str(i + read_offset + 1).zfill(5)}| {line}"
        for i, line in enumerate(raw_lines)
    ]
    
    # Build output
    total_lines = len(all_lines)
    last_read_line = read_offset + len(raw_lines)
    has_more_lines = total_lines > last_read_line
    truncated = has_more_lines or truncated_by_bytes
    
    output = "<file>\n"
    output += "\n".join(content_lines)
    
    if truncated_by_bytes:
        remaining = total_lines - last_read_line
        output += (
            f"\n\n(Output truncated at {MAX_BYTES} bytes — showed lines {read_offset + 1}-{last_read_line} of {total_lines}. "
            f"{remaining} lines remaining. To continue reading, call read with offset={last_read_line})"
        )
    elif has_more_lines:
        remaining = total_lines - last_read_line
        output += (
            f"\n\n(Showed lines {read_offset + 1}-{last_read_line} of {total_lines}. "
            f"{remaining} lines remaining. To continue reading, call read with offset={last_read_line})"
        )
    else:
        output += f"\n\n(End of file — total {total_lines} lines)"
    
    output += "\n</file>"
    
    # Preview is first 20 lines
    preview = "\n".join(raw_lines[:20])
    
    return ToolResult(
        success=True,
        output=output,
        title=title,
        truncated=truncated,
        metadata={
            "preview": preview,
            "truncated": truncated
        }
    )
