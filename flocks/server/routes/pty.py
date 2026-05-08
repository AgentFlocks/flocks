"""
PTY routes - API endpoints for PTY session management

Matches Flocks' ported src/server/routes/pty.ts
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from flocks.server.auth import apply_auth_for_request, clear_auth_context
from flocks.utils.log import Log
from flocks.pty.pty import Pty, PtyInfo, CreateInput, UpdateInput, PtyStatus


router = APIRouter()
log = Log.create(service="pty-routes")


# Response models

class PtyInfoResponse(BaseModel):
    """PTY session info response"""
    id: str = Field(..., description="PTY session ID")
    title: str = Field(..., description="Session title")
    command: str = Field(..., description="Shell command")
    args: list = Field(default_factory=list, description="Command arguments")
    cwd: str = Field(..., description="Working directory")
    status: str = Field(..., description="Session status")
    pid: int = Field(..., description="Process ID")


def _to_response(info: PtyInfo) -> PtyInfoResponse:
    """Convert PtyInfo to response model"""
    return PtyInfoResponse(
        id=info.id,
        title=info.title,
        command=info.command,
        args=info.args,
        cwd=info.cwd,
        status=info.status.value,
        pid=info.pid,
    )


# Routes - matching Flocks' PtyRoutes

@router.get(
    "",
    response_model=list[PtyInfoResponse],
    summary="List PTY sessions",
    description="Get a list of all active pseudo-terminal (PTY) sessions.",
)
async def list_sessions():
    """List all PTY sessions - operationId: pty.list"""
    sessions = Pty.list()
    return [_to_response(s) for s in sessions]


@router.post(
    "",
    response_model=PtyInfoResponse,
    summary="Create PTY session",
    description="Create a new pseudo-terminal (PTY) session for running shell commands.",
)
async def create_session(input_data: CreateInput):
    """Create a new PTY session - operationId: pty.create"""
    try:
        info = await Pty.create(input_data)
        return _to_response(info)
    except Exception as e:
        log.error("pty.create.error", {"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get(
    "/{pty_id}",
    response_model=PtyInfoResponse,
    summary="Get PTY session",
    description="Retrieve detailed information about a specific PTY session.",
)
async def get_session(pty_id: str):
    """Get PTY session info - operationId: pty.get"""
    info = Pty.get(pty_id)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    return _to_response(info)


@router.put(
    "/{pty_id}",
    response_model=PtyInfoResponse,
    summary="Update PTY session",
    description="Update properties of an existing PTY session.",
)
async def update_session(pty_id: str, input_data: UpdateInput):
    """Update PTY session - operationId: pty.update"""
    info = await Pty.update(pty_id, input_data)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    return _to_response(info)


@router.delete(
    "/{pty_id}",
    response_model=bool,
    summary="Remove PTY session",
    description="Remove and terminate a specific PTY session.",
)
async def remove_session(pty_id: str):
    """Remove PTY session - operationId: pty.remove"""
    if not Pty.get(pty_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    await Pty.remove(pty_id)
    return True


# WebSocket endpoint for real-time PTY connection
@router.websocket("/{pty_id}/connect")
async def connect_session(websocket: WebSocket, pty_id: str):
    """
    Connect to PTY session via WebSocket - operationId: pty.connect
    
    Establish a WebSocket connection to interact with a PTY session in real-time.
    """
    token = None
    try:
        _blocked, token, _user = await apply_auth_for_request(websocket)
    except HTTPException as exc:
        close_code = 4403 if exc.status_code == status.HTTP_403_FORBIDDEN else 4401
        await websocket.close(code=close_code, reason=str(exc.detail))
        return

    try:
        # Check only after authentication so unauthenticated callers cannot
        # probe for active PTY identifiers.
        if not Pty.get(pty_id):
            await websocket.close(code=4004, reason="Session not found")
            return

        await websocket.accept()

        # Connect to PTY
        handlers = await Pty.connect(pty_id, websocket)
        if not handlers:
            await websocket.close(code=4004, reason="Session not found")
            return
        
        # Handle messages
        while True:
            try:
                data = await websocket.receive_text()
                handlers["onMessage"](data)
            except WebSocketDisconnect:
                break
            except Exception as e:
                log.error("pty.ws.error", {"id": pty_id, "error": str(e)})
                break
        
        # Cleanup
        handlers["onClose"]()
        
    except Exception as e:
        log.error("pty.ws.connect.error", {"id": pty_id, "error": str(e)})
    finally:
        if token is not None:
            clear_auth_context(token)
