"""
Task Center API routes

RESTful endpoints for task management.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.task")


# ======================================================================
# Request / Response models
# ======================================================================

class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    description: str = ""
    type: str = Field("queued", description="queued | scheduled")
    priority: str = Field("normal", description="urgent | high | normal | low")
    run_once: bool = Field(False, alias="runOnce")
    run_at: Optional[str] = Field(None, alias="runAt", description="ISO 8601 datetime for one-time tasks")
    cron: Optional[str] = None
    cron_description: Optional[str] = Field(None, alias="cronDescription")
    timezone: str = "Asia/Shanghai"
    user_prompt: Optional[str] = Field(None, alias="userPrompt")
    workspace_directory: Optional[str] = Field(None, alias="workspaceDirectory")
    tags: List[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)

    execution_mode: str = Field("agent", alias="executionMode", description="agent | workflow")
    agent_name: str = Field("rex", alias="agentName")
    workflow_id: Optional[str] = Field(None, alias="workflowID")
    skills: List[str] = Field(default_factory=list)
    category: Optional[str] = None


class TaskUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    tags: Optional[List[str]] = None
    execution_mode: Optional[str] = Field(None, alias="executionMode")
    agent_name: Optional[str] = Field(None, alias="agentName")
    workflow_id: Optional[str] = Field(None, alias="workflowID")
    skills: Optional[List[str]] = None
    category: Optional[str] = None
    # Scheduled task fields
    run_once: Optional[bool] = Field(None, alias="runOnce")
    run_at: Optional[str] = Field(None, alias="runAt")
    cron: Optional[str] = None
    cron_description: Optional[str] = Field(None, alias="cronDescription")
    timezone: Optional[str] = None
    user_prompt: Optional[str] = Field(None, alias="userPrompt")
    workspace_directory: Optional[str] = Field(None, alias="workspaceDirectory")


class BatchRequest(BaseModel):
    task_ids: List[str] = Field(..., alias="taskIds")
    model_config = ConfigDict(populate_by_name=True)


class PaginatedResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list
    total: int
    offset: int
    limit: int


# ======================================================================
# Dashboard & queue (before /{task_id} to avoid path conflicts)
# ======================================================================

@router.get("/dashboard", summary="Dashboard counts")
async def dashboard():
    from flocks.task.manager import TaskManager
    return await TaskManager.dashboard()


@router.get("/queue/status", summary="Queue status")
async def queue_status():
    from flocks.task.manager import TaskManager
    return await TaskManager.queue_status()


@router.get("/queue/items", summary="Queue items")
async def list_queue_items(
    status_filter: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = Query(None),
    delivery_status: Optional[str] = Query(None, alias="deliveryStatus"),
    sort_by: str = Query("created_at", alias="sortBy"),
    sort_order: str = Query("desc", alias="sortOrder"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    from flocks.task.manager import TaskManager
    from flocks.task.models import DeliveryStatus, TaskPriority, TaskStatus

    items, total = await TaskManager.list_queue_items(
        status=TaskStatus(status_filter) if status_filter else None,
        priority=TaskPriority(priority) if priority else None,
        delivery_status=DeliveryStatus(delivery_status) if delivery_status else None,
        sort_by=sort_by,
        sort_order=sort_order,
        offset=offset,
        limit=limit,
    )
    return PaginatedResponse(
        items=[item.model_dump(mode="json", by_alias=True) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/queue/items/{item_id}/cancel", summary="Cancel queue item")
async def cancel_queue_item(item_id: str):
    from flocks.task.manager import TaskManager

    ok = await TaskManager.cancel_queue_item(item_id)
    if not ok:
        raise HTTPException(404, "Queue item not found")
    return {"ok": True}


@router.post("/queue/items/{item_id}/rerun", summary="Rerun queue item")
async def rerun_queue_item(item_id: str):
    from flocks.task.manager import TaskManager

    ok = await TaskManager.rerun_queue_item(item_id)
    if not ok:
        raise HTTPException(404, "Queue item not found")
    return {"ok": True}


@router.delete("/queue/items/{item_id}", summary="Delete queue item")
async def delete_queue_item(item_id: str):
    from flocks.task.manager import TaskManager

    ok = await TaskManager.delete_queue_item(item_id)
    if not ok:
        raise HTTPException(404, "Queue item not found")
    return {"ok": True}


@router.post("/queue/pause", summary="Pause queue")
async def pause_queue():
    from flocks.task.manager import TaskManager
    TaskManager.pause_queue()
    return {"paused": True}


@router.post("/queue/resume", summary="Resume queue")
async def resume_queue():
    from flocks.task.manager import TaskManager
    TaskManager.resume_queue()
    return {"paused": False}


# ======================================================================
# Built-in tasks (filesystem-defined, before /{task_id})
# ======================================================================

@router.get("/builtin", summary="List built-in task definitions from .flocks/plugins/tasks/")
async def list_builtin_tasks():
    """
    Return built-in task definitions discovered by the PluginLoader (TASKS
    extension point), enriched with the live DB task so the UI can show
    enable/disable state.

    Data is sourced from ``flocks.task.plugin.list_builtin_task_files_as_dicts()`` —
    the in-memory list populated during startup seeding.  If not yet seeded
    (e.g. first request before startup completes), a fresh scan is triggered.
    """
    from flocks.task.plugin import (
        list_builtin_task_files_as_dicts,
        list_loaded_task_specs,
        seed_tasks_from_plugin,
    )
    from flocks.task.store import TaskStore

    if not list_loaded_task_specs():
        await seed_tasks_from_plugin()

    defs = list_builtin_task_files_as_dicts()
    result = []
    for spec in defs:
        dedup_key = spec.get("dedupKey", "")
        db_task = await TaskStore.get_by_dedup_key(dedup_key) if dedup_key else None
        result.append({
            "definition": spec,
            "task": db_task.model_dump(mode="json", by_alias=True) if db_task else None,
        })
    return result


# ======================================================================
# Scheduled tasks (before /{task_id})
# ======================================================================

@router.get("/scheduled", summary="List scheduled tasks")
async def list_scheduled():
    from flocks.task.manager import TaskManager

    tasks = await TaskManager.list_scheduled()
    return [t.model_dump(mode="json", by_alias=True) for t in tasks]


@router.post("/scheduled/{task_id}/enable", summary="Enable scheduled task")
async def enable_scheduled(task_id: str):
    from flocks.task.manager import TaskManager

    task = await TaskManager.enable_scheduled(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump(mode="json", by_alias=True)


@router.post("/scheduled/{task_id}/disable", summary="Disable scheduled task")
async def disable_scheduled(task_id: str):
    from flocks.task.manager import TaskManager

    task = await TaskManager.disable_scheduled(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump(mode="json", by_alias=True)


# ======================================================================
# Batch operations (before /{task_id})
# ======================================================================

@router.post("/batch/cancel", summary="Batch cancel tasks")
async def batch_cancel(req: BatchRequest):
    from flocks.task.manager import TaskManager

    count = await TaskManager.batch_cancel(req.task_ids)
    return {"cancelled": count}


@router.post("/batch/delete", summary="Batch delete tasks")
async def batch_delete(req: BatchRequest):
    from flocks.task.manager import TaskManager

    count = await TaskManager.batch_delete(req.task_ids)
    return {"deleted": count}


# ======================================================================
# Task CRUD
# ======================================================================

@router.get(
    "",
    summary="List tasks",
    response_model=PaginatedResponse,
)
async def list_tasks(
    status_filter: Optional[str] = Query(None, alias="status"),
    type_filter: Optional[str] = Query(None, alias="type"),
    priority: Optional[str] = Query(None),
    delivery_status: Optional[str] = Query(None, alias="deliveryStatus"),
    sort_by: str = Query("created_at", alias="sortBy"),
    sort_order: str = Query("desc", alias="sortOrder"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    from flocks.task.manager import TaskManager
    from flocks.task.models import DeliveryStatus, TaskPriority, TaskStatus, TaskType

    items, total = await TaskManager.list_tasks(
        status=TaskStatus(status_filter) if status_filter else None,
        task_type=TaskType(type_filter) if type_filter else None,
        priority=TaskPriority(priority) if priority else None,
        delivery_status=DeliveryStatus(delivery_status) if delivery_status else None,
        sort_by=sort_by,
        sort_order=sort_order,
        offset=offset,
        limit=limit,
    )
    return PaginatedResponse(
        items=[t.model_dump(mode="json", by_alias=True) for t in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "",
    summary="Create task",
    status_code=status.HTTP_201_CREATED,
)
async def create_task(req: TaskCreateRequest):
    from flocks.task.manager import TaskManager
    from flocks.task.models import ExecutionMode, TaskPriority, TaskSource, TaskType, build_schedule

    _ALLOWED_TYPES = {"queued", "scheduled"}
    if req.type not in _ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported task type: {req.type!r}. Allowed: {', '.join(sorted(_ALLOWED_TYPES))}")

    schedule = None
    if req.type == "scheduled":
        try:
            schedule = build_schedule(
                run_once=req.run_once,
                run_at=req.run_at,
                cron=req.cron,
                cron_description=req.cron_description,
                timezone=req.timezone,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    task = await TaskManager.create_task(
        title=req.title,
        description=req.description,
        task_type=TaskType(req.type),
        priority=TaskPriority(req.priority),
        source=TaskSource(user_prompt=req.user_prompt) if req.user_prompt else None,
        schedule=schedule,
        context=req.context,
        workspace_directory=req.workspace_directory,
        tags=req.tags,
        execution_mode=ExecutionMode(req.execution_mode),
        agent_name=req.agent_name,
        workflow_id=req.workflow_id,
        skills=req.skills,
        category=req.category,
    )
    return task.model_dump(mode="json", by_alias=True)


@router.get("/{task_id}", summary="Get task detail")
async def get_task(task_id: str):
    from flocks.task.manager import TaskManager

    task = await TaskManager.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump(mode="json", by_alias=True)


@router.put("/{task_id}", summary="Update task")
async def update_task(task_id: str, req: TaskUpdateRequest):
    from flocks.task.manager import TaskManager

    fields = {k: v for k, v in req.model_dump(exclude_none=True).items()}
    if "priority" in fields:
        from flocks.task.models import TaskPriority
        fields["priority"] = TaskPriority(fields["priority"])
    if "execution_mode" in fields:
        from flocks.task.models import ExecutionMode
        fields["execution_mode"] = ExecutionMode(fields["execution_mode"])

    cron = fields.pop("cron", None)
    tz = fields.pop("timezone", None)
    cron_desc = fields.pop("cron_description", None)
    run_once = fields.pop("run_once", None)
    run_at_str = fields.pop("run_at", None)
    user_prompt = fields.pop("user_prompt", None)

    try:
        task = await TaskManager.update_task_with_schedule(
            task_id,
            fields=fields,
            cron=cron,
            timezone=tz,
            cron_description=cron_desc,
            run_once=run_once,
            run_at=run_at_str,
            user_prompt=user_prompt,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump(mode="json", by_alias=True)


@router.delete("/{task_id}", summary="Delete task")
async def delete_task(task_id: str):
    from flocks.task.manager import TaskManager

    ok = await TaskManager.delete_task(task_id)
    if not ok:
        raise HTTPException(404, "Task not found")
    return {"ok": True}


# ======================================================================
# Task operations
# ======================================================================

@router.post("/{task_id}/cancel", summary="Cancel task")
async def cancel_task(task_id: str):
    from flocks.task.manager import TaskManager

    task = await TaskManager.cancel_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump(mode="json", by_alias=True)


@router.post("/{task_id}/pause", summary="Pause task")
async def pause_task(task_id: str):
    from flocks.task.manager import TaskManager

    task = await TaskManager.pause_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump(mode="json", by_alias=True)


@router.post("/{task_id}/resume", summary="Resume task")
async def resume_task(task_id: str):
    from flocks.task.manager import TaskManager

    task = await TaskManager.resume_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump(mode="json", by_alias=True)


@router.post("/{task_id}/retry", summary="Retry failed task")
async def retry_task(task_id: str):
    from flocks.task.manager import TaskManager

    task = await TaskManager.retry_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump(mode="json", by_alias=True)


@router.post("/{task_id}/rerun", summary="Rerun task (stop if running, requeue)")
async def rerun_task(task_id: str):
    from flocks.task.manager import TaskManager

    task = await TaskManager.rerun_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task.model_dump(mode="json", by_alias=True)


@router.get("/{task_id}/records", summary="Execution records for a scheduled task")
async def list_records(
    task_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(5, ge=1, le=50),
):
    from flocks.task.manager import TaskManager

    records, total = await TaskManager.list_records(
        task_id, limit=limit, offset=offset,
    )
    return PaginatedResponse(
        items=[r.model_dump(mode="json", by_alias=True) for r in records],
        total=total,
        offset=offset,
        limit=limit,
    )
