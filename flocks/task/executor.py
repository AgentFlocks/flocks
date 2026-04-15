"""Task Executor — dispatch a task execution instance."""

import asyncio
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flocks.utils.log import Log

from .models import (
    DeliveryStatus,
    ExecutionMode,
    SchedulerMode,
    TaskExecution,
    TaskScheduler,
    TaskStatus,
)
from .store import TaskStore

log = Log.create(service="task.executor")

_TASK_ABSOLUTE_TIMEOUT_S: int = 2 * 3600
_workflow_cancel_lock = threading.Lock()
_workflow_cancel_events: dict[str, threading.Event] = {}
_workflow_done_events: dict[str, threading.Event] = {}


class TaskExecutor:
    @classmethod
    def request_runtime_cancel(cls, execution: TaskExecution) -> bool:
        if execution.execution_mode != ExecutionMode.WORKFLOW:
            return False
        with _workflow_cancel_lock:
            cancel_event = _workflow_cancel_events.get(execution.id)
        if cancel_event is None:
            return False
        cancel_event.set()
        return True

    @classmethod
    async def dispatch(
        cls,
        execution: TaskExecution,
        scheduler: TaskScheduler,
    ) -> TaskExecution:
        session_id: Optional[str] = None
        if execution.execution_mode == ExecutionMode.AGENT:
            session_id = await cls._create_task_session(execution, scheduler)

        started_at = datetime.now(timezone.utc)
        execution.status = TaskStatus.RUNNING
        execution.started_at = started_at
        execution.session_id = session_id
        await TaskStore.update_execution(execution)

        if session_id:
            try:
                from flocks.server.routes.event import publish_event

                await publish_event(
                    "task.updated",
                    {
                        "executionID": execution.id,
                        "schedulerID": execution.scheduler_id,
                        "sessionID": session_id,
                        "status": execution.status.value,
                    },
                )
            except Exception as exc:
                log.warn("task.dispatch.sse_error", {"execution_id": execution.id, "error": str(exc)})

        try:
            if execution.execution_mode == ExecutionMode.WORKFLOW:
                result = await cls._trigger_workflow(execution, scheduler)
            else:
                result = await cls._run_agent_session(execution, session_id)
            final_status = TaskStatus.COMPLETED
            execution.result_summary = result
            execution.delivery_status = DeliveryStatus.UNREAD
        except Exception as exc:
            final_status = TaskStatus.FAILED
            execution.error = str(exc)
            log.error("task.dispatch.failed", {"id": execution.id, "error": str(exc)})

        current = await TaskStore.get_execution(execution.id)
        if current and current.status != TaskStatus.RUNNING:
            return current

        completed_at = datetime.now(timezone.utc)
        execution.status = final_status
        execution.completed_at = completed_at
        if execution.started_at:
            execution.duration_ms = int(
                (completed_at - execution.started_at).total_seconds() * 1000
            )
        await TaskStore.update_execution(execution)
        return execution

    @classmethod
    async def _create_task_session(
        cls, execution: TaskExecution, scheduler: TaskScheduler
    ) -> str:
        from flocks.session.session import Session
        from flocks.session.message import Message, MessageRole

        directory, project_id = await cls._resolve_task_session_context(execution)
        session = await Session.create(
            project_id=project_id,
            directory=directory,
            title=execution.title,
            agent=execution.agent_name,
            category="task",
        )
        await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content=cls._build_prompt(execution, scheduler),
            agent=execution.agent_name,
        )
        return session.id

    @classmethod
    async def _run_agent_session(
        cls, execution: TaskExecution, session_id: str
    ) -> Optional[str]:
        from flocks.task.background import get_background_manager

        if not session_id:
            raise RuntimeError("Agent task session was not created")
        manager = get_background_manager()
        bg_task = await manager.run_existing_session(
            session_id=session_id,
            description=execution.title,
            agent=execution.agent_name,
            allow_user_questions=False,
        )
        completed = await manager.wait_for(
            bg_task.id,
            timeout_ms=_TASK_ABSOLUTE_TIMEOUT_S * 1000,
        )
        if completed is None:
            try:
                manager.cancel(bg_task.id)
            except Exception:
                pass
            raise TimeoutError(
                f"Task exceeded absolute timeout of {_TASK_ABSOLUTE_TIMEOUT_S}s "
                f"({_TASK_ABSOLUTE_TIMEOUT_S // 3600}h)"
            )
        if completed.status == "cancelled":
            raise RuntimeError(completed.error or "Agent execution was cancelled")
        if completed.status == "error":
            raise RuntimeError(completed.error or "Agent execution failed")
        return completed.output

    @classmethod
    async def _trigger_workflow(
        cls, execution: TaskExecution, scheduler: TaskScheduler
    ) -> Optional[str]:
        from flocks.workflow.fs_store import read_workflow_from_fs

        if not execution.workflow_id:
            raise ValueError("workflow execution_mode requires workflow_id")
        workflow_data = read_workflow_from_fs(execution.workflow_id)
        if workflow_data is None:
            raise FileNotFoundError(f"Workflow not found: {execution.workflow_id}")
        snapshot = execution.execution_input_snapshot or {}
        inputs = snapshot.get("context") or scheduler.context or {}
        result = await asyncio.to_thread(
            cls._run_workflow_sync,
            execution.id,
            workflow_data["workflowJson"],
            inputs,
        )
        result_status = getattr(result, "status", None)
        if result_status == "CANCELLED":
            raise RuntimeError(result.error or "Workflow execution cancelled")
        if result_status == "TIMED_OUT":
            raise TimeoutError(result.error or "Workflow execution timed out")
        if result.error:
            raise RuntimeError(f"Workflow failed: {result.error}")
        return str(result.outputs) if result.outputs else None

    @classmethod
    def _run_workflow_sync(
        cls,
        execution_id: str,
        workflow: dict[str, Any],
        inputs: Dict[str, Any],
    ):
        from flocks.workflow.runner import run_workflow

        cancel_event = threading.Event()
        done_event = threading.Event()
        with _workflow_cancel_lock:
            _workflow_cancel_events[execution_id] = cancel_event
            _workflow_done_events[execution_id] = done_event
        try:
            return run_workflow(
                workflow=workflow,
                inputs=inputs,
                timeout_s=_TASK_ABSOLUTE_TIMEOUT_S,
                cancel=cancel_event.is_set,
            )
        finally:
            done_event.set()
            with _workflow_cancel_lock:
                current_cancel = _workflow_cancel_events.get(execution_id)
                if current_cancel is cancel_event:
                    _workflow_cancel_events.pop(execution_id, None)
                current_done = _workflow_done_events.get(execution_id)
                if current_done is done_event:
                    _workflow_done_events.pop(execution_id, None)

    @classmethod
    async def _resolve_task_session_context(
        cls, execution: TaskExecution
    ) -> tuple[str, str]:
        from flocks.project.project import Project
        from flocks.workspace.manager import WorkspaceManager

        if execution.workspace_directory:
            directory = Path(execution.workspace_directory)
        else:
            workspace_root = WorkspaceManager.get_instance().get_workspace_dir()
            today = date.today().isoformat()
            directory = workspace_root / "tasks" / today / execution.id
        directory.mkdir(parents=True, exist_ok=True)
        project_ctx = await Project.from_directory(str(directory))
        project = project_ctx.get("project")
        project_id = getattr(project, "id", None)
        if not project_id:
            raise RuntimeError("Failed to resolve internal project context for task session")
        return str(directory), project_id

    @staticmethod
    def _build_prompt(
        execution: TaskExecution, scheduler: TaskScheduler
    ) -> str:
        snapshot = execution.execution_input_snapshot or {}
        source = execution.source
        base_body = (
            source.user_prompt
            if source and source.user_prompt
            else execution.description or execution.title
        )
        visible_context: Dict[str, Any] = {
            k: v
            for k, v in (snapshot.get("context") or scheduler.context or {}).items()
            if not str(k).startswith("_")
        }
        if scheduler.mode in (SchedulerMode.CRON, SchedulerMode.ONCE):
            clean_body = execution.description or execution.title
            user_prompt = source.user_prompt.strip() if source and source.user_prompt else ""
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
