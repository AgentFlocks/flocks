"""
Tests for process-scoped ownership of task executions.

With two instances sharing one task database, "this execution has been running
a while" says nothing about whether anybody is still working on it.  Recovery
therefore has to go by lease expiry so that
restarting one instance does not cancel and re-run work its peer is executing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.models import (
    ExecutionTriggerType,
    SchedulerMode,
    SchedulerStatus,
    TaskExecution,
    TaskScheduler,
    TaskStatus,
    TaskTrigger,
)
from flocks.task.node import current_node_id, reset_node_id_cache
from flocks.task.store import TaskStore


NODE_A = "node-a"
NODE_B = "node-b"


@pytest.fixture(autouse=True)
async def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    TaskStore._initialized = False
    TaskStore._handle = None
    reset_node_id_cache()

    await Storage.init()
    await TaskStore.init()
    yield
    await TaskStore.close()
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    TaskStore._initialized = False
    TaskStore._handle = None
    reset_node_id_cache()


async def _make_running_execution(*, started_minutes_ago: int = 0) -> TaskExecution:
    scheduler = TaskScheduler(
        title="lease test",
        description="",
        mode=SchedulerMode.ONCE,
        status=SchedulerStatus.ACTIVE,
        trigger=TaskTrigger(type=ExecutionTriggerType.RUN_ONCE),
    )
    await TaskStore.create_scheduler(scheduler)

    execution = TaskExecution(
        schedulerID=scheduler.id,
        title="lease test",
        status=TaskStatus.RUNNING,
        startedAt=datetime.now(timezone.utc) - timedelta(minutes=started_minutes_ago),
    )
    await TaskStore.create_execution(execution)
    return execution


# ---------------------------------------------------------------------------
# L1-L4: claiming
# ---------------------------------------------------------------------------

async def test_claim_records_owner_and_lease():
    """L1: a claim names the owner, when it expires, and its generation."""
    execution = await _make_running_execution()

    claimed = await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=60)

    assert claimed is not None
    assert claimed.owner_node_id == NODE_A
    assert claimed.owner_generation == 1
    assert claimed.lease_is_live()


async def test_claim_is_refused_while_another_node_holds_a_live_lease():
    """L2: a live claim is not stealable, so two nodes cannot both run it."""
    execution = await _make_running_execution()
    await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=60)

    assert await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=60) is None


async def test_expired_lease_can_be_taken_over():
    """L3: once renewals stop, the work becomes available again."""
    execution = await _make_running_execution()
    await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=1)

    # Age the lease out rather than sleeping for it.
    db = await TaskStore._db()
    await db.execute(
        "UPDATE task_executions SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), execution.id),
    )
    await db.commit()

    taken = await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=60)

    assert taken is not None
    assert taken.owner_node_id == NODE_B
    assert taken.owner_generation == 2  # bumped, so node A's late writes are stale


@pytest.mark.parametrize("allow_own_live", [False, True])
async def test_a_node_cannot_reclaim_its_own_live_lease(allow_own_live):
    """L4: even the legacy opt-in cannot supersede a live dispatcher."""
    execution = await _make_running_execution()
    await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=60)

    again = await TaskStore.claim_execution(
        execution.id, node_id=NODE_A, lease_seconds=60, allow_own_live=allow_own_live,
    )

    assert again is None
    assert (await TaskStore.get_execution(execution.id)).owner_generation == 1


# ---------------------------------------------------------------------------
# L5-L7: renewal and release
# ---------------------------------------------------------------------------

async def test_renewal_extends_the_lease():
    """L5: the owner keeps its claim alive by renewing."""
    execution = await _make_running_execution()
    claimed = await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=60)

    renewed = await TaskStore.renew_execution_lease(
        execution.id, node_id=NODE_A, generation=claimed.owner_generation, lease_seconds=120
    )

    assert renewed is True
    after = await TaskStore.get_execution(execution.id)
    assert after.lease_expires_at > claimed.lease_expires_at


async def test_renewal_with_a_stale_generation_is_refused():
    """L6: a superseded node cannot renew, so it learns it was replaced.

    This is what keeps an old owner from writing back over the new one's work
    after a long stall.
    """
    execution = await _make_running_execution()
    first = await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=60)
    stale_generation = first.owner_generation

    # Node A stalls, its lease expires, node B takes over.
    db = await TaskStore._db()
    await db.execute(
        "UPDATE task_executions SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), execution.id),
    )
    await db.commit()
    await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=60)

    # Node A wakes up and tries to carry on.
    renewed = await TaskStore.renew_execution_lease(
        execution.id, node_id=NODE_A, generation=stale_generation, lease_seconds=60
    )

    assert renewed is False


async def test_release_clears_ownership():
    """L7: finishing the work frees the row immediately."""
    execution = await _make_running_execution()
    await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=60)

    assert await TaskStore.release_execution_lease(execution.id, node_id=NODE_A) is True

    after = await TaskStore.get_execution(execution.id)
    assert after.owner_node_id is None
    assert after.lease_is_live() is False


# ---------------------------------------------------------------------------
# L8-L12: what recovery may reclaim
# ---------------------------------------------------------------------------

async def test_peer_work_with_a_live_lease_is_not_reclaimable():
    """L8: the defect this exists to prevent.

    Judged only by age, a long-running execution on the other node looks stale
    from here, and restarting this node would cancel and re-run it.
    """
    execution = await _make_running_execution(started_minutes_ago=120)
    await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=300)

    reclaimable = await TaskStore.list_reclaimable_executions(
        node_id=NODE_A,
        stale_before=datetime.now(timezone.utc),  # old enough by the age rule
    )

    assert [item.id for item in reclaimable] == []


async def test_expired_peer_lease_is_reclaimable():
    """L9: once the peer stops renewing, its work is fair game."""
    execution = await _make_running_execution(started_minutes_ago=120)
    await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=60)
    db = await TaskStore._db()
    await db.execute(
        "UPDATE task_executions SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), execution.id),
    )
    await db.commit()

    reclaimable = await TaskStore.list_reclaimable_executions(
        node_id=NODE_A, stale_before=datetime.now(timezone.utc)
    )

    assert [item.id for item in reclaimable] == [execution.id]


async def test_same_owner_live_lease_is_not_reclaimable():
    """L10: a matching logical or process id does not establish abandonment."""
    execution = await _make_running_execution()
    await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=600)

    reclaimable = await TaskStore.list_reclaimable_executions(
        node_id=NODE_A,
        stale_before=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    assert reclaimable == []


async def test_unowned_old_rows_still_follow_the_age_rule():
    """L11: rows written before ownership existed are judged as before."""
    execution = await _make_running_execution(started_minutes_ago=120)

    reclaimable = await TaskStore.list_reclaimable_executions(
        node_id=NODE_A,
        stale_before=datetime.now(timezone.utc) - timedelta(minutes=60),
    )

    assert [item.id for item in reclaimable] == [execution.id]


async def test_unowned_recent_rows_are_left_alone():
    """L12: a just-started unclaimed execution is not treated as abandoned."""
    execution = await _make_running_execution(started_minutes_ago=0)

    reclaimable = await TaskStore.list_reclaimable_executions(
        node_id=NODE_A,
        stale_before=datetime.now(timezone.utc) - timedelta(minutes=60),
    )

    assert [item.id for item in reclaimable] == []


# ---------------------------------------------------------------------------
# L13-L14: migration and node identity
# ---------------------------------------------------------------------------

async def test_column_migration_is_idempotent():
    """L13: re-running the migration on an up-to-date database is a no-op."""
    await TaskStore._add_missing_execution_columns()
    await TaskStore._add_missing_execution_columns()

    db = await TaskStore._db()
    rows = await db.fetch_all("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?", ("task_executions",))
    columns = {row[0] for row in rows}

    assert {"owner_node_id", "lease_expires_at", "owner_generation"} <= columns


def test_node_id_is_stable_and_configurable(monkeypatch):
    """L14: the id must survive a restart, or every restart looks like a new node."""
    monkeypatch.setenv("FLOCKS_NODE_ID", "instance-a")
    reset_node_id_cache()
    assert current_node_id() == "instance-a"

    reset_node_id_cache()
    monkeypatch.delenv("FLOCKS_NODE_ID", raising=False)
    assert current_node_id()  # falls back to something non-empty and host-stable


# ---------------------------------------------------------------------------
# L15-L17: ownership on the terminal write and on startup recovery
# ---------------------------------------------------------------------------

async def test_a_node_that_lost_the_lease_cannot_overwrite_the_new_owner():
    """L15: a late result must not replace the run that superseded it.

    A node whose lease expired mid-execution -- a long pause, a partition --
    comes back holding a result for work another node has since claimed and may
    already have finished. Writing by id alone let that late report silently
    replace the new owner's status and result, so the row ended up describing a
    run that was no longer the one in charge.
    """
    execution = await _make_running_execution()

    a = await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=60)
    assert a is not None

    # Age the lease out rather than sleeping for it, then let node-b take over.
    await _expire_lease(execution.id)
    b = await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=60)
    assert b is not None and b.owner_generation > a.owner_generation

    stale = await TaskStore.get_execution(execution.id)
    stale.status = TaskStatus.FAILED
    stale.result_summary = "written by the node that lost the lease"
    written = await TaskStore.finish_execution(
        stale, node_id=NODE_A, expected_generation=a.owner_generation
    )

    assert written is None
    current = await TaskStore.get_execution(execution.id)
    assert current.owner_node_id == NODE_B
    assert current.result_summary != "written by the node that lost the lease"
    assert current.status is TaskStatus.RUNNING


async def test_the_owner_can_write_its_own_result():
    """L16: the guard must not block the node that actually holds the claim."""
    execution = await _make_running_execution()
    claimed = await TaskStore.claim_execution(
        execution.id, node_id=NODE_A, lease_seconds=60
    )

    current = await TaskStore.get_execution(execution.id)
    current.status = TaskStatus.COMPLETED
    current.result_summary = "done"
    written = await TaskStore.finish_execution(
        current, node_id=NODE_A, expected_generation=claimed.owner_generation
    )

    assert written is not None
    stored = await TaskStore.get_execution(execution.id)
    assert stored.status is TaskStatus.COMPLETED
    assert stored.result_summary == "done"


async def _expire_lease(execution_id: str) -> None:
    """Age a lease out instead of sleeping for it."""
    db = await TaskStore._db()
    await db.execute(
        "UPDATE task_executions SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), execution_id),
    )
    await db.commit()


async def _mark_ref_running(execution_id: str) -> None:
    db = await TaskStore._db()
    await db.execute(
        "UPDATE task_execution_queue_refs SET status = 'running' WHERE execution_id = ?",
        (execution_id,),
    )
    await db.commit()


async def test_requeue_running_refs_leaves_a_peers_live_execution_alone():
    """L17: restarting one instance must not requeue the other's work.

    Startup recovery treated every ``running`` reference as its own leftover.
    On a shared database a peer's healthy execution looks identical, so
    restarting node A dropped node B's in-progress work back into the queue and
    it ran a second time.
    """
    mine = await _make_running_execution()
    theirs = await _make_running_execution()
    await TaskStore.enqueue_execution_ref(mine.id)
    await TaskStore.enqueue_execution_ref(theirs.id)
    await _mark_ref_running(mine.id)
    await _mark_ref_running(theirs.id)

    await TaskStore.claim_execution(mine.id, node_id=NODE_A, lease_seconds=60)
    await TaskStore.claim_execution(theirs.id, node_id=NODE_B, lease_seconds=60)

    requeued = await TaskStore.requeue_running_refs(node_id=NODE_A)

    assert requeued == 0
    assert (await TaskStore.get_queue_ref(mine.id)).status.value == "running"
    assert (await TaskStore.get_queue_ref(theirs.id)).status.value == "running"


async def test_requeue_running_refs_reclaims_an_expired_lease():
    """L18: an owner that stopped renewing is gone; its work is requeued."""
    abandoned = await _make_running_execution()
    await TaskStore.enqueue_execution_ref(abandoned.id)
    await _mark_ref_running(abandoned.id)
    await TaskStore.claim_execution(abandoned.id, node_id=NODE_B, lease_seconds=60)
    await _expire_lease(abandoned.id)

    requeued = await TaskStore.requeue_running_refs(node_id=NODE_A)

    assert requeued == 1
    assert (await TaskStore.get_queue_ref(abandoned.id)).status.value == "queued"


# ---------------------------------------------------------------------------
# Regression: the terminal write must happen under the claim, and the periodic
# recovery loop must not fail this node's own live work.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["retry", "queued_orphan", "expiry"])
async def test_scanned_tasks_cannot_overwrite_a_dequeued_execution(monkeypatch, kind):
    from flocks.task.manager import TaskManager

    execution = await _make_running_execution(started_minutes_ago=180)
    execution.status = TaskStatus.FAILED if kind == "retry" else TaskStatus.QUEUED
    execution.retry.max_retries = 3
    execution.retry.retry_count = 1
    execution.retry.retry_after = datetime.now(timezone.utc) - timedelta(seconds=5)
    await TaskStore.update_execution(execution)
    if kind == "expiry":
        db = await TaskStore._db()
        await db.execute(
            "UPDATE task_executions SET updated_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), execution.id),
        )
        await db.commit()
    listing, recover = {
        "retry": ("list_retryable_failed_executions", "_process_retry_queue"),
        "queued_orphan": ("list_orphaned_queued_executions", "_recover_orphaned_queued_executions"),
        "expiry": ("list_stale_queued_executions", "_expire_stale_executions"),
    }[kind]
    original = getattr(TaskStore, listing)

    async def dequeue_after_snapshot(cls, *args, **kwargs):
        rows = await original(*args, **kwargs)
        assert [item.id for item in rows] == [execution.id]
        await TaskStore.requeue_execution(execution)
        assert await TaskStore.claim_next_queue_execution() is not None
        # Dispatch has not claimed a lease yet: only the source-status guard
        # protects this interval because the generation is still unchanged.
        return rows

    monkeypatch.setattr(TaskStore, listing, classmethod(dequeue_after_snapshot))
    await getattr(TaskManager(), recover)()
    current = await TaskStore.get_execution(execution.id)
    assert current.status is TaskStatus.RUNNING
    assert (await TaskStore.get_queue_ref(execution.id)).status is TaskStatus.RUNNING


async def test_dispatch_writes_terminal_result_while_still_holding_the_lease(monkeypatch):
    """A finished run must land as ``completed`` with its result.

    ``finish_execution`` only writes for the node that still owns the row.
    Releasing the lease *before* that write cleared the owner, so the write
    was refused as superseded and every execution stayed ``running`` forever.
    """
    from flocks.task.executor import TaskExecutor

    execution = await _make_running_execution()
    scheduler = await TaskStore.get_scheduler(execution.scheduler_id)

    async def _fake_session(cls_execution, cls_scheduler):
        return "ses_fake"

    async def _fake_run(cls_execution, session_id):
        return "hello from the agent"

    monkeypatch.setattr(TaskExecutor, "_create_task_session", classmethod(lambda cls, e, s: _fake_session(e, s)))
    monkeypatch.setattr(TaskExecutor, "_run_agent_session", classmethod(lambda cls, e, sid: _fake_run(e, sid)))

    result = await TaskExecutor.dispatch(execution, scheduler)

    assert result.status is TaskStatus.COMPLETED
    stored = await TaskStore.get_execution(execution.id)
    assert stored.status is TaskStatus.COMPLETED
    assert stored.result_summary == "hello from the agent"
    assert stored.completed_at is not None
    # The claim is released once the result is on disk.
    assert stored.owner_node_id is None
    assert stored.lease_expires_at is None


async def test_periodic_recovery_leaves_this_nodes_live_execution_alone():
    """The 30s recovery loop runs beside this node's own in-flight work.

    Treating "owned by us" as "abandoned" there failed every execution that
    ran longer than one recovery interval on the node that was running it.
    """
    from flocks.task.manager import TaskManager

    execution = await _make_running_execution()
    await TaskStore.enqueue_execution_ref(execution.id)
    node = current_node_id()
    claimed = await TaskStore.claim_execution(execution.id, node_id=node, lease_seconds=90)
    assert claimed is not None and claimed.lease_is_live()

    manager = TaskManager(max_concurrent=1, poll_interval=999, scheduler_interval=999)
    recovered = await manager._recover_stale_active_executions()

    assert recovered == 0
    current = await TaskStore.get_execution(execution.id)
    assert current.status is TaskStatus.RUNNING
    assert current.owner_node_id == node

    # Once the lease lapses without renewal the same row *is* abandoned.
    await _expire_lease(execution.id)
    recovered = await manager._recover_stale_active_executions()
    assert recovered == 1
    assert (await TaskStore.get_execution(execution.id)).status is TaskStatus.FAILED


async def test_startup_recovery_preserves_own_live_rows():
    """Another process on the same host may still be running this work."""
    from flocks.task.manager import TaskManager
    execution = await _make_running_execution()
    node = current_node_id()
    await TaskStore.claim_execution(execution.id, node_id=node, lease_seconds=90)

    rows = await TaskStore.list_reclaimable_executions(
        node_id=node, stale_before=datetime.now(timezone.utc), include_own_live=True,
    )
    assert rows == []
    assert await TaskManager()._recover_orphaned_executions() == 0

    rows = await TaskStore.list_reclaimable_executions(
        node_id=node, stale_before=datetime.now(timezone.utc), include_own_live=False
    )
    assert rows == []


@pytest.mark.parametrize("recovery", ["_recover_stale_active_executions", "_recover_orphaned_executions"])
async def test_recovery_snapshot_cannot_overwrite_a_new_peer_claim(monkeypatch, recovery):
    from flocks.task.manager import TaskManager

    execution = await _make_running_execution(started_minutes_ago=180)
    await TaskStore.enqueue_execution_ref(execution.id)
    await _mark_ref_running(execution.id)
    await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=90)
    await _expire_lease(execution.id)
    original = TaskStore.list_reclaimable_executions

    async def peer_claim_after_snapshot(cls, **kwargs):
        rows = await original(**kwargs)
        peer = await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=90)
        assert peer is not None and peer.owner_generation == 2
        return rows

    monkeypatch.setattr(TaskStore, "list_reclaimable_executions", classmethod(peer_claim_after_snapshot))
    manager = TaskManager()
    assert await getattr(manager, recovery)() == 0
    current = await TaskStore.get_execution(execution.id)
    assert current.status is TaskStatus.RUNNING
    assert current.owner_node_id == NODE_B and current.owner_generation == 2
    assert current.lease_is_live()
    assert (await TaskStore.get_queue_ref(execution.id)).status is TaskStatus.RUNNING


async def test_periodic_recovery_respects_new_generation_after_its_snapshot(monkeypatch):
    from flocks.task.manager import TaskManager

    execution = await _make_running_execution(started_minutes_ago=180)
    node = current_node_id()
    claim = await TaskStore.claim_execution(execution.id, node_id=node, lease_seconds=90)
    await _expire_lease(execution.id)
    original = TaskStore.list_reclaimable_executions

    async def renew_after_snapshot(cls, **kwargs):
        rows = await original(**kwargs)
        assert not await TaskStore.renew_execution_lease(
            execution.id, node_id=node, generation=claim.owner_generation, lease_seconds=90,
        )
        assert await TaskStore.claim_execution(execution.id, node_id=node, lease_seconds=90)
        return rows

    monkeypatch.setattr(TaskStore, "list_reclaimable_executions", classmethod(renew_after_snapshot))
    assert await TaskManager()._recover_stale_active_executions() == 0
    current = await TaskStore.get_execution(execution.id)
    assert current.status is TaskStatus.RUNNING and current.lease_is_live()
    assert current.owner_generation == claim.owner_generation + 1


async def test_recovery_losing_its_claim_cannot_cancel_peer_session(monkeypatch):
    from flocks.task.executor import TaskExecutor
    from flocks.task.manager import TaskManager

    execution = await _make_running_execution(started_minutes_ago=180)
    await TaskStore.enqueue_execution_ref(execution.id)
    await _mark_ref_running(execution.id)
    original = TaskManager._cancel_execution_runtime
    cancellation_ids = []
    monkeypatch.setattr(TaskExecutor, "request_runtime_cancel", lambda e: cancellation_ids.append(e.id))

    async def peer_takes_over_before_cancel(cls, snapshot, **kwargs):
        await _expire_lease(snapshot.id)
        peer = await TaskStore.claim_execution(snapshot.id, node_id=NODE_B, lease_seconds=90)
        assert peer is not None
        await TaskStore.set_execution_session(
            snapshot.id, "ses_new_peer", node_id=NODE_B, expected_generation=peer.owner_generation,
        )
        return await original(snapshot, **kwargs)

    monkeypatch.setattr(TaskManager, "_cancel_execution_runtime", classmethod(peer_takes_over_before_cancel))
    assert await TaskManager()._recover_stale_active_executions() == 0
    current = await TaskStore.get_execution(execution.id)
    assert current.status is TaskStatus.RUNNING and current.owner_node_id == NODE_B
    assert current.session_id == "ses_new_peer"
    assert not cancellation_ids
    assert (await TaskStore.get_queue_ref(execution.id)).status is TaskStatus.RUNNING


async def test_requeue_execution_and_queue_reference_commit_together(monkeypatch):
    import asyncio
    from flocks.db.backends.postgres import PostgresTransaction
    from flocks.task.manager import TaskManager

    execution = await _make_running_execution()
    await TaskStore.enqueue_execution_ref(execution.id)
    await _mark_ref_running(execution.id)
    claimed = await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=90)
    wrote_execution = asyncio.Event()
    continue_requeue = asyncio.Event()
    original = PostgresTransaction._execute

    async def pause_after_execution_write(handle, sql, params=()):
        result = await original(handle, sql, params)
        if "UPDATE task_executions SET" in sql and "title=?" in sql:
            wrote_execution.set()
            await continue_requeue.wait()
        return result

    monkeypatch.setattr(PostgresTransaction, "_execute", pause_after_execution_write)
    requeue = asyncio.create_task(TaskManager._requeue_execution(claimed, expected_generation=claimed.owner_generation))
    await asyncio.wait_for(wrote_execution.wait(), 2)
    dequeue = asyncio.create_task(TaskStore.claim_next_queue_execution())
    # SKIP LOCKED returns no candidate while the requeue transaction is open.
    assert await dequeue is None
    continue_requeue.set()
    await requeue
    taken = await TaskStore.claim_next_queue_execution()
    assert taken is not None
    peer = await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=90)
    assert peer is not None
    assert (await TaskStore.get_queue_ref(execution.id)).status is TaskStatus.RUNNING


async def test_dispatch_does_not_create_session_or_run_when_peer_owns_execution(monkeypatch):
    from unittest.mock import AsyncMock

    from flocks.task.executor import TaskExecutor

    execution = await _make_running_execution()
    scheduler = await TaskStore.get_scheduler(execution.scheduler_id)
    await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=90)
    before = await TaskStore.get_execution(execution.id)
    create_session = AsyncMock(return_value="unexpected-session")
    run_agent = AsyncMock(return_value="unexpected-result")
    monkeypatch.setattr(TaskExecutor, "_create_task_session", create_session)
    monkeypatch.setattr(TaskExecutor, "_run_agent_session", run_agent)

    # A refused dispatch may return or signal the manager, but must do no work.
    try:
        await TaskExecutor.dispatch(execution, scheduler)
    except RuntimeError as exc:
        assert "ownership" in str(exc)

    create_session.assert_not_awaited()
    run_agent.assert_not_awaited()
    assert await TaskStore.get_execution(execution.id) == before


async def test_refused_dispatch_does_not_remove_peers_queue_reference(monkeypatch):
    from unittest.mock import AsyncMock

    from flocks.task.executor import TaskExecutor
    from flocks.task.manager import TaskManager

    execution = await _make_running_execution()
    await TaskStore.enqueue_execution_ref(execution.id)
    await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=90)
    monkeypatch.setattr(TaskExecutor, "_create_task_session", AsyncMock(return_value="unexpected"))
    run_agent = AsyncMock(return_value="unexpected")
    monkeypatch.setattr(TaskExecutor, "_run_agent_session", run_agent)
    manager = TaskManager(max_concurrent=1)
    publish = AsyncMock()
    monkeypatch.setattr(manager, "_publish_execution_update", publish)

    await manager._run_execution(execution)

    run_agent.assert_not_awaited()
    publish.assert_not_awaited()
    assert await TaskStore.get_queue_ref(execution.id) is not None
    stored = await TaskStore.get_execution(execution.id)
    assert stored.status is TaskStatus.RUNNING
    assert stored.owner_node_id == NODE_B


async def test_dispatch_drops_late_result_after_lease_is_taken_over(monkeypatch):
    from unittest.mock import AsyncMock

    from flocks.task.executor import TaskExecutor
    from flocks.task.manager import TaskManager

    execution = await _make_running_execution()
    await TaskStore.enqueue_execution_ref(execution.id)
    monkeypatch.setattr(TaskExecutor, "_create_task_session", AsyncMock(return_value="session-a"))

    async def take_over(_execution, _session_id):
        await _expire_lease(execution.id)
        claimed = await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=90)
        assert claimed is not None
        return "late result from A"

    monkeypatch.setattr(TaskExecutor, "_run_agent_session", take_over)
    manager = TaskManager(max_concurrent=1)
    monkeypatch.setattr(manager, "_publish_execution_update", AsyncMock())

    await manager._run_execution(execution)

    stored = await TaskStore.get_execution(execution.id)
    assert stored.owner_node_id == NODE_B
    assert stored.status is TaskStatus.RUNNING
    assert stored.result_summary != "late result from A"
    assert await TaskStore.get_queue_ref(execution.id) is not None


async def test_dispatch_does_not_start_work_after_ownership_changes_during_session_creation(monkeypatch):
    from unittest.mock import AsyncMock

    from flocks.task.executor import TaskExecutor
    from flocks.task.lease import ExecutionOwnershipError

    execution = await _make_running_execution()
    scheduler = await TaskStore.get_scheduler(execution.scheduler_id)

    async def create_session(_execution, _scheduler):
        await _expire_lease(execution.id)
        assert await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=90)
        return "stale-session"

    monkeypatch.setattr(TaskExecutor, "_create_task_session", create_session)
    run_agent = AsyncMock()
    monkeypatch.setattr(TaskExecutor, "_run_agent_session", run_agent)
    with pytest.raises(ExecutionOwnershipError):
        await TaskExecutor.dispatch(execution, scheduler)

    run_agent.assert_not_awaited()
    stored = await TaskStore.get_execution(execution.id)
    assert stored.owner_node_id == NODE_B
    assert stored.session_id is None


async def test_old_lease_context_does_not_release_new_generation_on_same_node():
    from flocks.task.lease import ExecutionLease

    execution = await _make_running_execution()
    async with ExecutionLease(execution.id, node_id=NODE_A) as first:
        await _expire_lease(execution.id)
        newer = await TaskStore.claim_execution(execution.id, node_id=NODE_A, lease_seconds=90)
        assert newer.owner_generation > first.generation

    stored = await TaskStore.get_execution(execution.id)
    assert stored.owner_node_id == NODE_A
    assert stored.owner_generation == newer.owner_generation


async def test_cancelled_dispatch_does_not_requeue_new_owners_work(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from flocks.task.executor import TaskExecutor
    from flocks.task.manager import TaskManager

    execution = await _make_running_execution()
    await TaskStore.enqueue_execution_ref(execution.id)
    monkeypatch.setattr(TaskExecutor, "_create_task_session", AsyncMock(return_value="session-a"))
    taken_over = asyncio.Event()

    async def take_over_then_wait(_execution, _session_id):
        await _expire_lease(execution.id)
        assert await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=90)
        taken_over.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(TaskExecutor, "_run_agent_session", take_over_then_wait)
    manager = TaskManager(max_concurrent=1)
    publish = AsyncMock()
    monkeypatch.setattr(manager, "_publish_execution_update", publish)
    task = asyncio.create_task(manager._run_execution(execution))
    try:
        await asyncio.wait_for(taken_over.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    stored = await TaskStore.get_execution(execution.id)
    assert stored.owner_node_id == NODE_B
    assert stored.status is TaskStatus.RUNNING
    assert stored.session_id == "session-a"
    assert await TaskStore.get_queue_ref(execution.id) is not None
    publish.assert_not_awaited()


@pytest.mark.parametrize("peer_owned", [False, True])
async def test_missing_scheduler_failure_respects_execution_ownership(monkeypatch, peer_owned):
    from unittest.mock import AsyncMock

    from flocks.task.manager import TaskManager

    execution = await _make_running_execution()
    await TaskStore.enqueue_execution_ref(execution.id)
    if peer_owned:
        await TaskStore.claim_execution(execution.id, node_id=NODE_B, lease_seconds=90)
    before = await TaskStore.get_execution(execution.id)
    monkeypatch.setattr(TaskStore, "get_scheduler", AsyncMock(return_value=None))
    manager = TaskManager(max_concurrent=1)
    monkeypatch.setattr(manager, "_publish_execution_update", AsyncMock())
    monkeypatch.setattr(manager, "_handle_failure", AsyncMock())

    await manager._run_execution(execution)

    current = await TaskStore.get_execution(execution.id)
    if peer_owned:
        assert current == before
        assert await TaskStore.get_queue_ref(execution.id) is not None
    else:
        assert current.status is TaskStatus.FAILED
        assert current.error == f"Scheduler {execution.scheduler_id} not found"
        assert await TaskStore.get_queue_ref(execution.id) is None
