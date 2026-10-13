"""
Background Task management routes

Routes for listing and managing background tasks spawned by agents.
Moved from agent.py to avoid route conflict with /api/agent/{name}.
"""

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from flocks.auth.context import AuthUser
from flocks.server.auth import require_user
from flocks.session.policy import SessionPolicy
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


def _background_managers() -> List[Any]:
    # Tasks live in the manager of the instance that launched them (scheduled
    # tasks run outside any request's instance), so look at all of them.
    from flocks.task.background import all_background_managers

    return all_background_managers()


def _find_task(task_id: str) -> Tuple[Any, Any]:
    for manager in _background_managers():
        task = manager.get_task(task_id)
        if task is not None:
            return manager, task
    return None, None


async def _task_session_access(
    task: Any,
    user: AuthUser,
    sessions: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, bool]:
    """(can_read, can_write) for the session the task runs in.

    The task's own session decides; its parent session stands in when the
    task has not created one yet. A task tied to no stored session is only
    visible to admins, like other ownerless runtime state. ``sessions``
    caches lookups across the tasks of one request.
    """
    from flocks.session.session import Session

    cache = sessions if sessions is not None else {}
    for session_id in (task.session_id, task.parent_session_id):
        if not session_id:
            continue
        if session_id not in cache:
            cache[session_id] = await Session.get_by_id_unfiltered(session_id)
        session = cache[session_id]
        if session is not None:
            return SessionPolicy.can_read(session, user), SessionPolicy.can_write(session, user)
    is_admin = SessionPolicy.is_admin(user)
    return is_admin, is_admin


def _not_found(task_id: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task {task_id} not found")


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

    Returns the background tasks (pending, running, finished) of the sessions
    the caller can read.
    """
    try:
        tasks = [
            task
            for manager in _background_managers()
            for task in manager.list_tasks()
        ]
        visible = []
        sessions: Dict[str, Any] = {}
        for task in sorted(tasks, key=lambda item: item.created_at):
            can_read, _ = await _task_session_access(task, user, sessions)
            if can_read:
                visible.append(_task_to_response(task))
        return visible
    except Exception as e:
        log.error("background_task.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to list background tasks")


@router.get("/{task_id}", response_model=BackgroundTaskResponse, summary="Get background task")
async def get_background_task(task_id: str, user: AuthUser = Depends(require_user)):
    """
    Get background task details

    Returns detailed information about a specific background task.
    """
    _, task = _find_task(task_id)
    if task is None:
        raise _not_found(task_id)
    can_read, _ = await _task_session_access(task, user)
    if not can_read:
        raise _not_found(task_id)
    return _task_to_response(task)


@router.post("/{task_id}/cancel", summary="Cancel background task")
async def cancel_background_task(task_id: str, user: AuthUser = Depends(require_user)):
    """
    Cancel a background task

    Cancels a running or pending background task.
    """
    manager, task = _find_task(task_id)
    if task is None:
        raise _not_found(task_id)
    can_read, can_write = await _task_session_access(task, user)
    if not can_read:
        raise _not_found(task_id)
    if not can_write:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No permission to cancel this task")
    if task.status not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task {task_id} has already finished",
        )
    if task.session_id:
        from flocks.task.manager import TaskManager
        from flocks.task.store import TaskStore

        executions = await TaskStore.list_active_executions_for_session(task.session_id)
        if executions:
            # The run of a scheduled task: cancel it as the task center does.
            # Cancelling only the background run made the task manager queue
            # the execution again.
            for execution in executions:
                await TaskManager.cancel_execution(execution.id)
            log.info("background_task.cancelled", {
                "task_id": task_id,
                "task_executions": [execution.id for execution in executions],
            })
            return {
                "status": "success",
                "message": f"Task {task_id} cancelled",
                "cancelled": 1,
                "taskExecutionIds": [execution.id for execution in executions],
            }
    cancelled = manager.cancel(task_id=task_id)
    if cancelled == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task {task_id} has already finished",
        )
    log.info("background_task.cancelled", {"task_id": task_id})
    return {"status": "success", "message": f"Task {task_id} cancelled", "cancelled": cancelled}
