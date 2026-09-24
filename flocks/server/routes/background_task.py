"""
Background Task management routes

Routes for listing and managing background tasks spawned by agents.
Moved from agent.py to avoid route conflict with /api/agent/{name}.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from flocks.auth.context import AuthUser
from flocks.server.auth import require_user
from flocks.session.policy import SessionPolicy
from flocks.session.session import Session
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.background_task")


class BackgroundTaskResponse(BaseModel):
    """Background task response"""

    id: str = Field(..., description="Task ID")
    status: str = Field(..., description="Task status")
    description: str = Field(..., description="Task description")
    prompt: str = Field(..., description="Task prompt")
    agent: str = Field(..., description="Agent name")
    parentSessionId: Optional[str] = Field(None, description="Parent session ID")
    parentMessageId: Optional[str] = Field(None, description="Parent message ID")
    sessionId: Optional[str] = Field(None, description="Session ID")
    error: Optional[str] = Field(None, description="Error message")
    output: Optional[str] = Field(None, description="Task output")
    createdAt: int = Field(..., description="Created timestamp (ms)")
    startedAt: Optional[int] = Field(None, description="Started timestamp (ms)")
    completedAt: Optional[int] = Field(None, description="Completed timestamp (ms)")


def _get_background_manager():
    """Return the per-instance background manager, or None when unavailable."""
    try:
        from flocks.task.background import get_background_manager

        return get_background_manager()
    except Exception as exc:
        log.warning("background_task.manager_unavailable", {"error": str(exc)})
        return None


async def _can_access_task(task, user: AuthUser, *, write: bool) -> bool:
    """A task is visible to whoever can read its parent session (or its own
    session); cancelling requires write access on it. Same rules as the
    session list, so admins see exactly what they can see there."""
    for session_id in (task.parent_session_id, task.session_id):
        if not session_id:
            continue
        session = await Session.get_by_id(session_id)
        if session is None:
            continue
        allowed = (
            SessionPolicy.can_write(session, user)
            if write
            else SessionPolicy.can_read(session, user)
        )
        if allowed:
            return True
    return False


def _task_to_response(task) -> BackgroundTaskResponse:
    return BackgroundTaskResponse(
        id=task.id,
        status=task.status,
        description=task.description,
        prompt=task.prompt,
        agent=task.agent,
        parentSessionId=task.parent_session_id,
        parentMessageId=task.parent_message_id,
        sessionId=task.session_id,
        error=task.error,
        output=task.output,
        createdAt=task.created_at,
        startedAt=task.started_at,
        completedAt=task.completed_at,
    )


@router.get("", response_model=List[BackgroundTaskResponse], summary="List background tasks")
async def list_background_tasks(user: AuthUser = Depends(require_user)):
    """
    List background tasks

    Returns the background tasks (pending, running, completed) whose parent
    session the caller can read.
    """
    try:
        manager = _get_background_manager()
        if not manager:
            return []
        visible = []
        for task in manager.list_tasks():
            if await _can_access_task(task, user, write=False):
                visible.append(_task_to_response(task))
        return visible
    except Exception as e:
        log.error("background_task.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}", response_model=BackgroundTaskResponse, summary="Get background task")
async def get_background_task(task_id: str, user: AuthUser = Depends(require_user)):
    """
    Get background task details

    Returns detailed information about a specific background task.
    """
    try:
        manager = _get_background_manager()
        if not manager:
            raise HTTPException(status_code=404, detail="Background manager not available")
        task = manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        if not await _can_access_task(task, user, write=False):
            raise HTTPException(status_code=403, detail="Not allowed to view this task")
        return _task_to_response(task)
    except HTTPException:
        raise
    except Exception as e:
        log.error("background_task.get.error", {"error": str(e), "task_id": task_id})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/cancel", summary="Cancel background task")
async def cancel_background_task(task_id: str, user: AuthUser = Depends(require_user)):
    """
    Cancel a background task

    Cancels a running or pending background task.
    """
    try:
        manager = _get_background_manager()
        if not manager:
            raise HTTPException(status_code=404, detail="Background manager not available")
        task = manager.get_task(task_id)
        if task is not None and not await _can_access_task(task, user, write=True):
            raise HTTPException(status_code=403, detail="Only the session owner can cancel this task")
        cancelled = manager.cancel(task_id=task_id)
        if cancelled == 0:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found or already completed")
        log.info("background_task.cancelled", {"task_id": task_id})
        return {"status": "success", "message": f"Task {task_id} cancelled", "cancelled": cancelled}
    except HTTPException:
        raise
    except Exception as e:
        log.error("background_task.cancel.error", {"error": str(e), "task_id": task_id})
        raise HTTPException(status_code=500, detail=str(e))
