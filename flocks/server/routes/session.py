"""
Session management routes

Compatible with TypeScript API.
Uses camelCase field names for TypeScript compatibility.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import List, Optional, Any, Dict, Literal, Union, Tuple
from fastapi import APIRouter, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ConfigDict

from flocks.session.session import Session, SessionInfo as SessionModel
from flocks.utils.log import Log
from flocks.utils.json_repair import parse_json_robust, repair_truncated_json
from flocks.server.auth import require_user

router = APIRouter()
log = Log.create(service="session-routes")

# Default agent name constant
DEFAULT_AGENT = "rex"

# Import monitor for metrics endpoint
from flocks.utils.monitor import get_monitor


# =============================================================================
# Request/Response Models - API Compatible (camelCase)
# =============================================================================

class PermissionRule(BaseModel):
    """Permission rule for API compatibility"""
    permission: str = Field(..., description="Permission name (tool name)")
    action: str = Field("allow", description="Action: allow or deny")
    pattern: str = Field("*", description="Pattern to match")


class SessionCreateRequest(BaseModel):
    """
    Request to create a new session
    
    Schema follows standard Session.create format (matches Flocks):
    - parentID: optional parent session ID
    - title: optional session title
    - permission: optional permission ruleset
    
    Note: Model is not stored at session level (matches Flocks).
    Model is selected per-message based on priority:
    request.model > agent.model > lastModel > defaultModel
    """
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    
    parentID: Optional[str] = Field(None, alias="parent_id", description="Parent session ID")
    title: Optional[str] = Field(None, description="Session title")
    permission: Optional[List[PermissionRule]] = Field(None, description="Permission rules")
    category: Optional[str] = Field(None, description="Session category (e.g. 'user', 'workflow')")


class FileDiff(BaseModel):
    """File diff info for API compatibility"""
    model_config = ConfigDict(populate_by_name=True)
    
    file: str = Field(..., description="File path")
    before: str = Field("", description="Content before changes")
    after: str = Field("", description="Content after changes")
    additions: int = Field(0, description="Lines added")
    deletions: int = Field(0, description="Lines deleted")


class SessionTime(BaseModel):
    """Session time information for API compatibility"""
    model_config = ConfigDict(populate_by_name=True)
    
    created: int = Field(..., description="Creation timestamp (ms)")
    updated: int = Field(..., description="Last update timestamp (ms)")
    compacting: Optional[int] = Field(None, description="Compaction timestamp (ms)")
    archived: Optional[int] = Field(None, description="Archive timestamp (ms)")


class SessionResponse(BaseModel):
    """
    Session response - Flocks compatible
    
    Matches Flocks Session.Info format exactly.
    No agent/model/provider at top level - these come from messages.
    """
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(..., description="Session ID")
    slug: str = Field("", description="Session slug")
    projectID: str = Field(..., description="Project ID")
    directory: str = Field(..., description="Working directory")
    parentID: Optional[str] = Field(None, description="Parent session ID")
    summary: Optional[Dict[str, Any]] = Field(None, description="Session summary with diffs")
    share: Optional[Dict[str, Any]] = Field(None, description="Share information")
    title: str = Field(..., description="Session title")
    version: str = Field("1.0.0", description="Session version")
    time: SessionTime = Field(..., description="Session timestamps")
    permission: Optional[List[Dict[str, Any]]] = Field(None, description="Permission rules")
    revert: Optional[Dict[str, Any]] = Field(None, description="Revert state")
    category: str = Field("user", description="Session category: user or task")


def _session_to_response(session: SessionModel) -> SessionResponse:
    """
    Convert SessionModel to SessionResponse
    
    Note: agent/model/provider are NOT included at session level.
    They are retrieved from the latest user message in the session.
    """
    return SessionResponse(
        id=session.id,
        slug=session.slug,
        projectID=session.project_id,
        directory=session.directory,
        title=session.title,
        version=session.version,
        parentID=session.parent_id,
        time=SessionTime(
            created=session.time.created,
            updated=session.time.updated,
            compacting=session.time.compacting,
            archived=session.time.archived,
        ),
        summary=session.summary.model_dump() if session.summary else None,
        share=session.share.model_dump() if session.share else None,
        revert=session.revert.model_dump(by_alias=True) if session.revert else None,
        permission=[p.model_dump() for p in session.permission] if session.permission else None,
        category=session.category,
    )


def _is_hidden_from_session_manager(session: SessionModel) -> bool:
    """Return whether a session should be excluded from manager listings."""
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    return bool(metadata.get("hideFromSessionManager"))


# =============================================================================
# Session CRUD Routes
# =============================================================================

@router.get(
    "/status",
    response_model=Dict[str, Any],
    summary="Get session status",
    description="Retrieve the current status of all sessions (idle, busy, retry)",
)
async def get_session_status() -> Dict[str, Any]:
    """
    Get session status for all sessions
    
    Returns a dictionary mapping session IDs to their status:
    - idle: Session is not processing
    - busy: Session is currently processing
    - retry: Session is retrying after an error
    
    Flocks compatible endpoint.
    """
    from flocks.session.core.status import SessionStatus
    from flocks.session.core.turn_state import get_turn_state, get_context_state
    
    statuses = SessionStatus.list()
    return {
        session_id: {
            **status.model_dump(),
            "turn": get_turn_state(session_id).model_dump(by_alias=True),
            "context": get_context_state(session_id).model_dump(by_alias=True),
        }
        for session_id, status in statuses.items()
    }


@router.get(
    "",
    response_model=List[SessionResponse],
    summary="List sessions",
    description="Get a list of all sessions, sorted by most recently updated",
)
async def list_sessions(
    directory: Optional[str] = Query(None, description="Filter by project directory"),
    roots: Optional[bool] = Query(None, description="Only return root sessions (no parentID)"),
    start: Optional[int] = Query(None, description="Filter sessions updated on or after this timestamp"),
    search: Optional[str] = Query(None, description="Filter by title (case-insensitive)"),
    limit: Optional[int] = Query(None, ge=1, description="Maximum sessions to return"),
    category: Optional[str] = Query(None, description="Filter by category: user or task"),
) -> List[SessionResponse]:
    """List all sessions with optional filters"""
    all_sessions = await Session.list_all()
    
    filtered = []
    term = search.lower() if search else None
    
    for session in all_sessions:
        if _is_hidden_from_session_manager(session):
            continue
        if directory is not None and session.directory != directory:
            continue
        if roots and session.parent_id:
            continue
        if start is not None and session.time.updated < start:
            continue
        if term is not None and term not in session.title.lower():
            continue
        if category is not None:
            if session.category != category:
                continue
        elif session.category == "test":
            # exclude test sessions from the default listing
            continue
        
        filtered.append(session)
        
        if limit is not None and len(filtered) >= limit:
            break
    
    return [_session_to_response(s) for s in filtered]


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Create session",
    description="Create a new session",
)
async def create_session(request: Optional[SessionCreateRequest] = None) -> SessionResponse:
    """Create a new session"""
    import os
    
    if request is None:
        request = SessionCreateRequest()
    
    # Use Instance context if available, otherwise use cwd
    from flocks.project.instance import Instance
    try:
        directory = Instance.directory
        project_id = Instance.project.id if hasattr(Instance, 'project') else "default"
    except Exception:
        directory = os.getcwd()
        project_id = "default"
    
    # Trigger command:new hook if creating from parent (like /new command)
    if request.parentID:
        try:
            from flocks.hooks import trigger_hook, create_command_event
            from flocks.config import Config
            
            config = await Config.get()
            
            # Create hook event for the parent session
            event = create_command_event(
                action="new",
                session_id=request.parentID,
                context={
                    "previous_session_id": request.parentID,
                    "project_id": project_id,
                    "workspace_dir": directory,
                },
            )
            
            # Trigger hook (non-blocking, errors are caught)
            await trigger_hook(event)
            
        except Exception as e:
            # Hook failure should not block session creation
            log.warn("session.create.hook_failed", {
                "error": str(e),
                "parent_id": request.parentID,
            })
    
    # Convert permission rules
    permission = None
    if request.permission:
        from flocks.session.session import PermissionRule as SessionPermRule
        permission = [
            SessionPermRule(
                permission=p.permission,
                action=p.action,
                pattern=p.pattern,
            )
            for p in request.permission
        ]
    
    session = await Session.create(
        project_id=project_id,
        directory=directory,
        title=request.title,
        parent_id=request.parentID,
        permission=permission,
        **({"category": request.category} if request.category else {}),
    )

    log.info("session.created", {"session_id": session.id})
    return _session_to_response(session)




@router.get(
    "/{sessionID}",
    response_model=SessionResponse,
    summary="Get session",
    description="Get session by ID",
)
async def get_session(sessionID: str) -> SessionResponse:
    """Get session by ID"""
    session = await Session.get_by_id(sessionID)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    return _session_to_response(session)


@router.get(
    "/{sessionID}/children",
    response_model=List[SessionResponse],
    summary="Get session children",
    description="Get all child sessions forked from the specified parent",
)
async def get_session_children(sessionID: str) -> List[SessionResponse]:
    """Get child sessions"""
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    children = await Session.children(session.project_id, sessionID)
    return [_session_to_response(s) for s in children]


class TodoInfo(BaseModel):
    """Todo item info for API compatibility"""
    model_config = ConfigDict(populate_by_name=True)
    
    id: str = Field(..., description="Todo ID")
    content: str = Field(..., description="Task description")
    status: str = Field(..., description="Status: pending, in_progress, completed, cancelled")
    priority: str = Field("medium", description="Priority: high, medium, low")


@router.get(
    "/{sessionID}/todo",
    response_model=List[TodoInfo],
    summary="Get session todos",
    description="Get the todo list for a session",
)
async def get_session_todos(sessionID: str) -> List[TodoInfo]:
    """Get session todos"""
    from flocks.storage.storage import Storage
    
    try:
        todos = await Storage.read(["todo", sessionID])
        if todos is None:
            return []
        return [TodoInfo(**todo) for todo in todos]
    except Exception as e:
        log.warn("session.todo.read_error", {"sessionID": sessionID, "error": str(e)})
        return []


@router.post(
    "/{sessionID}/todo",
    response_model=List[TodoInfo],
    summary="Update session todos",
    description="Update the todo list for a session",
)
async def update_session_todos(sessionID: str, todos: List[TodoInfo]) -> List[TodoInfo]:
    """Update session todos"""
    from flocks.storage.storage import Storage
    from flocks.server.routes.event import publish_event
    
    try:
        await Storage.write(["todo", sessionID], [t.model_dump() for t in todos])
        
        await publish_event("todo.updated", {
            "sessionID": sessionID,
            "todos": [t.model_dump() for t in todos],
        })
        
        return todos
    except Exception as e:
        log.error("session.todo.write_error", {"sessionID": sessionID, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{sessionID}",
    status_code=status.HTTP_200_OK,
    summary="Delete session",
    description="Delete session by ID",
)
async def delete_session(sessionID: str) -> bool:
    """Delete session by ID (returns true)"""
    session = await Session.get_by_id(sessionID)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    await Session.delete(session.project_id, sessionID)
    log.info("session.deleted", {"session_id": sessionID})
    return True


class SessionUpdateRequest(BaseModel):
    """Request to update session"""
    model_config = ConfigDict(populate_by_name=True)
    
    title: Optional[str] = Field(None, description="New title")
    time: Optional[Dict[str, Any]] = Field(None, description="Time updates (archived)")


@router.patch(
    "/{sessionID}",
    response_model=SessionResponse,
    summary="Update session",
    description="Update session properties",
)
async def update_session(
    sessionID: str,
    request: SessionUpdateRequest,
) -> SessionResponse:
    """Update session"""
    existing = await Session.get_by_id(sessionID)
    
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    updates = {}
    if request.title is not None:
        updates["title"] = request.title
    if request.time and request.time.get("archived") is not None:
        updates["archived"] = request.time["archived"]
    
    session = await Session.update(
        project_id=existing.project_id,
        session_id=sessionID,
        **updates,
    )
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    log.info("session.updated", {"session_id": sessionID})
    return _session_to_response(session)


# =============================================================================
# Session Actions
# =============================================================================

@router.post(
    "/{sessionID}/abort",
    summary="Abort session",
    description="Abort an active session and stop any ongoing processing",
)
async def abort_session(sessionID: str) -> bool:
    """Abort session processing.

    Aborts both the SessionLoop (sets abort_event so the next step check
    stops the loop) and the SessionRunner (stops the current LLM stream).
    Also auto-rejects any pending Question tool requests so the question
    handler polling loop unblocks immediately instead of timing out.

    Cascades abort to all child sub-agent sessions (synchronous subtasks
    and background tasks) so they stop together with the parent.
    """
    from flocks.session.runner import SessionRunner
    from flocks.session.session_loop import SessionLoop
    from flocks.server.routes.question import reject_session_questions

    # Abort the loop-level context (propagates to runner via shared abort_event)
    loop_aborted = SessionLoop.abort(sessionID)

    # Also cancel through the runner's own path (sets status to idle)
    SessionRunner.cancel(sessionID)

    # Unblock any pending Question tool waiting for user input
    questions_rejected = await reject_session_questions(sessionID)

    # --- Cascade abort to child sub-agent sessions ---
    children_loops_aborted = SessionLoop.abort_children(sessionID)
    children_runners_cancelled = SessionRunner.cancel_children(sessionID)

    # Cancel background sub-agent tasks spawned by this session
    bg_cancelled = 0
    try:
        from flocks.task.background import get_background_manager
        bg_cancelled = get_background_manager().cancel_by_parent_session_id(sessionID)
    except Exception as exc:
        log.warn("session.abort.bg_cancel_error", {"error": str(exc)})

    log.info("session.aborted", {
        "session_id": sessionID,
        "loop_aborted": loop_aborted,
        "questions_rejected": questions_rejected,
        "children_loops_aborted": children_loops_aborted,
        "children_runners_cancelled": children_runners_cancelled,
        "bg_tasks_cancelled": bg_cancelled,
    })

    # Publish SSE event so frontend knows execution stopped
    try:
        from flocks.server.routes.event import publish_event
        await publish_event("session.updated", {
            "id": sessionID,
            "status": "idle",
        })
    except Exception as exc:
        log.warn("session.abort.event_error", {"error": str(exc)})

    return True


class ForkRequest(BaseModel):
    """Request to fork session"""
    messageID: Optional[str] = Field(None, description="Message ID to fork up to")


class InitRequest(BaseModel):
    """Request to initialize session"""
    model_config = ConfigDict(populate_by_name=True)
    
    modelID: str = Field(..., description="Model ID")
    providerID: str = Field(..., description="Provider ID")
    messageID: str = Field(..., description="Message ID")


@router.post(
    "/{sessionID}/init",
    summary="Initialize session",
    description="Analyze the current application and create an AGENTS.md file with project-specific agent configurations",
)
async def initialize_session(sessionID: str, request: InitRequest) -> bool:
    """Initialize session"""
    from flocks.session.runner import SessionRunner
    
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    # Execute INIT command
    await SessionRunner.command(
        session_id=sessionID,
        command="init",
        arguments="",
        message_id=request.messageID,
        model=f"{request.providerID}/{request.modelID}",
    )
    
    log.info("session.initialized", {"session_id": sessionID})
    return True


@router.post(
    "/{sessionID}/fork",
    response_model=SessionResponse,
    summary="Fork session",
    description="Create a new session by forking at a specific message point",
)
async def fork_session(sessionID: str, request: Optional[ForkRequest] = None) -> SessionResponse:
    """Fork session"""
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    message_id = request.messageID if request else None
    forked = await Session.fork(session.project_id, sessionID, message_id)
    
    log.info("session.forked", {"from": sessionID, "to": forked.id})
    return _session_to_response(forked)


@router.post(
    "/{sessionID}/share",
    response_model=SessionResponse,
    summary="Share session",
    description="Create a shareable link for the session",
)
async def share_session(sessionID: str) -> SessionResponse:
    """Share session"""
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    await Session.share(session.project_id, sessionID)
    updated = await Session.get_by_id(sessionID)
    log.info("session.shared", {"session_id": sessionID})
    return _session_to_response(updated)


@router.get(
    "/{sessionID}/diff",
    response_model=List[FileDiff],
    summary="Get session diff",
    description="Get file diffs for a session or specific message",
)
async def get_session_diff(
    sessionID: str,
    messageID: Optional[str] = Query(None, description="Message ID to get diff for"),
) -> List[FileDiff]:
    """Get session diff"""
    from flocks.storage.storage import Storage
    from flocks.session.lifecycle.summary import SessionSummary
    
    try:
        if messageID:
            # Get diff for specific message
            diffs = await SessionSummary.diff(session_id=sessionID, message_id=messageID)
        else:
            # Get overall session diff
            diffs = await Storage.read(["session_diff", sessionID])
        
        if diffs is None:
            return []
        return [FileDiff(**diff) for diff in diffs]
    except Exception as e:
        log.warn("session.diff.read_error", {"sessionID": sessionID, "error": str(e)})
        return []


@router.delete(
    "/{sessionID}/share",
    response_model=SessionResponse,
    summary="Unshare session",
    description="Remove the shareable link for the session",
)
async def unshare_session(sessionID: str) -> SessionResponse:
    """Unshare session"""
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    await Session.unshare(session.project_id, sessionID)
    updated = await Session.get_by_id(sessionID)
    log.info("session.unshared", {"session_id": sessionID})
    return _session_to_response(updated)


class SummarizeRequest(BaseModel):
    """Request to summarize session"""
    providerID: str = Field(..., description="Provider ID")
    modelID: str = Field(..., description="Model ID")
    auto: bool = Field(False, description="Auto compaction mode")


@router.post(
    "/{sessionID}/summarize",
    summary="Summarize session",
    description="Generate a summary using AI compaction",
)
async def summarize_session(sessionID: str, request: SummarizeRequest) -> bool:
    """Summarize session"""
    from flocks.project.bootstrap import instance_bootstrap
    from flocks.project.instance import Instance
    from flocks.server.routes.event import publish_event
    from flocks.session.message import Message, MessageRole
    
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    # Get all messages to find current agent from last user message
    # This matches Flocks logic in session.ts:520-528
    messages = await Message.list(sessionID)
    current_agent = DEFAULT_AGENT
    
    for msg in reversed(messages):
        if msg.role == MessageRole.USER:
            current_agent = msg.agent or DEFAULT_AGENT
            break
    
    async def _run_in_background():
        try:
            await Instance.provide(
                directory=session.directory,
                init=instance_bootstrap,
                fn=lambda: _run_session_compaction(
                    sessionID,
                    requested_agent=current_agent,
                    explicit_provider_id=request.providerID,
                    explicit_model_id=request.modelID,
                    auto=request.auto,
                    event_publish_callback=publish_event,
                ),
            )
        except Exception as e:
            log.error("session.summarize.error", {
                "session_id": sessionID,
                "error": str(e),
            })
            await publish_event("session.error", {
                "sessionID": sessionID,
                "error": {
                    "name": type(e).__name__,
                    "message": str(e),
                    "data": {"message": str(e)},
                },
            })

    import asyncio
    asyncio.create_task(_run_in_background())
    
    log.info("session.summarized", {"session_id": sessionID})
    return True


class RevertRequest(BaseModel):
    """Request to revert session"""
    messageID: str = Field(..., description="Message ID to revert to")
    partID: Optional[str] = Field(None, description="Part ID for partial revert")


@router.post(
    "/{sessionID}/revert",
    response_model=SessionResponse,
    summary="Revert session",
    description="Revert session to a specific message point",
)
async def revert_session(sessionID: str, request: RevertRequest) -> SessionResponse:
    """Revert session"""
    from flocks.session.lifecycle.revert import SessionRevert
    
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    updated = await SessionRevert.revert(
        session_id=sessionID,
        message_id=request.messageID,
        part_id=request.partID,
    )
    
    log.info("session.reverted", {"session_id": sessionID, "message_id": request.messageID})
    return _session_to_response(updated)


@router.post(
    "/{sessionID}/unrevert",
    response_model=SessionResponse,
    summary="Unrevert session",
    description="Restore previously reverted messages",
)
async def unrevert_session(sessionID: str) -> SessionResponse:
    """Unrevert session"""
    from flocks.session.lifecycle.revert import SessionRevert
    
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    updated = await SessionRevert.unrevert(session_id=sessionID)
    
    log.info("session.unreverted", {"session_id": sessionID})
    return _session_to_response(updated)


# =============================================================================
# Message Routes
# =============================================================================

class ModelInfo(BaseModel):
    """Model selection info for API compatibility"""
    providerID: str = Field(..., description="Provider ID")
    modelID: str = Field(..., description="Model ID")


class TextPartInput(BaseModel):
    """Text part input for API compatibility"""
    type: Literal["text"] = "text"
    id: Optional[str] = Field(None, description="Part ID")
    text: str = Field(..., description="Text content")


class FilePartInput(BaseModel):
    """File part input for API compatibility"""
    type: Literal["file"] = "file"
    id: Optional[str] = Field(None, description="Part ID")
    url: str = Field(..., description="File URL")
    mime: str = Field(..., description="MIME type")
    filename: Optional[str] = Field(None, description="File name")


class AgentPartInput(BaseModel):
    """Agent part input for API compatibility"""
    type: Literal["agent"] = "agent"
    id: Optional[str] = Field(None, description="Part ID")
    name: str = Field(..., description="Agent name")


class SubtaskPartInput(BaseModel):
    """Subtask part input for API compatibility"""
    type: Literal["subtask"] = "subtask"
    id: Optional[str] = Field(None, description="Part ID")
    agent: str = Field(..., description="Agent name")
    prompt: str = Field(..., description="Subtask prompt")
    description: Optional[str] = Field(None, description="Subtask description")


class PromptRequest(BaseModel):
    """
    Request to send a prompt/message
    
    Schema follows standard SessionPrompt.PromptInput format
    """
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    
    parts: List[Dict[str, Any]] = Field(default_factory=list, description="Message parts")
    model: Optional[ModelInfo] = Field(None, description="Model selection")
    messageID: Optional[str] = Field(None, description="Message ID")
    agent: Optional[str] = Field(None, description="Agent name")
    noReply: Optional[bool] = Field(None, description="Skip AI response")
    mockReply: Optional[str] = Field(None, description="Inject a mock assistant message after noReply user message")
    tools: Optional[Dict[str, bool]] = Field(None, description="Tool settings (deprecated)")
    system: Optional[str] = Field(None, description="System prompt override")
    variant: Optional[str] = Field(None, description="Model variant")


class UserMessageInfo(BaseModel):
    """
    User message info - Flocks TUI compatible format.
    
    Flocks expects:
    {
        "id": string,
        "sessionID": string,
        "role": "user",
        "time": { "created": number },
        "agent": string,
        "model": { "providerID": string, "modelID": string },
        // optional fields...
    }
    """
    id: str
    sessionID: str
    role: Literal["user"] = "user"
    time: Dict[str, Any]
    agent: str = DEFAULT_AGENT
    model: Dict[str, str]  # { "providerID": string, "modelID": string }
    summary: Optional[Dict[str, Any]] = None
    system: Optional[str] = None
    tools: Optional[Dict[str, bool]] = None
    variant: Optional[str] = None
    compacted: Optional[bool] = None


class AssistantMessageInfo(BaseModel):
    """
    Assistant message info - Flocks TUI compatible format.
    
    Flocks expects:
    {
        "id": string,
        "sessionID": string,
        "role": "assistant",
        "time": { "created": number, "completed"?: number },
        "parentID": string,
        "modelID": string,
        "providerID": string,
        "mode": string,
        "agent": string,
        "path": { "cwd": string, "root": string },
        "cost": number,
        "tokens": { "input": number, "output": number, ... },
        // optional fields...
    }
    """
    id: str
    sessionID: str
    role: Literal["assistant"] = "assistant"
    time: Dict[str, Any]
    parentID: Optional[str] = None
    modelID: str
    providerID: str
    mode: str = DEFAULT_AGENT
    agent: str = DEFAULT_AGENT
    path: Dict[str, str]  # { "cwd": string, "root": string }
    cost: float = 0.0
    tokens: Dict[str, Any] = Field(default_factory=lambda: {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0}
    })
    error: Optional[Dict[str, Any]] = None
    summary: Optional[bool] = None
    finish: Optional[str] = None
    compacted: Optional[bool] = None


# Union type for message info
MessageInfo = Union[UserMessageInfo, AssistantMessageInfo]


class MessagePartInfo(BaseModel):
    """Message part info for API compatibility"""
    id: str
    messageID: str
    sessionID: str
    type: str
    text: Optional[str] = None
    synthetic: Optional[bool] = None
    tool: Optional[str] = None
    state: Optional[Dict[str, Any]] = None
    callID: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class MessageWithParts(BaseModel):
    """Message with parts for API compatibility"""
    info: MessageInfo
    parts: List[MessagePartInfo] = []


class MessageEditRequest(BaseModel):
    """Request to edit message text."""

    text: str = Field(..., description="Updated raw text content")
    partID: Optional[str] = Field(None, description="Specific text part ID to edit")


@router.get(
    "/{sessionID}/message",
    response_model=List[MessageWithParts],
    summary="Get session messages",
    description="Get all messages in a session",
)
async def get_session_messages(
    sessionID: str,
    limit: Optional[int] = Query(None, ge=1, description="Maximum messages to return"),
) -> List[MessageWithParts]:
    """Get session messages"""
    from flocks.session.message import Message
    import os
    
    try:
        messages_with_parts = await Message.list_with_parts(sessionID, include_archived=True)
        if limit:
            messages_with_parts = messages_with_parts[-limit:]
        
        result = []
        cwd = os.getcwd()
        
        for msg_with_parts in messages_with_parts:
            msg = msg_with_parts.info
            
            # Create appropriate message info based on role
            if msg.role == "user":
                # Extract model from msg.model dict (UserMessageInfo has model as dict)
                model_dict = getattr(msg, 'model', None)
                if model_dict and isinstance(model_dict, dict):
                    model_info = model_dict
                else:
                    # Fallback: try to get from Agent.default_agent's model
                    try:
                        from flocks.agent.registry import Agent
                        default_agent = await Agent.default_agent()
                        agent_obj = await Agent.get(default_agent)
                        if agent_obj and hasattr(agent_obj, 'model') and agent_obj.model:
                            model_info = agent_obj.model
                        else:
                            model_info = {"providerID": "openai", "modelID": "gpt-4-turbo-preview"}
                    except Exception:
                        model_info = {"providerID": "openai", "modelID": "gpt-4-turbo-preview"}
                
                info = UserMessageInfo(
                    id=msg.id,
                    sessionID=msg.sessionID,
                    role="user",
                    time=msg.time,
                    agent=getattr(msg, 'agent', None) or DEFAULT_AGENT,
                    model=model_info,
                    compacted=getattr(msg, 'compacted', None),
                )
            else:
                # Convert tokens to dict if it's a TokenUsage object
                tokens_raw = getattr(msg, 'tokens', None)
                if tokens_raw is not None and hasattr(tokens_raw, 'model_dump'):
                    tokens_dict = tokens_raw.model_dump()
                elif isinstance(tokens_raw, dict):
                    tokens_dict = tokens_raw
                else:
                    tokens_dict = {
                        "input": 0,
                        "output": 0,
                        "reasoning": 0,
                        "cache": {"read": 0, "write": 0}
                    }
                
                # Convert path to dict if it's a MessagePath object
                path_raw = getattr(msg, 'path', None)
                if path_raw is not None and hasattr(path_raw, 'model_dump'):
                    path_dict = path_raw.model_dump()
                elif isinstance(path_raw, dict):
                    path_dict = path_raw
                else:
                    path_dict = {"cwd": cwd, "root": cwd}
                
                info = AssistantMessageInfo(
                    id=msg.id,
                    sessionID=msg.sessionID,
                    role="assistant",
                    time=msg.time,
                    parentID=getattr(msg, 'parentID', None),
                    modelID=getattr(msg, 'modelID', None) or "claude-sonnet-4-5-20250929",
                    providerID=getattr(msg, 'providerID', None) or "anthropic",
                    mode=getattr(msg, 'agent', None) or DEFAULT_AGENT,
                    agent=getattr(msg, 'agent', None) or DEFAULT_AGENT,
                    path=path_dict,
                    cost=getattr(msg, 'cost', 0.0) or 0.0,
                    tokens=tokens_dict,
                    error=getattr(msg, 'error', None),
                    finish=getattr(msg, 'finish', None),
                    compacted=getattr(msg, 'compacted', None),
                )
            
            parts = []
            for i, part in enumerate(msg_with_parts.parts):
                # Convert state to dict if it's a Pydantic model
                state_value = None
                if part.type == "tool":
                    raw_state = getattr(part, 'state', None)
                    if raw_state is not None:
                        if hasattr(raw_state, 'model_dump'):
                            state_value = raw_state.model_dump()
                        elif isinstance(raw_state, dict):
                            state_value = raw_state
                
                part_info = MessagePartInfo(
                    id=part.id if hasattr(part, 'id') else f"{msg.id}_part_{i}",
                    messageID=msg.id,
                    sessionID=sessionID,
                    type=part.type,
                    text=getattr(part, 'text', None) if part.type in ("text", "reasoning") else None,
                    synthetic=getattr(part, 'synthetic', None),
                    tool=getattr(part, 'tool', None) if part.type == "tool" else None,
                    state=state_value,
                    callID=getattr(part, 'callID', None) if part.type == "tool" else None,
                    metadata=getattr(part, 'metadata', None),
                )
                parts.append(part_info)
            result.append(MessageWithParts(info=info, parts=parts))
        
        return result
    except Exception as e:
        log.error("session.messages.error", {"error": str(e), "sessionID": sessionID})
        return []


@router.get(
    "/{sessionID}/message/{messageID}",
    response_model=MessageWithParts,
    summary="Get message",
    description="Get a specific message by ID",
)
async def get_message(sessionID: str, messageID: str) -> MessageWithParts:
    """Get single message"""
    from flocks.session.message import Message
    import os
    
    msg_with_parts = await Message.get_with_parts(sessionID, messageID)
    if msg_with_parts:
        msg = msg_with_parts.info
        cwd = os.getcwd()
        
        # Create appropriate message info based on role
        if msg.role == "user":
            info = UserMessageInfo(
                id=msg.id,
                sessionID=msg.sessionID,
                role="user",
                time=msg.time,
                agent=getattr(msg, 'agent', None) or DEFAULT_AGENT,
                model={
                    "providerID": getattr(msg, 'providerID', None) or "anthropic",
                    "modelID": getattr(msg, 'modelID', None) or "claude-sonnet-4-5-20250929",
                },
            )
        else:
            # Convert tokens to dict if it's a TokenUsage object
            tokens_raw = getattr(msg, 'tokens', None)
            if tokens_raw is not None and hasattr(tokens_raw, 'model_dump'):
                tokens_dict = tokens_raw.model_dump()
            elif isinstance(tokens_raw, dict):
                tokens_dict = tokens_raw
            else:
                tokens_dict = {
                    "input": 0,
                    "output": 0,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0}
                }
            
            # Convert path to dict if it's a MessagePath object
            path_raw = getattr(msg, 'path', None)
            if path_raw is not None and hasattr(path_raw, 'model_dump'):
                path_dict = path_raw.model_dump()
            elif isinstance(path_raw, dict):
                path_dict = path_raw
            else:
                path_dict = {"cwd": cwd, "root": cwd}
            
            info = AssistantMessageInfo(
                id=msg.id,
                sessionID=msg.sessionID,
                role="assistant",
                time=msg.time,
                parentID=getattr(msg, 'parentID', None),
                modelID=getattr(msg, 'modelID', None) or "claude-sonnet-4-5-20250929",
                providerID=getattr(msg, 'providerID', None) or "anthropic",
                mode=getattr(msg, 'agent', None) or DEFAULT_AGENT,
                agent=getattr(msg, 'agent', None) or DEFAULT_AGENT,
                path=path_dict,
                cost=getattr(msg, 'cost', 0.0) or 0.0,
                tokens=tokens_dict,
            )
        
        parts = []
        for i, part in enumerate(msg_with_parts.parts):
            part_info = MessagePartInfo(
                id=part.id if hasattr(part, 'id') else f"{msg.id}_part_{i}",
                messageID=msg.id,
                sessionID=sessionID,
                type=part.type,
                text=getattr(part, 'text', None) if part.type in ("text", "reasoning") else None,
                synthetic=getattr(part, 'synthetic', None),
                tool=getattr(part, 'tool', None) if part.type == "tool" else None,
                state=getattr(part, 'state', None) if part.type == "tool" else None,
                callID=getattr(part, 'callID', None) if part.type == "tool" else None,
                metadata=getattr(part, 'metadata', None),
            )
            parts.append(part_info)
        return MessageWithParts(info=info, parts=parts)
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Message {messageID} not found in session {sessionID}"
    )


@router.delete(
    "/{sessionID}/message/{messageID}/part/{partID}",
    summary="Delete message part",
    description="Delete a specific part from a message",
)
async def delete_message_part(sessionID: str, messageID: str, partID: str) -> bool:
    """Delete message part"""
    from flocks.session.message import Message
    
    try:
        await Message.remove_part(sessionID, messageID, partID)
        log.info("message.part.deleted", {
            "sessionID": sessionID,
            "messageID": messageID,
            "partID": partID,
        })
        return True
    except Exception as e:
        log.error("message.part.delete.error", {"error": str(e)})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch(
    "/{sessionID}/message/{messageID}/part/{partID}",
    response_model=MessagePartInfo,
    summary="Update message part",
    description="Update a specific part in a message",
)
async def update_message_part(
    sessionID: str,
    messageID: str,
    partID: str,
    body: MessagePartInfo,
) -> MessagePartInfo:
    """Update message part"""
    if body.id != partID or body.messageID != messageID or body.sessionID != sessionID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Part IDs do not match URL parameters"
        )
    
    from flocks.session.message import Message
    
    try:
        await Message.update_part(sessionID, messageID, partID, **body.model_dump())
        log.info("message.part.updated", {
            "sessionID": sessionID,
            "messageID": messageID,
            "partID": partID,
        })
        return body
    except Exception as e:
        log.error("message.part.update.error", {"error": str(e)})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


async def _get_message_text_part(
    session_id: str,
    message_id: str,
    part_id: Optional[str] = None,
):
    """Return the target message and an editable text part."""
    from flocks.session.message import Message

    message = await Message.get(session_id, message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found in session {session_id}",
        )

    parts = await Message.parts(message_id, session_id)
    if part_id:
        text_part = next((part for part in parts if getattr(part, "id", None) == part_id), None)
        if not text_part:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Part {part_id} not found in message {message_id}",
            )
        if getattr(text_part, "type", None) != "text":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Part {part_id} is not an editable text part",
            )
    else:
        text_part = next((part for part in parts if getattr(part, "type", None) == "text"), None)
    if not text_part:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Message {message_id} does not have an editable text part",
        )

    return message, text_part


async def _publish_text_part_update(
    session_id: str,
    message_id: str,
    part_id: str,
    text: str,
) -> None:
    """Broadcast a text part update so other subscribers stay in sync."""
    from flocks.server.routes.event import publish_event

    await publish_event("message.part.updated", {
        "part": {
            "id": part_id,
            "messageID": message_id,
            "sessionID": session_id,
            "type": "text",
            "text": text,
        }
    })


def _track_background_task(task: "asyncio.Task[Any]") -> None:
    """Keep background tasks alive until completion."""
    if not hasattr(router, "_pending_tasks"):
        router._pending_tasks = set()
    router._pending_tasks.add(task)
    task.add_done_callback(lambda t: router._pending_tasks.discard(t))


def _schedule_background_coro(
    coro,
    *,
    session_id: Optional[str] = None,
    action: str = "session.background",
) -> None:
    """Schedule a background coroutine with unified error reporting."""
    import asyncio

    async def _guarded_coro() -> None:
        try:
            await coro
        except Exception as exc:
            log.error("session.background.error", {
                "sessionID": session_id,
                "action": action,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
            if session_id:
                from flocks.server.routes.event import publish_event

                try:
                    await publish_event("session.error", {
                        "sessionID": session_id,
                        "error": {
                            "name": type(exc).__name__,
                            "message": str(exc),
                            "data": {"message": str(exc), "action": action},
                        },
                    })
                except Exception as publish_exc:
                    log.error("session.background.error.publish_failed", {
                        "sessionID": session_id,
                        "action": action,
                        "error": str(publish_exc),
                        "error_type": type(publish_exc).__name__,
                    })

    task = asyncio.get_running_loop().create_task(_guarded_coro())
    _track_background_task(task)


async def _prepare_replay_runtime(
    session_id: str,
    user_message,
) -> Dict[str, str]:
    """Resolve replay runtime state before mutating session history."""
    from flocks.agent.registry import Agent
    from flocks.config.config import Config
    from flocks.provider.provider import Provider

    agent_name = getattr(user_message, "agent", None) or await Agent.default_agent()
    agent = await Agent.get(agent_name) or await Agent.get(DEFAULT_AGENT)

    model_info = getattr(user_message, "model", None)
    provider_id = model_info.get("providerID") if isinstance(model_info, dict) else None
    model_id = model_info.get("modelID") if isinstance(model_info, dict) else None
    if not provider_id or not model_id:
        dummy_request = type(
            "_MessageReplayRequest",
            (),
            {"model": None, "agent": agent_name},
        )()
        provider_id, model_id, _ = await _resolve_model(dummy_request, agent, session_id)

    Provider._ensure_initialized()
    config = await Config.get()
    await Provider.apply_config(config, provider_id=provider_id)
    provider = Provider.get(provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {provider_id} not found",
        )

    return {
        "agent_name": agent_name,
        "provider_id": provider_id,
        "model_id": model_id,
    }


async def _run_existing_user_message(
    session_id: str,
    session,
    user_message,
    working_directory: str,
    runtime: Optional[Dict[str, str]] = None,
):
    """Run SessionLoop using an already-persisted user message."""
    from flocks.server.routes.event import publish_event
    from flocks.session.lifecycle.revert import SessionRevert
    from flocks.session.message import Message
    from flocks.session.session_loop import SessionLoop, LoopCallbacks
    from flocks.utils.id import Identifier

    runtime = runtime or await _prepare_replay_runtime(session_id, user_message)
    agent_name = runtime["agent_name"]
    provider_id = runtime["provider_id"]
    model_id = runtime["model_id"]

    await SessionRevert.cleanup(session)

    async def _on_error(error: str):
        await publish_event("session.error", {
            "sessionID": session_id,
            "error": {"name": "SessionError", "message": error, "data": {"message": error}},
        })

    loop_callbacks = LoopCallbacks(
        on_error=_on_error,
        event_publish_callback=publish_event,
    )
    result = await SessionLoop.run(
        session_id=session_id,
        provider_id=provider_id,
        model_id=model_id,
        agent_name=agent_name,
        callbacks=loop_callbacks,
    )

    if result.action == "queued":
        log.info("session.message.replay.queued", {
            "sessionID": session_id,
            "user_message_id": user_message.id,
        })
        return {
            "status": "queued",
            "sessionID": session_id,
            "messageID": user_message.id,
        }

    end_ms = int(time.time() * 1000)
    finish_reason = "stop"
    final_content = ""
    assistant_message_id = None
    created_ms = end_ms

    if result.last_message:
        assistant_message_id = result.last_message.id
        final_content = await Message.get_text_content(result.last_message)
        finish = getattr(result.last_message, "finish", None)
        if finish:
            finish_reason = finish
        result_time = getattr(result.last_message, "time", None)
        if isinstance(result_time, dict):
            created_ms = result_time.get("created", created_ms)

    if result.action == "error":
        finish_reason = "error"
        if not assistant_message_id:
            assistant_message_id = Identifier.create("message")

    if not assistant_message_id:
        assistant_message_id = Identifier.create("message")

    await publish_event("message.updated", {
        "info": {
            "id": assistant_message_id,
            "sessionID": session_id,
            "role": "assistant",
            "time": {"created": created_ms, "completed": end_ms},
            "parentID": user_message.id,
            "modelID": model_id,
            "providerID": provider_id,
            "mode": agent_name,
            "agent": agent_name,
            "path": {"cwd": working_directory, "root": working_directory},
            "cost": 0,
            "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "finish": finish_reason,
        }
    })

    log.info("session.message.replay.completed", {
        "sessionID": session_id,
        "user_message_id": user_message.id,
        "assistant_message_id": assistant_message_id,
        "finish": finish_reason,
        "content_length": len(final_content),
    })

    return {
        "status": "completed",
        "sessionID": session_id,
        "messageID": assistant_message_id,
        "finish": finish_reason,
    }


@router.post(
    "/{sessionID}/message/{messageID}/resend",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replay edited user message",
    description="Update a user message and regenerate subsequent assistant output",
)
async def resend_session_message(
    sessionID: str,
    messageID: str,
    body: MessageEditRequest,
) -> Dict[str, str]:
    import os

    from flocks.project.bootstrap import instance_bootstrap
    from flocks.project.instance import Instance
    from flocks.session.lifecycle.revert import SessionRevert
    from flocks.session.message import Message
    from flocks.session.session_loop import SessionLoop

    text = body.text.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Edited message text cannot be empty",
        )

    message, text_part = await _get_message_text_part(sessionID, messageID, body.partID)
    if getattr(message, "role", None) != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only user messages can be resent",
        )

    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )

    if SessionLoop.is_running(sessionID):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session is currently generating a response",
        )

    working_directory = session.directory or os.getcwd()

    async def _handle_resend() -> None:
        runtime = await _prepare_replay_runtime(sessionID, message)
        updated_session = await SessionRevert.revert(sessionID, messageID)
        await Message.update_part(sessionID, messageID, text_part.id, text=text)
        await _publish_text_part_update(sessionID, messageID, text_part.id, text)

        refreshed_message = await Message.get(sessionID, messageID)
        if not refreshed_message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {messageID} not found after update",
            )

        await Instance.provide(
            directory=working_directory,
            init=instance_bootstrap,
            fn=lambda: _run_existing_user_message(
                sessionID,
                updated_session or session,
                refreshed_message,
                working_directory,
                runtime=runtime,
            ),
        )

    _schedule_background_coro(
        _handle_resend(),
        session_id=sessionID,
        action="message.resend",
    )
    return {"status": "accepted", "sessionID": sessionID, "messageID": messageID}


@router.post(
    "/{sessionID}/message/{messageID}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Regenerate assistant message",
    description="Discard an assistant reply and regenerate it from its parent user message",
)
async def regenerate_session_message(
    sessionID: str,
    messageID: str,
) -> Dict[str, str]:
    import os

    from flocks.project.bootstrap import instance_bootstrap
    from flocks.project.instance import Instance
    from flocks.session.lifecycle.revert import SessionRevert
    from flocks.session.message import Message
    from flocks.session.session_loop import SessionLoop

    message = await Message.get(sessionID, messageID)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {messageID} not found in session {sessionID}",
        )
    if getattr(message, "role", None) != "assistant":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only assistant messages can be regenerated",
        )

    parent_message_id = getattr(message, "parentID", None)
    if not parent_message_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assistant message does not have a parent user message",
        )

    parent_message, _ = await _get_message_text_part(sessionID, parent_message_id)
    if getattr(parent_message, "role", None) != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assistant parent message must be a user message",
        )

    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )

    if SessionLoop.is_running(sessionID):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session is currently generating a response",
        )

    working_directory = session.directory or os.getcwd()

    async def _handle_regenerate() -> None:
        runtime = await _prepare_replay_runtime(sessionID, parent_message)
        updated_session = await SessionRevert.revert(sessionID, parent_message_id)
        refreshed_parent = await Message.get(sessionID, parent_message_id)
        if not refreshed_parent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent message {parent_message_id} not found after revert",
            )

        await Instance.provide(
            directory=working_directory,
            init=instance_bootstrap,
            fn=lambda: _run_existing_user_message(
                sessionID,
                updated_session or session,
                refreshed_parent,
                working_directory,
                runtime=runtime,
            ),
        )

    _schedule_background_coro(
        _handle_regenerate(),
        session_id=sessionID,
        action="message.regenerate",
    )
    return {"status": "accepted", "sessionID": sessionID, "messageID": messageID}


@router.post(
    "/{sessionID}/message",
    summary="Send message",
    description="Send a new message and get AI response",
)
async def send_session_message(sessionID: str, request: PromptRequest):
    """
    Send message to session
    
    Supports full agent loop with tool execution.
    Real-time updates are sent via the /event SSE endpoint.
    """
    log.info("session.message.send.start", {"sessionID": sessionID})
    
    from flocks.session.message import Message, MessageRole
    from flocks.server.routes.event import publish_event
    from flocks.utils.id import Identifier
    from flocks.tool.registry import ToolRegistry, ToolContext
    from flocks.agent.registry import Agent
    from flocks.project.instance import Instance
    import time
    import json
    import os
    
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    working_directory = session.directory or os.getcwd()
    
    log.info("session.message.send.processing", {
        "sessionID": sessionID,
        "working_directory": working_directory,
    })
    
    # Ensure instance is bootstrapped with MCP
    from flocks.project.bootstrap import instance_bootstrap
    
    try:
        result = await Instance.provide(
            directory=working_directory,
            init=instance_bootstrap,
            fn=lambda: _process_session_message(sessionID, session, request, working_directory)
        )
        log.info("session.message.send.complete", {"sessionID": sessionID})
        return result
    except Exception as e:
        log.error("session.message.send.error", {
            "sessionID": sessionID,
            "error": str(e),
            "error_type": type(e).__name__,
        })
        raise


async def _get_last_model(session_id: str) -> Optional[Dict[str, str]]:
    """
    Get the last model used in the session (from last user message).
    Ported from original lastModel function.
    
    Returns:
        Dict with 'providerID' and 'modelID', or None
    """
    from flocks.session.message import Message
    
    try:
        # Get messages in reverse order (newest first)
        messages = await Message.list(session_id)
        
        # Find the last user message with model info
        for msg in reversed(messages):
            if msg.role == "user" and hasattr(msg, 'model') and msg.model:
                if isinstance(msg.model, dict) and 'providerID' in msg.model and 'modelID' in msg.model:
                    return msg.model
    except Exception as e:
        log.debug("session.last_model.error", {"error": str(e)})
    
    return None


def _parse_model_string(model: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse a provider/model string into separate IDs."""
    if not model:
        return None, None
    provider_id, sep, model_id = model.partition("/")
    if not sep or not provider_id or not model_id:
        return None, None
    return provider_id, model_id


async def _resolve_compaction_context(
    session_id: str,
    *,
    requested_agent: Optional[str] = None,
    requested_model: Optional[str] = None,
    explicit_provider_id: Optional[str] = None,
    explicit_model_id: Optional[str] = None,
) -> tuple[str, str, str]:
    """Resolve agent/provider/model for an explicit compaction request."""
    import os

    from flocks.agent.registry import Agent
    from flocks.config.config import Config
    from flocks.session.message import Message, MessageRole
    from flocks.storage.storage import Storage

    provider_id = explicit_provider_id
    model_id = explicit_model_id
    parsed_provider_id, parsed_model_id = _parse_model_string(requested_model)
    if not provider_id:
        provider_id = parsed_provider_id
    if not model_id:
        model_id = parsed_model_id

    agent_name = requested_agent or DEFAULT_AGENT

    try:
        messages = await Message.list(session_id)
    except Exception as exc:
        log.debug("session.compaction_context.messages_error", {
            "sessionID": session_id,
            "error": str(exc),
        })
        messages = []

    if not requested_agent:
        for msg in reversed(messages):
            if msg.role != MessageRole.USER:
                continue
            agent_name = getattr(msg, "agent", None) or agent_name
            model_dict = getattr(msg, "model", None)
            if isinstance(model_dict, dict):
                provider_id = provider_id or model_dict.get("providerID")
                model_id = model_id or model_dict.get("modelID")
            if provider_id and model_id:
                break

    if not provider_id or not model_id:
        try:
            overrides = await Storage.read("agent/model_overrides")
            if not isinstance(overrides, dict):
                overrides = {}
            override = overrides.get(agent_name) if agent_name else None
            if isinstance(override, dict):
                provider_id = provider_id or override.get("providerID")
                model_id = model_id or override.get("modelID")
        except Exception as exc:
            log.debug("session.compaction_context.agent_override_error", {
                "sessionID": session_id,
                "agent": agent_name,
                "error": str(exc),
            })

    if not provider_id or not model_id:
        try:
            agent = await Agent.get(agent_name) or await Agent.get(DEFAULT_AGENT)
            if agent and getattr(agent, "model", None):
                if isinstance(agent.model, dict):
                    provider_id = provider_id or agent.model.get("providerID") or agent.model.get("provider_id")
                    model_id = model_id or agent.model.get("modelID") or agent.model.get("model_id")
                else:
                    provider_id = provider_id or getattr(agent.model, "providerID", None) or getattr(agent.model, "provider_id", None)
                    model_id = model_id or getattr(agent.model, "modelID", None) or getattr(agent.model, "model_id", None)
        except Exception as exc:
            log.debug("session.compaction_context.agent_error", {
                "sessionID": session_id,
                "agent": agent_name,
                "error": str(exc),
            })

    if not provider_id or not model_id:
        try:
            default_llm = await Config.resolve_default_llm()
            if default_llm:
                provider_id = provider_id or default_llm["provider_id"]
                model_id = model_id or default_llm["model_id"]
        except Exception as exc:
            log.debug("session.compaction_context.default_model_error", {
                "sessionID": session_id,
                "error": str(exc),
            })

    if not provider_id or not model_id:
        try:
            last_model = await _get_last_model(session_id)
            if last_model:
                provider_id = provider_id or last_model.get("providerID")
                model_id = model_id or last_model.get("modelID")
        except Exception as exc:
            log.debug("session.compaction_context.last_model_error", {
                "sessionID": session_id,
                "error": str(exc),
            })

    if not provider_id or not model_id:
        provider_id = provider_id or os.environ.get("LLM_PROVIDER", "openai")
        model_id = model_id or os.environ.get("LLM_MODEL", "gpt-4-turbo-preview")

    return agent_name, provider_id, model_id


async def _run_session_compaction(
    session_id: str,
    *,
    requested_agent: Optional[str] = None,
    requested_model: Optional[str] = None,
    explicit_provider_id: Optional[str] = None,
    explicit_model_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
    auto: bool = False,
    event_publish_callback=None,
    focus_instruction: Optional[str] = None,
) -> tuple[str, str, str]:
    """Execute session compaction directly without routing through the LLM loop.

    ``focus_instruction`` is forwarded verbatim to ``run_compaction`` so
    manual ``/compact <focus>`` invocations can bias what the
    summariser preserves.  ``None``/empty leaves the default behaviour.
    """
    from flocks.session.lifecycle.compaction import run_compaction
    from flocks.session.lifecycle.compaction.compaction import (
        pop_last_compaction_error,
    )
    from flocks.session.lifecycle.revert import SessionRevert
    from flocks.session.message import Message, MessageRole

    session = await Session.get_by_id(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    await SessionRevert.cleanup(session)
    agent_name, provider_id, model_id = await _resolve_compaction_context(
        session_id,
        requested_agent=requested_agent,
        requested_model=requested_model,
        explicit_provider_id=explicit_provider_id,
        explicit_model_id=explicit_model_id,
    )

    messages = await Message.list(session_id)
    if not parent_message_id:
        for msg in reversed(messages):
            if msg.role == MessageRole.USER:
                parent_message_id = msg.id
                break
    if not parent_message_id:
        raise ValueError(f"Session {session_id} has no user message to compact")

    progress_callback = None
    if event_publish_callback is not None:
        # Adapter that bridges ``ProgressCallback(stage, data)`` from the
        # compaction pipeline onto the existing ``publish_event`` SSE
        # channel.  We use a dedicated event type
        # (``session.compaction_progress``) rather than overloading
        # ``session.status`` so the front-end dispatcher stays
        # explicit and unrelated consumers do not need to filter on a
        # nested ``stage`` field.  ``sessionID`` is closed over from
        # the enclosing scope.
        async def progress_callback(stage: str, data: dict) -> None:
            await event_publish_callback("session.compaction_progress", {
                "sessionID": session_id,
                "stage": stage,
                "data": data,
            })

    result = await run_compaction(
        session_id,
        parent_message_id=parent_message_id,
        messages=messages,
        provider_id=provider_id,
        model_id=model_id,
        auto=auto,
        event_publish_callback=event_publish_callback,
        status_after="idle",
        focus_instruction=focus_instruction,
        progress_callback=progress_callback,
    )
    if result == "stop":
        # ``SessionCompaction.process`` swallows the underlying provider
        # exception (so the loop path stays simple) but stashes the
        # user-facing message via ``_record_compaction_error``.  Surface
        # it verbatim here so the SSE ``session.error`` payload — and
        # therefore the front-end toast — shows e.g. "Error code: 529
        # — 模型服务暂时不可用" instead of an opaque "Compaction failed".
        detail = pop_last_compaction_error(session_id) or "Compaction failed"
        raise RuntimeError(detail)
    return agent_name, provider_id, model_id


# JSON repair utilities — delegated to flocks.utils.json_repair
_parse_json_robust = parse_json_robust
_repair_json_string = repair_truncated_json


def _check_session_aborted(sessionID: str, checkpoint: str, step: int, **extra_context) -> bool:
    """
    检查 session 是否被 abort
    
    Args:
        sessionID: Session ID
        checkpoint: 检查点名称（如 "before_step", "in_stream", "skip_tool_processing"）
        step: 当前 step 数
        **extra_context: 额外的日志上下文信息
    
    Returns:
        True 表示 session 已被 abort，应该停止执行
    """
    from flocks.session.core.status import SessionStatus
    
    current_status = SessionStatus.get(sessionID)
    if current_status and current_status.type == "idle":
        log.info(f"session.message.aborted.{checkpoint}", {
            "sessionID": sessionID,
            "step": step,
            **extra_context,
        })
        return True
    return False


async def _process_session_message(
    sessionID: str,
    session,
    request: PromptRequest,
    working_directory: str,
):
    """
    Process session message within Instance context.
    
    Delegates to SessionLoop.run() for the agent loop, eliminating
    duplicated loop/streaming/tool logic. SSE events flow through
    the event_publish_callback → StreamProcessor pipeline.
    """
    from flocks.session.message import Message, MessageRole
    from flocks.session.lifecycle.revert import SessionRevert
    from flocks.server.routes.event import publish_event
    from flocks.utils.id import Identifier
    from flocks.tool.registry import ToolRegistry
    from flocks.agent.registry import Agent
    from flocks.provider.provider import Provider
    from flocks.session.session_loop import SessionLoop, LoopCallbacks
    from flocks.session.runner import RunnerCallbacks
    import time
    import os
    
    # Clean up revert state before processing (Flocks compatibility)
    await SessionRevert.cleanup(session)
    
    # ------------------------------------------------------------------
    # 1. Extract text content
    # ------------------------------------------------------------------
    text_content = ""
    for part in request.parts:
        if part.get("type") == "text":
            text_content += part.get("text", "")
    
    if not text_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No text content in message"
        )
    
    log.info("session.message.received", {
        "sessionID": sessionID,
        "content_length": len(text_content),
    })
    
    # ------------------------------------------------------------------
    # 2. Resolve agent and model (5-level priority)
    # ------------------------------------------------------------------
    agent_name = request.agent or await Agent.default_agent()
    agent = await Agent.get(agent_name) or await Agent.get(DEFAULT_AGENT)
    
    provider_id, model_id, model_source = await _resolve_model(
        request, agent, sessionID
    )
    
    log.info("session.message.model", {
        "provider_id": provider_id,
        "model_id": model_id,
        "source": model_source,
    })

    if request.model:
        pinned_session = await Session.update(
            session.project_id,
            sessionID,
            **Session.explicit_model_updates(provider_id, model_id),
        )
        if pinned_session is not None:
            session = pinned_session
        else:
            session.provider = provider_id
            session.model = model_id
            session.model_pinned = True
    
    # Ensure providers are initialized and configured
    Provider._ensure_initialized()
    from flocks.config.config import Config
    config = await Config.get()
    await Provider.apply_config(config, provider_id=provider_id)
    
    provider = Provider.get(provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {provider_id} not found"
        )
    
    ToolRegistry.init()
    
    # ------------------------------------------------------------------
    # 3. Create user message and publish SSE events
    # ------------------------------------------------------------------
    now_ms = int(time.time() * 1000)
    user_message_id = request.messageID or Identifier.create("message")
    user_part_id = Identifier.create("part")

    # display_text (optional) is the user-visible text shown in the chat bubble.
    # It differs from text_content when a command generates a derived LLM prompt
    # (e.g. "/tools create foo" stores the slash command text, not the full skill
    # prompt that is sent to the LLM).
    display_text = getattr(request, "display_text", None) or text_content

    _is_no_reply = bool(request.noReply)
    user_message = await Message.create(
        session_id=sessionID,
        role=MessageRole.USER,
        content=display_text,
        id=user_message_id,
        time={"created": now_ms},
        agent=agent_name,
        model={"providerID": provider_id, "modelID": model_id},
        part_id=user_part_id,
        synthetic=True if _is_no_reply else None,
    )
    user_message_id = user_message.id
    
    await publish_event("message.updated", {
        "info": {
            "id": user_message_id,
            "sessionID": sessionID,
            "role": "user",
            "time": {"created": now_ms},
            "agent": agent_name,
            "model": {"providerID": provider_id, "modelID": model_id},
        }
    })
    _part_event: dict = {
        "id": user_part_id,
        "messageID": user_message_id,
        "sessionID": sessionID,
        "type": "text",
        "text": display_text,
        "time": {"start": now_ms},
    }
    if _is_no_reply:
        _part_event["synthetic"] = True
    await publish_event("message.part.updated", {"part": _part_event})

    # ------------------------------------------------------------------
    # noReply: store message only, skip AI loop
    # ------------------------------------------------------------------
    if request.noReply:
        log.info("session.message.no_reply", {"sessionID": sessionID})

        # Optionally inject a mock assistant reply
        if request.mockReply:
            mock_msg_id = Identifier.ascending("message")
            mock_part_id = Identifier.ascending("part")
            mock_now = int(time.time() * 1000)
            await Message.create(
                session_id=sessionID,
                role=MessageRole.ASSISTANT,
                content=request.mockReply,
                id=mock_msg_id,
                time={"created": mock_now, "completed": mock_now},
                parentID=user_message_id,
                modelID="mock",
                part_id=mock_part_id,
            )
            await publish_event("message.updated", {
                "info": {
                    "id": mock_msg_id,
                    "sessionID": sessionID,
                    "role": "assistant",
                    "time": {"created": mock_now, "completed": mock_now},
                    "parentID": user_message_id,
                    "modelID": "mock",
                    "finish": "stop",
                }
            })
            await publish_event("message.part.updated", {
                "part": {
                    "id": mock_part_id,
                    "messageID": mock_msg_id,
                    "sessionID": sessionID,
                    "type": "text",
                    "text": request.mockReply,
                    "time": {"start": mock_now, "end": mock_now},
                },
            })

        return {
            "id": user_message_id,
            "sessionID": sessionID,
            "role": "user",
            "content": text_content,
            "finish": "stop",
        }

    # ------------------------------------------------------------------
    # 4. Run unified SessionLoop (replaces ~700 lines of inline loop)
    # ------------------------------------------------------------------
    async def _on_error(error: str):
        await publish_event("session.error", {
            "sessionID": sessionID,
            "error": {"name": "SessionError", "message": error, "data": {"message": error}},
        })
    
    loop_callbacks = LoopCallbacks(
        on_error=_on_error,
        event_publish_callback=publish_event,
    )
    
    result = await SessionLoop.run(
        session_id=sessionID,
        provider_id=provider_id,
        model_id=model_id,
        agent_name=agent_name,
        callbacks=loop_callbacks,
    )

    # ------------------------------------------------------------------
    # 4a. already_running: user message was persisted but the active loop
    #     will pick it up on the next iteration — do NOT emit a fake empty
    #     assistant completion event here.
    # ------------------------------------------------------------------
    if result.action == "queued":
        log.info("session.message.queued", {
            "sessionID": sessionID,
            "user_message_id": user_message_id,
            "reason": "loop already running; message queued for next iteration",
        })
        return {
            "id": user_message_id,
            "sessionID": sessionID,
            "role": "user",
            "content": text_content,
            "status": "queued",
        }
    
    # ------------------------------------------------------------------
    # 5. Build response from loop result
    # ------------------------------------------------------------------
    end_ms = int(time.time() * 1000)
    finish_reason = "stop"
    final_content = ""
    assistant_message_id = None
    
    if result.last_message:
        assistant_message_id = result.last_message.id
        final_content = await Message.get_text_content(result.last_message)
        finish = getattr(result.last_message, 'finish', None)
        if finish:
            finish_reason = finish
    
    if result.action == "error":
        finish_reason = "error"
        if not assistant_message_id:
            assistant_message_id = Identifier.create("message")
    
    if not assistant_message_id:
        assistant_message_id = Identifier.create("message")
    
    # Publish final completion event
    await publish_event("message.updated", {
        "info": {
            "id": assistant_message_id,
            "sessionID": sessionID,
            "role": "assistant",
            "time": {"created": now_ms, "completed": end_ms},
            "parentID": user_message_id,
            "modelID": model_id,
            "providerID": provider_id,
            "mode": agent_name,
            "agent": agent_name,
            "path": {"cwd": working_directory, "root": working_directory},
            "cost": 0,
            "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "finish": finish_reason,
        }
    })
    
    # Collect parts for the response
    all_parts = []
    if result.last_message:
        parts = await Message.parts(result.last_message.id, sessionID)
        for part in parts:
            part_dict = part.model_dump() if hasattr(part, 'model_dump') else {}
            all_parts.append(part_dict)
    
    log.info("message.completed", {
        "id": assistant_message_id,
        "session_id": sessionID,
        "role": "assistant",
        "content_length": len(final_content),
        "total_steps": result.metadata.get("steps", 0),
    })
    
    # Generate session title after first user message (async, don't block response)
    try:
        from flocks.session.lifecycle.title import SessionTitle
        import asyncio
        loop = asyncio.get_running_loop()
        from flocks.server.routes.event import publish_event
        title_task = loop.create_task(
            SessionTitle.generate_title_after_first_message(
                session_id=sessionID,
                model_id=model_id,
                provider_id=provider_id,
                event_publish_callback=publish_event,
            )
        )
        if not hasattr(router, '_title_tasks'):
            router._title_tasks = set()
        router._title_tasks.add(title_task)
        title_task.add_done_callback(lambda t: router._title_tasks.discard(t))
    except Exception as e:
        log.warn("session.title.trigger_error", {"error": str(e)})
    
    return {
        "info": {
            "id": assistant_message_id,
            "sessionID": sessionID,
            "role": "assistant",
            "time": {"created": now_ms, "completed": end_ms},
            "parentID": user_message_id,
            "modelID": model_id,
            "providerID": provider_id,
            "mode": agent_name,
            "agent": agent_name,
            "path": {"cwd": working_directory, "root": working_directory},
            "cost": 0,
            "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "finish": finish_reason,
        },
        "parts": all_parts if all_parts else [{
            "id": Identifier.create("part"),
            "messageID": assistant_message_id,
            "sessionID": sessionID,
            "type": "text",
            "text": final_content,
        }]
    }


async def _resolve_model(request, agent, sessionID: str):
    """
    Resolve model using the same explicit-pinning semantics as SessionLoop.

    Priority:
    1. request.model (explicit in request)
    2. session pinned model
    3. agent model override (storage) or agent.model (AgentInfo field)
    4. parent session pinned model
    5. config model (flocks.json)
    6. lastModel(sessionID) (last used model)
    7. environment variables (final fallback)

    Returns (provider_id, model_id, source).
    """
    import os
    
    provider_id = None
    model_id = None
    source = "unknown"
    
    # Priority 1: User specified model
    if request.model:
        provider_id = request.model.providerID
        model_id = request.model.modelID
        source = "request"

    # Priority 2: Session explicit pin
    session = None
    if not provider_id or not model_id:
        session = await Session.get_by_id(sessionID)
        if Session.has_pinned_model(session):
            provider_id = session.provider
            model_id = session.model
            source = "session"

    # Priority 3: Agent model (override from storage, then AgentInfo.model)
    if not provider_id or not model_id:
        # 2a: Check model overrides from storage (set via UI for native agents)
        from flocks.storage.storage import Storage
        try:
            overrides = await Storage.read("agent/model_overrides")
            if not isinstance(overrides, dict):
                overrides = {}
        except Exception:
            overrides = {}
        agent_name = agent.name if hasattr(agent, 'name') else None
        if agent_name and agent_name in overrides:
            override = overrides[agent_name]
            override_provider = override.get('providerID')
            override_model = override.get('modelID')
            if override_provider and override_model:
                provider_id = override_provider
                model_id = override_model
                source = "agent_override"
        
        # 2b: Check AgentInfo.model field (for custom agents or programmatic config)
        if not provider_id or not model_id:
            if hasattr(agent, 'model') and agent.model:
                if isinstance(agent.model, dict):
                    provider_id = agent.model.get('providerID') or agent.model.get('provider_id')
                    model_id = agent.model.get('modelID') or agent.model.get('model_id')
                else:
                    provider_id = getattr(agent.model, 'provider_id', None) or getattr(agent.model, 'providerID', None)
                    model_id = getattr(agent.model, 'model_id', None) or getattr(agent.model, 'modelID', None)
                if provider_id and model_id:
                    source = "agent"

    # Priority 4: Parent session explicit model
    if not provider_id or not model_id:
        if session is None:
            session = await Session.get_by_id(sessionID)
        parent_id = getattr(session, "parent_id", None) if session else None
        if parent_id:
            parent = await Session.get_by_id(parent_id)
            if Session.has_pinned_model(parent):
                provider_id = parent.provider
                model_id = parent.model
                source = "parent_session"

    # Priority 5: System default from config (default_models.llm -> config.model fallback)
    if not provider_id or not model_id:
        try:
            from flocks.config.config import Config
            default_llm = await Config.resolve_default_llm()
            if default_llm:
                provider_id = default_llm["provider_id"]
                model_id = default_llm["model_id"]
                source = "config"
        except Exception:
            pass

    # Priority 6: Last model used in session
    if not provider_id or not model_id:
        last_model = await _get_last_model(sessionID)
        if last_model:
            last_provider = last_model.get('providerID')
            last_model_id = last_model.get('modelID')
            if last_provider and last_model_id:
                provider_id = last_provider
                model_id = last_model_id
                source = "lastModel"

    # Priority 7: Fallback to environment variables
    if not provider_id or not model_id:
        provider_id = os.environ.get("LLM_PROVIDER", "openai")
        model_id = os.environ.get("LLM_MODEL", "gpt-4-turbo-preview")
        source = "env_default"
    
    return provider_id, model_id, source


@router.post(
    "/{sessionID}/prompt_async",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send async message",
    description="Send a message asynchronously (returns immediately)",
)
async def send_session_message_async(
    sessionID: str,
    request: PromptRequest,
):
    """Send message asynchronously - returns 202 immediately, response via SSE"""
    import os
    
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    working_directory = session.directory or os.getcwd()
    
    log.info("session.prompt_async.accepted", {
        "sessionID": sessionID,
        "directory": working_directory,
    })
    
    # Use the same synchronous processing path as send_session_message
    # but run it as a background task via asyncio.ensure_future
    import asyncio
    
    async def _run_in_background():
        import traceback
        import sys
        try:
            log.info("session.prompt_async.processing_start", {
                "sessionID": sessionID,
            })
            
            from flocks.project.instance import Instance
            from flocks.project.bootstrap import instance_bootstrap
            
            await Instance.provide(
                directory=working_directory,
                init=instance_bootstrap,
                fn=lambda: _process_session_message(
                    sessionID, session, request, working_directory
                ),
            )
            log.info("session.prompt_async.processing_complete", {
                "sessionID": sessionID,
            })
        except Exception as e:
            tb = traceback.format_exc()
            log.error("session.prompt_async.error", {
                "sessionID": sessionID,
                "error": str(e),
                "error_type": type(e).__name__,
            })
            print(f"[prompt_async ERROR] {sessionID}: {e}\n{tb}", file=sys.stderr, flush=True)
            # Clear session busy status on error
            try:
                from flocks.session.core.status import SessionStatus
                SessionStatus.clear(sessionID)
            except Exception:
                pass
            # Publish error event so frontend gets notified
            from flocks.server.routes.event import publish_event
            error_msg = str(e)
            await publish_event("session.error", {
                "sessionID": sessionID,
                "error": {"name": type(e).__name__, "message": error_msg, "data": {"message": error_msg}},
            })
    
    # Schedule as asyncio task with explicit reference tracking
    loop = asyncio.get_running_loop()
    task = loop.create_task(_run_in_background())
    # Store reference on the app state to prevent GC
    if not hasattr(router, '_pending_tasks'):
        router._pending_tasks = set()
    router._pending_tasks.add(task)
    task.add_done_callback(lambda t: router._pending_tasks.discard(t))
    
    return {"status": "accepted", "sessionID": sessionID}


class CommandRequest(BaseModel):
    """Request to execute a command"""
    model_config = ConfigDict(populate_by_name=True)
    
    command: str = Field(..., description="Command name")
    arguments: str = Field("", description="Command arguments")
    messageID: Optional[str] = Field(None, description="Message ID")
    agent: Optional[str] = Field(None, description="Agent name")
    model: Optional[str] = Field(None, description="Model string (provider/model)")
    variant: Optional[str] = Field(None, description="Model variant")
    parts: Optional[List[Dict[str, Any]]] = Field(None, description="Additional parts")


@router.post(
    "/{sessionID}/command",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send command",
    description="Execute a slash command in the session (returns 202, result via SSE)",
)
async def send_session_command(sessionID: str, request: CommandRequest):
    """
    Execute a slash command.

    Direct commands (/tools, /skills, /help, /mcp, /clear, /restart) are handled
    without calling the LLM.  Their output is pushed as an assistant message
    directly via SSE.

    LLM-based commands (/plan, /ask, /init, /compact, ...) are routed through
    the normal session-loop pipeline.

    In both cases the user message (showing the raw slash command text, e.g.
    "/tools") is created inside the background task so there is exactly ONE user
    message in the session history.  The frontend shows a temporary placeholder
    that is replaced as soon as the real SSE event arrives.
    """
    import asyncio
    import time as _time
    import os
    from flocks.session.message import Message, MessageRole
    from flocks.server.routes.event import publish_event
    from flocks.utils.id import Identifier
    from flocks.command.handler import handle_slash_command

    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )

    working_directory = session.directory or os.getcwd()
    agent_name = request.agent or "rex"

    # The text the user typed, shown verbatim in the chat bubble
    slash_text = f"/{request.command}"
    if request.arguments:
        slash_text += f" {request.arguments}"

    # ── Helper: create user message + publish SSE ───────────────────────────
    async def _create_user_message(
        model_info: Optional[Dict[str, str]] = None,
        *,
        agent_override: Optional[str] = None,
    ) -> str:
        now_ms = int(_time.time() * 1000)
        user_msg_id = Identifier.create("message")
        user_part_id = Identifier.create("part")
        message_agent = agent_override or agent_name
        await Message.create(
            session_id=sessionID,
            role=MessageRole.USER,
            content=slash_text,
            id=user_msg_id,
            time={"created": now_ms},
            agent=message_agent,
            **({"model": model_info} if model_info else {}),
            part_id=user_part_id,
        )
        await publish_event("message.updated", {
            "info": {
                "id": user_msg_id,
                "sessionID": sessionID,
                "role": "user",
                "time": {"created": now_ms},
                "agent": message_agent,
                **({"model": model_info} if model_info else {}),
            }
        })
        await publish_event("message.part.updated", {
            "part": {
                "id": user_part_id,
                "messageID": user_msg_id,
                "sessionID": sessionID,
                "type": "text",
                "text": slash_text,
                "time": {"start": now_ms},
            }
        })
        return user_msg_id

    # ── Helper: publish a direct (non-LLM) assistant message ────────────────
    async def _publish_direct_response(text: str, parent_msg_id: str) -> None:
        asst_now = int(_time.time() * 1000)
        asst_msg_id = Identifier.ascending("message")
        asst_part_id = Identifier.ascending("part")
        await Message.create(
            session_id=sessionID,
            role=MessageRole.ASSISTANT,
            content=text,
            id=asst_msg_id,
            time={"created": asst_now, "completed": asst_now},
            parentID=parent_msg_id,
            modelID="command",
            providerID="builtin",
            agent=agent_name,
            finish="stop",
            part_id=asst_part_id,
        )
        await publish_event("message.updated", {
            "info": {
                "id": asst_msg_id,
                "sessionID": sessionID,
                "role": "assistant",
                "time": {"created": asst_now, "completed": asst_now},
                "parentID": parent_msg_id,
                "modelID": "command",
                "providerID": "builtin",
                "agent": agent_name,
                "mode": agent_name,
                "finish": "stop",
                "tokens": {"input": 0, "output": 0, "reasoning": 0,
                           "cache": {"read": 0, "write": 0}},
            }
        })
        await publish_event("message.part.updated", {
            "part": {
                "id": asst_part_id,
                "messageID": asst_msg_id,
                "sessionID": sessionID,
                "type": "text",
                "text": text,
                "time": {"start": asst_now, "end": asst_now},
            }
        })

    # ── Helper: run a prompt through the LLM (session loop) ─────────────────
    async def _run_via_llm(prompt_text: str, display_text: str | None = None) -> None:
        """
        Route a prompt through the LLM pipeline via _process_session_message.
        This creates its own user message, so call ONLY when no user message
        has been created yet for this command invocation.

        display_text: optional override for the user-visible message bubble.
        When provided (e.g. "/tools create foo"), the user message stored in
        the DB and shown in the chat shows display_text, while the LLM still
        receives the full prompt_text (e.g. the tool-builder skill content).
        """
        import types
        from flocks.project.instance import Instance
        from flocks.project.bootstrap import instance_bootstrap

        cmd_request = types.SimpleNamespace(
            parts=[{"type": "text", "text": prompt_text}],
            display_text=display_text,
            agent=request.agent,
            model=request.model,
            variant=request.variant,
            messageID=None,
            mockReply=None,
            noReply=False,
        )

        await Instance.provide(
            directory=working_directory,
            init=instance_bootstrap,
            fn=lambda: _process_session_message(
                sessionID, session, cmd_request, working_directory
            ),
        )

    async def _run_compaction_command(
        parent_msg_id: str,
        *,
        agent_for_compaction: Optional[str],
        provider_id: str,
        model_id: str,
        focus_instruction: Optional[str] = None,
    ) -> None:
        from flocks.project.bootstrap import instance_bootstrap
        from flocks.project.instance import Instance

        await Instance.provide(
            directory=working_directory,
            init=instance_bootstrap,
            fn=lambda: _run_session_compaction(
                sessionID,
                requested_agent=agent_for_compaction,
                explicit_provider_id=provider_id,
                explicit_model_id=model_id,
                parent_message_id=parent_msg_id,
                auto=False,
                event_publish_callback=publish_event,
                focus_instruction=focus_instruction,
            ),
        )

    # ── Background task ──────────────────────────────────────────────────────
    async def _handle_command() -> None:
        result_texts: list[str] = []
        llm_prompts: list[str] = []

        async def _send_text(text: str) -> None:
            result_texts.append(text)

        async def _send_prompt(prompt: str) -> None:
            llm_prompts.append(prompt)

        try:
            if request.command.lower() == "compact":
                # Manual compaction trigger.  ``request.arguments`` is
                # treated as a free-form "focus instruction" appended to
                # the summarisation prompt — e.g. ``/compact focus on
                # unresolved decisions``.  Empty arguments preserve the
                # legacy default-prompt behaviour.
                focus_instruction = request.arguments.strip() or None
                compact_agent, compact_provider_id, compact_model_id = await _resolve_compaction_context(
                    sessionID,
                    requested_agent=request.agent,
                    requested_model=request.model,
                )
                user_msg_id = await _create_user_message(
                    {
                        "providerID": compact_provider_id,
                        "modelID": compact_model_id,
                    },
                    agent_override=compact_agent,
                )
                await _run_compaction_command(
                    user_msg_id,
                    agent_for_compaction=compact_agent,
                    provider_id=compact_provider_id,
                    model_id=compact_model_id,
                    focus_instruction=focus_instruction,
                )
                return

            handled = await handle_slash_command(
                slash_text,
                send_text=_send_text,
                send_prompt=_send_prompt,
            )

            if handled:
                if llm_prompts:
                    # Command collected a prompt to run through LLM
                    # (e.g., "/tools create <requirement>" → tool-builder skill).
                    # display_text=slash_text ensures the user bubble shows the
                    # original slash command, while the LLM receives the full
                    # skill/tool prompt.
                    await _run_via_llm(llm_prompts[0], display_text=slash_text)
                elif result_texts:
                    # Direct text response — create the user+assistant pair now
                    user_msg_id = await _create_user_message()
                    await _publish_direct_response("\n".join(result_texts), user_msg_id)
                # else: handled but produced nothing (rare edge case)
            else:
                # Not handled directly (e.g. /plan, /ask, /init, /compact …)
                # _process_session_message creates the user message; pass the raw
                # slash text so Rex can interpret it via its slash-command knowledge.
                await _run_via_llm(slash_text)

        except Exception as exc:
            import traceback
            log.error("session.command.error", {
                "sessionID": sessionID,
                "command": request.command,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            })
            await publish_event("session.error", {
                "sessionID": sessionID,
                "error": {
                    "name": type(exc).__name__,
                    "message": str(exc),
                    "data": {},
                },
            })

    loop = asyncio.get_running_loop()
    task = loop.create_task(_handle_command())
    if not hasattr(router, "_pending_tasks"):
        router._pending_tasks = set()
    router._pending_tasks.add(task)
    task.add_done_callback(lambda t: router._pending_tasks.discard(t))

    log.info("session.command.accepted", {
        "sessionID": sessionID,
        "command": request.command,
    })

    return {"status": "accepted", "sessionID": sessionID}


class ShellRequest(BaseModel):
    """Request to run shell command"""
    agent: str = Field(..., description="Agent name")
    command: str = Field(..., description="Shell command to execute")
    model: Optional[ModelInfo] = Field(None, description="Model selection")


@router.post(
    "/{sessionID}/shell",
    summary="Run shell command",
    description="Execute a shell command in the session context",
)
async def run_shell_command(sessionID: str, request: ShellRequest):
    """Run shell command"""
    from flocks.session.runner import SessionRunner
    
    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    model = None
    if request.model:
        model = {"providerID": request.model.providerID, "modelID": request.model.modelID}
    
    result = await SessionRunner.shell(
        session_id=sessionID,
        agent=request.agent,
        command=request.command,
        model=model,
    )
    
    log.info("session.shell.executed", {
        "sessionID": sessionID,
        "command": request.command[:50],
    })
    
    return result


# =============================================================================
# Permission Routes
# =============================================================================

class PermissionResponse(BaseModel):
    """Permission response for API compatibility"""
    response: str = Field(..., description="Response: allow, deny, always, never, or allow_session")


@router.post(
    "/{sessionID}/permissions/{permissionID}",
    summary="Respond to permission",
    description="Approve or deny a permission request",
)
async def respond_to_permission(
    sessionID: str,
    permissionID: str,
    request: PermissionResponse,
) -> bool:
    """Respond to permission request"""
    from flocks.permission.next import PermissionNext
    
    await PermissionNext.reply(
        request_id=permissionID,
        reply=request.response,
    )
    
    log.info("permission.responded", {
        "sessionID": sessionID,
        "permissionID": permissionID,
        "response": request.response,
    })
    
    return True


# =============================================================================
# Diff Routes (FileDiff class defined at top of file)
# =============================================================================


# =============================================================================
# Monitoring & Metrics Routes
# =============================================================================

@router.get("/metrics")
async def get_metrics():
    """
    Get system-wide monitoring metrics
    
    Returns metrics including:
    - Tool call parsing success/failure rates
    - Repair strategy success rates
    - Top failing tools
    - Recent failure details
    """
    monitor = get_monitor()
    metrics = monitor.get_metrics()
    
    return {
        "status": "success",
        "metrics": metrics,
    }


@router.get("/{sessionID}/metrics")
async def get_session_metrics(sessionID: str):
    """
    Get metrics for a specific session
    
    Returns session-specific metrics including:
    - Tool call counts and success rates
    - Failed tool calls
    - Repair attempts
    """
    monitor = get_monitor()
    session_metrics = monitor.get_session_metrics(sessionID)
    
    if session_metrics is None:
        return {
            "status": "success",
            "sessionID": sessionID,
            "metrics": None,
            "message": "No metrics available for this session"
        }
    
    return {
        "status": "success",
        "sessionID": sessionID,
        "metrics": session_metrics,
    }


# =============================================================================
# WebUI Enhancement Routes
# =============================================================================

@router.get("/recent")
async def get_recent_sessions(limit: int = Query(10, ge=1, le=50, description="Number of sessions")):
    """
    Get recent sessions
    
    Returns list of recently active sessions for WebUI home page.
    """
    try:
        # Get all sessions
        sessions_result = await Session.list()
        
        # Convert to response format
        sessions = []
        for session_model in sessions_result:
            try:
                session_dict = session_model.model_dump(mode="json", by_alias=True)
                sessions.append(SessionResponse(**session_dict))
            except Exception as e:
                log.warning("session.recent.skip", {"session_id": session_model.id, "error": str(e)})
                continue
        
        # Sort by updated time (most recent first)
        sessions.sort(key=lambda s: s.time.updated, reverse=True)
        
        # Limit results
        sessions = sessions[:limit]
        
        log.info("session.recent", {"count": len(sessions), "limit": limit})
        return sessions
    except Exception as e:
        log.error("session.recent.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get recent sessions: {str(e)}")


@router.get("/{sessionID}/statistics")
async def get_session_statistics(sessionID: str):
    """
    Get session statistics
    
    Returns detailed statistics including:
    - Message count
    - Token count
    - Tool calls
    - Session duration
    - Model usage
    """
    try:
        # Get session
        session = await Session.load(sessionID)
        
        # Get messages
        messages = await session.get_messages()
        
        # Calculate statistics
        message_count = len(messages)
        token_count = 0
        tool_call_count = 0
        model_usage = {}
        
        for msg in messages:
            # Count tokens (approximate from parts)
            for part in msg.parts:
                if hasattr(part, 'text') and part.text:
                    token_count += len(part.text.split())  # Rough approximation
                
                # Count tool calls
                if hasattr(part, 'toolCall') and part.toolCall:
                    tool_call_count += 1
            
            # Track model usage
            if msg.model:
                model_usage[msg.model] = model_usage.get(msg.model, 0) + 1
        
        # Get session info
        info = await session.get_info()
        
        # Calculate duration
        created_ms = info.time.created
        updated_ms = info.time.updated
        duration_ms = updated_ms - created_ms
        duration_seconds = duration_ms / 1000
        
        stats = {
            "sessionID": sessionID,
            "messageCount": message_count,
            "tokenCount": token_count,
            "toolCallCount": tool_call_count,
            "modelUsage": model_usage,
            "durationSeconds": duration_seconds,
            "createdAt": created_ms,
            "updatedAt": updated_ms,
        }
        
        log.info("session.statistics", {"sessionID": sessionID, "messages": message_count})
        return stats
    except Exception as e:
        log.error("session.statistics.error", {"sessionID": sessionID, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get session statistics: {str(e)}")


@router.post("/{sessionID}/clear")
async def clear_session(sessionID: str):
    """
    Clear session messages
    
    Removes all messages from the session while keeping the session itself.
    """
    try:
        # Verify session exists
        session_info = await Session.get_by_id(sessionID)
        if not session_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {sessionID} not found",
            )

        # Use Message.clear which handles bulk deletion atomically
        from flocks.session.message import Message
        deleted_count = await Message.clear(sessionID)

        log.info("session.cleared", {"sessionID": sessionID, "deleted": deleted_count})
        return {
            "status": "success",
            "sessionID": sessionID,
            "deletedMessages": deleted_count,
        }
    except Exception as e:
        log.error("session.clear.error", {"sessionID": sessionID, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to clear session: {str(e)}")


