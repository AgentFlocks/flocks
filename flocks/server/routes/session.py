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
from fastapi import APIRouter, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ConfigDict

from flocks.auth.context import get_current_auth_user, set_current_auth_user, reset_current_auth_user
from flocks.server.routes._timing import log_route_timing
from flocks.audit import emit_audit_event
from flocks.license import assert_license_active
from flocks.session.context_usage import (
    ContextUsageSnapshot,
    build_context_usage_snapshot,
    token_usage_to_dict,
)
from flocks.session.session import Session, SessionInfo as SessionModel
from flocks.session.policy import SessionPolicy
from flocks.utils.log import Log
from flocks.utils.json_repair import parse_json_robust, repair_truncated_json
from flocks.utils.monitor import get_monitor
from flocks.server.auth import require_user

router = APIRouter()
log = Log.create(service="session-routes")

# Default agent name constant
DEFAULT_AGENT = "rex"
DEFAULT_MESSAGE_PAGE_LIMIT = 50
_DESCENDANT_ABORT_SCAN_LIMIT = 3

# File extensions that are safe to persist when materialising data-URL uploads.
# Intentionally narrow: any extension outside this set is rejected to prevent
# OS tools (Finder, ``open``) from misidentifying content based on the
# extension (e.g. a PNG named report.pdf.exe whose tail would otherwise be
# ".exe").
_UPLOAD_SAFE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "pdf"})

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


class SessionGoalResponse(BaseModel):
    """Persisted goal state shown by the WebUI composer banner."""
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["active", "paused", "completed", "blocked"] = Field(..., description="Goal status")
    objective: str = Field(..., description="Goal objective")
    reason: Optional[str] = Field(None, description="Last goal judge reason")


class SessionResponse(BaseModel):
    """
    Session response - Flocks compatible
    
    Matches Flocks Session.Info format.
    """
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(..., description="Session ID")
    slug: str = Field("", description="Session slug")
    projectID: str = Field(..., description="Project ID")
    directory: str = Field(..., description="Working directory")
    parentID: Optional[str] = Field(None, description="Parent session ID")
    summary: Optional[Dict[str, Any]] = Field(None, description="Session summary with diffs")
    title: str = Field(..., description="Session title")
    version: str = Field("1.0.0", description="Session version")
    time: SessionTime = Field(..., description="Session timestamps")
    permission: Optional[List[Dict[str, Any]]] = Field(None, description="Permission rules")
    revert: Optional[Dict[str, Any]] = Field(None, description="Revert state")
    category: str = Field("user", description="Session category: user or task")
    provider: Optional[str] = Field(None, description="Pinned provider ID")
    model: Optional[str] = Field(None, description="Pinned model ID")
    model_pinned: bool = Field(False, description="Whether provider/model are pinned for this session")
    ownerUserID: Optional[str] = Field(None, description="Session owner user id")
    ownerUsername: Optional[str] = Field(None, description="Session owner username")
    canWrite: bool = Field(False, description="Whether current user can continue this session")
    canDelete: bool = Field(False, description="Whether current user can delete this session")
    isShared: bool = Field(False, description="Whether this session is locally shared")
    goal: Optional[SessionGoalResponse] = Field(None, description="Persisted session goal state")


class SessionListItem(BaseModel):
    """Lightweight session row for the session manager sidebar."""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)

    id: str
    title: str
    time: SessionTime
    category: str = "user"
    parentID: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    model_pinned: bool = False
    canWrite: bool = False
    canDelete: bool = False
    isShared: bool = False


def _session_to_response(session: SessionModel) -> SessionResponse:
    """
    Convert SessionModel to SessionResponse
    """
    current_user = get_current_auth_user()
    can_write = SessionPolicy.can_write(session, current_user)
    can_delete = SessionPolicy.can_delete(session, current_user)
    is_shared = SessionPolicy.is_shared(session)

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
        revert=session.revert.model_dump(by_alias=True) if session.revert else None,
        permission=[p.model_dump() for p in session.permission] if session.permission else None,
        category=session.category,
        provider=session.provider,
        model=session.model,
        model_pinned=session.model_pinned,
        ownerUserID=session.owner_user_id,
        ownerUsername=session.owner_username,
        canWrite=can_write,
        canDelete=can_delete,
        isShared=is_shared,
    )


def _session_to_list_item(session: SessionModel) -> SessionListItem:
    """Convert a session to the lightweight manager-list response shape."""
    current_user = get_current_auth_user()
    return SessionListItem(
        id=session.id,
        title=session.title,
        time=SessionTime(
            created=session.time.created,
            updated=session.time.updated,
            compacting=session.time.compacting,
            archived=session.time.archived,
        ),
        category=session.category,
        parentID=session.parent_id,
        provider=session.provider,
        model=session.model,
        model_pinned=session.model_pinned,
        canWrite=SessionPolicy.can_write(session, current_user),
        canDelete=SessionPolicy.can_delete(session, current_user),
        isShared=SessionPolicy.is_shared(session),
    )


async def _session_to_response_with_goal(session: SessionModel) -> SessionResponse:
    """Convert SessionModel to SessionResponse and attach persisted goal state."""
    response = _session_to_response(session)
    try:
        from flocks.session.goal import GoalManager

        goal_state = await GoalManager.get(session.id)
    except Exception as exc:
        log.warn("session.goal.response_error", {"sessionID": session.id, "error": str(exc)})
        goal_state = None

    if goal_state is not None:
        response.goal = SessionGoalResponse(
            status=goal_state.status,
            objective=goal_state.objective,
            reason=goal_state.last_reason or goal_state.paused_reason,
        )
    return response


def _require_session_read_access(session: SessionModel, user) -> None:
    if not SessionPolicy.can_read(session, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅会话所有者或受邀只读用户可访问会话")


def _require_session_write_access(session: SessionModel, user) -> None:
    if not SessionPolicy.can_write(session, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅会话所有者可写，受邀用户为只读")


async def _require_agent_usable_for_chat(agent_name: Optional[str]) -> None:
    """Validate an explicitly requested chat agent.

    The Agent page "enabled" toggle is stored as ``delegatable`` for backward
    compatibility, but product semantics treat disabled subagents as unusable
    from both delegation and direct chat selection.
    """
    if not agent_name:
        return

    from flocks.agent.registry import Agent

    agent = await Agent.get(agent_name)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Agent "{agent_name}" is not available',
        )

    tags = getattr(agent, "tags", None) or []
    if getattr(agent, "hidden", False) or "system" in tags:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Agent "{agent_name}" is not available for chat',
        )

    if getattr(agent, "mode", None) != "primary" and getattr(agent, "delegatable", True) is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Agent "{agent_name}" is disabled',
        )


def _is_hidden_from_session_manager(session: SessionModel) -> bool:
    """Return whether a session should be excluded from manager listings."""
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    return bool(metadata.get("hideFromSessionManager"))


def _share_metadata(session: SessionModel, *, shared: bool, actor_user_id: str) -> Dict[str, Any]:
    metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
    metadata["shared_local"] = shared
    if shared:
        metadata["shared_local_by"] = actor_user_id
        metadata["shared_local_at"] = int(time.time() * 1000)
    else:
        metadata.pop("shared_local_by", None)
        metadata.pop("shared_local_at", None)
    return metadata


async def _get_session_by_id_unfiltered(session_id: str) -> Optional[SessionModel]:
    """Fetch session by id while bypassing policy filtering."""
    token = set_current_auth_user(None)
    try:
        return await Session.get_by_id(session_id)
    finally:
        reset_current_auth_user(token)


async def _publish_context_usage_update(
    event_publish_callback,
    session_id: str,
    *,
    session: Optional[SessionModel] = None,
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
) -> None:
    """Best-effort SSE update for the composer context-usage meter."""
    if event_publish_callback is None:
        return
    try:
        if session is None:
            session = await _get_session_by_id_unfiltered(session_id)
        snapshot = await build_context_usage_snapshot(
            session_id,
            session=session,
            provider_id=provider_id,
            model_id=model_id,
        )
        await event_publish_callback(
            "context.usage.updated",
            snapshot.model_dump(by_alias=True),
        )
    except Exception as exc:
        log.debug("session.context_usage.publish_failed", {
            "sessionID": session_id,
            "error": str(exc),
        })


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
    response_model=List[Union[SessionResponse, SessionListItem]],
    summary="List sessions",
    description="Get a list of all sessions, sorted by most recently updated",
)
async def list_sessions(
    request: Request,
    view: Optional[Literal["list"]] = Query(None, description="Use lightweight list rows"),
    manager: Optional[bool] = Query(None, description="Apply session-manager visibility filters"),
    directory: Optional[str] = Query(None, description="Filter by project directory"),
    roots: Optional[bool] = Query(None, description="Only return root sessions (no parentID)"),
    start: Optional[int] = Query(None, description="Filter sessions updated on or after this timestamp"),
    search: Optional[str] = Query(None, description="Filter by title (case-insensitive)"),
    limit: Optional[int] = Query(None, ge=1, description="Maximum sessions to return"),
    offset: Optional[int] = Query(None, ge=0, description="Number of filtered sessions to skip"),
    category: Optional[str] = Query(None, description="Filter by category: user or task"),
) -> List[Union[SessionResponse, SessionListItem]]:
    """List all sessions with optional filters"""
    started_at = time.perf_counter()
    _current_user = require_user(request)
    list_started_at = time.perf_counter()
    all_sessions = await Session.list_all()
    list_elapsed_ms = (time.perf_counter() - list_started_at) * 1000
    
    filtered = []
    term = search.lower() if search else None
    manager_categories = {"user", "workflow", "entity-config"}
    skip_remaining = offset or 0
    
    for session in all_sessions:
        if _is_hidden_from_session_manager(session):
            continue
        if directory is not None and session.directory != directory:
            continue
        if (roots or manager) and session.parent_id:
            continue
        if start is not None and session.time.updated < start:
            continue
        if term is not None and term not in session.title.lower():
            continue
        if manager:
            if session.category not in manager_categories:
                continue
        elif category is not None:
            if session.category != category:
                continue
        elif session.category == "test":
            # exclude test sessions from the default listing
            continue

        if skip_remaining > 0:
            skip_remaining -= 1
            continue
        
        filtered.append(session)
        
        if limit is not None and len(filtered) >= limit:
            break

    if view == "list":
        response = [_session_to_list_item(s) for s in filtered]
        log_route_timing(log, "session.list.light.complete", started_at=started_at, extra={
            "total": len(all_sessions),
            "count": len(response),
            "roots": roots,
            "manager": manager,
            "limit": limit,
            "offset": offset,
            "search": bool(search),
            "category": category,
            "list_ms": round(list_elapsed_ms, 2),
        })
        return response

    response = [await _session_to_response_with_goal(s) for s in filtered]
    log_route_timing(log, "session.list.complete", started_at=started_at, extra={
        "count": len(response),
        "roots": roots,
        "manager": manager,
        "limit": limit,
        "offset": offset,
        "search": bool(search),
        "category": category,
        "list_ms": round(list_elapsed_ms, 2),
    })
    return response


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Create session",
    description="Create a new session",
)
async def create_session(http_request: Request, request: Optional[SessionCreateRequest] = None) -> SessionResponse:
    """Create a new session"""
    current_user = require_user(http_request)
    await assert_license_active(feature="session_create")
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
        owner_user_id=current_user.id,
        **({"category": request.category} if request.category else {}),
    )

    log.info("session.created", {"session_id": session.id})
    try:
        await emit_audit_event(
            "session_action",
            {
                "action": "create",
                "actor_id": current_user.username,
                "actor_name": current_user.username,
                "user_name": current_user.username,
                "username": current_user.username,
                "session_id": session.id,
                "owner_user_id": current_user.id,
                "project_id": session.project_id,
            },
        )
    except Exception:
        pass
    return await _session_to_response_with_goal(session)




@router.get(
    "/{sessionID}",
    response_model=SessionResponse,
    summary="Get session",
    description="Get session by ID",
)
async def get_session(sessionID: str, request: Request) -> SessionResponse:
    """Get session by ID"""
    _current_user = require_user(request)
    session = await _get_session_by_id_unfiltered(sessionID)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_read_access(session, _current_user)
    return await _session_to_response_with_goal(session)


@router.get(
    "/{sessionID}/context-usage",
    response_model=ContextUsageSnapshot,
    summary="Get session context usage",
    description="Get the current context usage snapshot for the composer meter",
)
async def get_session_context_usage(sessionID: str, request: Request) -> ContextUsageSnapshot:
    """Return current prompt/context usage for a session."""
    started_at = time.perf_counter()
    current_user = require_user(request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    _require_session_read_access(session, current_user)
    snapshot = await build_context_usage_snapshot(sessionID, session=session)
    log_route_timing(log, "session.context_usage.complete", started_at=started_at, extra={
        "sessionID": sessionID,
        "source": snapshot.source,
        "used_tokens": snapshot.used_tokens,
        "segments": len(snapshot.segments),
    })
    return snapshot


@router.get(
    "/{sessionID}/children",
    response_model=List[SessionResponse],
    summary="Get session children",
    description="Get all child sessions forked from the specified parent",
)
async def get_session_children(sessionID: str, request: Request) -> List[SessionResponse]:
    """Get child sessions"""
    current_user = require_user(request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_read_access(session, current_user)
    children = await Session.children(session.project_id, sessionID)
    return [await _session_to_response_with_goal(s) for s in children if SessionPolicy.can_read(s, current_user)]


class TodoInfo(BaseModel):
    """Todo item info for API compatibility"""
    model_config = ConfigDict(populate_by_name=True)
    
    id: str = Field(..., description="Todo ID")
    content: str = Field(..., description="Task description")
    activeForm: Optional[str] = Field(None, description="Active/progressive task description")
    status: str = Field(..., description="Status: pending, in_progress, completed, cancelled")
    priority: str = Field("medium", description="Priority: high, medium, low")


@router.get(
    "/{sessionID}/todo",
    response_model=List[TodoInfo],
    summary="Get session todos",
    description="Get the todo list for a session",
)
async def get_session_todos(sessionID: str, request: Request) -> List[TodoInfo]:
    """Get session todos"""
    from flocks.session.features.todo import Todo
    _current_user = require_user(request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_read_access(session, _current_user)
    try:
        todos = await Todo.get(sessionID)
        return [TodoInfo(**todo.model_dump(exclude_none=True)) for todo in todos]
    except Exception as e:
        log.warn("session.todo.read_error", {"sessionID": sessionID, "error": str(e)})
        return []


@router.post(
    "/{sessionID}/todo",
    response_model=List[TodoInfo],
    summary="Update session todos",
    description="Update the todo list for a session",
)
async def update_session_todos(sessionID: str, todos: List[TodoInfo], request: Request) -> List[TodoInfo]:
    """Update session todos"""
    from flocks.session.features.todo import Todo, TodoInfo as SessionTodoInfo
    _current_user = require_user(request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_write_access(session, _current_user)
    try:
        await Todo.update(
            sessionID,
            [SessionTodoInfo(**t.model_dump(exclude_none=True)) for t in todos],
        )
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
async def delete_session(sessionID: str, request: Request) -> bool:
    """Delete session by ID (returns true)"""
    current_user = require_user(request)
    session = await _get_session_by_id_unfiltered(sessionID)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    
    if not SessionPolicy.can_delete(session, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅会话所有者可删除会话")

    from flocks.session.goal import GoalManager
    from flocks.session.interaction_queue import InteractionQueue

    await _abort_session_processing(sessionID)
    await InteractionQueue.clear(sessionID)
    await GoalManager.clear(sessionID)
    await _wait_for_sessions_idle([sessionID])
    await _abort_and_wait_descendant_sessions(session.project_id, sessionID)
    await Session.delete(session.project_id, sessionID)

    # Best-effort cleanup of any image/file uploads materialised for this
    # session via ``_materialize_data_url_to_disk`` (see prompt_async).
    # The session DB row is gone, so the on-disk bytes are now orphaned —
    # remove them to keep the workspace tidy. We deliberately swallow any
    # filesystem errors: deletion of the session record is the contract,
    # the upload cleanup is incidental.
    try:
        import shutil
        from flocks.workspace.manager import WorkspaceManager

        ws = WorkspaceManager.get_instance()
        uploads_root = ws.resolve_workspace_path(f"uploads/{sessionID}")
        if uploads_root.exists() and uploads_root.is_dir():
            shutil.rmtree(uploads_root, ignore_errors=True)
            log.info("session.uploads.cleaned", {
                "session_id": sessionID,
                "path": str(uploads_root),
            })
    except Exception as exc:
        log.warn("session.uploads.cleanup_failed", {
            "session_id": sessionID,
            "error": str(exc),
        })

    log.info("session.deleted", {"session_id": sessionID})
    try:
        await emit_audit_event(
            "session_action",
            {
                "action": "delete",
                "actor_id": current_user.username,
                "actor_name": current_user.username,
                "user_name": current_user.username,
                "username": current_user.username,
                "session_id": sessionID,
                "owner_user_id": current_user.id,
                "project_id": session.project_id,
            },
        )
    except Exception:
        pass
    return True


async def _collect_descendant_session_ids(project_id: str, session_id: str) -> List[str]:
    """Return child session IDs in the same order Session.delete will recurse."""
    sessions = await Session.list(project_id)
    children_by_parent: Dict[str, List[str]] = {}
    for child in sessions:
        if child.parent_id is None:
            continue
        children_by_parent.setdefault(child.parent_id, []).append(child.id)

    descendants: List[str] = []
    seen: set[str] = set()

    def visit(parent_id: str) -> None:
        for child_id in children_by_parent.get(parent_id, []):
            if child_id in seen:
                continue
            seen.add(child_id)
            descendants.append(child_id)
            visit(child_id)

    visit(session_id)
    return descendants


async def _abort_and_wait_descendant_sessions(project_id: str, session_id: str) -> None:
    """Abort descendants that exist after the parent stops and wait as a batch."""
    known: set[str] = set()
    latest: List[str] = []

    for _ in range(_DESCENDANT_ABORT_SCAN_LIMIT):
        latest = await _collect_descendant_session_ids(project_id, session_id)
        new_ids = [sid for sid in latest if sid not in known]
        for descendant_id in new_ids:
            await _abort_session_processing(descendant_id)
        known.update(new_ids)

        if latest:
            await _wait_for_sessions_idle(latest)

        refreshed = await _collect_descendant_session_ids(project_id, session_id)
        if set(refreshed).issubset(known):
            return
        latest = refreshed

    log.warn("session.delete.descendants_unstable", {
        "session_id": session_id,
        "descendants": latest,
    })


class SessionUpdateRequest(BaseModel):
    """Request to update session"""
    model_config = ConfigDict(populate_by_name=True)
    
    title: Optional[str] = Field(None, description="New title")
    time: Optional[Dict[str, Any]] = Field(None, description="Time updates (archived)")
    provider: Optional[str] = Field(None, description="Pinned provider ID")
    model: Optional[str] = Field(None, description="Pinned model ID")
    model_pinned: Optional[bool] = Field(None, description="Whether provider/model are pinned for this session")


@router.patch(
    "/{sessionID}",
    response_model=SessionResponse,
    summary="Update session",
    description="Update session properties",
)
async def update_session(
    sessionID: str,
    request: SessionUpdateRequest,
    http_request: Request,
) -> SessionResponse:
    """Update session"""
    existing = await _get_session_by_id_unfiltered(sessionID)
    
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    current_user = require_user(http_request)
    _require_session_write_access(existing, current_user)

    updates = {}
    if request.title is not None:
        updates["title"] = request.title
    if request.time and request.time.get("archived") is not None:
        updates["archived"] = request.time["archived"]
    if request.provider is not None:
        updates["provider"] = request.provider
    if request.model is not None:
        updates["model"] = request.model
    if request.model_pinned is not None:
        updates["model_pinned"] = request.model_pinned
    
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
    return await _session_to_response_with_goal(session)


@router.post(
    "/{sessionID}/share-local",
    response_model=SessionResponse,
    summary="Share session locally",
    description="Share this session to all local accounts as read-only",
)
async def share_session_local(sessionID: str, http_request: Request) -> SessionResponse:
    current_user = require_user(http_request)
    token = set_current_auth_user(current_user)
    try:
        existing = await Session.get_by_id(sessionID)
    finally:
        reset_current_auth_user(token)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    _require_session_write_access(existing, current_user)
    metadata = _share_metadata(existing, shared=True, actor_user_id=current_user.id)
    session = await Session.update(
        project_id=existing.project_id,
        session_id=sessionID,
        metadata=metadata,
    )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    return await _session_to_response_with_goal(session)


@router.post(
    "/{sessionID}/unshare-local",
    response_model=SessionResponse,
    summary="Unshare session locally",
    description="Cancel local sharing of this session",
)
async def unshare_session_local(sessionID: str, http_request: Request) -> SessionResponse:
    current_user = require_user(http_request)
    token = set_current_auth_user(current_user)
    try:
        existing = await Session.get_by_id(sessionID)
    finally:
        reset_current_auth_user(token)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    _require_session_write_access(existing, current_user)
    metadata = _share_metadata(existing, shared=False, actor_user_id=current_user.id)
    session = await Session.update(
        project_id=existing.project_id,
        session_id=sessionID,
        metadata=metadata,
    )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    return await _session_to_response_with_goal(session)


# =============================================================================
# Session Actions
# =============================================================================

async def _abort_session_processing(sessionID: str) -> bool:
    """Abort active processing for a session and notify subscribers.

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


@router.post(
    "/{sessionID}/abort",
    summary="Abort session",
    description="Abort an active session and stop any ongoing processing",
)
async def abort_session(sessionID: str, http_request: Request = None) -> bool:
    """Abort session processing."""
    if http_request is not None:
        current_user = require_user(http_request)
        session = await _get_session_by_id_unfiltered(sessionID)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {sessionID} not found",
            )
        _require_session_write_access(session, current_user)
    return await _abort_session_processing(sessionID)


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
async def initialize_session(sessionID: str, request: InitRequest, http_request: Request) -> bool:
    """Initialize session"""
    from flocks.session.runner import SessionRunner

    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_write_access(session, current_user)

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
async def fork_session(sessionID: str, http_request: Request, request: Optional[ForkRequest] = None) -> SessionResponse:
    """Fork session"""
    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_write_access(session, current_user)

    message_id = request.messageID if request else None
    forked = await Session.fork(session.project_id, sessionID, message_id)
    
    log.info("session.forked", {"from": sessionID, "to": forked.id})
    return await _session_to_response_with_goal(forked)


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
async def summarize_session(sessionID: str, request: SummarizeRequest, http_request: Request) -> bool:
    """Summarize session"""
    from flocks.project.bootstrap import instance_bootstrap
    from flocks.project.instance import Instance
    from flocks.server.routes.event import publish_event
    from flocks.session.message import Message, MessageRole

    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_write_access(session, current_user)

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
async def revert_session(sessionID: str, request: RevertRequest, http_request: Request) -> SessionResponse:
    """Revert session"""
    from flocks.session.lifecycle.revert import SessionRevert

    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_write_access(session, current_user)

    updated = await SessionRevert.revert(
        session_id=sessionID,
        message_id=request.messageID,
        part_id=request.partID,
    )
    
    log.info("session.reverted", {"session_id": sessionID, "message_id": request.messageID})
    return await _session_to_response_with_goal(updated)


@router.post(
    "/{sessionID}/unrevert",
    response_model=SessionResponse,
    summary="Unrevert session",
    description="Restore previously reverted messages",
)
async def unrevert_session(sessionID: str, http_request: Request) -> SessionResponse:
    """Unrevert session"""
    from flocks.session.lifecycle.revert import SessionRevert

    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_write_access(session, current_user)

    updated = await SessionRevert.unrevert(session_id=sessionID)
    
    log.info("session.unreverted", {"session_id": sessionID})
    return await _session_to_response_with_goal(updated)


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
    display_text: Optional[str] = Field(None, alias="displayText", description="User-visible message text")
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
    # File / image attachment fields (populated when ``type == "file"``).
    url: Optional[str] = None
    mime: Optional[str] = None
    filename: Optional[str] = None


class MessageWithParts(BaseModel):
    """Message with parts for API compatibility"""
    info: MessageInfo
    parts: List[MessagePartInfo] = []


class MessagePage(BaseModel):
    """Paged message response used by the WebUI session page."""
    sessionID: str
    items: List[MessageWithParts] = []
    hasMore: bool = False
    nextBefore: Optional[str] = None


class MessageEditRequest(BaseModel):
    """Request to edit message text."""

    text: str = Field(..., description="Updated raw text content")
    partID: Optional[str] = Field(None, description="Specific text part ID to edit")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def _part_to_response_info(
    part: Any,
    *,
    session_id: str,
    message_id: str,
    index: int = 0,
) -> MessagePartInfo:
    text_value = getattr(part, "text", None) if part.type in ("text", "reasoning", "thinking") else None

    url_value = getattr(part, "url", None) if part.type == "file" else None

    state_value = None
    if part.type == "tool":
        raw_state = getattr(part, "state", None)
        if raw_state is not None:
            if hasattr(raw_state, "model_dump"):
                state_value = raw_state.model_dump()
            elif isinstance(raw_state, dict):
                state_value = raw_state

    return MessagePartInfo(
        id=part.id if hasattr(part, "id") else f"{message_id}_part_{index}",
        messageID=message_id,
        sessionID=session_id,
        type=part.type,
        text=text_value,
        synthetic=getattr(part, "synthetic", None),
        tool=getattr(part, "tool", None) if part.type == "tool" else None,
        state=state_value,
        callID=getattr(part, "callID", None) if part.type == "tool" else None,
        metadata=getattr(part, "metadata", None),
        url=url_value,
        mime=getattr(part, "mime", None) if part.type == "file" else None,
        filename=getattr(part, "filename", None) if part.type == "file" else None,
    )


async def _message_to_response_info(msg: Any, *, cwd: str) -> MessageInfo:
    if msg.role == "user":
        model_dict = getattr(msg, "model", None)
        if model_dict and isinstance(model_dict, dict):
            model_info = model_dict
        else:
            try:
                from flocks.agent.registry import Agent

                default_agent = await Agent.default_agent()
                agent_obj = await Agent.get(default_agent)
                if agent_obj and hasattr(agent_obj, "model") and agent_obj.model:
                    model_info = agent_obj.model
                else:
                    model_info = {"providerID": "openai", "modelID": "gpt-4-turbo-preview"}
            except Exception:
                model_info = {"providerID": "openai", "modelID": "gpt-4-turbo-preview"}

        return UserMessageInfo(
            id=msg.id,
            sessionID=msg.sessionID,
            role="user",
            time=msg.time,
            agent=getattr(msg, "agent", None) or DEFAULT_AGENT,
            model=model_info,
            compacted=getattr(msg, "compacted", None),
        )

    tokens_raw = getattr(msg, "tokens", None)
    if tokens_raw is not None and hasattr(tokens_raw, "model_dump"):
        tokens_dict = tokens_raw.model_dump()
    elif isinstance(tokens_raw, dict):
        tokens_dict = tokens_raw
    else:
        tokens_dict = {
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        }

    path_raw = getattr(msg, "path", None)
    if path_raw is not None and hasattr(path_raw, "model_dump"):
        path_dict = path_raw.model_dump()
    elif isinstance(path_raw, dict):
        path_dict = path_raw
    else:
        path_dict = {"cwd": cwd, "root": cwd}

    return AssistantMessageInfo(
        id=msg.id,
        sessionID=msg.sessionID,
        role="assistant",
        time=msg.time,
        parentID=getattr(msg, "parentID", None),
        modelID=getattr(msg, "modelID", None) or "claude-sonnet-4-5-20250929",
        providerID=getattr(msg, "providerID", None) or "anthropic",
        mode=getattr(msg, "agent", None) or DEFAULT_AGENT,
        agent=getattr(msg, "agent", None) or DEFAULT_AGENT,
        path=path_dict,
        cost=getattr(msg, "cost", 0.0) or 0.0,
        tokens=tokens_dict,
        error=getattr(msg, "error", None),
        finish=getattr(msg, "finish", None),
        compacted=getattr(msg, "compacted", None),
    )


async def _message_with_parts_to_response(
    msg_with_parts: Any,
    *,
    session_id: str,
    cwd: str,
) -> MessageWithParts:
    msg = msg_with_parts.info
    info = await _message_to_response_info(msg, cwd=cwd)
    parts = [
        _part_to_response_info(
            part,
            session_id=session_id,
            message_id=msg.id,
            index=i,
        )
        for i, part in enumerate(msg_with_parts.parts)
    ]
    return MessageWithParts(info=info, parts=parts)


@router.get(
    "/{sessionID}/message",
    response_model=Union[List[MessageWithParts], MessagePage],
    summary="Get session messages",
    description="Get all messages in a session",
)
async def get_session_messages(
    sessionID: str,
    http_request: Request,
    limit: Optional[int] = Query(None, ge=1, description="Maximum messages to return"),
    before: Optional[str] = Query(None, description="Load messages before this message id"),
    page: bool = Query(False, description="Return paged response metadata"),
    include_archived: bool = Query(True, description="Include compacted/archived messages"),
) -> Union[List[MessageWithParts], MessagePage]:
    """Get session messages"""
    from flocks.session.message import Message
    import os

    started_at = time.perf_counter()
    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    _require_session_read_access(session, current_user)

    try:
        from flocks.session.orphan_tools import abort_orphan_running_parts_in_messages
        from flocks.session.core.status import SessionStatus

        paged = page or before is not None
        if paged or limit:
            page_limit = limit or DEFAULT_MESSAGE_PAGE_LIMIT
            messages_with_parts, has_more, next_before = await Message.list_recent_with_parts(
                sessionID,
                limit=page_limit,
                before=before,
                include_archived=include_archived,
            )
        else:
            messages_with_parts = await Message.list_with_parts(
                sessionID,
                include_archived=include_archived,
            )
            has_more = False
            next_before = None

        if sessionID not in SessionStatus.get_busy_session_ids():
            await abort_orphan_running_parts_in_messages(sessionID, messages_with_parts)

        cwd = os.getcwd()
        result = [
            await _message_with_parts_to_response(
                msg_with_parts,
                session_id=sessionID,
                cwd=cwd,
            )
            for msg_with_parts in messages_with_parts
        ]

        payload_bytes = len(_json_bytes([item.model_dump() for item in result]))
        log_route_timing(log, "session.messages.page.complete", started_at=started_at, extra={
            "sessionID": sessionID,
            "count": len(result),
            "parts": sum(len(message.parts) for message in result),
            "paged": paged,
            "limit": limit,
            "before": bool(before),
            "has_more": has_more,
            "payload_bytes": payload_bytes,
        })

        if paged:
            return MessagePage(
                sessionID=sessionID,
                items=result,
                hasMore=has_more,
                nextBefore=next_before,
            )
        return result
    except Exception as e:
        log.error("session.messages.error", {"error": str(e), "sessionID": sessionID})
        if page or before is not None:
            return MessagePage(sessionID=sessionID, items=[], hasMore=False, nextBefore=None)
        return []


@router.get(
    "/{sessionID}/message/{messageID}",
    response_model=MessageWithParts,
    summary="Get message",
    description="Get a specific message by ID",
)
async def get_message(sessionID: str, messageID: str, http_request: Request) -> MessageWithParts:
    """Get single message"""
    from flocks.session.message import Message
    import os

    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    _require_session_read_access(session, current_user)

    msg_with_parts = await Message.get_with_parts_lazy(sessionID, messageID)
    if msg_with_parts:
        return await _message_with_parts_to_response(
            msg_with_parts,
            session_id=sessionID,
            cwd=os.getcwd(),
        )
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Message {messageID} not found in session {sessionID}"
    )


@router.delete(
    "/{sessionID}/message/{messageID}/part/{partID}",
    summary="Delete message part",
    description="Delete a specific part from a message",
)
async def delete_message_part(sessionID: str, messageID: str, partID: str, http_request: Request) -> bool:
    """Delete message part"""
    from flocks.session.message import Message

    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    _require_session_write_access(session, current_user)

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
    http_request: Request,
) -> MessagePartInfo:
    """Update message part"""
    if body.id != partID or body.messageID != messageID or body.sessionID != sessionID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Part IDs do not match URL parameters"
        )
    
    from flocks.session.message import Message

    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    _require_session_write_access(session, current_user)

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
    """Schedule a background coroutine with its opaque execution context."""
    import asyncio
    from flocks.hooks.execution import current_execution_context, execution_context_scope

    execution_context = current_execution_context()

    async def _guarded_coro() -> None:
        with execution_context_scope(execution_context):
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
    # Replay should follow the model that is active *now* for this session
    # (current session pin / current default / current agent override), not the
    # historical model stored on the original user message being replayed.
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
    final_tokens = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}

    if result.last_message:
        assistant_message_id = result.last_message.id
        final_content = await Message.get_text_content(result.last_message)
        final_tokens = token_usage_to_dict(getattr(result.last_message, "tokens", None))
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
            "tokens": final_tokens,
            "finish": finish_reason,
        }
    })
    await _publish_context_usage_update(
        publish_event,
        session_id,
        session=session,
        provider_id=provider_id,
        model_id=model_id,
    )

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
    http_request: Request,
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

    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    current_user = require_user(http_request)
    _require_session_write_access(session, current_user)

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
    http_request: Request,
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

    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    current_user = require_user(http_request)
    _require_session_write_access(session, current_user)

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
async def send_session_message(sessionID: str, request: PromptRequest, http_request: Request):
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
    
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    current_user = require_user(http_request)
    _require_session_write_access(session, current_user)
    
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

    async def publish_current_context_usage() -> None:
        await _publish_context_usage_update(
            event_publish_callback,
            session_id,
            session=session,
            provider_id=provider_id,
            model_id=model_id,
        )

    try:
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
    except Exception:
        await publish_current_context_usage()
        raise
    if result == "stop":
        # ``SessionCompaction.process`` swallows the underlying provider
        # exception (so the loop path stays simple) but stashes the
        # user-facing message via ``_record_compaction_error``.  Surface
        # it verbatim here so the SSE ``session.error`` payload — and
        # therefore the front-end toast — shows the provider's original
        # error text instead of an opaque "Compaction failed".
        await publish_current_context_usage()
        detail = pop_last_compaction_error(session_id) or "Compaction failed"
        raise RuntimeError(detail)
    await publish_current_context_usage()
    return agent_name, provider_id, model_id


# JSON repair utilities — delegated to flocks.utils.json_repair
_parse_json_robust = parse_json_robust
_repair_json_string = repair_truncated_json


def _check_session_aborted(sessionID: str, checkpoint: str, step: int, **extra_context) -> bool:
    """
    Check whether the session has been aborted.
    
    Args:
        sessionID: Session ID
        checkpoint: Checkpoint name, such as "before_step", "in_stream", or "skip_tool_processing".
        step: Current step number.
        **extra_context: Additional log context.
    
    Returns:
        True when the session has been aborted and execution should stop.
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
    has_non_text_parts = False
    for part in request.parts:
        part_type = part.get("type")
        if part_type == "text":
            text_content += part.get("text", "")
        elif part_type:
            has_non_text_parts = True

    # Allow messages that only contain attachments (e.g. an image with no caption)
    if not text_content and not has_non_text_parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message must contain text or at least one attachment"
        )

    log.info("session.message.received", {
        "sessionID": sessionID,
        "content_length": len(text_content),
        "has_non_text_parts": has_non_text_parts,
    })
    
    # ------------------------------------------------------------------
    # 2. Resolve agent and model (5-level priority)
    # ------------------------------------------------------------------
    await _require_agent_usable_for_chat(request.agent)
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

    # display_text (optional) is UI-only. The stored text part must stay as the
    # real prompt so SessionLoop, hooks, title generation, and queued prompts keep
    # seeing the same content the model receives.
    display_text = getattr(request, "display_text", None)
    display_metadata = {"displayText": display_text} if display_text else None

    _is_no_reply = bool(request.noReply)
    user_message = await Message.create(
        session_id=sessionID,
        role=MessageRole.USER,
        content=text_content,
        id=user_message_id,
        time={"created": now_ms},
        agent=agent_name,
        model={"providerID": provider_id, "modelID": model_id},
        part_id=user_part_id,
        part_metadata=display_metadata,
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
        "text": text_content,
        "time": {"start": now_ms},
    }
    if display_metadata:
        _part_event["metadata"] = display_metadata
    if _is_no_reply:
        _part_event["synthetic"] = True
    await publish_event("message.part.updated", {"part": _part_event})

    # ------------------------------------------------------------------
    # 3a. Persist any non-text parts (file/image attachments) so the
    #     SessionLoop sees them when building the LLM request. Without
    #     this, file parts sent from clients would be silently dropped.
    #
    #     For ``data:`` URLs we materialize the bytes to disk and store
    #     a ``file://`` reference instead. Keeping the raw base64 string
    #     in the message database is dangerous: any code path that later
    #     stringifies the part (legacy LLM adapters, logging, compaction)
    #     would tokenize hundreds of KB of base64 and blow past the
    #     model's context window.
    # ------------------------------------------------------------------
    from flocks.session.message import FilePart

    def _materialize_data_url_to_disk(
        data_url: str, mime_hint: str, filename_hint: Optional[str]
    ) -> str:
        """Decode a ``data:`` URL to ``~/.flocks/workspace/uploads/<session>/...``.

        Returns a ``file://`` URL pointing at the persisted file. On failure
        the original ``data:`` URL is returned unchanged (older code paths
        still cope with that, just with the now-known token-cost penalty).
        """
        try:
            import base64
            from flocks.workspace.manager import WorkspaceManager

            header, _, encoded = data_url.partition(",")
            if not encoded:
                return data_url
            raw_bytes = base64.b64decode(encoded)

            ws = WorkspaceManager.get_instance()
            # Use resolve_workspace_path to guard against path traversal if
            # sessionID were ever user-controlled (e.g. ../../../tmp/x).
            uploads_root = ws.resolve_workspace_path(f"uploads/{sessionID}")
            uploads_root.mkdir(parents=True, exist_ok=True)

            ext_map = {
                "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
                "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
                "application/pdf": ".pdf",
            }
            ext = ext_map.get(mime_hint, "")
            if not ext and filename_hint:
                _, _, tail = filename_hint.rpartition(".")
                if tail.lower() in _UPLOAD_SAFE_EXTS:
                    ext = "." + tail.lower()
            unique_name = f"{Identifier.create('part')}{ext}"
            target = uploads_root / unique_name
            target.write_bytes(raw_bytes)
            return target.resolve().as_uri()
        except Exception as exc:
            log.warn("session.message.file_part.materialize_failed", {
                "sessionID": sessionID,
                "error": str(exc),
            })
            return data_url

    for raw_part in request.parts or []:
        part_type = raw_part.get("type")
        if part_type == "text":
            continue  # Already stored as the message's TextPart above
        if part_type == "file":
            url = raw_part.get("url") or ""
            mime = raw_part.get("mime") or ""
            if not url or not mime:
                log.warn("session.message.file_part.skipped", {
                    "sessionID": sessionID,
                    "reason": "missing url or mime",
                })
                continue
            # Materialize ``data:`` URLs to disk before persisting the part.
            if url.startswith("data:"):
                url = _materialize_data_url_to_disk(url, mime, raw_part.get("filename"))
            file_part_id = raw_part.get("id") or Identifier.create("part")
            file_part = FilePart(
                id=file_part_id,
                sessionID=sessionID,
                messageID=user_message_id,
                mime=mime,
                filename=raw_part.get("filename"),
                url=url,
            )
            await Message.add_part(sessionID, user_message_id, file_part)
            await publish_event("message.part.updated", {
                "part": {
                    "id": file_part_id,
                    "messageID": user_message_id,
                    "sessionID": sessionID,
                    "type": "file",
                    "mime": mime,
                    "filename": raw_part.get("filename"),
                    "url": url,
                    "time": {"start": now_ms},
                }
            })

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

        await _publish_context_usage_update(
            publish_event,
            sessionID,
            session=session,
            provider_id=provider_id,
            model_id=model_id,
        )

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
    final_tokens = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    
    if result.last_message:
        assistant_message_id = result.last_message.id
        final_content = await Message.get_text_content(result.last_message)
        final_tokens = token_usage_to_dict(getattr(result.last_message, "tokens", None))
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
            "tokens": final_tokens,
            "finish": finish_reason,
        }
    })
    await _publish_context_usage_update(
        publish_event,
        sessionID,
        session=session,
        provider_id=provider_id,
        model_id=model_id,
    )
    
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


def _extract_text_from_parts(parts: List[Dict[str, Any]]) -> str:
    return "".join(part.get("text", "") for part in parts if part.get("type") == "text")


def _replace_text_parts(
    parts: Optional[List[Dict[str, Any]]],
    text: str,
    text_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    updated_parts: List[Dict[str, Any]] = []
    replaced = False
    for part in parts or []:
        if part.get("type") == "text" and not replaced:
            next_part = dict(part)
            next_part["text"] = text
            if text_metadata:
                merged_metadata = dict(next_part.get("metadata") or {})
                merged_metadata.update(text_metadata)
                next_part["metadata"] = merged_metadata
            updated_parts.append(next_part)
            replaced = True
            continue
        if part.get("type") == "text":
            continue
        updated_parts.append(dict(part))

    if not replaced:
        next_part: Dict[str, Any] = {"type": "text", "text": text}
        if text_metadata:
            next_part["metadata"] = dict(text_metadata)
        updated_parts.insert(0, next_part)
    return updated_parts


def _coerce_model_for_prompt_request(model: Any):
    import types

    if not model:
        return None
    if isinstance(model, str):
        if "/" not in model:
            return None
        provider_id, model_id = model.split("/", 1)
        return types.SimpleNamespace(providerID=provider_id, modelID=model_id)
    if isinstance(model, dict):
        provider_id = model.get("providerID") or model.get("provider_id")
        model_id = model.get("modelID") or model.get("model_id")
        if provider_id and model_id:
            return types.SimpleNamespace(providerID=provider_id, modelID=model_id)
        return None
    return model


def _prompt_queue_lock(session_id: str) -> asyncio.Lock:
    if not hasattr(router, "_prompt_queue_drain_locks"):
        router._prompt_queue_drain_locks = {}
    locks = router._prompt_queue_drain_locks
    lock = locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[session_id] = lock
    return lock


def _is_prompt_chain_active(session_id: str) -> bool:
    return session_id in getattr(router, "_prompt_queue_active_sessions", set())


def _set_prompt_chain_active(session_id: str, active: bool) -> None:
    if not hasattr(router, "_prompt_queue_active_sessions"):
        router._prompt_queue_active_sessions = set()
    active_sessions = router._prompt_queue_active_sessions
    if active:
        active_sessions.add(session_id)
    else:
        active_sessions.discard(session_id)


async def _publish_prompt_queue(session_id: str) -> None:
    from flocks.server.routes.event import publish_event
    from flocks.session.interaction_queue import InteractionQueue

    items = await InteractionQueue.list(session_id)
    await publish_event("session.prompt_queue.updated", {
        "sessionID": session_id,
        "items": [item.model_dump() for item in items],
    })


def _materialize_queued_parts(session_id: str, parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Persist queued data URLs so large base64 payloads do not sit in memory."""
    prepared: List[Dict[str, Any]] = []
    for part in parts:
        next_part = dict(part)
        url = next_part.get("url")
        if next_part.get("type") == "file" and isinstance(url, str) and url.startswith("data:"):
            mime = next_part.get("mime") or ""
            filename = next_part.get("filename")
            next_part["url"] = _materialize_data_url_part(session_id, url, mime, filename)
        prepared.append(next_part)
    return prepared


def _materialize_data_url_part(
    session_id: str,
    data_url: str,
    mime_hint: str,
    filename_hint: Optional[str],
) -> str:
    try:
        import base64
        from flocks.workspace.manager import WorkspaceManager
        from flocks.utils.id import Identifier

        _header, _sep, encoded = data_url.partition(",")
        if not encoded:
            return data_url
        raw_bytes = base64.b64decode(encoded)
        ws = WorkspaceManager.get_instance()
        uploads_root = ws.resolve_workspace_path(f"uploads/{session_id}")
        uploads_root.mkdir(parents=True, exist_ok=True)

        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
            "application/pdf": ".pdf",
        }
        ext = ext_map.get(mime_hint, "")
        if not ext and filename_hint:
            _, _, tail = filename_hint.rpartition(".")
            if tail.lower() in _UPLOAD_SAFE_EXTS:
                ext = "." + tail.lower()
        target = uploads_root / f"{Identifier.create('part')}{ext}"
        target.write_bytes(raw_bytes)
        return target.resolve().as_uri()
    except Exception as exc:
        log.warn("session.prompt_queue.materialize_failed", {
            "sessionID": session_id,
            "error": str(exc),
        })
        return data_url


def _event_from_queued_prompt(item, working_directory: str):
    from flocks.input.events import UserInputEvent

    return UserInputEvent(
        source_type="webui",
        sessionID=item.sessionID,
        text=_extract_text_from_parts(item.parts),
        parts=[dict(part) for part in item.parts],
        agent=item.agent,
        model=item.model,
        variant=item.variant,
        display_text=item.display_text,
        messageID=item.messageID,
        noReply=item.noReply,
        mockReply=item.mockReply,
        tools=item.tools,
        system=item.system,
        working_directory=working_directory,
    )


async def _drain_prompt_queue_locked(session_id: str, working_directory: str) -> bool:
    from flocks.hooks.execution import execution_context_scope
    from flocks.project.bootstrap import instance_bootstrap
    from flocks.project.instance import Instance
    from flocks.session.interaction_queue import InteractionQueue
    from flocks.session.session_loop import SessionLoop

    while True:
        if SessionLoop.is_running(session_id):
            return False

        item = await InteractionQueue.pop_next(session_id)
        if item is None:
            await _publish_prompt_queue(session_id)
            return True

        await _publish_prompt_queue(session_id)
        session = await Session.get_by_id(session_id)
        if not session:
            log.warn("session.prompt_queue.session_missing", {"sessionID": session_id, "queueID": item.id})
            continue

        event = _event_from_queued_prompt(item, working_directory)
        log.info("session.prompt_queue.dispatch", {
            "sessionID": session_id,
            "queueID": item.id,
        })
        # A queue item may be dispatched by a different worker task than the
        # request that submitted it. Restore the item's captured context (e.g.
        # trusted identity transfer) and never inherit unrelated worker state.
        with execution_context_scope(item.execution_context or {}, inherit=False):
            await Instance.provide(
                directory=working_directory,
                init=instance_bootstrap,
                fn=lambda: _dispatch_sse_input(
                    session_id, session, event, working_directory
                ),
            )


async def _run_prompt_event_chain(session_id: str, session, event, working_directory: str) -> None:
    from flocks.project.bootstrap import instance_bootstrap
    from flocks.project.instance import Instance

    try:
        async with _prompt_queue_lock(session_id):
            dispatch_failed = False
            try:
                await Instance.provide(
                    directory=working_directory,
                    init=instance_bootstrap,
                    fn=lambda: _dispatch_sse_input(session_id, session, event, working_directory),
                )
            except Exception:
                dispatch_failed = True
                raise
            finally:
                try:
                    await _drain_prompt_queue_locked(session_id, working_directory)
                except Exception as drain_exc:
                    if dispatch_failed:
                        log.error("session.prompt_queue.drain_after_error_failed", {
                            "sessionID": session_id,
                            "error": str(drain_exc),
                        })
                    else:
                        raise
    finally:
        _set_prompt_chain_active(session_id, False)


async def _schedule_prompt_queue_drain(session_id: str, working_directory: str) -> None:
    max_attempts = 80
    retry_interval_s = 0.25

    async def _run() -> None:
        try:
            async with _prompt_queue_lock(session_id):
                for attempt in range(max_attempts):
                    completed = await _drain_prompt_queue_locked(session_id, working_directory)
                    if completed:
                        return
                    await asyncio.sleep(retry_interval_s)
                log.warn("session.prompt_queue.drain_retry_exhausted", {
                    "sessionID": session_id,
                    "attempts": max_attempts,
                })
        finally:
            _set_prompt_chain_active(session_id, False)

    _set_prompt_chain_active(session_id, True)
    _schedule_background_coro(
        _run(),
        session_id=session_id,
        action="prompt_queue.drain",
    )


async def _wait_for_session_idle(session_id: str, timeout_s: float = 5.0) -> None:
    await _wait_for_sessions_idle([session_id], timeout_s=timeout_s)


async def _wait_for_sessions_idle(session_ids: List[str], timeout_s: float = 5.0) -> None:
    from flocks.session.session_loop import SessionLoop

    pending = set(session_ids)
    if not pending:
        return

    deadline = time.time() + timeout_s
    while pending:
        running = {sid for sid in pending if SessionLoop.is_running(sid)}
        if not running:
            return
        now = time.time()
        if now >= deadline:
            log.warn("session.wait_idle.timeout", {
                "session_ids": sorted(running),
                "timeout_s": timeout_s,
            })
            return
        pending = running
        await asyncio.sleep(min(0.05, max(0.0, deadline - now)))


def _build_prompt_request_from_event(event, prompt_text: str, display_text: Optional[str] = None):
    import types

    return types.SimpleNamespace(
        parts=_replace_text_parts(event.parts, prompt_text, event.metadata or None),
        display_text=display_text,
        agent=event.agent,
        model=_coerce_model_for_prompt_request(event.model),
        variant=event.variant,
        messageID=event.message_id,
        mockReply=event.mock_reply,
        noReply=event.no_reply,
        tools=event.tools,
        system=event.system,
    )


async def _dispatch_sse_input(sessionID: str, session, event, working_directory: str) -> None:
    import time as _time

    from flocks.input.dispatcher import dispatch_user_input
    from flocks.input.output import SSEOutputSink
    from flocks.server.routes.event import publish_event
    from flocks.session.message import Message, MessageRole
    from flocks.utils.id import Identifier

    agent_name = event.agent or "rex"

    async def _create_user_message(
        user_text: str,
        model_info: Optional[Dict[str, str]] = None,
        *,
        agent_override: Optional[str] = None,
    ) -> str:
        now_ms = int(_time.time() * 1000)
        user_msg_id = event.message_id or Identifier.create("message")
        user_part_id = Identifier.create("part")
        message_agent = agent_override or agent_name
        await Message.create(
            session_id=sessionID,
            role=MessageRole.USER,
            content=user_text,
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
                "text": user_text,
                "time": {"start": now_ms},
            }
        })
        return user_msg_id

    async def _publish_direct_response(output_event, text: str) -> None:
        user_text = output_event.user_visible_text
        parent_msg_id = await _create_user_message(user_text)
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
                "tokens": {
                    "input": 0,
                    "output": 0,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
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
        await _publish_context_usage_update(
            publish_event,
            sessionID,
            session=session,
            provider_id="builtin",
            model_id="command",
        )

    async def _run_llm(output_event, prompt_text: str, display_text: Optional[str] = None) -> None:
        request = _build_prompt_request_from_event(output_event, prompt_text, display_text)
        await _process_session_message(sessionID, session, request, working_directory)

    async def _clear_history() -> None:
        await _clear_session_history(sessionID)

    async def _run_session_control(output_event, parsed) -> bool:
        if parsed.canonical_name != "compact":
            return False

        focus_instruction = parsed.args.strip() or None
        compact_agent, compact_provider_id, compact_model_id = await _resolve_compaction_context(
            sessionID,
            requested_agent=output_event.agent,
            requested_model=output_event.model,
        )
        parent_msg_id = await _create_user_message(
            output_event.user_visible_text,
            {
                "providerID": compact_provider_id,
                "modelID": compact_model_id,
            },
            agent_override=compact_agent,
        )
        await _run_session_compaction(
            sessionID,
            requested_agent=compact_agent,
            explicit_provider_id=compact_provider_id,
            explicit_model_id=compact_model_id,
            parent_message_id=parent_msg_id,
            auto=False,
            event_publish_callback=publish_event,
            focus_instruction=focus_instruction,
        )
        return True

    sink = SSEOutputSink(
        "webui",
        direct_response=_publish_direct_response,
        run_llm=_run_llm,
        session_control=_run_session_control,
        clear_history=_clear_history,
    )
    result = await dispatch_user_input(event, sink)
    if result.command_name == "goal" and result.action == "llm":
        from flocks.session.goal import GoalManager

        state = await GoalManager.get(sessionID)
        if state is not None:
            await publish_event("session.goal.updated", {
                "sessionID": sessionID,
                "status": state.status,
                "objective": state.objective,
                "reason": state.last_reason,
            })


class PromptQueueUpdateRequest(BaseModel):
    text: str = Field(..., description="Updated queued prompt text")


async def _enqueue_prompt_request(
    session_id: str,
    request: PromptRequest,
):
    from flocks.session.interaction_queue import InteractionQueue
    from flocks.hooks.execution import current_execution_context

    await _require_agent_usable_for_chat(request.agent)
    model = request.model.model_dump(by_alias=True) if request.model else None
    parts = _materialize_queued_parts(session_id, [dict(part) for part in request.parts])
    execution_context = current_execution_context()
    return await InteractionQueue.enqueue(
        session_id,
        parts=parts,
        agent=request.agent,
        model=model,
        variant=request.variant,
        display_text=request.display_text,
        message_id=request.messageID,
        no_reply=request.noReply,
        mock_reply=request.mockReply,
        tools=request.tools,
        system=request.system,
        execution_context=execution_context,
    )


@router.get(
    "/{sessionID}/prompt_queue",
    summary="List queued prompts",
    description="List pending non-blocking prompts for a session",
)
async def list_prompt_queue(sessionID: str) -> Dict[str, Any]:
    from flocks.session.interaction_queue import InteractionQueue

    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    items = await InteractionQueue.list(sessionID)
    return {"sessionID": sessionID, "items": [item.model_dump() for item in items]}


@router.post(
    "/{sessionID}/prompt_queue",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue prompt",
    description="Queue a prompt without writing it to the formal message history",
)
async def enqueue_prompt(sessionID: str, request: PromptRequest) -> Dict[str, Any]:
    from flocks.session.interaction_queue import QueueFullError

    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    await _require_agent_usable_for_chat(request.agent)
    try:
        item = await _enqueue_prompt_request(sessionID, request)
    except QueueFullError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _publish_prompt_queue(sessionID)
    return {"status": "queued", "sessionID": sessionID, "queueID": item.id}


@router.patch(
    "/{sessionID}/prompt_queue/{queueID}",
    summary="Update queued prompt",
    description="Update the text part of a queued prompt",
)
async def update_prompt_queue_item(
    sessionID: str,
    queueID: str,
    request: PromptQueueUpdateRequest,
) -> Dict[str, Any]:
    from flocks.session.interaction_queue import InteractionQueue, QueueItemNotFoundError

    text = request.text.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Queued prompt text cannot be empty",
        )
    try:
        item = await InteractionQueue.update_text(sessionID, queueID, text)
    except QueueItemNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await _publish_prompt_queue(sessionID)
    return {"status": "updated", "sessionID": sessionID, "item": item.model_dump()}


@router.delete(
    "/{sessionID}/prompt_queue/{queueID}",
    summary="Remove queued prompt",
    description="Remove a queued prompt before it executes",
)
async def remove_prompt_queue_item(sessionID: str, queueID: str) -> Dict[str, Any]:
    from flocks.session.interaction_queue import InteractionQueue, QueueItemNotFoundError

    try:
        await InteractionQueue.remove(sessionID, queueID)
    except QueueItemNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await _publish_prompt_queue(sessionID)
    return {"status": "removed", "sessionID": sessionID, "queueID": queueID}


@router.post(
    "/{sessionID}/prompt_queue/{queueID}/run_now",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run queued prompt now",
    description="Abort the current prompt and run the selected queued prompt next",
)
async def run_prompt_queue_item_now(sessionID: str, queueID: str) -> Dict[str, Any]:
    import os

    from flocks.session.interaction_queue import InteractionQueue, QueueItemNotFoundError
    from flocks.session.session_loop import SessionLoop

    session = await Session.get_by_id(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    working_directory = session.directory or os.getcwd()
    try:
        await InteractionQueue.promote(sessionID, queueID)
    except QueueItemNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await _publish_prompt_queue(sessionID)

    if SessionLoop.is_running(sessionID):
        await abort_session(sessionID)
        await _wait_for_session_idle(sessionID)

    await _schedule_prompt_queue_drain(sessionID, working_directory)
    return {"status": "accepted", "sessionID": sessionID, "queueID": queueID}


@router.post(
    "/{sessionID}/prompt_async",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send async message",
    description="Send a message asynchronously (returns immediately)",
)
async def send_session_message_async(
    sessionID: str,
    request: PromptRequest,
    http_request: Request = None,
):
    """Send message asynchronously - returns 202 immediately, response via SSE"""
    import os
    from flocks.input.events import UserInputEvent
    from flocks.session.interaction_queue import InteractionQueue, QueueFullError
    from flocks.session.session_loop import SessionLoop

    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    if http_request is not None:
        current_user = require_user(http_request)
        _require_session_write_access(session, current_user)
    
    working_directory = session.directory or os.getcwd()
    await _require_agent_usable_for_chat(request.agent)
    
    log.info("session.prompt_async.accepted", {
        "sessionID": sessionID,
        "directory": working_directory,
    })

    event = UserInputEvent(
        source_type="webui",
        sessionID=sessionID,
        text=_extract_text_from_parts(request.parts),
        parts=[dict(part) for part in request.parts],
        agent=request.agent,
        model=request.model.model_dump(by_alias=True) if request.model else None,
        variant=request.variant,
        display_text=request.display_text,
        messageID=request.messageID,
        noReply=request.noReply,
        mockReply=request.mockReply,
        tools=request.tools,
        system=request.system,
        working_directory=working_directory,
    )

    existing_queue = await InteractionQueue.list(sessionID)
    if SessionLoop.is_running(sessionID) or existing_queue or _is_prompt_chain_active(sessionID):
        try:
            item = await _enqueue_prompt_request(sessionID, request)
        except QueueFullError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        await _publish_prompt_queue(sessionID)
        if not SessionLoop.is_running(sessionID):
            await _schedule_prompt_queue_drain(sessionID, working_directory)
        return {"status": "queued", "sessionID": sessionID, "queueID": item.id}

    _set_prompt_chain_active(sessionID, True)
    _schedule_background_coro(
        _run_prompt_event_chain(sessionID, session, event, working_directory),
        session_id=sessionID,
        action="session.prompt_async",
    )
    return {"status": "accepted", "sessionID": sessionID}


class CommandRequest(BaseModel):
    """Request to execute a command"""
    model_config = ConfigDict(populate_by_name=True)
    
    command: str = Field(..., description="Command name")
    arguments: str = Field("", description="Command arguments")
    arguments_json: Optional[Any] = Field(None, alias="argumentsJson", description="Structured command arguments")
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
async def send_session_command(sessionID: str, request: CommandRequest, http_request: Request = None):
    """
    Execute a slash command.

    Direct commands (/tools, /skills, /help, /mcp) are handled without calling
    the LLM. Their output is pushed as an assistant message directly via SSE.
    Side-effecting direct commands like /clear run without creating a chat
    message and instead update session state via callbacks.

    LLM-based commands (/init, /compact, ...) are routed through
    the normal session-loop pipeline.

    In both cases the user message (showing the raw slash command text, e.g.
    "/tools") is created inside the background task so there is exactly ONE user
    message in the session history.  The frontend shows a temporary placeholder
    that is replaced as soon as the real SSE event arrives.
    """
    import asyncio
    import os

    from flocks.input.events import UserInputEvent

    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )
    if http_request is not None:
        current_user = require_user(http_request)
        _require_session_write_access(session, current_user)

    working_directory = session.directory or os.getcwd()
    await _require_agent_usable_for_chat(request.agent)
    raw_arguments = request.arguments
    if not raw_arguments and request.arguments_json is not None:
        raw_arguments = json.dumps(request.arguments_json, ensure_ascii=False)
    command_metadata: Dict[str, Any] = {}
    if request.arguments_json is not None:
        command_metadata["commandArgumentsJson"] = request.arguments_json

    # The text the user typed, shown verbatim in the chat bubble
    slash_text = f"/{request.command}"
    if raw_arguments:
        slash_text += f" {raw_arguments}"

    # ── Background task ──────────────────────────────────────────────────────
    async def _handle_command() -> None:
        event = UserInputEvent(
            source_type="webui",
            sessionID=sessionID,
            text=slash_text,
            parts=[dict(part) for part in (request.parts or [])],
            agent=request.agent,
            model=request.model,
            variant=request.variant,
            metadata=command_metadata,
            display_text=slash_text,
            messageID=request.messageID,
            working_directory=working_directory,
        )

        try:
            from flocks.project.instance import Instance
            from flocks.project.bootstrap import instance_bootstrap

            await Instance.provide(
                directory=working_directory,
                init=instance_bootstrap,
                fn=lambda: _dispatch_sse_input(sessionID, session, event, working_directory),
            )

        except Exception as exc:
            import traceback
            from flocks.server.routes.event import publish_event

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
async def run_shell_command(sessionID: str, request: ShellRequest, http_request: Request):
    """Run shell command"""
    from flocks.hooks.execution import ExecutionStopped
    from flocks.session.runner import SessionRunner

    current_user = require_user(http_request)
    session = await _get_session_by_id_unfiltered(sessionID)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found"
        )
    _require_session_write_access(session, current_user)

    model = None
    if request.model:
        model = {"providerID": request.model.providerID, "modelID": request.model.modelID}
    
    try:
        result = await SessionRunner.shell(
            session_id=sessionID,
            agent=request.agent,
            command=request.command,
            model=model,
        )
    except ExecutionStopped as exc:
        # A generic extension lifecycle may stop this operation.  Keep the
        # response stable without interpreting extension-specific policy data.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="execution stopped by extension",
        ) from exc
    
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
        session = await _get_session_by_id_unfiltered(sessionID)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {sessionID} not found",
            )

        from flocks.session.message import Message

        messages = await Message.list_with_parts(sessionID, include_archived=True)
        
        # Calculate statistics
        message_count = len(messages)
        token_count = 0
        tool_call_count = 0
        model_usage = {}
        
        for message_with_parts in messages:
            msg = message_with_parts.info

            # Count tokens (approximate from parts)
            for part in message_with_parts.parts:
                if hasattr(part, "text") and part.text:
                    token_count += len(part.text.split())  # Rough approximation
                
                # Count tool calls
                if getattr(part, "type", None) == "tool":
                    tool_call_count += 1
            
            # Track model usage
            model = getattr(msg, "model", None)
            if model:
                model_key = model if isinstance(model, str) else json.dumps(model, sort_keys=True, default=str)
                model_usage[model_key] = model_usage.get(model_key, 0) + 1
        
        # Calculate duration
        created_ms = session.time.created
        updated_ms = session.time.updated
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
    except HTTPException:
        raise
    except Exception as e:
        log.error("session.statistics.error", {"sessionID": sessionID, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get session statistics: {str(e)}")


async def _clear_session_history(sessionID: str) -> int:
    """Clear stored messages for a session and notify subscribed UIs."""
    session_info = await _get_session_by_id_unfiltered(sessionID)
    if not session_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {sessionID} not found",
        )

    from flocks.server.routes.event import publish_event
    from flocks.session.goal import GoalManager
    from flocks.session.interaction_queue import InteractionQueue
    from flocks.session.message import Message

    await abort_session(sessionID)
    await InteractionQueue.clear(sessionID)
    await GoalManager.clear(sessionID)
    try:
        await _publish_prompt_queue(sessionID)
    except Exception as exc:
        log.warn("session.clear.prompt_queue_event_error", {"sessionID": sessionID, "error": str(exc)})
    await _wait_for_session_idle(sessionID)

    deleted_count = await Message.clear(sessionID)
    log.info("session.cleared", {"sessionID": sessionID, "deleted": deleted_count})

    try:
        await publish_event("session.cleared", {
            "sessionID": sessionID,
            "deletedMessages": deleted_count,
        })
    except Exception as exc:
        log.warn("session.clear.event_error", {"sessionID": sessionID, "error": str(exc)})

    return deleted_count


@router.post("/{sessionID}/clear")
async def clear_session(sessionID: str, http_request: Request):
    """
    Clear session messages
    
    Removes all messages from the session while keeping the session itself.
    """
    try:
        session_info = await _get_session_by_id_unfiltered(sessionID)
        if not session_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {sessionID} not found",
            )
        current_user = require_user(http_request)
        _require_session_write_access(session_info, current_user)

        deleted_count = await _clear_session_history(sessionID)
        return {
            "status": "success",
            "sessionID": sessionID,
            "deletedMessages": deleted_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("session.clear.error", {"sessionID": sessionID, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to clear session: {str(e)}")
