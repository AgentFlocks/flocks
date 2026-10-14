"""Task Executor — dispatch a task execution instance."""

import asyncio
import contextvars
import functools
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
from .lease import ExecutionLease, ExecutionOwnershipError, current_execution_cancelled
from .store import TaskStore

log = Log.create(service="task.executor")

_TASK_ABSOLUTE_TIMEOUT_S: int = 2 * 3600
_workflow_cancel_lock = threading.Lock()
_workflow_cancel_events: dict[str, threading.Event] = {}
_workflow_done_events: dict[str, threading.Event] = {}


class TaskExecutionStopped(RuntimeError):
    """The task's agent run was stopped before it finished."""


def _release_workflow_run(
    execution_id: str,
    cancel_event: threading.Event,
    done_event: threading.Event,
) -> None:
    done_event.set()
    with _workflow_cancel_lock:
        if _workflow_cancel_events.get(execution_id) is cancel_event:
            _workflow_cancel_events.pop(execution_id, None)
        if _workflow_done_events.get(execution_id) is done_event:
            _workflow_done_events.pop(execution_id, None)


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
        scheduler: Optional[TaskScheduler],
    ) -> TaskExecution:
        # Claim before creating a session, publishing events, or writing any
        # execution fields. Another node may have reclaimed the dequeued row.
        async with ExecutionLease(execution.id) as lease:
            if not lease.acquired:
                raise ExecutionOwnershipError(f"execution ownership refused: {execution.id}")
            lease.require_owned()

            execution.owner_node_id = lease.node_id
            execution.owner_generation = lease.generation or 0
            session_id: Optional[str] = None
            try:
                if scheduler is None:
                    raise ValueError(f"Scheduler {execution.scheduler_id} not found")
                if execution.execution_mode == ExecutionMode.AGENT:
                    session_id = await cls._create_task_session(execution, scheduler)
                if not await TaskStore.set_execution_session(
                    execution.id,
                    session_id,
                    node_id=lease.node_id,
                    expected_generation=lease.generation or 0,
                ):
                    current = await TaskStore.get_execution(execution.id)
                    if (
                        current is not None
                        and current.is_terminal
                        and current.owner_generation == (lease.generation or 0)
                    ):
                        # Cancelled while the session was being created: do not start it.
                        return current
                    raise ExecutionOwnershipError(f"execution ownership superseded: {execution.id}")
                execution.session_id = session_id
                lease.require_owned()

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

                if execution.execution_mode == ExecutionMode.WORKFLOW:
                    result = await cls._trigger_workflow(execution, scheduler)
                else:
                    result = await cls._run_agent_session(execution, session_id)
                final_status = TaskStatus.COMPLETED
                execution.result_summary = result
                execution.delivery_status = DeliveryStatus.UNREAD
            except ExecutionOwnershipError:
                raise
            except TaskExecutionStopped as exc:
                # Stopped by the user (e.g. from the task's session): a failure
                # would be retried automatically, so record it as cancelled.
                final_status = TaskStatus.CANCELLED
                execution.error = str(exc)
            except Exception as exc:
                final_status = TaskStatus.FAILED
                execution.error = str(exc)
                log.error("task.dispatch.failed", {"id": execution.id, "error": str(exc)})

            lease.require_owned()

            current = await TaskStore.get_execution(execution.id)
            if current is None or (
                current.owner_node_id != lease.node_id
                or current.owner_generation != lease.generation
            ):
                raise ExecutionOwnershipError(f"execution ownership superseded: {execution.id}")
            if current and current.status != TaskStatus.RUNNING:
                return current

            completed_at = datetime.now(timezone.utc)
            execution.status = final_status
            execution.completed_at = completed_at
            if execution.started_at:
                execution.duration_ms = int(
                    (completed_at - execution.started_at).total_seconds() * 1000
                )

            # The result is written under the claim it was produced under. A
            # node that lost its lease while the work ran -- a long pause, a
            # partition -- would otherwise report over the top of whichever
            # node took the execution over, and the row would end up
            # describing a run that is not the one that produced it.
            written = await lease.finish_execution(execution)
            if written is None:
                log.warn(
                    "task.dispatch.result_dropped_superseded",
                    {"id": execution.id, "node_id": lease.node_id},
                )
                raise ExecutionOwnershipError(f"execution ownership expired or superseded: {execution.id}")
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
        # Register the run first, held at the gate, then read the record: a
        # cancel landing from here on finds the run, one that landed earlier
        # (after the record said running, before any run existed) is in the
        # record.
        start_gate = asyncio.Event()
        bg_task = await manager.run_existing_session(
            session_id=session_id,
            description=execution.title,
            agent=execution.agent_name,
            allow_user_questions=False,
            start_gate=start_gate,
        )
        opened = False
        try:
            if not await cls._still_running(execution.id):
                raise TaskExecutionStopped("Cancelled before the agent run started")
            start_gate.set()
            opened = True
        finally:
            if not opened:
                # Stopped, the read failed, or this coroutine was cancelled:
                # never leave the held run behind.
                manager.cancel(bg_task.id)
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
            raise TaskExecutionStopped(completed.error or "Agent execution was cancelled")
        if completed.status == "error":
            raise RuntimeError(completed.error or "Agent execution failed")
        return completed.output

    @classmethod
    async def _trigger_workflow(
        cls, execution: TaskExecution, scheduler: TaskScheduler
    ) -> Optional[str]:
        from flocks.workflow.fs_store import read_workflow_from_fs
        from flocks.workflow.tool_context import build_workflow_tool_context
        from flocks.hooks.execution import current_execution_context

        if not execution.workflow_id:
            raise ValueError("workflow execution_mode requires workflow_id")
        # Register the cancel event before anything is awaited, then read the
        # record (see _run_agent_session).  Once the worker thread has the
        # events it owns them: it releases them when it really exits.
        cancel_event = threading.Event()
        done_event = threading.Event()
        with _workflow_cancel_lock:
            _workflow_cancel_events[execution.id] = cancel_event
            _workflow_done_events[execution.id] = done_event
        handed_off = False
        try:
            if not await cls._still_running(execution.id):
                raise TaskExecutionStopped("Cancelled before the workflow started")
            workflow_data = read_workflow_from_fs(execution.workflow_id)
            if workflow_data is None:
                raise FileNotFoundError(f"Workflow not found: {execution.workflow_id}")
            snapshot = execution.execution_input_snapshot or {}
            inputs = snapshot.get("context") or scheduler.context or {}
            context_kwargs: dict[str, Any] = {}
            execution_context = current_execution_context()
            if execution_context:
                context_kwargs["execution_context"] = execution_context
            tool_context = await build_workflow_tool_context(
                workflow_id=execution.workflow_id,
                action_name="task",
                **context_kwargs,
            )
            if cancel_event.is_set():
                raise TaskExecutionStopped("Cancelled before the workflow started")
            # Like asyncio.to_thread, but the thread is not abandoned when
            # this coroutine is cancelled (dispatch timeout, shutdown).
            worker = asyncio.get_running_loop().run_in_executor(
                None,
                functools.partial(
                    contextvars.copy_context().run,
                    cls._run_workflow_sync,
                    execution.id,
                    workflow_data["workflowJson"],
                    inputs,
                    tool_context,
                    cancel_event,
                    done_event,
                ),
            )
            handed_off = True
            worker.add_done_callback(lambda done: done.cancelled() or done.exception())
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Ask the still running thread to stop; its cancel event stays
                # registered until it exits, so later cancels still reach it.
                cancel_event.set()
                raise
        finally:
            if not handed_off:
                _release_workflow_run(execution.id, cancel_event, done_event)
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
        tool_context: Any = None,
        cancel_event: Optional[threading.Event] = None,
        done_event: Optional[threading.Event] = None,
    ):
        """Run the workflow with the events _trigger_workflow registered; release them on exit."""
        from flocks.workflow.runner import run_workflow

        cancel = cancel_event if cancel_event is not None else threading.Event()
        done = done_event if done_event is not None else threading.Event()
        try:
            if cancel.is_set():
                # Cancelled while it waited for a pool thread: run_workflow
                # would still set up (install dependencies) before it checks.
                raise TaskExecutionStopped("Cancelled before the workflow started")
            return run_workflow(
                workflow=workflow,
                inputs=inputs,
                timeout_s=_TASK_ABSOLUTE_TIMEOUT_S,
                cancel=lambda: cancel.is_set() or current_execution_cancelled(),
                tool_context=tool_context,
            )
        finally:
            _release_workflow_run(execution_id, cancel, done)

    @staticmethod
    async def _still_running(execution_id: str) -> bool:
        """Whether the record still lets this run start.

        A missing record does not: deleting a running execution cancels it
        and then removes the row, which can land in between.
        """
        latest = await TaskStore.get_execution(execution_id)
        return latest is not None and latest.status == TaskStatus.RUNNING

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
                "Do NOT call schedule_task or schedule any new tasks.]\n\n"
            )
            return header + clean_body
        body = base_body
        if visible_context:
            ctx_str = "\n".join(f"- {k}: {v}" for k, v in visible_context.items())
            body += f"\n\nAdditional context:\n{ctx_str}"
        return body
