"""P1 crash/restart, election, backlog, and bounded database load regressions."""

import asyncio
import json
import os
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from filelock import FileLock
import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.evidence import FailureEvidence
from flocks.task.executor import TaskExecutor
from flocks.task.manager import TaskManager
from flocks.task.models import (
    ExecutionMode, ExecutionTriggerType, MisfirePolicy, SchedulerMode,
    TaskExecution, TaskStatus, TaskTrigger,
)
from flocks.task.receipts import CompletionReceipts, disk_operation, initialization_lock
from flocks.task.scheduler import TaskScheduler as SchedulerLoop
from flocks.task.store import TaskStore


@pytest.fixture(autouse=True)
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / ".flocks"))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    Config._global_config = Config._cached_config = None
    Storage._initialized = False
    Storage._db_path = None
    TaskManager._instance = None
    await TaskStore.close()
    await Storage.init()
    await TaskStore.init()
    yield
    await TaskManager.stop()
    await TaskStore.close()
    Storage._initialized = False
    Storage._db_path = None
    Config._global_config = Config._cached_config = None


async def definition(**kwargs):
    return await TaskManager.create_scheduler(
        title="durability test", mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="* * * * *", nextRun=datetime.now(timezone.utc) + timedelta(days=1), **kwargs),
        execution_mode=ExecutionMode.WORKFLOW, workflow_id="offline-fixture",
    )


async def claimed(scheduler=None):
    scheduler = scheduler or await definition()
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RERUN)
    execution, _ = await TaskStore.claim_next_queue_execution()
    return scheduler, execution


def terminal(execution, status=TaskStatus.COMPLETED):
    result = execution.model_copy(deep=True)
    result.status = status
    result.completed_at = datetime.now(timezone.utc)
    result.result_summary = "business ran exactly once in the fixture"
    return result


@pytest.mark.parametrize("phase", ["started", "completed"])
async def test_real_process_exit_preserves_completion_or_holds_uncertainty(phase, monkeypatch):
    _, execution = await claimed()
    fact = terminal(execution) if phase == "completed" else execution
    receipts = CompletionReceipts.for_store()
    # The child exits without Python cleanup. It never starts an application
    # instance and uses only this test's temporary receipt directory.
    script = """
import json, os, sys
from pathlib import Path
from flocks.task.models import TaskExecution
from flocks.task.receipts import CompletionReceipts
value = json.load(sys.stdin)
CompletionReceipts(Path(value['root']))._write(TaskExecution.model_validate(value['execution']), value['phase'])
os._exit(23)
"""
    child = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script],
        input=json.dumps({"root": str(receipts.root), "phase": phase,
                          "execution": fact.model_dump(mode="json")}),
        text=True, capture_output=True, timeout=15,
    )
    assert child.returncode == 23, child.stderr
    dispatch = AsyncMock()
    monkeypatch.setattr(TaskExecutor, "dispatch", dispatch)
    restarted = TaskManager()
    await restarted._reconcile_receipts()
    current = await TaskStore.get_execution(execution.id)
    if phase == "completed":
        assert current.status == TaskStatus.COMPLETED
        assert current.result_summary == fact.result_summary
        assert restarted._recovery_counts["finished_unsynced"] == 1
    else:
        assert current.status == TaskStatus.FAILED
        assert current.retry.blocked_reason == "unverified_effects"
        assert current.retry.retry_after is None
        assert restarted._recovery_counts["interrupted"] == 1
    dispatch.assert_not_called()
    assert not receipts.paths()


async def test_database_save_failure_replays_receipt_after_restart_without_business(monkeypatch):
    scheduler, execution = await claimed()
    manager = TaskManager()
    manager.queue.mark_started(execution.id)
    run = AsyncMock(return_value="done once")
    monkeypatch.setattr(TaskExecutor, "_trigger_workflow", run)
    original = TaskStore.update_active_execution
    monkeypatch.setattr(TaskStore, "update_active_execution", AsyncMock(side_effect=sqlite3.OperationalError("locked")))
    await manager._run_execution(execution)
    assert len(manager._pending_completions) == 1
    assert CompletionReceipts.for_store().read(CompletionReceipts.for_store().paths()[0])[0] == "completed"
    monkeypatch.setattr(TaskStore, "update_active_execution", original)
    await TaskManager()._reconcile_receipts()
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.COMPLETED
    assert run.await_count == 1


@pytest.mark.parametrize("new_state", ["new_attempt", "cancelled", "deleted", "already_saved"])
async def test_receipt_replay_respects_newer_attempt_and_user_changes(new_state):
    _, execution = await claimed()
    fact = terminal(execution)
    receipts = CompletionReceipts.for_store()
    await receipts.completed(fact)
    current = execution.model_copy(deep=True)
    if new_state == "new_attempt":
        current.started_at += timedelta(microseconds=1)
        current.result_summary = "new owner"
        await TaskStore.update_execution(current)
    elif new_state == "cancelled":
        await TaskStore.cancel_active_execution(execution.id, completed_at=datetime.now(timezone.utc),
                                               duration_ms=None, error="user cancelled")
    elif new_state == "deleted":
        await TaskStore.delete_execution(execution.id)
    else:
        await TaskStore.update_active_execution(fact)
    await TaskManager()._reconcile_receipts()
    stored = await TaskStore.get_execution(execution.id)
    if new_state == "new_attempt":
        assert stored.status == TaskStatus.RUNNING and stored.result_summary == "new owner"
    elif new_state == "deleted":
        assert stored is None
    else:
        assert stored.status == (TaskStatus.CANCELLED if new_state == "cancelled" else TaskStatus.COMPLETED)
    assert not receipts.paths()


async def test_corrupt_receipt_pauses_all_startup_dispatch_and_preserves_files(monkeypatch):
    _, execution = await claimed()
    receipts = CompletionReceipts.for_store()
    await receipts.completed(terminal(execution))
    bad = receipts.root / ("f" * 64 + ".json")
    bad.write_text("broken", encoding="utf-8")
    dispatch = AsyncMock()
    monkeypatch.setattr(TaskExecutor, "dispatch", dispatch)
    manager = await TaskManager.start(poll_interval=100)
    state = TaskManager.health_snapshot()
    assert manager._reconciling and state["status"] == "degraded"
    assert state["role"] == "leader"
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.RUNNING
    assert len(receipts.paths()) == 2
    dispatch.assert_not_called()


async def test_failure_policy_survives_crash_after_terminal_commit(monkeypatch):
    _, execution = await claimed()
    fact = terminal(execution, TaskStatus.FAILED)
    await CompletionReceipts.for_store().completed(fact)
    await TaskStore.update_active_execution(fact)
    from flocks.task.outcome import RunOutcome, SAFE_TO_RETRY
    monkeypatch.setattr("flocks.task.outcome.run_outcome", AsyncMock(return_value=RunOutcome(SAFE_TO_RETRY)))
    await TaskManager()._reconcile_receipts()
    current = await TaskStore.get_execution(execution.id)
    assert current.retry.retry_count == 1 and current.retry.retry_after
    # Repeated receipt replay must not schedule another retry of this failure.
    await CompletionReceipts.for_store().completed(fact)
    await TaskManager()._reconcile_receipts()
    assert (await TaskStore.get_execution(execution.id)).retry.retry_count == 1


async def test_receipt_storage_failure_stops_business_before_dispatch(monkeypatch):
    _, execution = await claimed()
    run = AsyncMock()
    monkeypatch.setattr(TaskExecutor, "dispatch", run)
    monkeypatch.setattr(CompletionReceipts, "MAX_TOTAL_BYTES", 1)
    manager = TaskManager()
    await manager._run_execution(execution)
    run.assert_not_called()
    current = await TaskStore.get_execution(execution.id)
    assert current.status == TaskStatus.QUEUED and current.retry.retry_count == 0
    assert manager._receipt_admission_failed


async def test_receipts_are_private_and_detect_content_tampering():
    _, execution = await claimed()
    receipts = CompletionReceipts.for_store()
    await receipts.completed(terminal(execution))
    path = receipts.paths()[0]
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(receipts.root.stat().st_mode) == 0o700
    value = json.loads(path.read_text())
    value["execution"]["resultSummary"] = "tampered"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="checksum"):
        receipts.read(path)


async def test_second_manager_stays_standby_then_acquires_released_leadership(monkeypatch):
    first, second = TaskManager(), TaskManager()
    await first._try_become_leader()
    try:
        await second._try_become_leader()
        assert first._is_leader and not second._is_leader
        _, execution = await claimed()
        # start() uses a third lock holder and must not touch the active row.
        follower = await TaskManager.start(poll_interval=100)
        assert not follower._is_leader
        assert TaskManager.health_snapshot()["role"] == "standby"
        assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.RUNNING
        await TaskManager.stop()
        first._leadership.release()
        await second._try_become_leader()
        assert second._is_leader
    finally:
        first._leadership.release()
        if second._leadership:
            second._leadership.release()


async def test_manager_lock_excludes_a_separate_process():
    manager = TaskManager()
    await manager._try_become_leader()
    try:
        script = """
import sys
from filelock import FileLock, Timeout
try:
    FileLock(sys.argv[1]).acquire(timeout=0)
except Timeout:
    sys.exit(42)
sys.exit(0)
"""
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script, manager._leadership.lock_file],
                                         capture_output=True, timeout=10)
        assert result.returncode == 42
    finally:
        manager._leadership.release()


async def test_core_initialization_lock_is_bounded_and_does_not_block_loop(tmp_path):
    path = tmp_path / "init.lock"
    lock = FileLock(str(path))
    lock.acquire(timeout=0)
    ticks = 0
    async def ticker():
        nonlocal ticks
        for _ in range(10):
            ticks += 1
            await asyncio.sleep(0.01)
    ticking = asyncio.create_task(ticker())
    try:
        from filelock import Timeout
        with pytest.raises(Timeout):
            async with initialization_lock(path, timeout=0.1):
                pytest.fail("second initializer entered")
        assert ticks >= 5
        await ticking
        assert ticks == 10
    finally:
        lock.release()


@pytest.mark.parametrize("policy,expected", [(MisfirePolicy.COALESCE, 1), (MisfirePolicy.SKIP, 0)])
async def test_overdue_policy_is_atomic_and_audited(policy, expected):
    scheduler = await definition(misfirePolicy=policy)
    now = datetime.now(timezone.utc)
    scheduler.trigger.next_run = now - timedelta(hours=5)
    await TaskStore.update_scheduler(scheduler)
    await SchedulerLoop()._tick_scheduler(scheduler, now)
    executions, count = await TaskStore.list_scheduler_executions(scheduler.id)
    assert count == expected
    stored = await TaskStore.get_scheduler(scheduler.id)
    assert stored.trigger.next_run > now
    assert stored.trigger.last_decision["reason"] == ("coalesced" if expected else "missed_deadline")
    if expected:
        assert executions[0].execution_input_snapshot["scheduleDecision"] == stored.trigger.last_decision


async def test_catch_up_budget_survives_reload_and_caps_backlog():
    scheduler = await definition(misfirePolicy="catch_up", maxCatchUp=3)
    now = datetime.now(timezone.utc)
    scheduler.trigger.next_run = now - timedelta(days=3)
    await TaskStore.update_scheduler(scheduler)
    for index in range(3):
        scheduler = await TaskStore.get_scheduler(scheduler.id)
        await SchedulerLoop()._tick_scheduler(scheduler, now)
        rows, total = await TaskStore.list_scheduler_executions(scheduler.id)
        assert total == index + 1
        execution = next(e for e in rows if not e.is_terminal)
        await TaskStore.update_execution(terminal(execution))
    stored = await TaskStore.get_scheduler(scheduler.id)
    assert stored.trigger.next_run > now
    assert stored.trigger.catch_up_until is None
    assert stored.trigger.last_decision["reason"] == "catch_up_limit"
    await SchedulerLoop()._tick_scheduler(stored, now)
    assert (await TaskStore.list_scheduler_executions(scheduler.id))[1] == 3


async def test_skip_allows_normal_poll_lateness_within_grace():
    scheduler = await definition(misfirePolicy="skip", misfireGraceSeconds=60)
    now = datetime.now(timezone.utc)
    scheduler.trigger.next_run = now - timedelta(seconds=20)
    await TaskStore.update_scheduler(scheduler)
    await SchedulerLoop()._tick_scheduler(scheduler, now)
    assert (await TaskStore.list_scheduler_executions(scheduler.id))[1] == 1


async def test_stale_skip_decision_cannot_undo_user_edit():
    scheduler = await definition(misfirePolicy="skip")
    now = datetime.now(timezone.utc)
    scheduler.trigger.next_run = now - timedelta(hours=1)
    await TaskStore.update_scheduler(scheduler)
    stale = scheduler.model_copy(deep=True)
    scheduler.title = "edited by user"
    await TaskStore.update_scheduler(scheduler)
    await SchedulerLoop()._tick_scheduler(stale, now)
    stored = await TaskStore.get_scheduler(scheduler.id)
    assert stored.title == "edited by user" and stored.trigger.next_run < now


async def test_dashboard_single_flight_core_invalidation_and_external_ttl():
    scheduler = await definition()
    db = await TaskStore._db()
    statements = []
    await db.set_trace_callback(statements.append)
    counts = await asyncio.gather(*(TaskStore.dashboard_counts() for _ in range(40)))
    assert len([s for s in statements if "AS completed_week" in s]) == 1
    counts[0]["running"] = 999
    assert (await TaskStore.dashboard_counts())["running"] == 0
    _, execution = await claimed(scheduler)
    assert (await TaskStore.dashboard_counts())["running"] == 1
    with sqlite3.connect(TaskStore.get_db_path()) as other:
        other.execute("UPDATE task_executions SET status='completed' WHERE id=?", (execution.id,))
    assert (await TaskStore.dashboard_counts())["running"] == 1
    stamp, cache = TaskStore._dashboard_cache
    TaskStore._dashboard_cache = (stamp - 3, cache)
    assert (await TaskStore.dashboard_counts())["running"] == 0


async def test_first_failure_is_sanitized_aggregated_and_persists_after_restart(tmp_path):
    evidence = FailureEvidence(tmp_path / "failures.json")
    try:
        error = sqlite3.OperationalError("SECRET SQL / token / prompt")
        error.sqlite_errorcode = 517
        raise error
    except sqlite3.Error as exc:
        for _ in range(7):
            await evidence.failed(exc, operation="create_execution", phase="execute", rollback="succeeded")
    initial = json.loads(evidence.path.read_text())["incidents"][0]
    assert initial["count"] == 1 and initial["sqlite_error_code"] == 517
    assert initial["frames"] and "SECRET" not in evidence.path.read_text()
    await evidence.recovered()
    persisted = json.loads(evidence.path.read_text())["incidents"][0]
    assert persisted["count"] == 7 and persisted["recovered_at"]
    restarted = FailureEvidence(evidence.path)
    await restarted.failed(RuntimeError("other private text"), operation="update_execution")
    history = json.loads(evidence.path.read_text())["incidents"]
    assert history[0]["first_at"] == initial["first_at"] and len(history) == 2


async def test_corrupt_evidence_is_not_overwritten_and_reports_write_failure(tmp_path):
    evidence = FailureEvidence(tmp_path / "failures.json")
    evidence.path.write_text("keep this evidence")
    await evidence.failed(RuntimeError("private"), operation="fixture")
    assert evidence.snapshot()["write_error"]
    assert evidence.path.read_text() == "keep this evidence"


async def test_evidence_history_has_a_fixed_retention_limit(tmp_path):
    evidence = FailureEvidence(tmp_path / "failures.json")
    for _ in range(40):
        await evidence.failed(RuntimeError(), operation="fixture")
        await evidence.recovered()
    assert len(json.loads(evidence.path.read_text())["incidents"]) == 32


async def test_standby_health_reports_degraded_or_stalled_leader():
    leader, standby = TaskManager(), TaskManager()
    await leader._try_become_leader()
    try:
        leader._reconciling = True
        await leader._exchange_runtime_health()
        await standby._exchange_runtime_health()
        state = standby._local_health()
        assert state["role"] == "standby" and state["status"] == "degraded"
        standby._leader_health["recorded_at"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        assert standby._local_health()["leader_stalled"]
    finally:
        leader._leadership.release()


async def test_maintenance_batches_are_bounded_and_make_progress():
    scheduler = await definition()
    old = datetime.now(timezone.utc) - timedelta(days=2)
    for _ in range(105):
        execution = TaskExecution(schedulerID=scheduler.id, title="expired fixture", updatedAt=old)
        await TaskStore.create_execution(execution, enqueue=True)
    db = await TaskStore.raw_db()
    await db.execute("UPDATE task_executions SET updated_at=?", (old.isoformat(),))
    assert len(await TaskStore.list_stale_queued_executions(before=old + timedelta(hours=1))) == 100
    manager = TaskManager()
    assert await manager._expire_stale_executions() == 100
    assert await manager._expire_stale_executions() == 5
    assert await TaskStore.count_queued_refs() == 0


async def test_cancellation_during_final_commit_does_not_requeue_finished_business(monkeypatch):
    _, execution = await claimed()
    manager = TaskManager()
    entered, release = asyncio.Event(), asyncio.Event()
    original = TaskStore.update_active_execution
    async def delayed_save(result):
        entered.set()
        await release.wait()
        return await original(result)
    monkeypatch.setattr(TaskStore, "update_active_execution", delayed_save)
    monkeypatch.setattr(TaskExecutor, "_trigger_workflow", AsyncMock(return_value="finished"))
    worker = asyncio.create_task(manager._run_execution(execution))
    await asyncio.wait_for(entered.wait(), 2)
    worker.cancel()
    await asyncio.sleep(0)
    worker.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await worker
    current = await TaskStore.get_execution(execution.id)
    assert current.status == TaskStatus.COMPLETED
    assert await TaskStore.count_queued_refs() == 0
    # A restart can safely acknowledge the fact left by interrupted shutdown.
    await TaskManager()._reconcile_receipts()
    assert not CompletionReceipts.for_store().paths()


async def test_spool_failure_still_commits_to_healthy_database(monkeypatch):
    _, execution = await claimed()
    monkeypatch.setattr(CompletionReceipts, "completed", AsyncMock(side_effect=OSError("disk full")))
    business = AsyncMock(return_value="large or disk-constrained result")
    monkeypatch.setattr(TaskExecutor, "_trigger_workflow", business)
    manager = TaskManager()
    await manager._run_execution(execution)
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.COMPLETED
    assert business.await_count == 1
    assert not manager._pending_completions
    assert not CompletionReceipts.for_store().paths()


async def test_double_storage_failure_holds_unknown_result_after_restart(monkeypatch):
    _, execution = await claimed()
    original = TaskStore.update_active_execution
    monkeypatch.setattr(CompletionReceipts, "completed", AsyncMock(side_effect=OSError("disk full")))
    monkeypatch.setattr(TaskStore, "update_active_execution", AsyncMock(side_effect=sqlite3.OperationalError("locked")))
    business = AsyncMock(return_value="completed before both storage failures")
    monkeypatch.setattr(TaskExecutor, "_trigger_workflow", business)
    manager = TaskManager()
    await manager._run_execution(execution)
    assert execution.id in manager._pending_completions
    monkeypatch.setattr(TaskStore, "update_active_execution", original)
    await TaskManager()._reconcile_receipts()
    current = await TaskStore.get_execution(execution.id)
    assert current.status == TaskStatus.FAILED and current.retry.blocked_reason == "unverified_effects"
    assert business.await_count == 1


async def test_cancelled_receipt_admission_never_starts_business(monkeypatch):
    _, execution = await claimed()
    entered = asyncio.Event()
    async def delayed_start(*_):
        entered.set()
        await asyncio.sleep(100)
    monkeypatch.setattr(CompletionReceipts, "started", delayed_start)
    business = AsyncMock()
    monkeypatch.setattr(TaskExecutor, "dispatch", business)
    manager = TaskManager()
    worker = asyncio.create_task(manager._run_execution(execution))
    await entered.wait()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    current = await TaskStore.get_execution(execution.id)
    assert current.status == TaskStatus.QUEUED and current.retry.blocked_reason is None
    business.assert_not_called()


async def test_atomic_replace_crash_leaves_only_discardable_temporary_content():
    _, execution = await claimed()
    receipts = CompletionReceipts.for_store()
    script = """
import json, os, sys
from pathlib import Path
from flocks.task.models import TaskExecution
from flocks.task.receipts import CompletionReceipts
value = json.load(sys.stdin)
os.replace = lambda *_: os._exit(24)
CompletionReceipts(Path(value['root']))._write(TaskExecution.model_validate(value['execution']), 'started')
"""
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script],
        input=json.dumps({"root": str(receipts.root), "execution": execution.model_dump(mode="json")}),
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 24
    assert not receipts.paths() and list(receipts.root.glob(".receipt-*"))
    receipts.cleanup_temporary()
    assert not list(receipts.root.iterdir())


async def test_concurrent_receipt_limits_and_acknowledgement_are_consistent(monkeypatch):
    _, execution = await claimed()
    receipts = CompletionReceipts.for_store()
    monkeypatch.setattr(CompletionReceipts, "MAX_FILES", 3)
    attempts = []
    for index in range(20):
        attempt = execution.model_copy(deep=True)
        attempt.id = f"receipt-fixture-{index}"
        attempts.append(attempt)
    results = await asyncio.gather(*(receipts.started(e) for e in attempts), return_exceptions=True)
    successful = [e for e, result in zip(attempts, results) if not isinstance(result, BaseException)]
    assert len(successful) == 3 and len(receipts.paths()) == 3
    assert all(result is None or isinstance(result, OSError) for result in results)
    await asyncio.gather(*(receipts.acknowledge(e) for e in successful))
    assert not receipts.paths()


async def test_queue_control_from_standby_reaches_owner_and_survives_restart():
    leader, standby = TaskManager(), TaskManager()
    TaskManager._instance = standby
    await TaskManager.set_queue_paused(True)
    await leader._sync_runtime_controls()
    assert leader.queue.paused
    restarted = TaskManager()
    await restarted._sync_runtime_controls()
    assert restarted.queue.paused
    await TaskManager.set_queue_paused(False)
    await leader._sync_runtime_controls()
    assert not leader.queue.paused


async def test_cancellation_from_other_process_stops_owner_runtime(monkeypatch):
    _, execution = await claimed()
    started = asyncio.Event()
    async def business(*_):
        started.set()
        await asyncio.sleep(100)
    monkeypatch.setattr(TaskExecutor, "_trigger_workflow", business)
    manager = TaskManager()
    worker = asyncio.create_task(manager._run_execution(execution))
    manager._execution_workers[execution.id] = (execution, worker)
    await started.wait()
    # A different API process changes the row but has no local runtime handle.
    await TaskStore.cancel_active_execution(execution.id, completed_at=datetime.now(timezone.utc),
                                           duration_ms=None, error="other process cancelled")
    await manager._sync_runtime_controls()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.CANCELLED


async def test_interruption_reconciliation_never_fails_a_newer_attempt(monkeypatch):
    _, execution = await claimed()
    old = execution.model_copy(deep=True)
    await CompletionReceipts.for_store().started(old)
    replacement = execution.model_copy(deep=True)
    replacement.started_at += timedelta(seconds=1)
    original = TaskStore.get_execution
    calls = 0
    async def retry_between_reads(execution_id):
        nonlocal calls
        current = await original(execution_id)
        calls += 1
        if calls == 1:
            await TaskStore.update_execution(replacement)
        return current
    monkeypatch.setattr(TaskStore, "get_execution", retry_between_reads)
    await TaskManager()._reconcile_receipts()
    current = await original(execution.id)
    assert current.started_at == replacement.started_at and current.status == TaskStatus.RUNNING


async def test_standby_automatically_takes_over_and_reconciles_after_owner_exit():
    _, execution = await claimed()
    await CompletionReceipts.for_store().completed(terminal(execution))
    owner, standby = TaskManager(), TaskManager(poll_interval=0.01)
    await owner._try_become_leader()
    await owner._exchange_runtime_health()
    standby._running = True
    standby._loop_task = asyncio.create_task(standby._leadership_loop())
    TaskManager._instance = standby
    try:
        await asyncio.sleep(0.03)
        assert not standby._is_leader
        assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.RUNNING
        owner._leadership.release()
        async with asyncio.timeout(2):
            while not standby._is_leader or standby._reconciling:
                await asyncio.sleep(0.01)
        assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.COMPLETED
        assert standby.scheduler.is_running
    finally:
        owner._leadership.release()
        await TaskManager.stop()


async def test_skipped_schedule_audit_survives_later_normal_dispatch():
    scheduler = await definition(misfirePolicy="skip")
    now = datetime.now(timezone.utc)
    scheduler.trigger.next_run = now - timedelta(hours=1)
    await TaskStore.update_scheduler(scheduler)
    await SchedulerLoop()._tick_scheduler(scheduler, now)
    scheduler = await TaskStore.get_scheduler(scheduler.id)
    missed = scheduler.trigger.schedule_audit[0]
    await SchedulerLoop()._tick_scheduler(scheduler, scheduler.trigger.next_run + timedelta(seconds=5))
    scheduler = await TaskStore.get_scheduler(scheduler.id)
    assert scheduler.trigger.last_decision["reason"] == "on_time"
    assert scheduler.trigger.schedule_audit == [missed]


async def test_cancellation_after_commit_preserves_scheduler_and_committed_work(monkeypatch):
    scheduler = await definition()
    now = datetime.now(timezone.utc)
    scheduler.trigger.next_run = now - timedelta(seconds=1)
    await TaskStore.update_scheduler(scheduler)
    writer = await TaskStore.raw_db()
    original = writer.commit
    committed, release = asyncio.Event(), asyncio.Event()
    async def delayed_commit_return():
        await original()
        committed.set()
        await release.wait()
    monkeypatch.setattr(writer, "commit", delayed_commit_return)
    worker = asyncio.create_task(SchedulerLoop()._tick_scheduler(scheduler, now))
    await committed.wait()
    worker.cancel()
    await asyncio.sleep(0)
    worker.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await worker
    stored = await TaskStore.get_scheduler(scheduler.id)
    assert scheduler.trigger == stored.trigger and scheduler.updated_at == stored.updated_at
    assert (await TaskStore.list_scheduler_executions(scheduler.id))[1] == 1
    assert not writer.in_transaction


async def test_startup_repairs_queued_execution_with_running_reference():
    scheduler = await definition()
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RERUN)
    db = await TaskStore.raw_db()
    await db.execute("UPDATE task_execution_queue_refs SET status='running' WHERE execution_id=?", (execution.id,))
    assert await TaskStore.claim_next_queue_execution() is None
    assert await TaskStore.reconcile_queue_refs() == 1
    claimed_execution, _ = await TaskStore.claim_next_queue_execution()
    assert claimed_execution.id == execution.id


async def test_startup_removes_terminal_queue_refs_without_requeueing_work():
    _, execution = await claimed()
    db = await TaskStore.raw_db()
    await db.execute("UPDATE task_executions SET status='completed' WHERE id=?", (execution.id,))
    assert await TaskStore.get_queue_ref(execution.id)
    assert await TaskStore.reconcile_queue_refs() == 1
    assert await TaskStore.get_queue_ref(execution.id) is None
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.COMPLETED


async def test_file_failure_cannot_mask_concurrent_shutdown_cancellation():
    import threading
    entered, release = threading.Event(), threading.Event()
    def fail_after_release():
        entered.set()
        assert release.wait(2)
        raise FileNotFoundError("failed concurrently with shutdown")
    worker = asyncio.create_task(disk_operation(fail_after_release))
    async with asyncio.timeout(2):
        while not entered.is_set():
            await asyncio.sleep(0.001)
    worker.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await worker
