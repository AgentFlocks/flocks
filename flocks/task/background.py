"""
Background task manager for subagent execution.
Ported from oh-my-opencode background agent manager behavior.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Any, List

from flocks.utils.id import Identifier
from flocks.utils.log import Log
from flocks.project.instance import Instance
from flocks.session.session import Session
from flocks.session.message import Message, MessageRole
from flocks.session.session_loop import SessionLoop


log = Log.create(service="background.manager")

# 任务无活跃交互超时时间（秒），超过此时间任务将被标记为失败
_INACTIVITY_TIMEOUT_SECONDS = 300  # 5 minutes
# 看门狗检查间隔（秒）
_WATCHDOG_CHECK_INTERVAL = 30


@dataclass
class BackgroundTask:
    id: str
    status: str
    description: str
    prompt: str
    agent: str
    parent_session_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    parent_call_id: Optional[str] = None
    parent_agent: Optional[str] = None
    parent_model: Optional[dict] = None
    model: Optional[dict] = None
    category: Optional[str] = None
    session_id: Optional[str] = None
    error: Optional[str] = None
    output: Optional[str] = None
    created_at: int = field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    last_activity_at: Optional[int] = None  # 最近一次有交互的时间戳（ms），用于不活跃超时检测
    allow_user_questions: bool = True
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    completion_injected: bool = False


@dataclass
class LaunchInput:
    description: str
    prompt: str
    agent: str
    parent_session_id: Optional[str]
    parent_message_id: Optional[str]
    parent_agent: Optional[str]
    parent_call_id: Optional[str] = None
    parent_model: Optional[dict] = None
    model: Optional[dict] = None
    model_pinned: bool = False
    category: Optional[str] = None
    directory: Optional[str] = None
    project_id: Optional[str] = None


@dataclass
class ResumeInput:
    session_id: str
    prompt: str
    parent_session_id: Optional[str]
    parent_message_id: Optional[str]
    parent_agent: Optional[str]
    parent_call_id: Optional[str] = None
    parent_model: Optional[dict] = None


class BackgroundManager:
    def __init__(self, max_concurrency: int = 2):
        self._tasks: Dict[str, BackgroundTask] = {}
        self._task_handles: Dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def list_tasks(self) -> List[BackgroundTask]:
        return list(self._tasks.values())

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        return self._tasks.get(task_id)

    async def launch(self, input_data: LaunchInput) -> BackgroundTask:
        task_id = f"bg_{Identifier.ascending('task')[:8]}"
        task = BackgroundTask(
            id=task_id,
            status="pending",
            description=input_data.description,
            prompt=input_data.prompt,
            agent=input_data.agent,
            parent_session_id=input_data.parent_session_id,
            parent_message_id=input_data.parent_message_id,
            parent_call_id=input_data.parent_call_id,
            parent_agent=input_data.parent_agent,
            parent_model=input_data.parent_model,
            model=input_data.model,
            category=input_data.category,
        )
        self._tasks[task_id] = task
        handle = asyncio.create_task(self._run_task(task, input_data))
        self._task_handles[task_id] = handle
        return task

    async def resume(self, input_data: ResumeInput) -> BackgroundTask:
        task_id = f"bg_{Identifier.ascending('task')[:8]}"
        task = BackgroundTask(
            id=task_id,
            status="pending",
            description=f"Continue: {input_data.session_id}",
            prompt=input_data.prompt,
            agent="continue",
            parent_session_id=input_data.parent_session_id,
            parent_message_id=input_data.parent_message_id,
            parent_call_id=input_data.parent_call_id,
            parent_agent=input_data.parent_agent,
            parent_model=input_data.parent_model,
            session_id=input_data.session_id,
        )
        self._tasks[task_id] = task
        handle = asyncio.create_task(self._run_resume(task, input_data))
        self._task_handles[task_id] = handle
        return task

    async def run_existing_session(
        self,
        session_id: str,
        description: str,
        agent: str,
        *,
        allow_user_questions: bool = True,
        parent_session_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        parent_call_id: Optional[str] = None,
        parent_agent: Optional[str] = None,
        parent_model: Optional[dict] = None,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> BackgroundTask:
        """Run the session loop on an already-created session.

        The session and its initial user message must already exist.
        This is used by TaskExecutor which creates the session upfront so that
        the task record can hold sessionID at the moment it becomes RUNNING.
        """
        task_id = f"bg_{Identifier.ascending('task')[:8]}"
        task = BackgroundTask(
            id=task_id,
            status="pending",
            description=description,
            prompt="",
            agent=agent,
            parent_session_id=parent_session_id,
            parent_message_id=parent_message_id,
            parent_call_id=parent_call_id,
            parent_agent=parent_agent,
            parent_model=parent_model,
            session_id=session_id,
            allow_user_questions=allow_user_questions,
            provider_id=provider_id,
            model_id=model_id,
        )
        self._tasks[task_id] = task
        handle = asyncio.create_task(self._run_existing_session(task, session_id))
        self._task_handles[task_id] = handle
        return task

    async def _run_existing_session(self, task: BackgroundTask, session_id: str) -> None:
        async with self._semaphore:
            task.started_at = int(datetime.now().timestamp() * 1000)
            task.last_activity_at = task.started_at
            task.status = "running"
            try:
                callbacks = self._build_activity_callbacks(task)
                result = await self._run_session_with_watchdog(
                    task,
                    session_id,
                    callbacks,
                    allow_user_questions=task.allow_user_questions,
                    provider_id=task.provider_id,
                    model_id=task.model_id,
                )
                output = ""
                if result.last_message:
                    output = await Message.get_text_content(result.last_message)
                task.output = output
                task.status = "completed"
                task.completed_at = int(datetime.now().timestamp() * 1000)
                await self._inject_parent_completion(task)
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.completed_at = int(datetime.now().timestamp() * 1000)
                await self._inject_parent_completion(task)
                raise
            except Exception as exc:
                log.error("background.existing_session.error", {"error": str(exc), "task_id": task.id})
                task.error = str(exc)
                task.status = "error"
                task.completed_at = int(datetime.now().timestamp() * 1000)
                await self._inject_parent_completion(task)

    async def _inject_parent_completion(self, task: BackgroundTask) -> None:
        """Inject completed background task output into the parent context."""
        if task.completion_injected or not task.parent_session_id:
            return

        state = task.status
        if state == "completed":
            body_tag = "task_result"
            body = task.output or "(no output)"
            summary = f"Background task completed: {task.description}"
        elif state == "cancelled":
            body_tag = "task_error"
            body = task.error or "Task was cancelled."
            summary = f"Background task cancelled: {task.description}"
        else:
            body_tag = "task_error"
            body = task.error or "Task failed without an error message."
            summary = f"Background task failed: {task.description}"

        content = (
            f'<task id="{task.id}" session_id="{task.session_id or ""}" state="{state}">\n'
            f"  <summary>{summary}</summary>\n"
            f"  <{body_tag}>{body}</{body_tag}>\n"
            "</task>"
        )
        try:
            await Message.create(
                session_id=task.parent_session_id,
                role=MessageRole.USER,
                content=content,
                agent=task.parent_agent or "rex",
                model=task.parent_model,
                synthetic=True,
                part_metadata={
                    "kind": "background_task_result",
                    "task_id": task.id,
                    "session_id": task.session_id,
                    "status": state,
                },
            )
            await self._update_parent_tool_part(task)
            task.completion_injected = True
            self._schedule_parent_resume(task)
        except Exception as exc:
            log.warn("background.inject_completion_failed", {
                "task_id": task.id,
                "parent_session_id": task.parent_session_id,
                "error": str(exc),
            })

    def _schedule_parent_resume(self, task: BackgroundTask) -> None:
        """Kick the parent session so Rex consumes injected background results."""
        if not task.parent_session_id:
            return
        if task.status not in ("completed", "error"):
            return
        if SessionLoop.is_running(task.parent_session_id):
            log.info("background.parent_resume.already_running", {
                "task_id": task.id,
                "parent_session_id": task.parent_session_id,
            })
            return

        async def _run_parent() -> None:
            try:
                from flocks.server.routes.event import publish_event
                from flocks.session.session_loop import LoopCallbacks

                model = task.parent_model or {}
                result = await SessionLoop.run(
                    session_id=task.parent_session_id,
                    provider_id=model.get("providerID"),
                    model_id=model.get("modelID"),
                    agent_name=task.parent_agent,
                    callbacks=LoopCallbacks(event_publish_callback=publish_event),
                )
                log.info("background.parent_resume.completed", {
                    "task_id": task.id,
                    "parent_session_id": task.parent_session_id,
                    "result_action": result.action,
                })
            except Exception as exc:
                log.warn("background.parent_resume.failed", {
                    "task_id": task.id,
                    "parent_session_id": task.parent_session_id,
                    "error": str(exc),
                })

        asyncio.create_task(_run_parent())

    async def _update_parent_tool_part(self, task: BackgroundTask) -> None:
        """Mark the original background launch tool part with child completion."""
        if not task.parent_session_id or not task.parent_message_id or not task.parent_call_id:
            return

        try:
            from flocks.session.message import ToolPart, ToolStateCompleted, ToolStateError

            parts = await Message.parts(task.parent_message_id, task.parent_session_id)
            target = next(
                (
                    part for part in parts
                    if isinstance(part, ToolPart)
                    and getattr(part, "callID", None) == task.parent_call_id
                ),
                None,
            )
            if target is None:
                return

            previous_state = getattr(target, "state", None)
            previous_input = getattr(previous_state, "input", {}) or {}
            previous_metadata = dict(getattr(previous_state, "metadata", {}) or {})
            previous_metadata.update({
                "sessionId": task.session_id,
                "taskId": task.id,
                "status": task.status,
                "background": True,
            })
            start_time = None
            previous_time = getattr(previous_state, "time", None)
            if isinstance(previous_time, dict):
                start_time = previous_time.get("start")
            end_time = int(datetime.now().timestamp() * 1000)

            if task.status == "completed":
                state = ToolStateCompleted(
                    status="completed",
                    input=previous_input,
                    output=task.output or "",
                    title=task.description,
                    metadata=previous_metadata,
                    time={"start": start_time or end_time, "end": end_time},
                )
            else:
                state = ToolStateError(
                    status="error",
                    input=previous_input,
                    error=task.error or f"Background task {task.status}",
                    metadata=previous_metadata,
                    time={"start": start_time or end_time, "end": end_time},
                )

            await Message.update_part(
                session_id=task.parent_session_id,
                message_id=task.parent_message_id,
                part_id=target.id,
                state=state,
            )
        except Exception as exc:
            log.warn("background.update_parent_tool_part_failed", {
                "task_id": task.id,
                "parent_session_id": task.parent_session_id,
                "parent_message_id": task.parent_message_id,
                "error": str(exc),
            })

    async def wait_for(self, task_id: str, timeout_ms: Optional[int] = None) -> Optional[BackgroundTask]:
        handle = self._task_handles.get(task_id)
        if not handle:
            return self._tasks.get(task_id)
        timeout = None if timeout_ms is None else timeout_ms / 1000
        try:
            await asyncio.wait_for(handle, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return self._tasks.get(task_id)

    def cancel(self, task_id: Optional[str] = None, all_tasks: bool = False) -> int:
        cancelled = 0
        if all_tasks:
            task_ids = list(self._task_handles.keys())
        else:
            task_ids = [task_id] if task_id else []

        for tid in task_ids:
            handle = self._task_handles.get(tid)
            if not handle:
                continue
            if not handle.done():
                handle.cancel()
                cancelled += 1
            task = self._tasks.get(tid)
            if task:
                task.status = "cancelled"
                task.completed_at = int(datetime.now().timestamp() * 1000)
        return cancelled

    def cancel_by_session_id(self, session_id: str) -> int:
        """Cancel all background tasks associated with a given session ID."""
        task_ids = [
            t.id for t in self._tasks.values() if t.session_id == session_id
        ]
        cancelled = 0
        for tid in task_ids:
            cancelled += self.cancel(task_id=tid)
        return cancelled

    def cancel_by_parent_session_id(self, parent_session_id: str) -> int:
        """Cancel all background tasks spawned by a parent session."""
        task_ids = [
            t.id for t in self._tasks.values()
            if t.parent_session_id == parent_session_id and t.status in ("pending", "running")
        ]
        cancelled = 0
        for tid in task_ids:
            cancelled += self.cancel(task_id=tid)
        return cancelled

    def _build_activity_callbacks(self, task: BackgroundTask):
        """构建带活跃时间更新的 LoopCallbacks，用于不活跃超时检测。"""
        from flocks.session.session_loop import LoopCallbacks
        from flocks.session.runner import RunnerCallbacks
        from flocks.server.routes.event import publish_event

        def _touch() -> None:
            task.last_activity_at = int(datetime.now().timestamp() * 1000)

        async def _on_step_start(_step: int) -> None:
            _touch()

        async def _on_text_delta(_text: str) -> None:
            _touch()

        runner_cbs = RunnerCallbacks(on_text_delta=_on_text_delta)
        return LoopCallbacks(
            on_step_start=_on_step_start,
            runner_callbacks=runner_cbs,
            event_publish_callback=publish_event,
        )

    async def _run_session_with_watchdog(
        self,
        task: BackgroundTask,
        session_id: str,
        callbacks,
        timeout_seconds: int = _INACTIVITY_TIMEOUT_SECONDS,
        *,
        allow_user_questions: bool = True,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ):
        """运行 SessionLoop，若超过 timeout_seconds 无任何活跃交互则取消并抛出异常。"""
        inactivity_triggered: list[bool] = [False]
        question_blocked: list[bool] = [False]

        loop_task = asyncio.create_task(
            SessionLoop.run(
                session_id,
                provider_id=provider_id,
                model_id=model_id,
                callbacks=callbacks,
            )
        )

        async def _watchdog() -> None:
            while not loop_task.done():
                await asyncio.sleep(_WATCHDOG_CHECK_INTERVAL)
                if loop_task.done():
                    break
                # Skip timeout check while waiting for user to answer a question
                try:
                    from flocks.server.routes.question import (
                        has_pending_questions,
                        reject_session_questions,
                    )

                    if has_pending_questions(session_id):
                        if not allow_user_questions:
                            question_blocked[0] = True
                            await reject_session_questions(session_id)
                            loop_task.cancel()
                            break
                        task.last_activity_at = int(datetime.now().timestamp() * 1000)
                        continue
                except Exception:
                    pass
                last = task.last_activity_at or task.started_at or int(datetime.now().timestamp() * 1000)
                elapsed_s = (int(datetime.now().timestamp() * 1000) - last) / 1000
                if elapsed_s > timeout_seconds:
                    log.warn("background.task.inactivity_timeout", {
                        "task_id": task.id,
                        "session_id": session_id,
                        "inactive_seconds": elapsed_s,
                    })
                    inactivity_triggered[0] = True
                    loop_task.cancel()
                    break

        watchdog_task = asyncio.create_task(_watchdog())
        try:
            result = await loop_task
            return result
        except asyncio.CancelledError:
            if question_blocked[0]:
                raise RuntimeError(
                    "Scheduled/background tasks cannot wait for user input. "
                    "Remove question prompts, approvals, or other interactive steps "
                    "from this task."
                )
            if inactivity_triggered[0]:
                raise RuntimeError(
                    f"任务因 {timeout_seconds} 秒内无活跃交互而超时"
                )
            raise
        finally:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass

    async def _run_task(self, task: BackgroundTask, input_data: LaunchInput) -> None:
        async with self._semaphore:
            task.started_at = int(datetime.now().timestamp() * 1000)
            task.last_activity_at = task.started_at
            task.status = "running"
            try:
                parent_session = None
                if input_data.parent_session_id:
                    parent_session = await Session.get_by_id(input_data.parent_session_id)

                directory = (
                    (parent_session.directory if parent_session else None)
                    or input_data.directory
                    or Instance.get_directory()
                )
                project_id = (
                    (parent_session.project_id if parent_session else None)
                    or input_data.project_id
                )
                if not project_id:
                    project = Instance.get_project()
                    project_id = project.id if project else None
                if not project_id or not directory:
                    raise RuntimeError("Failed to resolve project context for background task")

                launch_model = input_data.model or {}
                launch_provider = launch_model.get("providerID")
                launch_model_id = launch_model.get("modelID")
                persist_model = bool(
                    input_data.model_pinned and launch_provider and launch_model_id
                )

                create_kwargs = dict(
                    project_id=project_id,
                    directory=directory,
                    title=f"{input_data.description} (@{input_data.agent} subagent)",
                    parent_id=input_data.parent_session_id,
                    agent=input_data.agent,
                    category=input_data.category or "task",
                )
                if persist_model:
                    create_kwargs.update(
                        model=launch_model_id,
                        provider=launch_provider,
                        model_pinned=True,
                    )

                child_session = await Session.create(
                    **create_kwargs,
                )
                task.session_id = child_session.id

                await Message.create(
                    session_id=child_session.id,
                    role=MessageRole.USER,
                    content=input_data.prompt,
                    agent=input_data.agent,
                )

                callbacks = self._build_activity_callbacks(task)
                runtime_provider = launch_provider if launch_provider and not persist_model else None
                runtime_model = launch_model_id if launch_model_id and not persist_model else None
                result = await self._run_session_with_watchdog(
                    task,
                    child_session.id,
                    callbacks,
                    provider_id=runtime_provider,
                    model_id=runtime_model,
                )
                output = ""
                if result.last_message:
                    output = await Message.get_text_content(result.last_message)
                task.output = output
                task.status = "completed"
                task.completed_at = int(datetime.now().timestamp() * 1000)
                await self._inject_parent_completion(task)
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.completed_at = int(datetime.now().timestamp() * 1000)
                await self._inject_parent_completion(task)
                raise
            except Exception as exc:
                log.error("background.task.error", {"error": str(exc), "task_id": task.id})
                task.error = str(exc)
                task.status = "error"
                task.completed_at = int(datetime.now().timestamp() * 1000)
                await self._inject_parent_completion(task)

    async def _run_resume(self, task: BackgroundTask, input_data: ResumeInput) -> None:
        async with self._semaphore:
            task.started_at = int(datetime.now().timestamp() * 1000)
            task.last_activity_at = task.started_at
            task.status = "running"
            try:
                session = await Session.get_by_id(input_data.session_id)
                if not session:
                    raise RuntimeError(f"Session {input_data.session_id} not found")

                await Message.create(
                    session_id=session.id,
                    role=MessageRole.USER,
                    content=input_data.prompt,
                    agent=session.agent or "rex",
                )

                callbacks = self._build_activity_callbacks(task)
                result = await self._run_session_with_watchdog(
                    task, session.id, callbacks
                )
                output = ""
                if result.last_message:
                    output = await Message.get_text_content(result.last_message)
                task.output = output
                task.status = "completed"
                task.completed_at = int(datetime.now().timestamp() * 1000)
                await self._inject_parent_completion(task)
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.completed_at = int(datetime.now().timestamp() * 1000)
                await self._inject_parent_completion(task)
                raise
            except Exception as exc:
                log.error("background.resume.error", {"error": str(exc), "task_id": task.id})
                task.error = str(exc)
                task.status = "error"
                task.completed_at = int(datetime.now().timestamp() * 1000)
                await self._inject_parent_completion(task)


def _create_manager() -> BackgroundManager:
    return BackgroundManager()


_manager_state = Instance.state(_create_manager)


def get_background_manager() -> BackgroundManager:
    return _manager_state()
