"""
PTY routes - API endpoints for PTY session management

Matches Flocks' ported src/server/routes/pty.ts
"""

import os

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from flocks.server.auth import apply_auth_for_request, clear_auth_context, require_admin
from flocks.hooks.execution import execution_context_scope, execution_lifecycle_scope
from flocks.identity import reset_current_subject, set_current_subject
from flocks.utils.log import Log
from flocks.pty.pty import Pty, PtyInfo, PtyLimitExceeded, CreateInput, UpdateInput, PtyStatus
from flocks.updater.deploy import declared_managed


router = APIRouter()


def interactive_pty_allowed() -> bool:
    """Whether a managed install offers interactive terminals (``FLOCKS_PTY_INTERACTIVE``)."""
    return os.environ.get("FLOCKS_PTY_INTERACTIVE", "").strip().lower() in {"1", "true", "yes", "allow"}


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
async def list_sessions(_admin: object = Depends(require_admin)):
    """List all PTY sessions - operationId: pty.list"""
    sessions = Pty.list()
    return [_to_response(s) for s in sessions]


@router.post(
    "",
    response_model=PtyInfoResponse,
    summary="Create PTY session",
    description="Create a new pseudo-terminal (PTY) session for running shell commands.",
)
async def create_session(input_data: CreateInput, _admin: object = Depends(require_admin)):
    """Create a new PTY session - operationId: pty.create"""
    try:
        info = await Pty.create(input_data)
        return _to_response(info)
    except PtyLimitExceeded as e:
        # Not a bad request: the caller has to close a session (or the operator
        # raise FLOCKS_PTY_MAX_SESSIONS) before this can succeed.
        log.warn("pty.create.limit", {"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e)
        )
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
async def get_session(pty_id: str, _admin: object = Depends(require_admin)):
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
async def update_session(pty_id: str, input_data: UpdateInput, _admin: object = Depends(require_admin)):
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
async def remove_session(pty_id: str, _admin: object = Depends(require_admin)):
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
        # WebSocket lifetimes outlive the ingress hook that authenticated the
        # handshake. Keep only neutral, opaque execution context alive for the
        # connection so extensions can restore their own verified bindings.
        with execution_lifecycle_scope():
            try:
                _blocked, token, _user = await apply_auth_for_request(websocket)
                # PTYs are process-wide and have no per-user owner. Match the
                # administrator boundary of create/update/delete before lookup
                # or acceptance; login alone must not grant shell access.
                require_admin(websocket)
            except HTTPException as exc:
                close_code = (
                    4403 if exc.status_code == status.HTTP_403_FORBIDDEN else 4401
                )
                await websocket.close(code=close_code, reason=str(exc.detail))
                return
            except Exception as exc:
                log.error("pty.ws.auth.error", {"error": str(exc)})
                await websocket.close(code=1011, reason="Authentication unavailable")
                return

            subject = getattr(websocket.state, "subject", None)
            subject_token = set_current_subject(subject) if subject is not None else None
            handlers = None
            try:
                extension_context = getattr(websocket.state, "extension_context", None)
                with execution_context_scope(extension_context):
                    # Check only after authentication so unauthenticated callers cannot
                    # probe for active PTY identifiers.
                    if not Pty.get(pty_id):
                        await websocket.close(code=4004, reason="Session not found")
                        return

                    # A managed install's tool policy cannot follow an interactive
                    # terminal keystroke by keystroke: it is off there unless the
                    # deployment turns it on, and then every input goes through the
                    # tool lifecycle as ``pty.input`` -- checked and audited like
                    # any command.  Elsewhere the administrator's terminal stays as
                    # it is (authenticated above, not an execution action).
                    managed = declared_managed()
                    if managed and not interactive_pty_allowed():
                        await websocket.close(
                            code=4403,
                            reason="interactive terminals are off on a managed install",
                        )
                        return

                    await websocket.accept()

                    handlers = await Pty.connect(
                        pty_id,
                        websocket,
                        use_action_lifecycle=managed,
                    )
                    if not handlers:
                        await websocket.close(code=4004, reason="Session not found")
                        return

                    while True:
                        try:
                            data = await websocket.receive_text()
                            await handlers["onMessage"](data)
                        except WebSocketDisconnect:
                            break
                        except Exception as exc:
                            log.error("pty.ws.error", {"id": pty_id, "error": str(exc)})
                            break
            finally:
                if handlers is not None:
                    handlers["onClose"]()
                if subject_token is not None:
                    reset_current_subject(subject_token)
    except Exception as e:
        log.error("pty.ws.connect.error", {"id": pty_id, "error": str(e)})
    finally:
        if token is not None:
            clear_auth_context(token)
