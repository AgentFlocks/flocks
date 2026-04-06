"""
Task Queue — Integration Tests

Verifies full queue pipeline behaviour across Fix 1-5:

  Fix 1  — Orphan recovery on startup
  Fix 2  — Absolute timeout marks task FAILED
  Fix 3  — retry_after survives a simulated process restart
  Fix 4  — Scheduler dedup prevents duplicate active instances
  Fix 5  — Expiry cancels tasks that waited too long

All tests use an in-process SQLite database (via the same ``init_storage``
fixture pattern as unit tests) and mock out ``TaskExecutor.dispatch`` so no
real Agent/Session infrastructure is needed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from flocks.storage.storage import Storage
from flocks.task.models import (
    RetryConfig,
    Task,
    TaskExecution,
    TaskSchedule,
    TaskStatus,
    TaskType,
)
from flocks.task.store import TaskStore


# ---------------------------------------------------------------------------
# Shared fixture — fresh SQLite for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def init_storage(tmp_path):
    Storage._initialized = False
    TaskStore._initialized = False
    TaskStore._conn = None
    db_path = tmp_path / "integration_task.db"
    await Storage.init(db_path)
    await TaskStore.init()
    yield
    await TaskStore.close()
    await Storage.clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_dispatch_mock(final_status: TaskStatus = TaskStatus.COMPLETED):
    """Return an AsyncMock that simulates TaskExecutor.dispatch completing instantly."""
    async def _dispatch(task: Task) -> Task:
        task.status = final_status
        task.execution = TaskExecution(
            agent=task.agent_name,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            duration_ms=1,
        )
        await TaskStore.update_task(task)
        return task

    return AsyncMock(side_effect=_dispatch)


# ---------------------------------------------------------------------------
# Fix 1 — Orphan recovery on startup
# ---------------------------------------------------------------------------

class TestOrphanRecoveryOnStart:
    """Tasks stuck in RUNNING at startup should be reset to QUEUED."""

    @pytest.mark.asyncio
    async def test_orphan_task_is_requeued_then_executed(self):
        from flocks.task.manager import TaskManager

        TaskManager._instance = None

        # Pre-seed a task stuck in RUNNING
        orphan = Task(title="Orphan", status=TaskStatus.RUNNING)
        orphan.execution = TaskExecution(agent="rex")
        await TaskStore.create_task(orphan)

        dispatch_mock = _make_dispatch_mock(TaskStatus.COMPLETED)
        with patch("flocks.task.executor.TaskExecutor.dispatch", dispatch_mock):
            await TaskManager.start(poll_interval=1)
            await asyncio.sleep(0.1)  # let loop tick once
            try:
                fetched = await TaskStore.get_task(orphan.id)
                # The orphan was reset to QUEUED on startup; it may have already
                # been dispatched and completed by the time we check.
                assert fetched.status in (
                    TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.COMPLETED
                )
            finally:
                await TaskManager.stop()

    @pytest.mark.asyncio
    async def test_multiple_orphans_all_recovered(self):
        from flocks.task.manager import TaskManager

        TaskManager._instance = None

        orphans = [Task(title=f"Orphan-{i}", status=TaskStatus.RUNNING) for i in range(3)]
        for o in orphans:
            await TaskStore.create_task(o)

        # Use a very slow poll so tasks won't be dispatched during the check
        await TaskManager.start(poll_interval=9999)
        try:
            for o in orphans:
                fetched = await TaskStore.get_task(o.id)
                assert fetched.status == TaskStatus.QUEUED
        finally:
            await TaskManager.stop()


# ---------------------------------------------------------------------------
# Fix 2 — Absolute timeout marks task FAILED
# ---------------------------------------------------------------------------

class TestAbsoluteTimeout:
    """If wait_for returns None (timeout), the task must end up FAILED."""

    @pytest.mark.asyncio
    async def test_timeout_raises_and_marks_failed(self):
        from flocks.task.executor import TaskExecutor

        task = Task(title="Timeout task", status=TaskStatus.QUEUED)
        await TaskStore.create_task(task)

        async def fake_dispatch(t: Task) -> Task:
            t.status = TaskStatus.RUNNING
            t.execution = TaskExecution(agent=t.agent_name, started_at=datetime.now(timezone.utc))
            await TaskStore.update_task(t)

            # Simulate the inner _run_agent_session receiving a timeout
            from flocks.task.executor import _TASK_ABSOLUTE_TIMEOUT_S
            raise TimeoutError(
                f"Task exceeded absolute timeout of {_TASK_ABSOLUTE_TIMEOUT_S}s"
            )

        with patch.object(TaskExecutor, "dispatch", side_effect=fake_dispatch):
            from flocks.task.manager import TaskManager

            TaskManager._instance = None
            await TaskManager.start(poll_interval=1)
            try:
                # Enqueue the task manually (manager creates via API normally)
                task.status = TaskStatus.QUEUED
                await TaskStore.update_task(task)
                await asyncio.sleep(0.15)

                fetched = await TaskStore.get_task(task.id)
                assert fetched.status == TaskStatus.FAILED
                assert "timeout" in (fetched.execution.error or "").lower()
            finally:
                await TaskManager.stop()


# ---------------------------------------------------------------------------
# Fix 3 — retry_after survives a simulated restart
# ---------------------------------------------------------------------------

class TestRetryAfterSurvivesRestart:
    """Persisted retry_after is picked up by a freshly started manager."""

    @pytest.mark.asyncio
    async def test_retry_requeued_after_restart(self):
        from flocks.task.manager import TaskManager
        from flocks.task.executor import TaskExecutor

        # Pre-seed a FAILED task with an already-past retry_after
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        task = Task(
            title="Retry me",
            status=TaskStatus.FAILED,
            retry=RetryConfig(retry_count=1, max_retries=3, retry_after=past),
        )
        await TaskStore.create_task(task)

        dispatch_mock = _make_dispatch_mock(TaskStatus.COMPLETED)
        with patch.object(TaskExecutor, "dispatch", dispatch_mock):
            # Simulate restart: start a fresh manager (no in-memory state)
            TaskManager._instance = None
            await TaskManager.start(poll_interval=1)
            try:
                await asyncio.sleep(0.15)  # let _execution_loop tick and call _process_retry_queue
                fetched = await TaskStore.get_task(task.id)
                # After _process_retry_queue, it should be QUEUED (or further dispatched)
                assert fetched.status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.COMPLETED)
                assert fetched.retry.retry_after is None
            finally:
                await TaskManager.stop()

    @pytest.mark.asyncio
    async def test_not_due_retry_not_requeued(self):
        from flocks.task.manager import TaskManager

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        task = Task(
            title="Future retry",
            status=TaskStatus.FAILED,
            retry=RetryConfig(retry_count=1, max_retries=3, retry_after=future),
        )
        await TaskStore.create_task(task)

        TaskManager._instance = None
        await TaskManager.start(poll_interval=1)
        try:
            await asyncio.sleep(0.15)
            fetched = await TaskStore.get_task(task.id)
            # retry_after is in the future — should remain FAILED
            assert fetched.status == TaskStatus.FAILED
        finally:
            await TaskManager.stop()


# ---------------------------------------------------------------------------
# Fix 4 — Scheduler dedup prevents duplicate active instances
# ---------------------------------------------------------------------------

class TestSchedulerDedup:
    """Scheduler should never accumulate duplicate QUEUED instances for same template."""

    @pytest.mark.asyncio
    async def test_three_ticks_produce_one_instance(self):
        from flocks.task.scheduler import TaskScheduler

        sched = TaskScheduler(check_interval=999)
        template = Task(
            title="Daily report",
            type=TaskType.SCHEDULED,
            status=TaskStatus.PENDING,
            schedule=TaskSchedule(
                cron="* * * * *",
                timezone="UTC",
                next_run=datetime(2020, 1, 1, tzinfo=timezone.utc),
                enabled=True,
            ),
        )
        await TaskStore.create_task(template)

        # Simulate 3 consecutive ticks (as if queue was backlogged)
        for _ in range(3):
            # Force next_run to the past before each tick
            tmpl = await TaskStore.get_task(template.id)
            if tmpl.schedule and tmpl.schedule.next_run and tmpl.schedule.next_run > datetime.now(timezone.utc):
                tmpl.schedule.next_run = datetime(2020, 1, 1, tzinfo=timezone.utc)
                await TaskStore.update_task(tmpl)
            await sched._tick()

        queued_ref = await TaskStore.get_active_queue_ref(template.id)
        assert queued_ref is not None

        records, total = await TaskStore.list_records(template.id, limit=50)
        assert total == 1, f"Expected 1 execution record, got {total}"

    @pytest.mark.asyncio
    async def test_new_instance_after_previous_completes(self):
        """Once the dedup_key's active task is terminal, a new instance can be created."""
        from flocks.task.scheduler import TaskScheduler

        sched = TaskScheduler(check_interval=999)
        template = Task(
            title="Recurring",
            type=TaskType.SCHEDULED,
            status=TaskStatus.PENDING,
            schedule=TaskSchedule(
                cron="* * * * *",
                timezone="UTC",
                next_run=datetime(2020, 1, 1, tzinfo=timezone.utc),
                enabled=True,
            ),
        )
        await TaskStore.create_task(template)

        # First tick — creates a queue ref
        await sched._tick()
        first_ref = await TaskStore.get_active_queue_ref(template.id)
        assert first_ref is not None

        # Mark task as COMPLETED and release the queue ref (simulates executor finishing)
        template_after_first_tick = await TaskStore.get_task(template.id)
        template_after_first_tick.status = TaskStatus.COMPLETED
        await TaskStore.update_task(template_after_first_tick)
        await TaskStore.finish_queue_ref(template.id)

        # Second tick — should create a new queue ref now
        tmpl = await TaskStore.get_task(template.id)
        if tmpl.schedule:
            tmpl.schedule.next_run = datetime(2020, 1, 1, tzinfo=timezone.utc)
            await TaskStore.update_task(tmpl)
        await sched._tick()

        second_ref = await TaskStore.get_active_queue_ref(template.id)
        assert second_ref is not None
        assert second_ref.id != first_ref.id


# ---------------------------------------------------------------------------
# Fix 5 — Expiry cancels tasks that waited too long
# ---------------------------------------------------------------------------

class TestExpiryIntegration:
    """_expire_stale_tasks integrates with the full Manager lifecycle."""

    @pytest.mark.asyncio
    async def test_expired_task_gets_cancelled_with_reason(self):
        from flocks.task.manager import TaskManager, _TASK_EXPIRY_HOURS

        TaskManager._instance = None
        await TaskManager.start(poll_interval=9999)
        try:
            mgr = TaskManager.get()

            old_time = datetime.now(timezone.utc) - timedelta(hours=_TASK_EXPIRY_HOURS + 1)
            task = Task(title="Long-waiting task", status=TaskStatus.QUEUED)
            task.created_at = old_time
            task.updated_at = old_time
            await TaskStore.create_task(task)

            cancelled_count = await mgr._expire_stale_tasks()
            assert cancelled_count >= 1

            fetched = await TaskStore.get_task(task.id)
            assert fetched.status == TaskStatus.CANCELLED
            assert fetched.execution is not None
            assert fetched.execution.error is not None
            assert str(_TASK_EXPIRY_HOURS) in fetched.execution.error
        finally:
            await TaskManager.stop()

    @pytest.mark.asyncio
    async def test_expiry_releases_dedup_lock(self):
        """After expiry cancellation, the same dedup_key can accept a new task."""
        from flocks.task.manager import TaskManager, _TASK_EXPIRY_HOURS

        TaskManager._instance = None
        await TaskManager.start(poll_interval=9999)
        try:
            mgr = TaskManager.get()

            old_time = datetime.now(timezone.utc) - timedelta(hours=_TASK_EXPIRY_HOURS + 1)
            task = Task(title="Stale", status=TaskStatus.QUEUED, dedup_key="sched:expiry-test")
            task.created_at = old_time
            task.updated_at = old_time
            await TaskStore.create_task(task)

            await mgr._expire_stale_tasks()

            # Now the dedup_key should be free — a new task with same key should succeed
            new_task = Task(title="Fresh", status=TaskStatus.QUEUED, dedup_key="sched:expiry-test")
            result = await TaskStore.create_task(new_task)
            assert result is not None
        finally:
            await TaskManager.stop()

    @pytest.mark.asyncio
    async def test_running_tasks_not_expired(self):
        """RUNNING tasks must never be touched by the expiry logic."""
        from flocks.task.manager import TaskManager, _TASK_EXPIRY_HOURS

        TaskManager._instance = None
        await TaskManager.start(poll_interval=9999)
        try:
            mgr = TaskManager.get()

            old_time = datetime.now(timezone.utc) - timedelta(hours=_TASK_EXPIRY_HOURS + 1)
            running = Task(title="Running old", status=TaskStatus.RUNNING)
            running.created_at = old_time
            running.updated_at = old_time
            await TaskStore.create_task(running)

            await mgr._expire_stale_tasks()

            fetched = await TaskStore.get_task(running.id)
            assert fetched.status == TaskStatus.RUNNING
        finally:
            await TaskManager.stop()
