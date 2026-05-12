"""
LSP Tool - Language Server Protocol operations

Provides LSP operations for code intelligence:
- Go to definition
- Find references
- Hover information
- Document/workspace symbols
- Call hierarchy
"""

import os
import json
from typing import Optional, List, Dict, Any

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.project.instance import Instance
from flocks.utils.log import Log


log = Log.create(service="tool.lsp")


# Supported LSP operations
LSP_OPERATIONS = [
    "goToDefinition",
    "findReferences",
    "hover",
    "documentSymbol",
    "workspaceSymbol",
    "goToImplementation",
    "prepareCallHierarchy",
    "incomingCalls",
    "outgoingCalls",
]


DESCRIPTION = """Perform LSP (Language Server Protocol) operations for code intelligence.

Supported operations:
- goToDefinition: Jump to where a symbol is defined
- findReferences: Find all usages of a symbol
- hover: Get type/documentation info for a symbol
- documentSymbol: List all symbols in a file
- workspaceSymbol: Search symbols across workspace
- goToImplementation: Find implementations of an interface
- prepareCallHierarchy: Get call hierarchy item at position
- incomingCalls: Find callers of a function
- outgoingCalls: Find functions called by a function

Parameters:
- operation: The LSP operation to perform
- filePath: Path to the file
- line: Line number (1-based)
- character: Character offset (1-based)"""


@ToolRegistry.register_function(
    name="lsp",
    description=DESCRIPTION,
    category=ToolCategory.CODE,
    native=False,
    parameters=[
        ToolParameter(
            name="operation",
            type=ParameterType.STRING,
            description="The LSP operation to perform",
            required=True,
            enum=LSP_OPERATIONS
        ),
        ToolParameter(
            name="filePath",
            type=ParameterType.STRING,
            description="The absolute or relative path to the file",
            required=True
        ),
        ToolParameter(
            name="line",
            type=ParameterType.INTEGER,
            description="The line number (1-based, as shown in editors)",
            required=True
        ),
        ToolParameter(
            name="character",
            type=ParameterType.INTEGER,
            description="The character offset (1-based, as shown in editors)",
            required=True
        ),
    ]
)
async def lsp_tool(
    ctx: ToolContext,
    operation: str,
    filePath: str,
    line: int,
    character: int,
) -> ToolResult:
    """
    Perform an LSP operation
    
    Args:
        ctx: Tool context
        operation: LSP operation to perform
        filePath: Target file path
        line: Line number (1-based)
        character: Character offset (1-based)
        
    Returns:
        ToolResult with LSP results
    """
    # Validate operation
    if operation not in LSP_OPERATIONS:
        return ToolResult(
            success=False,
            error=f"Invalid operation: {operation}. Supported: {', '.join(LSP_OPERATIONS)}"
        )
    
    # Resolve path
    base_dir = Instance.get_directory() or os.getcwd()
    filepath = filePath if os.path.isabs(filePath) else os.path.join(base_dir, filePath)
    
    # Check file exists
    if not os.path.exists(filepath):
        return ToolResult(
            success=False,
            error=f"File not found: {filepath}"
        )
    
    # Request permission
    await ctx.ask(
        permission="lsp",
        patterns=["*"],
        always=["*"],
        metadata={}
    )
    
    # Get relative path for title
    worktree = Instance.get_worktree() or os.getcwd()
    try:
        rel_path = os.path.relpath(filepath, worktree)
    except ValueError:
        rel_path = filepath
    
    title = f"{operation} {rel_path}:{line}:{character}"
    
    # Convert to 0-based indices for LSP
    position = {
        "line": line - 1,
        "character": character - 1
    }
    
    try:
        # Import LSP module
        from flocks.lsp import LSP
        
        # Check if LSP is available for this file
        has_client = await LSP.has_clients(filepath)
        if not has_client:
            return ToolResult(
                success=False,
                error="No LSP server available for this file type."
            )
        
        # Touch file to ensure LSP has it open
        await LSP.touch_file(filepath, sync=True)
        
        # Execute operation
        result: List[Any] = []
        
        if operation == "goToDefinition":
            result = await LSP.definition({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "findReferences":
            result = await LSP.references({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "hover":
            result = await LSP.hover({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "documentSymbol":
            uri = f"file://{filepath}"
            result = await LSP.document_symbol(uri)
        elif operation == "workspaceSymbol":
            result = await LSP.workspace_symbol("")
        elif operation == "goToImplementation":
            result = await LSP.implementation({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "prepareCallHierarchy":
            result = await LSP.prepare_call_hierarchy({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "incomingCalls":
            result = await LSP.incoming_calls({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        elif operation == "outgoingCalls":
            result = await LSP.outgoing_calls({
                "file": filepath,
                "line": position["line"],
                "character": position["character"]
            })
        
        # Format output
        if not result:
            output = f"No results found for {operation}"
        else:
            output = json.dumps(result, indent=2)
        
        return ToolResult(
            success=True,
            output=output,
            title=title,
            metadata={"result": result}
        )
        
    except ImportError:
        # LSP module not available, return placeholder
        return ToolResult(
            success=False,
            error="LSP module not initialized. Start the LSP subsystem first.",
            title=title
        )
    except Exception as e:
        log.error("lsp.error", {"operation": operation, "error": str(e)})
        return ToolResult(
            success=False,
            error=f"LSP operation failed: {str(e)}",
            title=title
        )
