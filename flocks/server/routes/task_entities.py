"""Execution-centric task scheduler/execution routes."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field


router = APIRouter()


class SchedulerCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    description: str = ""
    type: str = Field("queued", description="queued | scheduled")
    priority: str = Field("normal", description="urgent | high | normal | low")
    run_once: bool = Field(False, alias="runOnce")
    run_at: Optional[str] = Field(None, alias="runAt")
    cron: Optional[str] = None
    cron_description: Optional[str] = Field(None, alias="cronDescription")
    timezone: str = "Asia/Shanghai"
    user_prompt: Optional[str] = Field(None, alias="userPrompt")
    workspace_directory: Optional[str] = Field(None, alias="workspaceDirectory")
    tags: List[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    execution_mode: str = Field("agent", alias="executionMode")
    agent_name: str = Field("rex", alias="agentName")
    workflow_id: Optional[str] = Field(None, alias="workflowID")
    skills: List[str] = Field(default_factory=list)
    category: Optional[str] = None


class SchedulerUpdateRequest(BaseModel):
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
    run_once: Optional[bool] = Field(None, alias="runOnce")
    run_at: Optional[str] = Field(None, alias="runAt")
    cron: Optional[str] = None
    cron_description: Optional[str] = Field(None, alias="cronDescription")
    timezone: Optional[str] = None
    user_prompt: Optional[str] = Field(None, alias="userPrompt")
    workspace_directory: Optional[str] = Field(None, alias="workspaceDirectory")


class BatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    execution_ids: List[str] = Field(..., alias="executionIds")


class PaginatedResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    items: list
    total: int
    offset: int
    limit: int


@router.get("/task-system/notice")
async def get_task_system_notice():
    from flocks.task.manager import TaskManager

    return await TaskManager.get_task_page_notice()


@router.get("/task-system/dashboard")
async def task_dashboard():
    from flocks.task.manager import TaskManager

    return await TaskManager.dashboard()


@router.get("/task-system/queue/status")
async def task_queue_status():
    from flocks.task.manager import TaskManager

    return await TaskManager.queue_status()


@router.post("/task-system/queue/pause")
async def pause_task_queue():
    from flocks.task.manager import TaskManager

    TaskManager.pause_queue()
    return {"paused": True}


@router.post("/task-system/queue/resume")
async def resume_task_queue():
    from flocks.task.manager import TaskManager

    TaskManager.resume_queue()
    return {"paused": False}


@router.get("/task-schedulers")
async def list_schedulers(
    status_filter: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = Query(None),
    scheduled_only: bool = Query(False, alias="scheduledOnly"),
    sort_by: str = Query("created_at", alias="sortBy"),
    sort_order: str = Query("desc", alias="sortOrder"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    from flocks.task.manager import TaskManager
    from flocks.task.models import SchedulerStatus, TaskPriority

    items, total = await TaskManager.list_schedulers(
        status=SchedulerStatus(status_filter) if status_filter else None,
        priority=TaskPriority(priority) if priority else None,
        scheduled_only=scheduled_only,
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


@router.post("/task-schedulers", status_code=status.HTTP_201_CREATED)
async def create_scheduler(req: SchedulerCreateRequest):
    from flocks.task.manager import TaskManager
    from flocks.task.models import (
        ExecutionMode,
        SchedulerMode,
        TaskPriority,
        TaskSource,
        TaskTrigger,
        build_schedule,
    )

    if req.type == "queued":
        trigger = TaskTrigger(runImmediately=True)
        mode = SchedulerMode.ONCE
    elif req.run_once:
        trigger = build_schedule(
            run_once=True,
            run_at=req.run_at,
            cron=req.cron,
            cron_description=req.cron_description,
            timezone=req.timezone,
        )
        mode = SchedulerMode.ONCE
    else:
        trigger = build_schedule(
            run_once=False,
            cron=req.cron,
            cron_description=req.cron_description,
            timezone=req.timezone,
        )
        mode = SchedulerMode.CRON

    scheduler = await TaskManager.create_scheduler(
        title=req.title,
        description=req.description,
        mode=mode,
        priority=TaskPriority(req.priority),
        source=TaskSource(user_prompt=req.user_prompt) if req.user_prompt else None,
        trigger=trigger,
        execution_mode=ExecutionMode(req.execution_mode),
        agent_name=req.agent_name,
        workflow_id=req.workflow_id,
        skills=req.skills,
        category=req.category,
        context=req.context,
        workspace_directory=req.workspace_directory,
        tags=req.tags,
    )
    return scheduler.model_dump(mode="json", by_alias=True)


@router.get("/task-schedulers/{scheduler_id}")
async def get_scheduler(scheduler_id: str):
    from flocks.task.manager import TaskManager

    scheduler = await TaskManager.get_scheduler(scheduler_id)
    if not scheduler:
        raise HTTPException(404, "Task scheduler not found")
    return scheduler.model_dump(mode="json", by_alias=True)


@router.put("/task-schedulers/{scheduler_id}")
async def update_scheduler(scheduler_id: str, req: SchedulerUpdateRequest):
    from flocks.task.manager import TaskManager
    from flocks.task.models import ExecutionMode, TaskPriority

    fields = {k: v for k, v in req.model_dump(exclude_none=True).items()}
    if "priority" in fields:
        fields["priority"] = TaskPriority(fields["priority"])
    if "execution_mode" in fields:
        fields["execution_mode"] = ExecutionMode(fields["execution_mode"])
    cron = fields.pop("cron", None)
    tz = fields.pop("timezone", None)
    cron_desc = fields.pop("cron_description", None)
    run_once = fields.pop("run_once", None)
    run_at = fields.pop("run_at", None)
    user_prompt = fields.pop("user_prompt", None)
    scheduler = await TaskManager.update_scheduler_with_trigger(
        scheduler_id,
        fields=fields,
        cron=cron,
        timezone=tz,
        cron_description=cron_desc,
        run_once=run_once,
        run_at=run_at,
        user_prompt=user_prompt,
    )
    if not scheduler:
        raise HTTPException(404, "Task scheduler not found")
    return scheduler.model_dump(mode="json", by_alias=True)


@router.delete("/task-schedulers/{scheduler_id}")
async def delete_scheduler(scheduler_id: str):
    from flocks.task.manager import TaskManager

    if not await TaskManager.delete_scheduler(scheduler_id):
        raise HTTPException(404, "Task scheduler not found")
    return {"ok": True}


@router.post("/task-schedulers/{scheduler_id}/enable")
async def enable_scheduler(scheduler_id: str):
    from flocks.task.manager import TaskManager

    scheduler = await TaskManager.enable_scheduler(scheduler_id)
    if not scheduler:
        raise HTTPException(404, "Task scheduler not found")
    return scheduler.model_dump(mode="json", by_alias=True)


@router.post("/task-schedulers/{scheduler_id}/disable")
async def disable_scheduler(scheduler_id: str):
    from flocks.task.manager import TaskManager

    scheduler = await TaskManager.disable_scheduler(scheduler_id)
    if not scheduler:
        raise HTTPException(404, "Task scheduler not found")
    return scheduler.model_dump(mode="json", by_alias=True)


@router.get("/task-schedulers/{scheduler_id}/executions")
async def list_scheduler_executions(
    scheduler_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    from flocks.task.manager import TaskManager

    items, total = await TaskManager.list_scheduler_executions(
        scheduler_id, offset=offset, limit=limit
    )
    return PaginatedResponse(
        items=[item.model_dump(mode="json", by_alias=True) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/task-schedulers/{scheduler_id}/run")
async def run_scheduler(scheduler_id: str):
    from flocks.task.manager import TaskManager

    execution = await TaskManager.rerun_scheduler(scheduler_id)
    if not execution:
        raise HTTPException(404, "Task scheduler not found")
    return execution.model_dump(mode="json", by_alias=True)


@router.get("/task-executions")
async def list_executions(
    scheduler_id: Optional[str] = Query(None, alias="schedulerID"),
    status_filter: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = Query(None),
    delivery_status: Optional[str] = Query(None, alias="deliveryStatus"),
    sort_by: str = Query("queued_at", alias="sortBy"),
    sort_order: str = Query("desc", alias="sortOrder"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    from flocks.task.manager import TaskManager
    from flocks.task.models import DeliveryStatus, TaskPriority, TaskStatus

    items, total = await TaskManager.list_executions(
        scheduler_id=scheduler_id,
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


@router.get("/task-executions/{execution_id}")
async def get_execution(execution_id: str):
    from flocks.task.manager import TaskManager

    execution = await TaskManager.get_execution(execution_id)
    if not execution:
        raise HTTPException(404, "Task execution not found")
    return execution.model_dump(mode="json", by_alias=True)


@router.post("/task-executions/{execution_id}/viewed")
async def mark_execution_viewed(execution_id: str):
    from flocks.task.manager import TaskManager

    execution = await TaskManager.mark_viewed(execution_id)
    if not execution:
        raise HTTPException(404, "Task execution not found")
    return execution.model_dump(mode="json", by_alias=True)


@router.post("/task-executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str):
    from flocks.task.manager import TaskManager

    execution = await TaskManager.cancel_execution(execution_id)
    if not execution:
        raise HTTPException(404, "Task execution not found")
    return execution.model_dump(mode="json", by_alias=True)


@router.post("/task-executions/{execution_id}/pause")
async def pause_execution(execution_id: str):
    from flocks.task.manager import TaskManager

    execution = await TaskManager.pause_execution(execution_id)
    if not execution:
        raise HTTPException(404, "Task execution not found")
    return execution.model_dump(mode="json", by_alias=True)


@router.post("/task-executions/{execution_id}/resume")
async def resume_execution(execution_id: str):
    from flocks.task.manager import TaskManager

    execution = await TaskManager.resume_execution(execution_id)
    if not execution:
        raise HTTPException(404, "Task execution not found")
    return execution.model_dump(mode="json", by_alias=True)


@router.post("/task-executions/{execution_id}/retry")
async def retry_execution(execution_id: str):
    from flocks.task.manager import TaskManager

    execution = await TaskManager.retry_execution(execution_id)
    if not execution:
        raise HTTPException(404, "Task execution not found")
    return execution.model_dump(mode="json", by_alias=True)


@router.post("/task-executions/{execution_id}/rerun")
async def rerun_execution(execution_id: str):
    from flocks.task.manager import TaskManager

    execution = await TaskManager.rerun_execution(execution_id)
    if not execution:
        raise HTTPException(404, "Task execution not found")
    return execution.model_dump(mode="json", by_alias=True)


@router.delete("/task-executions/{execution_id}")
async def delete_execution(execution_id: str):
    from flocks.task.manager import TaskManager

    if not await TaskManager.delete_execution(execution_id):
        raise HTTPException(404, "Task execution not found")
    return {"ok": True}


@router.post("/task-executions/batch/cancel")
async def batch_cancel(req: BatchRequest):
    from flocks.task.manager import TaskManager

    return {"cancelled": await TaskManager.batch_cancel(req.execution_ids)}


@router.post("/task-executions/batch/delete")
async def batch_delete(req: BatchRequest):
    from flocks.task.manager import TaskManager

    return {"deleted": await TaskManager.batch_delete(req.execution_ids)}


