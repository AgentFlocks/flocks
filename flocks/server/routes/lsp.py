"""
LSP API routes

Provides REST API endpoints for Language Server Protocol features.
Implements server-side LSP routing functionality.
"""

from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel, Field, ConfigDict

from flocks.server.auth import require_admin
from flocks.utils.log import Log
from flocks import lsp


log = Log.create(service="routes.lsp")
router = APIRouter(tags=["lsp"])


# Request/Response models
class Position(BaseModel):
    """Text position"""
    line: int = Field(..., description="Line number (0-based)")
    character: int = Field(..., description="Character offset (0-based)")


class Range(BaseModel):
    """Text range"""
    start: Position
    end: Position


class Location(BaseModel):
    """Source location"""
    uri: str = Field(..., description="File URI")
    range: Range


class DiagnosticResponse(BaseModel):
    """Diagnostic information"""
    range: Range
    message: str
    severity: int = Field(1, description="1=Error, 2=Warning, 3=Info, 4=Hint")
    source: Optional[str] = None
    code: Optional[str] = None


class SymbolResponse(BaseModel):
    """Symbol information"""
    name: str
    kind: int
    location: Location


class DocumentSymbolResponse(BaseModel):
    """Document symbol"""
    model_config = ConfigDict(populate_by_name=True)
    
    name: str
    kind: int
    range: Range
    selection_range: Range = Field(..., alias="selectionRange")
    detail: Optional[str] = None


class HoverResponse(BaseModel):
    """Hover information"""
    contents: Any  # Can be string or MarkupContent
    range: Optional[Range] = None


class LSPStatusResponse(BaseModel):
    """LSP server status"""
    id: str
    name: str
    root: str
    status: str


class InitializeRequest(BaseModel):
    """LSP initialization request"""
    root: Optional[str] = Field(None, description="Workspace root path")


class TextDocumentPositionRequest(BaseModel):
    """Request with text document and position"""
    file: str = Field(..., description="File path")
    line: int = Field(..., ge=0, description="Line number (0-based)")
    character: int = Field(..., ge=0, description="Character offset (0-based)")


class WorkspaceSymbolRequest(BaseModel):
    """Workspace symbol search request"""
    query: str = Field(..., description="Search query")


class TouchFileRequest(BaseModel):
    """Touch file request"""
    file: str = Field(..., description="File path")
    wait_for_diagnostics: bool = Field(False, description="Wait for diagnostics")


# Routes
@router.get("", response_model=List[LSPStatusResponse], summary="Get LSP status", description="Get LSP server status", operation_id="lsp.status")
async def get_status():
    """
    Get status of all active LSP servers.
    
    This endpoint is available at /lsp (not /lsp/status).
    Returns list of connected LSP servers with their status.
    """
    try:
        statuses = await lsp.status()
        return statuses
    except Exception as e:
        log.error("routes.lsp.status.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/initialize")
async def initialize(request: InitializeRequest = Body(default=None)):
    """
    Initialize LSP subsystem
    
    Starts up LSP servers based on configuration.
    """
    try:
        await lsp.init()
        return {"status": "initialized"}
    except Exception as e:
        log.error("routes.lsp.initialize.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/shutdown")
async def shutdown(_admin: object = Depends(require_admin)):
    """
    Shutdown LSP subsystem
    
    Stops all LSP servers.
    """
    try:
        await lsp.shutdown()
        return {"status": "shutdown"}
    except Exception as e:
        log.error("routes.lsp.shutdown.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/touch")
async def touch_file(request: TouchFileRequest):
    """
    Notify LSP that a file was opened/changed
    
    This triggers diagnostics computation for the file.
    """
    try:
        await lsp.touch_file(request.file, request.wait_for_diagnostics)
        return {"status": "ok"}
    except Exception as e:
        log.error("routes.lsp.touch.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/diagnostics")
async def get_diagnostics(file: Optional[str] = Query(None, description="Filter by file path")):
    """
    Get diagnostics (errors, warnings) from LSP servers
    
    Returns all diagnostics or filtered by file path.
    """
    try:
        all_diagnostics = await lsp.diagnostics()
        
        if file:
            import os
            file = os.path.normpath(os.path.abspath(file))
            diags = all_diagnostics.get(file, [])
            return {
                file: [
                    {
                        "range": {
                            "start": {"line": d.range.start_line, "character": d.range.start_character},
                            "end": {"line": d.range.end_line, "character": d.range.end_character},
                        },
                        "message": d.message,
                        "severity": d.severity,
                        "source": d.source,
                        "code": d.code,
                    }
                    for d in diags
                ]
            }
        
        # Return all diagnostics
        result = {}
        for path, diags in all_diagnostics.items():
            result[path] = [
                {
                    "range": {
                        "start": {"line": d.range.start_line, "character": d.range.start_character},
                        "end": {"line": d.range.end_line, "character": d.range.end_character},
                    },
                    "message": d.message,
                    "severity": d.severity,
                    "source": d.source,
                    "code": d.code,
                }
                for d in diags
            ]
        
        return result
        
    except Exception as e:
        log.error("routes.lsp.diagnostics.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hover")
async def hover(request: TextDocumentPositionRequest):
    """
    Get hover information at position
    
    Returns type information, documentation, etc.
    """
    try:
        result = await lsp.hover(request.file, request.line, request.character)
        return result or {}
    except Exception as e:
        log.error("routes.lsp.hover.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/definition")
async def definition(request: TextDocumentPositionRequest):
    """
    Go to definition
    
    Returns locations where the symbol is defined.
    """
    try:
        locations = await lsp.definition(request.file, request.line, request.character)
        return {"locations": locations}
    except Exception as e:
        log.error("routes.lsp.definition.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/references")
async def references(request: TextDocumentPositionRequest):
    """
    Find all references
    
    Returns all locations where the symbol is referenced.
    """
    try:
        locations = await lsp.references(request.file, request.line, request.character)
        return {"locations": locations}
    except Exception as e:
        log.error("routes.lsp.references.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/symbol/workspace")
async def workspace_symbol(request: WorkspaceSymbolRequest):
    """
    Search for symbols in workspace
    
    Returns matching symbols across all files.
    """
    try:
        symbols = await lsp.workspace_symbol(request.query)
        return {"symbols": symbols}
    except Exception as e:
        log.error("routes.lsp.workspace_symbol.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/symbol/document")
async def document_symbol(file: str = Query(..., description="File path")):
    """
    Get symbols in document
    
    Returns all symbols (functions, classes, etc.) in the file.
    """
    try:
        symbols = await lsp.document_symbol(file)
        return {"symbols": symbols}
    except Exception as e:
        log.error("routes.lsp.document_symbol.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/servers")
async def list_servers():
    """
    List available LSP servers
    
    Returns all registered LSP server configurations.
    """
    try:
        from flocks.lsp.server import SERVERS
        
        servers = []
        for server_id, server in SERVERS.items():
            servers.append({
                "id": server_id,
                "extensions": server.extensions,
            })
        
        return {"servers": servers}
    except Exception as e:
        log.error("routes.lsp.servers.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/languages")
async def list_languages():
    """
    List language ID mappings
    
    Returns mapping of file extensions to language IDs.
    """
    return {"languages": lsp.LANGUAGE_EXTENSIONS}
