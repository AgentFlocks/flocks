"""
Tests for Task Center module

Covers: models, store CRUD, queue, manager lifecycle, scheduler helpers.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from flocks.storage.storage import Storage
from flocks.task.models import (
    DeliveryStatus,
    ExecutionMode,
    RetryConfig,
    Task,
    TaskExecution,
    TaskExecutionRecord,
    TaskPriority,
    TaskSchedule,
    TaskSource,
    TaskStatus,
    TaskType,
    build_schedule,
)
from flocks.task.queue import TaskQueue
from flocks.task.store import TaskStore


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def init_storage(tmp_path):
    """Fresh SQLite database for each test."""
    Storage._initialized = False
    TaskStore._initialized = False
    TaskStore._conn = None
    db_path = tmp_path / "test_task.db"
    await Storage.init(db_path)
    await TaskStore.init()
    yield
    await TaskStore.close()
    await Storage.clear()


# ------------------------------------------------------------------
# Model tests
# ------------------------------------------------------------------

class TestModels:
    def test_task_defaults(self):
        t = Task(title="Test task")
        assert t.id.startswith("tsk_")
        assert t.status == TaskStatus.PENDING
        assert t.priority == TaskPriority.NORMAL
        assert t.type == TaskType.QUEUED
        assert t.delivery_status == DeliveryStatus.UNREAD
        assert t.is_terminal is False

    def test_task_is_terminal(self):
        t = Task(title="Done", status=TaskStatus.COMPLETED)
        assert t.is_terminal is True
        t2 = Task(title="Failed", status=TaskStatus.FAILED)
        assert t2.is_terminal is True
        t3 = Task(title="Running", status=TaskStatus.RUNNING)
        assert t3.is_terminal is False

    def test_priority_weight(self):
        assert TaskPriority.URGENT.weight > TaskPriority.HIGH.weight
        assert TaskPriority.HIGH.weight > TaskPriority.NORMAL.weight
        assert TaskPriority.NORMAL.weight > TaskPriority.LOW.weight

    def test_task_touch(self):
        t = Task(title="Old")
        old_ts = t.updated_at
        t.touch()
        assert t.updated_at >= old_ts

    def test_execution_record_defaults(self):
        r = TaskExecutionRecord(task_id="task_123")
        assert r.id.startswith("txe_")
        assert r.status == TaskStatus.RUNNING
        assert r.delivery_status == DeliveryStatus.UNREAD

    def test_scheduled_prompt_preserves_user_prompt(self):
        from flocks.task.executor import TaskExecutor

        task = Task(
            title="Daily task",
            description="Description fallback",
            type=TaskType.SCHEDULED,
            schedule=TaskSchedule(cron="0 8 * * *", timezone="UTC"),
            source=TaskSource(user_prompt="Execute with this exact prompt"),
            context={"scope": "prod"},
        )

        prompt = TaskExecutor._build_prompt(task)
        assert "Scheduled task automated execution" in prompt
        assert "Execute with this exact prompt" in prompt
        assert "scope: prod" in prompt


# ------------------------------------------------------------------
# Store tests
# ------------------------------------------------------------------

class TestStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        task = Task(title="Store test", description="desc")
        created = await TaskStore.create_task(task)
        assert created.id == task.id

        fetched = await TaskStore.get_task(task.id)
        assert fetched is not None
        assert fetched.title == "Store test"

    @pytest.mark.asyncio
    async def test_list_with_filters(self):
        await TaskStore.create_task(Task(title="A", status=TaskStatus.QUEUED, priority=TaskPriority.HIGH))
        await TaskStore.create_task(Task(title="B", status=TaskStatus.COMPLETED))
        await TaskStore.create_task(Task(title="C", status=TaskStatus.QUEUED))

        items, total = await TaskStore.list_tasks(status=TaskStatus.QUEUED)
        assert total == 2
        assert all(t.status == TaskStatus.QUEUED for t in items)

    @pytest.mark.asyncio
    async def test_update_task(self):
        task = Task(title="Before")
        await TaskStore.create_task(task)
        task.title = "After"
        await TaskStore.update_task(task)

        fetched = await TaskStore.get_task(task.id)
        assert fetched.title == "After"

    @pytest.mark.asyncio
    async def test_delete_task(self):
        task = Task(title="Delete me")
        await TaskStore.create_task(task)
        assert await TaskStore.delete_task(task.id) is True
        assert await TaskStore.get_task(task.id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        assert await TaskStore.delete_task("nonexistent") is False

    @pytest.mark.asyncio
    async def test_batch_update_status(self):
        t1 = Task(title="T1", status=TaskStatus.QUEUED)
        t2 = Task(title="T2", status=TaskStatus.QUEUED)
        await TaskStore.create_task(t1)
        await TaskStore.create_task(t2)

        count = await TaskStore.batch_update_status(
            [t1.id, t2.id], TaskStatus.CANCELLED
        )
        assert count == 2
        fetched = await TaskStore.get_task(t1.id)
        assert fetched.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_dequeue_next_priority(self):
        low = Task(title="Low", status=TaskStatus.QUEUED, priority=TaskPriority.LOW)
        high = Task(title="High", status=TaskStatus.QUEUED, priority=TaskPriority.HIGH)
        await TaskStore.create_task(low)
        await TaskStore.create_task(high)

        picked = await TaskStore.dequeue_next()
        assert picked is not None
        assert picked.priority == TaskPriority.HIGH

    @pytest.mark.asyncio
    async def test_dashboard_counts(self):
        await TaskStore.create_task(Task(title="R", status=TaskStatus.RUNNING))
        await TaskStore.create_task(Task(title="Q", status=TaskStatus.QUEUED))
        counts = await TaskStore.dashboard_counts()
        assert counts["running"] == 1
        assert counts["queued"] == 1

    @pytest.mark.asyncio
    async def test_execution_records(self):
        task = Task(title="Sched", type=TaskType.SCHEDULED)
        await TaskStore.create_task(task)

        r = TaskExecutionRecord(
            task_id=task.id,
            started_at=datetime.now(timezone.utc),
        )
        await TaskStore.create_record(r)

        records, total = await TaskStore.list_records(task.id)
        assert total == 1
        assert records[0].task_id == task.id

    @pytest.mark.asyncio
    async def test_get_unviewed_results(self):
        t = Task(title="Done", status=TaskStatus.COMPLETED, delivery_status=DeliveryStatus.UNREAD)
        await TaskStore.create_task(t)
        unviewed = await TaskStore.get_unviewed_results()
        assert len(unviewed) == 1

    @pytest.mark.asyncio
    async def test_pagination(self):
        for i in range(5):
            await TaskStore.create_task(Task(title=f"P{i}"))
        items, total = await TaskStore.list_tasks(offset=2, limit=2)
        assert total == 5
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_sort_order(self):
        t1 = Task(title="First", priority=TaskPriority.LOW)
        t2 = Task(title="Second", priority=TaskPriority.HIGH)
        await TaskStore.create_task(t1)
        await TaskStore.create_task(t2)

        items, _ = await TaskStore.list_tasks(sort_by="priority", sort_order="asc")
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_list_queue_items_includes_scheduled_execution_records(self):
        scheduled = Task(title="Daily hello", type=TaskType.SCHEDULED)
        await TaskStore.create_task(scheduled)
        record = TaskExecutionRecord(
            task_id=scheduled.id,
            status=TaskStatus.COMPLETED,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            completed_at=datetime.now(timezone.utc),
            result_summary="done",
            session_id="ses_demo",
        )
        await TaskStore.create_record(record)

        items, total = await TaskStore.list_queue_items(limit=20)
        assert total >= 1
        matched = next(item for item in items if item.id == record.id)
        assert matched.task_id == scheduled.id
        assert matched.source.source_type == "scheduled_trigger"
        assert matched.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_dashboard_counts_include_scheduled_execution_records(self):
        manual = Task(title="Manual done", status=TaskStatus.COMPLETED)
        await TaskStore.create_task(manual)

        scheduled = Task(
            title="Daily hello",
            type=TaskType.SCHEDULED,
            schedule=TaskSchedule(cron="0 8 * * *", timezone="UTC"),
        )
        await TaskStore.create_task(scheduled)
        await TaskStore.create_record(
            TaskExecutionRecord(
                task_id=scheduled.id,
                status=TaskStatus.COMPLETED,
                started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
                completed_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                result_summary="done",
            )
        )
        await TaskStore.create_record(
            TaskExecutionRecord(
                task_id=scheduled.id,
                status=TaskStatus.FAILED,
                started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                completed_at=datetime.now(timezone.utc),
                error="boom",
            )
        )

        counts = await TaskStore.dashboard_counts()
        assert counts["completed_week"] == 2
        assert counts["completed_unviewed"] == 2
        assert counts["failed_week"] == 1
        assert counts["scheduled_active"] == 1


# ------------------------------------------------------------------
# Queue tests
# ------------------------------------------------------------------

class TestQueue:
    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self):
        q = TaskQueue(max_concurrent=1)
        task = Task(title="Q1", status=TaskStatus.PENDING)
        await TaskStore.create_task(task)
        await q.enqueue(task)

        picked = await q.dequeue()
        assert picked is not None
        assert picked.id == task.id

    @pytest.mark.asyncio
    async def test_paused_queue_returns_none(self):
        q = TaskQueue(max_concurrent=1)
        task = Task(title="Q2", status=TaskStatus.PENDING)
        await TaskStore.create_task(task)
        await q.enqueue(task)

        q.pause()
        assert await q.dequeue() is None

        q.resume()
        assert await q.dequeue() is not None

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        q = TaskQueue(max_concurrent=1)
        t1 = Task(title="C1", status=TaskStatus.QUEUED)
        t2 = Task(title="C2", status=TaskStatus.QUEUED)
        await TaskStore.create_task(t1)
        await TaskStore.create_task(t2)

        # Simulate t1 is running
        t1.status = TaskStatus.RUNNING
        await TaskStore.update_task(t1)

        picked = await q.dequeue()
        assert picked is None  # max_concurrent=1 reached

    @pytest.mark.asyncio
    async def test_queue_status(self):
        q = TaskQueue(max_concurrent=2)
        status = await q.status()
        assert status["max_concurrent"] == 2
        assert status["paused"] is False


# ------------------------------------------------------------------
# Scheduler helper tests
# ------------------------------------------------------------------

class TestSchedulerHelpers:
    def test_compute_next_run(self):
        try:
            from flocks.task.scheduler import TaskScheduler
            result = TaskScheduler.compute_next_run("0 8 * * *")
            if result is not None:
                assert result > datetime.now(timezone.utc)
        except ImportError:
            pytest.skip("croniter not installed")

    def test_compute_next_run_invalid_cron(self):
        try:
            from flocks.task.scheduler import TaskScheduler
            result = TaskScheduler.compute_next_run("invalid cron")
            assert result is None
        except ImportError:
            pytest.skip("croniter not installed")


# ------------------------------------------------------------------
# Manager integration tests
# ------------------------------------------------------------------

class TestManager:
    """Integration tests for TaskManager using mocked execution."""

    @pytest.fixture(autouse=True)
    async def setup_manager(self):
        from flocks.task.manager import TaskManager
        TaskManager._instance = None
        yield
        await TaskManager.stop()

    @pytest.mark.asyncio
    async def test_create_queued_task(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        task = await TaskManager.create_task(title="Test queued")
        assert task.status == TaskStatus.QUEUED
        assert task.type == TaskType.QUEUED

        fetched = await TaskManager.get_task(task.id)
        assert fetched is not None
        assert fetched.title == "Test queued"

    @pytest.mark.asyncio
    async def test_cancel_task(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        task = await TaskManager.create_task(title="To cancel")
        cancelled = await TaskManager.cancel_task(task.id)
        assert cancelled.status == TaskStatus.CANCELLED
        assert cancelled.is_terminal is True

    @pytest.mark.asyncio
    async def test_cancel_terminal_is_noop(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        task = await TaskManager.create_task(title="Already done")
        task.status = TaskStatus.COMPLETED
        await TaskStore.update_task(task)

        result = await TaskManager.cancel_task(task.id)
        assert result.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_retry_failed_task(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        task = await TaskManager.create_task(title="Will fail")
        task.status = TaskStatus.FAILED
        await TaskStore.update_task(task)

        retried = await TaskManager.retry_task(task.id)
        assert retried.status == TaskStatus.QUEUED
        assert retried.retry.retry_count == 1

    @pytest.mark.asyncio
    async def test_update_task_fields(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        task = await TaskManager.create_task(title="Old title")
        updated = await TaskManager.update_task(
            task.id, title="New title", priority=TaskPriority.HIGH
        )
        assert updated.title == "New title"
        assert updated.priority == TaskPriority.HIGH

    @pytest.mark.asyncio
    async def test_delete_task(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        task = await TaskManager.create_task(title="Delete me")
        ok = await TaskManager.delete_task(task.id)
        assert ok is True
        assert await TaskManager.get_task(task.id) is None

    @pytest.mark.asyncio
    async def test_cancel_queue_item_for_scheduled_execution(self):
        from flocks.task.manager import TaskManager

        await TaskManager.start(poll_interval=999)
        task = await TaskManager.create_task(
            title="Scheduled",
            task_type=TaskType.SCHEDULED,
            schedule=TaskSchedule(cron="0 8 * * *", timezone="UTC"),
        )
        record = await TaskManager._enqueue_scheduled_execution(task)

        ok = await TaskManager.cancel_queue_item(record.id)
        assert ok is True

        updated_record = await TaskStore.get_record(record.id)
        assert updated_record is not None
        assert updated_record.status == TaskStatus.CANCELLED
        updated_task = await TaskStore.get_task(task.id)
        assert updated_task is not None
        assert updated_task.status == TaskStatus.PENDING
        scheduled_tasks = await TaskStore.get_scheduled_tasks(enabled_only=True)
        assert task.id in [item.id for item in scheduled_tasks]

    @pytest.mark.asyncio
    async def test_rerun_queue_item_for_scheduled_execution(self):
        from flocks.task.manager import TaskManager

        await TaskManager.start(poll_interval=999)
        task = await TaskManager.create_task(
            title="Scheduled",
            task_type=TaskType.SCHEDULED,
            schedule=TaskSchedule(cron="0 8 * * *", timezone="UTC"),
        )
        record = await TaskManager._enqueue_scheduled_execution(task)
        record.status = TaskStatus.COMPLETED
        record.completed_at = datetime.now(timezone.utc)
        await TaskStore.update_record(record)
        await TaskStore.finish_queue_ref(task.id)

        ok = await TaskManager.rerun_queue_item(record.id)
        assert ok is True

        records, total = await TaskStore.list_records(task.id, limit=10)
        assert total == 2
        assert records[0].id != record.id

    @pytest.mark.asyncio
    async def test_delete_queue_item_for_scheduled_execution(self):
        from flocks.task.manager import TaskManager

        await TaskManager.start(poll_interval=999)
        task = await TaskManager.create_task(
            title="Scheduled",
            task_type=TaskType.SCHEDULED,
            schedule=TaskSchedule(cron="0 8 * * *", timezone="UTC"),
        )
        record = await TaskManager._enqueue_scheduled_execution(task)
        record.status = TaskStatus.COMPLETED
        record.completed_at = datetime.now(timezone.utc)
        await TaskStore.update_record(record)
        await TaskStore.finish_queue_ref(task.id)

        ok = await TaskManager.delete_queue_item(record.id)
        assert ok is True
        assert await TaskStore.get_record(record.id) is None

    @pytest.mark.asyncio
    async def test_batch_cancel(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        t1 = await TaskManager.create_task(title="B1")
        t2 = await TaskManager.create_task(title="B2")
        count = await TaskManager.batch_cancel([t1.id, t2.id])
        assert count == 2

    @pytest.mark.asyncio
    async def test_dashboard(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        await TaskManager.create_task(title="Running task")
        counts = await TaskManager.dashboard()
        assert "queued" in counts
        assert "running" in counts
        assert "queue_paused" in counts

    @pytest.mark.asyncio
    async def test_queue_pause_resume(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        TaskManager.pause_queue()
        status = await TaskManager.queue_status()
        assert status["paused"] is True

        TaskManager.resume_queue()
        status = await TaskManager.queue_status()
        assert status["paused"] is False

    @pytest.mark.asyncio
    async def test_list_tasks_pagination(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        for i in range(5):
            await TaskManager.create_task(title=f"Task {i}")
        items, total = await TaskManager.list_tasks(offset=0, limit=3)
        assert total == 5
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_mark_viewed(self):
        from flocks.task.manager import TaskManager
        await TaskManager.start(poll_interval=999)

        task = await TaskManager.create_task(title="View me")
        task.status = TaskStatus.COMPLETED
        await TaskStore.update_task(task)

        viewed = await TaskManager.mark_viewed(task.id)
        assert viewed.delivery_status == DeliveryStatus.VIEWED


# ------------------------------------------------------------------
# Scheduler trigger tests
# ------------------------------------------------------------------

class TestSchedulerTrigger:
    @pytest.mark.asyncio
    async def test_tick_advances_next_run(self):
        """Verify _tick updates next_run before triggering (race-condition fix)."""
        try:
            from flocks.task.scheduler import TaskScheduler
        except ImportError:
            pytest.skip("croniter not installed")

        sched = TaskScheduler(check_interval=999)
        task = Task(
            title="Scheduled",
            type=TaskType.SCHEDULED,
            status=TaskStatus.PENDING,
            schedule=TaskSchedule(
                cron="* * * * *",
                timezone="UTC",
                next_run=datetime(2020, 1, 1, tzinfo=timezone.utc),
                enabled=True,
            ),
        )
        await TaskStore.create_task(task)

        await sched._tick()

        updated = await TaskStore.get_task(task.id)
        assert updated.schedule.next_run is not None
        assert updated.schedule.next_run > datetime(2020, 1, 1, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_one_shot_disabled_after_trigger(self):
        try:
            from flocks.task.scheduler import TaskScheduler
        except ImportError:
            pytest.skip("croniter not installed")

        sched = TaskScheduler(check_interval=999)
        task = Task(
            title="One-time",
            type=TaskType.SCHEDULED,
            status=TaskStatus.PENDING,
            schedule=TaskSchedule(
                run_once=True,
                run_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                timezone="UTC",
                enabled=True,
            ),
        )
        await TaskStore.create_task(task)

        await sched._tick()

        updated = await TaskStore.get_task(task.id)
        assert updated.schedule.enabled is False

    @pytest.mark.asyncio
    async def test_trigger_creates_execution_record(self):
        """Verify _trigger creates an execution record and queue ref on the same task."""
        try:
            from flocks.task.scheduler import TaskScheduler
        except ImportError:
            pytest.skip("croniter not installed")

        sched = TaskScheduler(check_interval=999)
        task = Task(
            title="Scheduled with record",
            type=TaskType.SCHEDULED,
            status=TaskStatus.PENDING,
            schedule=TaskSchedule(
                cron="* * * * *",
                timezone="UTC",
                next_run=datetime(2020, 1, 1, tzinfo=timezone.utc),
                enabled=True,
            ),
        )
        await TaskStore.create_task(task)
        await sched._tick()

        records, total = await TaskStore.list_records(task.id)
        assert total == 1
        assert records[0].status == TaskStatus.QUEUED

        queued_ref = await TaskStore.get_active_queue_ref(task.id)
        assert queued_ref is not None
        assert queued_ref.execution_record_id == records[0].id

        updated = await TaskStore.get_task(task.id)
        assert "_execution_record_id" in updated.context
        assert updated.context["_execution_record_id"] == records[0].id


# ------------------------------------------------------------------
# Store column persistence tests (regression for DDL fix)
# ------------------------------------------------------------------

class TestStoreColumnPersistence:
    """Verify execution_mode/agent_name/workflow_id/skills/category survive roundtrip."""

    @pytest.mark.asyncio
    async def test_persists_execution_mode(self):
        task = Task(
            title="Workflow task",
            execution_mode=ExecutionMode.WORKFLOW,
            agent_name="hephaestus",
            workflow_id="wf_123",
            skills=["skill_a", "skill_b"],
            category="security",
        )
        await TaskStore.create_task(task)

        fetched = await TaskStore.get_task(task.id)
        assert fetched.execution_mode == ExecutionMode.WORKFLOW
        assert fetched.agent_name == "hephaestus"
        assert fetched.workflow_id == "wf_123"
        assert fetched.skills == ["skill_a", "skill_b"]
        assert fetched.category == "security"

    @pytest.mark.asyncio
    async def test_update_preserves_columns(self):
        task = Task(
            title="Agent task",
            execution_mode=ExecutionMode.AGENT,
            agent_name="oracle",
        )
        await TaskStore.create_task(task)
        task.title = "Updated title"
        await TaskStore.update_task(task)

        fetched = await TaskStore.get_task(task.id)
        assert fetched.title == "Updated title"
        assert fetched.agent_name == "oracle"

    @pytest.mark.asyncio
    async def test_defaults_for_missing_values(self):
        task = Task(title="Defaults")
        await TaskStore.create_task(task)

        fetched = await TaskStore.get_task(task.id)
        assert fetched.execution_mode == ExecutionMode.AGENT
        assert fetched.agent_name == "rex"
        assert fetched.workflow_id is None
        assert fetched.skills == []
        assert fetched.category is None

    @pytest.mark.asyncio
    async def test_batch_delete(self):
        t1 = Task(title="BD1")
        t2 = Task(title="BD2")
        t3 = Task(title="BD3")
        await TaskStore.create_task(t1)
        await TaskStore.create_task(t2)
        await TaskStore.create_task(t3)

        count = await TaskStore.batch_delete([t1.id, t2.id])
        assert count == 2
        assert await TaskStore.get_task(t1.id) is None
        assert await TaskStore.get_task(t2.id) is None
        assert await TaskStore.get_task(t3.id) is not None


# ------------------------------------------------------------------
# build_schedule helper tests
# ------------------------------------------------------------------

class TestBuildSchedule:
    def test_recurring_schedule(self):
        sched = build_schedule(cron="0 8 * * *", cron_description="每天早上8点")
        assert sched.cron == "0 8 * * *"
        assert sched.run_once is False
        assert sched.cron_description == "每天早上8点"

    def test_one_time_schedule_with_run_at(self):
        sched = build_schedule(
            run_once=True,
            run_at="2025-01-15T18:00:00+08:00",
            cron_description="1月15日下午6点执行一次",
        )
        assert sched.run_once is True
        assert sched.run_at is not None

    def test_recurring_missing_cron_raises(self):
        with pytest.raises(ValueError, match="cron is required"):
            build_schedule()

    def test_one_time_missing_fields_raises(self):
        with pytest.raises(ValueError, match="run_at or cron is required"):
            build_schedule(run_once=True)

    def test_invalid_run_at_raises(self):
        with pytest.raises(ValueError, match="Invalid run_at"):
            build_schedule(run_once=True, run_at="not-a-date")


# ------------------------------------------------------------------
# Executor tests
# ------------------------------------------------------------------

class TestExecutor:
    @pytest.mark.asyncio
    async def test_build_prompt_basic(self):
        from flocks.task.executor import TaskExecutor

        task = Task(
            title="Test",
            description="Run a scan",
            source=TaskSource(user_prompt="scan network"),
        )
        prompt = TaskExecutor._build_prompt(task)
        assert "scan network" in prompt

    @pytest.mark.asyncio
    async def test_build_prompt_with_context(self):
        from flocks.task.executor import TaskExecutor

        task = Task(
            title="Test",
            description="Scan",
            source=TaskSource(user_prompt="scan"),
            context={"target": "10.0.0.0/24"},
        )
        prompt = TaskExecutor._build_prompt(task)
        assert "target" in prompt
        assert "10.0.0.0/24" in prompt

    @pytest.mark.asyncio
    async def test_build_prompt_scheduled_trigger(self):
        from flocks.task.executor import TaskExecutor

        task = Task(
            title="Recurring scan",
            description="Scan the internal network",
            source=TaskSource(
                source_type="scheduled_trigger",
                user_prompt="每天扫描内网",
            ),
        )
        prompt = TaskExecutor._build_prompt(task)
        assert "Scheduled task" in prompt
        assert "Do NOT call task_create" in prompt
        assert "Scan the internal network" in prompt


# ------------------------------------------------------------------
# Task tool tests (task_center.py)
# ------------------------------------------------------------------

class TestTaskTools:
    @pytest.fixture(autouse=True)
    async def setup_manager(self):
        from flocks.task.manager import TaskManager
        TaskManager._instance = None
        await TaskManager.start(poll_interval=999)
        yield
        await TaskManager.stop()

    @pytest.mark.asyncio
    async def test_task_create_tool(self):
        from flocks.tool.task.task_center import task_create

        ctx = type("MockCtx", (), {"session_id": "sess_test"})()
        result = await task_create(
            ctx,
            title="Tool test",
            description="Created by tool",
            type="queued",
        )
        assert result.success is True
        assert "Tool test" in result.output

    @pytest.mark.asyncio
    async def test_task_list_tool(self):
        from flocks.tool.task.task_center import task_create, task_list

        ctx = type("MockCtx", (), {"session_id": "sess_test"})()
        await task_create(ctx, title="Listable", description="d", type="queued")

        result = await task_list(ctx)
        assert result.success is True
        assert "Listable" in result.output

    @pytest.mark.asyncio
    async def test_task_status_tool(self):
        from flocks.task.manager import TaskManager
        from flocks.tool.task.task_center import task_status

        ctx = type("MockCtx", (), {"session_id": None})()
        task = await TaskManager.create_task(title="Status check")

        result = await task_status(ctx, task_id=task.id)
        assert result.success is True
        assert "Status check" in result.output

    @pytest.mark.asyncio
    async def test_task_update_cancel_tool(self):
        from flocks.task.manager import TaskManager
        from flocks.tool.task.task_center import task_update

        ctx = type("MockCtx", (), {"session_id": None})()
        task = await TaskManager.create_task(title="Cancel me")

        result = await task_update(ctx, task_id=task.id, action="cancel")
        assert result.success is True
        assert "cancelled" in result.output.lower()

    @pytest.mark.asyncio
    async def test_task_delete_tool(self):
        from flocks.task.manager import TaskManager
        from flocks.tool.task.task_center import task_delete

        ctx = type("MockCtx", (), {"session_id": None})()
        task = await TaskManager.create_task(title="Delete me")

        result = await task_delete(ctx, task_id=task.id)
        assert result.success is True
        assert await TaskManager.get_task(task.id) is None

    @pytest.mark.asyncio
    async def test_task_create_scheduled(self):
        from flocks.tool.task.task_center import task_create

        ctx = type("MockCtx", (), {"session_id": "sess_test"})()
        result = await task_create(
            ctx,
            title="Daily scan",
            description="Scan every day",
            type="scheduled",
            cron="0 8 * * *",
            cron_description="每天早上8点",
        )
        assert result.success is True
        assert "Daily scan" in result.output

    @pytest.mark.asyncio
    async def test_task_create_invalid_schedule(self):
        from flocks.tool.task.task_center import task_create

        ctx = type("MockCtx", (), {"session_id": "sess_test"})()
        result = await task_create(
            ctx,
            title="Bad schedule",
            description="Missing cron",
            type="scheduled",
            run_once=True,
        )
        assert result.success is False


# ------------------------------------------------------------------
# Fix 1 — Startup Recovery
# ------------------------------------------------------------------

class TestStartupRecovery:
    """TaskStore.list_by_status + TaskManager._recover_orphaned_tasks."""

    @pytest.mark.asyncio
    async def test_list_by_status_returns_matching(self):
        running = Task(title="Running", status=TaskStatus.RUNNING)
        queued = Task(title="Queued", status=TaskStatus.QUEUED)
        await TaskStore.create_task(running)
        await TaskStore.create_task(queued)

        result = await TaskStore.list_by_status(TaskStatus.RUNNING)
        ids = [t.id for t in result]
        assert running.id in ids
        assert queued.id not in ids

    @pytest.mark.asyncio
    async def test_list_by_status_includes_scheduled_runtime_tasks(self):
        """Scheduled tasks now reuse the same task definition and must be recoverable."""
        orphan = Task(title="Orphan", status=TaskStatus.RUNNING, type=TaskType.QUEUED)
        scheduled = Task(
            title="Template", status=TaskStatus.RUNNING, type=TaskType.SCHEDULED,
        )
        await TaskStore.create_task(orphan)
        await TaskStore.create_task(scheduled)

        result = await TaskStore.list_by_status(TaskStatus.RUNNING)
        ids = [t.id for t in result]
        assert orphan.id in ids
        assert scheduled.id in ids

    @pytest.mark.asyncio
    async def test_recover_orphaned_tasks_resets_to_queued(self):
        from flocks.task.manager import TaskManager

        TaskManager._instance = None
        orphan = Task(title="Orphan", status=TaskStatus.RUNNING)
        await TaskStore.create_task(orphan)

        # Start manager — recovery runs automatically
        await TaskManager.start(poll_interval=9999)
        try:
            fetched = await TaskStore.get_task(orphan.id)
            assert fetched.status == TaskStatus.QUEUED
        finally:
            await TaskManager.stop()


# ------------------------------------------------------------------
# Fix 3 — Persistent retry_after
# ------------------------------------------------------------------

class TestRetryAfterPersistence:
    """RetryConfig.retry_after survives DB round-trips."""

    @pytest.mark.asyncio
    async def test_retry_after_persists_in_db(self):


        task = Task(title="Retryable", status=TaskStatus.FAILED, retry=RetryConfig())
        await TaskStore.create_task(task)

        retry_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        task.retry.retry_after = retry_at
        task.retry.retry_count = 1
        await TaskStore.update_task(task)

        fetched = await TaskStore.get_task(task.id)
        assert fetched.retry.retry_after is not None
        # Timestamps survive ISO-round-trip, compare to the second
        diff = abs((fetched.retry.retry_after - retry_at).total_seconds())
        assert diff < 2

    @pytest.mark.asyncio
    async def test_list_retryable_failed_returns_due_tasks(self):


        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        future = datetime.now(timezone.utc) + timedelta(seconds=3600)

        due = Task(
            title="Due retry",
            status=TaskStatus.FAILED,
            retry=RetryConfig(retry_count=1, max_retries=3, retry_after=past),
        )
        not_due = Task(
            title="Not due",
            status=TaskStatus.FAILED,
            retry=RetryConfig(retry_count=1, max_retries=3, retry_after=future),
        )
        exhausted = Task(
            title="Exhausted",
            status=TaskStatus.FAILED,
            retry=RetryConfig(retry_count=3, max_retries=3, retry_after=past),
        )
        for t in (due, not_due, exhausted):
            await TaskStore.create_task(t)

        result = await TaskStore.list_retryable_failed()
        ids = [t.id for t in result]
        assert due.id in ids
        assert not_due.id not in ids
        assert exhausted.id not in ids

    @pytest.mark.asyncio
    async def test_process_retry_queue_requeues_due_tasks(self):

        from flocks.task.manager import TaskManager

        TaskManager._instance = None
        await TaskManager.start(poll_interval=9999)
        try:
            mgr = TaskManager.get()

            past = datetime.now(timezone.utc) - timedelta(seconds=5)
            task = Task(
                title="Due retry",
                status=TaskStatus.FAILED,
                retry=RetryConfig(retry_count=1, max_retries=3, retry_after=past),
            )
            await TaskStore.create_task(task)

            await mgr._process_retry_queue()

            fetched = await TaskStore.get_task(task.id)
            assert fetched.status == TaskStatus.QUEUED
            assert fetched.retry.retry_after is None
        finally:
            await TaskManager.stop()


# ------------------------------------------------------------------
# Fix 4 — dedup_key (Scheduler Deduplication)
# ------------------------------------------------------------------

class TestDedupKey:
    """Task.dedup_key prevents duplicate active tasks."""

    @pytest.mark.asyncio
    async def test_create_task_dedup_skips_duplicate(self):
        task1 = Task(title="First", dedup_key="sched:template-1", status=TaskStatus.QUEUED)
        task2 = Task(title="Second", dedup_key="sched:template-1", status=TaskStatus.QUEUED)

        result1 = await TaskStore.create_task(task1)
        result2 = await TaskStore.create_task(task2)

        assert result1 is not None
        assert result2 is None  # dedup skipped

        items, total = await TaskStore.list_tasks(status=TaskStatus.QUEUED)
        assert total == 1
        assert items[0].id == task1.id

    @pytest.mark.asyncio
    async def test_create_task_dedup_allows_after_terminal(self):
        completed = Task(
            title="Done", dedup_key="sched:tmpl-2", status=TaskStatus.COMPLETED,
        )
        await TaskStore.create_task(completed)

        new_task = Task(title="New run", dedup_key="sched:tmpl-2", status=TaskStatus.QUEUED)
        result = await TaskStore.create_task(new_task)

        assert result is not None
        assert result.id == new_task.id

    @pytest.mark.asyncio
    async def test_create_task_no_dedup_key_always_inserts(self):
        t1 = Task(title="Task A")
        t2 = Task(title="Task B")

        r1 = await TaskStore.create_task(t1)
        r2 = await TaskStore.create_task(t2)

        assert r1 is not None
        assert r2 is not None

    @pytest.mark.asyncio
    async def test_dedup_key_persists_roundtrip(self):
        task = Task(title="Keyed", dedup_key="sched:roundtrip")
        await TaskStore.create_task(task)

        fetched = await TaskStore.get_task(task.id)
        assert fetched.dedup_key == "sched:roundtrip"

    @pytest.mark.asyncio
    async def test_scheduler_sets_dedup_key(self):
        """TaskScheduler keeps only one active queue ref per scheduled task."""
        from flocks.task.scheduler import TaskScheduler

        sched = TaskScheduler(check_interval=999)
        template = Task(
            title="Daily",
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
        await sched._tick()

        queued_ref = await TaskStore.get_active_queue_ref(template.id)
        assert queued_ref is not None
        assert queued_ref.task_id == template.id

    @pytest.mark.asyncio
    async def test_scheduler_dedup_skip_on_second_tick(self):
        """Second scheduler tick does NOT create a duplicate active queue ref."""
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

        # First tick — creates instance
        await sched._tick()
        # Force next_run to past again to simulate another fire
        tmpl = await TaskStore.get_task(template.id)
        if tmpl.schedule:
            tmpl.schedule.next_run = datetime(2020, 1, 1, tzinfo=timezone.utc)
            await TaskStore.update_task(tmpl)

        # Second tick — should be deduplicated
        await sched._tick()

        queued_ref = await TaskStore.get_active_queue_ref(template.id)
        assert queued_ref is not None
        records, total = await TaskStore.list_records(template.id, limit=10)
        assert total == 1


# ------------------------------------------------------------------
# Fix 5 — Expiry Auto-Cancel
# ------------------------------------------------------------------

class TestExpiryAutoCancel:
    """TaskStore.list_stale_queued + TaskManager._expire_stale_tasks."""

    @pytest.mark.asyncio
    async def test_list_stale_queued_returns_old_tasks(self):


        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        fresh_time = datetime.now(timezone.utc)

        stale = Task(title="Stale", status=TaskStatus.QUEUED)
        stale.created_at = old_time
        stale.updated_at = old_time
        await TaskStore.create_task(stale)

        fresh = Task(title="Fresh", status=TaskStatus.QUEUED)
        fresh.created_at = fresh_time
        fresh.updated_at = fresh_time
        await TaskStore.create_task(fresh)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = await TaskStore.list_stale_queued(before=cutoff)
        ids = [t.id for t in result]
        assert stale.id in ids
        assert fresh.id not in ids

    @pytest.mark.asyncio
    async def test_list_stale_queued_excludes_running(self):


        old_time = datetime.now(timezone.utc) - timedelta(hours=30)

        running = Task(title="Running old", status=TaskStatus.RUNNING)
        running.created_at = old_time
        running.updated_at = old_time
        await TaskStore.create_task(running)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = await TaskStore.list_stale_queued(before=cutoff)
        ids = [t.id for t in result]
        assert running.id not in ids

    @pytest.mark.asyncio
    async def test_expire_stale_tasks_cancels_and_sets_error(self):

        from flocks.task.manager import TaskManager, _TASK_EXPIRY_HOURS

        TaskManager._instance = None
        await TaskManager.start(poll_interval=9999)
        try:
            mgr = TaskManager.get()

            old_time = datetime.now(timezone.utc) - timedelta(hours=_TASK_EXPIRY_HOURS + 1)
            task = Task(title="Old queued", status=TaskStatus.QUEUED)
            task.created_at = old_time
            task.updated_at = old_time
            await TaskStore.create_task(task)

            cancelled = await mgr._expire_stale_tasks()
            assert cancelled >= 1

            fetched = await TaskStore.get_task(task.id)
            assert fetched.status == TaskStatus.CANCELLED
            assert fetched.execution is not None
            assert "自动取消" in fetched.execution.error
        finally:
            await TaskManager.stop()

    @pytest.mark.asyncio
    async def test_expire_does_not_cancel_recent_tasks(self):

        from flocks.task.manager import TaskManager

        TaskManager._instance = None
        await TaskManager.start(poll_interval=9999)
        try:
            mgr = TaskManager.get()

            task = Task(title="Recent queued", status=TaskStatus.QUEUED)
            await TaskStore.create_task(task)

            await mgr._expire_stale_tasks()

            fetched = await TaskStore.get_task(task.id)
            assert fetched.status == TaskStatus.QUEUED
        finally:
            await TaskManager.stop()
