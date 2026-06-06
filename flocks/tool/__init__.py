"""
Flocks Tool System

Provides a comprehensive tool system compatible with Flocks's TypeScript implementation.

Core Tools (P0):
- read: Read file contents (text, images, PDFs)
- write: Write files with diff generation
- edit: Edit files with string replacement
- bash: Execute shell commands
- grep: Search file contents using regex
- glob: Find files by pattern

P1 Tools:
- webfetch: Fetch web content
- todo: TODO list management
- question: User interaction

P2 Tools:
- task: Subagent execution
- lsp: LSP operations
- skill: Load skills

P3 Tools:
- websearch: Web search
- apply_patch: Patch application

Usage:
    from flocks.tool import ToolRegistry, ToolContext, ToolResult

    # Initialize registry with built-in tools
    ToolRegistry.init()

    # Execute a tool
    ctx = ToolContext(session_id="...", message_id="...")
    result = await ToolRegistry.execute(tool_name="read", ctx=ctx, filePath="/path/to/file")

    # List available tools
    tools = ToolRegistry.list_tools()
"""

from flocks.tool.registry import (
    ParameterType,
    PermissionRequest,
    Tool,
    ToolCategory,
    ToolContext,
    ToolHandler,
    ToolInfo,
    ToolParameter,
    ToolRegistry,
    ToolResult,
    ToolSchema,
)

__all__ = [
    "ToolRegistry",
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolInfo",
    "ToolSchema",
    "ToolParameter",
    "PermissionRequest",
    "ToolCategory",
    "ParameterType",
    "ToolHandler",
]
