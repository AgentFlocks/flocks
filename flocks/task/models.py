"""
Task Center data models.

The public domain is split into two entities:
- TaskScheduler: task definition and trigger policy
- TaskExecution: one execution instance and the only queue item type
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from flocks.utils.id import Identifier


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SchedulerStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class SchedulerMode(str, Enum):
    ONCE = "once"
    CRON = "cron"


class ExecutionTriggerType(str, Enum):
    RUN_ONCE = "run_once"
    SCHEDULED = "scheduled"
    RERUN = "rerun"


class ExecutionMode(str, Enum):
    AGENT = "agent"
    WORKFLOW = "workflow"


class TaskPriority(str, Enum):
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @property
    def weight(self) -> int:
        return {
            TaskPriority.URGENT: 4,
            TaskPriority.HIGH: 3,
            TaskPriority.NORMAL: 2,
            TaskPriority.LOW: 1,
        }[self]


class DeliveryStatus(str, Enum):
    UNREAD = "unread"
    NOTIFIED = "notified"
    VIEWED = "viewed"


class TaskSource(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_type: str = Field(
        "user_conversation",
        alias="sourceType",
        description="user_conversation | system_evolution | scheduled_trigger",
    )
    session_id: Optional[str] = Field(None, alias="sessionID")
    user_prompt: Optional[str] = Field(None, alias="userPrompt")


class TaskTrigger(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    run_immediately: bool = Field(False, alias="runImmediately")
    run_at: Optional[datetime] = Field(None, alias="runAt")
    cron: Optional[str] = None
    timezone: str = "Asia/Shanghai"
    next_run: Optional[datetime] = Field(None, alias="nextRun")
    cron_description: Optional[str] = Field(None, alias="cronDescription")


class RetryConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_retries: int = Field(3, alias="maxRetries")
    retry_count: int = Field(0, alias="retryCount")
    retry_delay_seconds: int = Field(60, alias="retryDelaySeconds")
    retry_after: Optional[datetime] = Field(None, alias="retryAfter")


class TaskScheduler(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: Identifier.descending("task"))
    title: str
    description: str = ""
    mode: SchedulerMode = SchedulerMode.ONCE
    status: SchedulerStatus = SchedulerStatus.ACTIVE
    priority: TaskPriority = TaskPriority.NORMAL
    source: TaskSource = Field(default_factory=TaskSource)
    trigger: TaskTrigger = Field(default_factory=TaskTrigger)
    execution_mode: ExecutionMode = Field(
        ExecutionMode.AGENT, alias="executionMode",
    )
    agent_name: str = Field("rex", alias="agentName")
    workflow_id: Optional[str] = Field(None, alias="workflowID")
    skills: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    workspace_directory: Optional[str] = Field(None, alias="workspaceDirectory")
    retry: RetryConfig = Field(default_factory=RetryConfig)
    tags: List[str] = Field(default_factory=list)
    dedup_key: Optional[str] = Field(None, alias="dedupKey")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), alias="createdAt",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt",
    )
    created_by: str = Field("rex", alias="createdBy")

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    @property
    def is_active(self) -> bool:
        return self.status == SchedulerStatus.ACTIVE

    @property
    def schedule(self) -> TaskTrigger:
        return self.trigger


class TaskExecution(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: Identifier.descending("texec"))
    scheduler_id: str = Field(..., alias="schedulerID")
    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.NORMAL
    source: TaskSource = Field(default_factory=TaskSource)
    trigger_type: ExecutionTriggerType = Field(
        ExecutionTriggerType.RUN_ONCE, alias="triggerType",
    )
    status: TaskStatus = TaskStatus.PENDING
    delivery_status: DeliveryStatus = Field(
        DeliveryStatus.UNREAD, alias="deliveryStatus",
    )
    queued_at: Optional[datetime] = Field(None, alias="queuedAt")
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    duration_ms: Optional[int] = Field(None, alias="durationMs")
    session_id: Optional[str] = Field(None, alias="sessionID")
    result_summary: Optional[str] = Field(None, alias="resultSummary")
    error: Optional[str] = None
    execution_input_snapshot: Dict[str, Any] = Field(
        default_factory=dict, alias="executionInputSnapshot",
    )
    workspace_directory: Optional[str] = Field(None, alias="workspaceDirectory")
    retry: RetryConfig = Field(default_factory=RetryConfig)
    execution_mode: ExecutionMode = Field(
        ExecutionMode.AGENT, alias="executionMode",
    )
    agent_name: str = Field("rex", alias="agentName")
    workflow_id: Optional[str] = Field(None, alias="workflowID")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), alias="createdAt",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt",
    )

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )


class TaskExecutionQueueRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: Identifier.descending("tqref"))
    execution_id: str = Field(..., alias="executionID")
    status: TaskStatus = TaskStatus.QUEUED
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), alias="createdAt",
    )
    started_at: Optional[datetime] = Field(None, alias="startedAt")


def build_schedule(
    *,
    run_once: bool = False,
    run_at: Optional[str] = None,
    cron: Optional[str] = None,
    cron_description: Optional[str] = None,
    timezone: str = "Asia/Shanghai",
) -> TaskTrigger:
    if run_once:
        if not run_at and not cron:
            raise ValueError(
                "run_at or cron is required for one-time scheduled tasks"
            )
        run_at_dt = None
        if run_at:
            try:
                run_at_dt = datetime.fromisoformat(run_at)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"Invalid run_at datetime format: {run_at!r}. Use ISO 8601."
                ) from exc
        return TaskTrigger(
            run_immediately=False,
            run_at=run_at_dt,
            cron=cron,
            timezone=timezone,
            cron_description=cron_description,
        )

    if not cron:
        raise ValueError("cron is required for recurring scheduled tasks")
    return TaskTrigger(
        run_immediately=False,
        cron=cron,
        timezone=timezone,
        cron_description=cron_description,
    )

