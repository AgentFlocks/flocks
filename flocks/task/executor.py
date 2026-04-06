"""
Task Executor — pure dispatcher, no execution logic.

Triggers BackgroundManager or WorkflowEngine, waits for result,
updates task status. Does NOT run SessionLoop — that is BackgroundManager's job.

Session creation for agent tasks is done HERE, before the task status is set to
RUNNING. This ensures sessionID and RUNNING status are written to the DB atomically,
so the UI can start streaming immediately without any polling delay.
"""

import asyncio
from datetime import date
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from flocks.utils.log import Log

from .models import (
    DeliveryStatus,
    ExecutionMode,
    Task,
    TaskExecutionRecord,
    TaskExecution,
    TaskStatus,
)
from .store import TaskStore

log = Log.create(service="task.executor")

# Maximum wall-clock time a single task may run (including agent execution).
# If BackgroundManager.wait_for() returns None (timeout), the task is marked FAILED.
_TASK_ABSOLUTE_TIMEOUT_S: int = 2 * 3600  # 2 hours


class TaskExecutor:
    """Triggers execution engines and collects results."""

    @classmethod
    async def dispatch(cls, task: Task) -> Task:
        """
        Dispatch a task to the appropriate engine:
        1. For AGENT mode: create session + initial message first
        2. Set task to RUNNING with sessionID already populated (atomic DB write)
        3. Emit SSE task.updated so UI reacts instantly
        4. Run agent loop / workflow
        5. Update task status (COMPLETED / FAILED)
        """
        started_at = datetime.now(timezone.utc)

        # For agent tasks: create the session BEFORE writing RUNNING status so
        # the DB always has sessionID at the moment the task becomes RUNNING.
        session_id: Optional[str] = None
        if task.execution_mode == ExecutionMode.AGENT:
            session_id = await cls._create_task_session(task)

        task.status = TaskStatus.RUNNING
        task.execution = TaskExecution(
            agent=task.agent_name,
            started_at=started_at,
            session_id=session_id,
        )
        task = await TaskStore.update_task(task)
        await cls._sync_running_record(task)
        log.info("task.dispatch", {
            "id": task.id,
            "mode": task.execution_mode.value,
            "session_id": session_id,
        })

        # Emit SSE so the UI reacts immediately (no polling delay).
        if session_id:
            try:
                from flocks.server.routes.event import publish_event
                await publish_event("task.updated", {
                    "taskID": task.id,
                    "sessionID": session_id,
                    "status": task.status.value,
                })
            except Exception as sse_exc:
                log.warn("task.dispatch.sse_error", {
                    "task_id": task.id,
                    "error": str(sse_exc),
                })

        final_status: TaskStatus
        final_exc: Optional[Exception] = None

        try:
            if task.execution_mode == ExecutionMode.WORKFLOW:
                result = await cls._trigger_workflow(task)
            else:
                result = await cls._run_agent_session(task, session_id)

            completed_at = datetime.now(timezone.utc)
            duration = int((completed_at - started_at).total_seconds() * 1000)
            final_status = TaskStatus.COMPLETED
            task.execution.completed_at = completed_at
            task.execution.duration_ms = duration
            task.execution.result_summary = result
            task.delivery_status = DeliveryStatus.UNREAD

        except Exception as exc:
            completed_at = datetime.now(timezone.utc)
            duration = int((completed_at - started_at).total_seconds() * 1000)
            final_status = TaskStatus.FAILED
            final_exc = exc
            task.execution.completed_at = completed_at
            task.execution.duration_ms = duration
            task.execution.error = str(exc)
            log.error("task.dispatch.failed", {"id": task.id, "error": str(exc)})

        # Before writing the final status, reload from DB to detect external
        # modifications (e.g. cancel or rerun called while the agent was running).
        # If the task is no longer in RUNNING state, another operation took
        # ownership — skip the status overwrite to avoid corrupting its state.
        current = await TaskStore.get_task(task.id)
        if current and current.status != TaskStatus.RUNNING:
            log.info("task.dispatch.skipped_overwrite", {
                "id": task.id,
                "computed_status": final_status.value,
                "current_status": current.status.value,
            })
            return current

        task.status = final_status
        task = await TaskStore.update_task(task)
        log.info("task.dispatch.done", {
            "id": task.id,
            "status": task.status.value,
            "duration_ms": task.execution.duration_ms,
        })
        return task

    @classmethod
    async def _create_task_session(cls, task: Task) -> str:
        """Create the session and initial user message for an agent task.

        Must be called BEFORE the task status is set to RUNNING so that
        sessionID is always present in the DB when status = RUNNING.

        Returns the new session ID.
        """
        from flocks.session.session import Session
        from flocks.session.message import Message, MessageRole

        directory, project_id = await cls._resolve_task_session_context(task)

        session = await Session.create(
            project_id=project_id,
            directory=directory,
            title=task.title,
            agent=task.agent_name,
            category="task",
        )

        await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content=cls._build_prompt(task),
            agent=task.agent_name,
        )

        log.info("task.session_created", {"task_id": task.id, "session_id": session.id})
        return session.id

    @classmethod
    async def _run_agent_session(cls, task: Task, session_id: str) -> Optional[str]:
        """Run the agent loop on an already-created session."""
        from flocks.task.background import get_background_manager

        if not session_id:
            raise RuntimeError("Agent task session was not created")

        manager = get_background_manager()
        bg_task = await manager.run_existing_session(
            session_id=session_id,
            description=task.title,
            agent=task.agent_name,
        )

        completed = await manager.wait_for(
            bg_task.id,
            timeout_ms=_TASK_ABSOLUTE_TIMEOUT_S * 1000,
        )

        if completed is None:
            # Absolute timeout hit — cancel the background task and raise.
            try:
                manager.cancel(bg_task.id)
            except Exception:
                pass
            raise TimeoutError(
                f"Task exceeded absolute timeout of {_TASK_ABSOLUTE_TIMEOUT_S}s "
                f"({_TASK_ABSOLUTE_TIMEOUT_S // 3600}h)"
            )

        if completed.status == "error":
            raise RuntimeError(completed.error or "Agent execution failed")

        return completed.output

    @classmethod
    async def _trigger_workflow(cls, task: Task) -> Optional[str]:
        """Trigger via WorkflowEngine, return result."""
        from flocks.workflow.runner import run_workflow

        if not task.workflow_id:
            raise ValueError("workflow execution_mode requires workflow_id")

        result = await asyncio.to_thread(
            run_workflow,
            workflow=task.workflow_id,
            inputs=task.context or {},
        )

        if result.error:
            raise RuntimeError(f"Workflow failed: {result.error}")

        return str(result.outputs) if result.outputs else None

    @classmethod
    async def _resolve_task_session_context(cls, task: Task) -> tuple[str, str]:
        """Resolve a standalone workspace directory and internal project_id."""
        from flocks.project.project import Project
        from flocks.workspace.manager import WorkspaceManager

        if task.workspace_directory:
            directory = Path(task.workspace_directory)
        else:
            workspace_root = WorkspaceManager.get_instance().get_workspace_dir()
            today = date.today().isoformat()
            directory = workspace_root / "tasks" / today / task.id

        directory.mkdir(parents=True, exist_ok=True)
        project_ctx = await Project.from_directory(str(directory))
        project = project_ctx.get("project")
        project_id = getattr(project, "id", None)
        if not project_id:
            raise RuntimeError("Failed to resolve internal project context for task session")
        return str(directory), project_id

    @staticmethod
    def _build_prompt(task: Task) -> str:
        base_body = (
            task.source.user_prompt
            if task.source and task.source.user_prompt
            else task.description or task.title
        )
        visible_context = {
            k: v for k, v in (task.context or {}).items()
            if not k.startswith("_")
        }

        # Scheduled tasks should execute as standalone work, without reusing
        # the scheduling instructions that created the task definition.
        if task.schedule is not None or (
            task.source and task.source.source_type == "scheduled_trigger"
        ):
            clean_body = task.description or task.title
            user_prompt = (
                task.source.user_prompt.strip()
                if task.source and task.source.user_prompt
                else ""
            )
            if user_prompt:
                if clean_body and user_prompt != clean_body:
                    clean_body += f"\n\nAdditional instructions:\n{user_prompt}"
                else:
                    clean_body = user_prompt
            if visible_context:
                ctx_str = "\n".join(f"- {k}: {v}" for k, v in visible_context.items())
                clean_body += f"\n\nAdditional context:\n{ctx_str}"
            header = (
                "[Scheduled task automated execution — "
                "complete the task described below and return your findings. "
                "Do NOT call task_create or schedule any new tasks.]\n\n"
            )
            return header + clean_body

        body = base_body
        if visible_context:
            ctx_str = "\n".join(f"- {k}: {v}" for k, v in visible_context.items())
            body += f"\n\nAdditional context:\n{ctx_str}"
        return body

    @staticmethod
    async def _sync_running_record(task: Task) -> None:
        record_id = (task.context or {}).get("_execution_record_id")
        if not record_id or not task.execution:
            return
        record = TaskExecutionRecord(
            id=record_id,
            task_id=task.id,
            status=TaskStatus.RUNNING,
            started_at=task.execution.started_at,
            completed_at=None,
            duration_ms=None,
            result_summary=None,
            error=None,
            session_id=task.execution.session_id,
            delivery_status=task.delivery_status,
        )
        await TaskStore.update_record(record)
