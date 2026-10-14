"""Real PostgreSQL regression coverage; every case owns a retained schema.

Set FLOCKS_TEST_PG_DSN to a disposable test cluster. Tests never remove schema
objects, so a failure's database evidence remains available for inspection.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest

from flocks.db import registry
from flocks.db.connect import close_pools, open_transaction, pool_for
from flocks.storage.storage import Storage
from flocks.task.models import TaskExecution, TaskScheduler, TaskStatus
from flocks.task.store import TaskStore
from flocks.workflow.store import WorkflowStore

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is not set")]


@pytest.fixture
async def pg_backend(tmp_path, monkeypatch):
    import asyncpg
    from flocks.db.active import reset
    from flocks.config.config import Config

    schema = "regression_" + uuid4().hex
    connection = await asyncpg.connect(PG_DSN, timeout=10)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    parts = urlsplit(PG_DSN)
    query = dict(parse_qsl(parts.query))
    query["search_path"] = schema + ",public"
    dsn = urlunsplit(parts._replace(query=urlencode(query)))
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(registry.BACKEND_ENV, "postgres")
    monkeypatch.setenv(registry.DSN_ENV, dsn)
    registry.reset()
    await reset()
    Config._global_config = None
    Config._cached_config = None
    Storage._initialized = False
    Storage._db_path = None
    Storage._db_identity = None
    TaskStore._initialized = False
    TaskStore._handle = None
    WorkflowStore._initialized = False
    WorkflowStore._handle = None
    try:
        yield dsn
    finally:
        await TaskStore.close()
        await WorkflowStore.close()
        await Storage.shutdown()
        await reset()
        await close_pools()
        registry.reset()


async def test_row_contract_and_twenty_concurrent_reads(pg_backend):
    handle = await open_transaction()
    try:
        rows = await asyncio.gather(*[handle.fetch_one("SELECT ?::int AS number", (i,)) for i in range(20)])
        assert rows == [(i,) for i in range(20)]
        row = await (await handle.execute("SELECT 12 AS number, 'named' AS label")).fetchone()
        assert row[0] == row["number"] == 12
        assert tuple(row) == (12, "named")
        assert dict(row) == {"number": 12, "label": "named"}
        pool = await pool_for(pg_backend)
        assert pool.get_idle_size() == pool.get_size()
    finally:
        await handle.close()


async def test_transaction_commit_rollback_exception_and_cancellation(pg_backend):
    handle = await open_transaction()
    try:
        await handle.execute("CREATE TABLE atomic_probe (id TEXT PRIMARY KEY)")
        async with handle.transaction() as tx:
            await tx.execute("INSERT INTO atomic_probe VALUES (?)", ("committed",))
            await tx.commit()  # Must not end the surrounding transaction early.
        with pytest.raises(RuntimeError, match="abort"):
            async with handle.transaction() as tx:
                await tx.execute("INSERT INTO atomic_probe VALUES (?)", ("failed",))
                await tx.commit()
                raise RuntimeError("abort")
        async with handle.transaction() as tx:
            await tx.execute("INSERT INTO atomic_probe VALUES (?)", ("rolled-back",))
            await tx.rollback()

        inserted = asyncio.Event()
        async def cancelled_write():
            async with handle.transaction() as tx:
                await tx.execute("INSERT INTO atomic_probe VALUES (?)", ("cancelled",))
                inserted.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(cancelled_write())
        await asyncio.wait_for(inserted.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await handle.fetch_all("SELECT id FROM atomic_probe") == [("committed",)]
        pool = await pool_for(pg_backend)
        assert pool.get_idle_size() == pool.get_size()
    finally:
        await handle.close()


async def _queued_executions(count):
    await TaskStore.init()
    scheduler = TaskScheduler(title="postgres regression")
    await TaskStore.create_scheduler(scheduler)
    executions = [TaskExecution(schedulerID=scheduler.id, title=f"task {i}", status=TaskStatus.QUEUED) for i in range(count)]
    await asyncio.gather(*[TaskStore.create_execution(item) for item in executions])
    await asyncio.gather(*[TaskStore.enqueue_execution_ref(item.id) for item in executions])
    return executions


async def test_task_store_concurrent_reads_and_competing_claims(pg_backend):
    executions = await _queued_executions(20)
    reads = await asyncio.gather(*[TaskStore.get_execution(executions[0].id) for _ in range(20)])
    assert all(item.id == executions[0].id for item in reads)
    claims = await asyncio.gather(*[TaskStore.claim_next_queue_execution() for _ in range(24)])
    won = [item for item in claims if item is not None]
    assert len(won) == len({item[0].id for item in won}) == 20
    assert all(item[0].status == item[1].status == TaskStatus.RUNNING for item in won)
    assert await TaskStore.count_queued_refs() == 0
    assert await TaskStore.count_running() == 20


async def test_task_scheduler_dashboard_and_retry_queries(pg_backend):
    from flocks.task.models import TaskTrigger, RetryConfig
    from flocks.task.manager import TaskManager
    await TaskStore.init()
    immediate = TaskScheduler(title="immediate", trigger=TaskTrigger(run_immediately=True))
    scheduled = TaskScheduler(title="scheduled", trigger=TaskTrigger(run_immediately=False))
    await TaskStore.create_scheduler(immediate)
    await TaskStore.create_scheduler(scheduled)
    listed, total = await TaskStore.list_schedulers(scheduled_only=True)
    assert total == 1 and listed[0].id == scheduled.id
    assert (await TaskStore.dashboard_counts())["scheduled_active"] == 1
    retry = TaskExecution(schedulerID=scheduled.id, title="retry", status=TaskStatus.FAILED,
        retry=RetryConfig(max_retries=12, retry_count=2, retry_after=datetime.now(timezone.utc)-timedelta(seconds=1)))
    await TaskStore.create_execution(retry)
    assert [item.id for item in await TaskStore.list_retryable_failed_executions()] == [retry.id]
    manager = TaskManager()
    await manager._process_retry_queue()
    queued = await TaskStore.get_execution(retry.id)
    assert queued.status == TaskStatus.QUEUED and queued.retry.retry_after is None
    claimed = await manager.queue.dequeue()
    assert claimed.id == retry.id and claimed.status == TaskStatus.RUNNING
    counts = await TaskManager.dashboard()
    assert counts["running"] == 1 and counts["queued"] == 0


async def test_queue_claim_failure_rolls_back_both_tables(pg_backend):
    import asyncpg
    executions = await _queued_executions(1)
    db = await TaskStore.raw_db()
    await db.execute("ALTER TABLE task_executions ADD CONSTRAINT reject_running CHECK (status != 'running')")
    with pytest.raises(asyncpg.CheckViolationError):
        await TaskStore.claim_next_queue_execution()
    execution = await TaskStore.get_execution(executions[0].id)
    ref = await TaskStore.get_queue_ref(executions[0].id)
    assert execution.status == ref.status == TaskStatus.QUEUED
    assert execution.started_at is None and ref.started_at is None


async def test_queue_finish_checks_terminal_state_and_generation(pg_backend):
    executions = await _queued_executions(1)
    claimed, _ = await TaskStore.claim_next_queue_execution()
    owner = await TaskStore.claim_execution(claimed.id, node_id="pg-owner", lease_seconds=60)
    await TaskStore.finish_queue_ref(claimed.id, expected_generation=owner.owner_generation)
    assert await TaskStore.get_queue_ref(claimed.id) is not None
    # A live lease is never claimed again, not even by its own node: let it lapse first.
    assert await TaskStore.claim_execution(claimed.id, node_id="pg-owner", lease_seconds=60) is None
    db = await TaskStore._db()
    await db.execute(
        "UPDATE task_executions SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), claimed.id),
    )
    newer = await TaskStore.claim_execution(claimed.id, node_id="pg-owner", lease_seconds=60)
    newer.status = TaskStatus.COMPLETED
    assert await TaskStore.finish_execution(newer, node_id="pg-owner", expected_generation=newer.owner_generation)
    await TaskStore.finish_queue_ref(claimed.id, expected_generation=owner.owner_generation)
    assert await TaskStore.get_queue_ref(claimed.id) is not None
    await TaskStore.finish_queue_ref(claimed.id, expected_generation=newer.owner_generation)
    assert await TaskStore.get_queue_ref(executions[0].id) is None


async def test_recovery_requeue_and_bulk_reference_locking(pg_backend, monkeypatch):
    from flocks.task.manager import TaskManager

    execution = (await _queued_executions(1))[0]
    await TaskStore.claim_next_queue_execution()
    original = await TaskStore.claim_execution(execution.id, node_id="old-node", lease_seconds=90)
    db = await TaskStore._db()
    await db.execute(
        "UPDATE task_executions SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), execution.id),
    )
    listing = TaskStore.list_reclaimable_executions

    async def peer_takes_over(cls, **kwargs):
        snapshots = await listing(**kwargs)
        peer = await TaskStore.claim_execution(execution.id, node_id="peer-node", lease_seconds=90)
        assert peer.owner_generation == original.owner_generation + 1
        return snapshots

    monkeypatch.setattr(TaskStore, "list_reclaimable_executions", classmethod(peer_takes_over))
    assert await TaskManager()._recover_stale_active_executions() == 0
    assert (await TaskStore.get_execution(execution.id)).owner_node_id == "peer-node"
    assert await TaskStore.requeue_running_refs(node_id="old-node") == 0
    # Node identity is no proof of death: the peer's live lease stays put until it lapses.
    assert await TaskStore.requeue_running_refs(node_id="peer-node") == 0
    await db.execute(
        "UPDATE task_executions SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), execution.id),
    )
    assert await TaskStore.requeue_running_refs(node_id="peer-node") == 1
    current = await TaskStore.get_execution(execution.id)
    await TaskStore.requeue_execution(
        current, expected_generation=current.owner_generation, expected_status=TaskStatus.RUNNING,
    )
    current = await TaskStore.get_execution(execution.id)
    assert current.status is TaskStatus.QUEUED and current.owner_node_id is None
    assert await TaskStore.claim_next_queue_execution() is not None
    peer = await TaskStore.claim_execution(execution.id, node_id="next-node", lease_seconds=90)
    assert peer is not None
    await db.execute(
        "ALTER TABLE task_execution_queue_refs ADD CONSTRAINT requeue_rejected CHECK (status <> 'queued')"
    )
    with pytest.raises(Exception, match="requeue_rejected"):
        await TaskStore.requeue_execution(
            peer, expected_generation=peer.owner_generation, expected_status=TaskStatus.RUNNING,
        )
    current = await TaskStore.get_execution(execution.id)
    assert current.status is TaskStatus.RUNNING and current.owner_node_id == "next-node"
    assert current.lease_is_live()
    assert (await TaskStore.get_queue_ref(execution.id)).status is TaskStatus.RUNNING


async def test_two_auth_initializers_and_two_bootstraps(pg_backend):
    from flocks.auth.service import LocalAuthBackend
    first = type("FirstAuthWorker", (LocalAuthBackend,), {"_initialized": False, "_has_users_cached": False})
    second = type("SecondAuthWorker", (LocalAuthBackend,), {"_initialized": False, "_has_users_cached": False})
    await asyncio.gather(first.init(), second.init())
    assert await first.has_users() is False
    outcomes = await asyncio.gather(
        first.bootstrap_admin("admin_one", "review-password-123"),
        second.bootstrap_admin("admin_two", "review-password-456"),
        return_exceptions=True,
    )
    winners = [item for item in outcomes if not isinstance(item, BaseException)]
    errors = [item for item in outcomes if isinstance(item, BaseException)]
    assert len(winners) == len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "已初始化" in str(errors[0])
    assert await first.has_users() is True
    assert (await first.get_user_by_id(winners[0].id)).username == winners[0].username
    async with Storage.transaction() as tx:
        assert await tx.fetch_one("SELECT COUNT(*) FROM users") == (1,)


async def test_auth_asgi_bootstrap_login_and_me(pg_backend, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from flocks.auth.service import AuthService, LocalAuthBackend
    from flocks.server.app import _AuthGuardMiddleware
    from flocks.server.routes.auth import router
    backend = type("HttpAuthWorker", (LocalAuthBackend,), {"_initialized": False, "_has_users_cached": False})
    monkeypatch.setattr(AuthService, "_backend", backend)
    app = FastAPI()
    app.include_router(router, prefix="/api/auth")
    app.add_middleware(_AuthGuardMiddleware)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost",
        headers={"user-agent": "Mozilla/5.0"},
    ) as client:
        response = await client.get("/api/auth/bootstrap-status")
        assert response.status_code == 200 and response.json() == {"bootstrapped": False}
        response = await client.post("/api/auth/bootstrap-admin", json={"username": "http_admin", "password": "review-password-789"})
        assert response.status_code == 200
        assert response.json()["username"] == "http_admin"
        assert response.json()["role"] == "admin"
        client.cookies.clear()
        response = await client.post("/api/auth/login", json={"username": "http_admin", "password": "review-password-789"})
        assert response.status_code == 200
        response = await client.get("/api/auth/me")
        assert response.status_code == 200 and response.json()["username"] == "http_admin"
        response = await client.post("/api/auth/bootstrap-admin", json={"username": "another", "password": "review-password-789"})
        assert response.status_code == 400


async def test_shared_pool_runtime_knowledge_and_channel(pg_backend):
    from flocks.runtime_state.postgres import PostgresRuntimeStateStore
    from flocks.knowledge.postgres import PostgresKnowledgeStore
    from flocks.channel.inbound.session_binding import _get_db, close_binding_db
    runtime = PostgresRuntimeStateStore(pg_backend)
    knowledge = PostgresKnowledgeStore(pg_backend)
    pool = await pool_for(pg_backend)
    assert await runtime._ensure_pool() is await knowledge._ensure_pool() is pool
    await runtime.initialize()
    await runtime.set("review-key", {"ok": True})
    await runtime.close()
    assert await knowledge._ensure_pool() is pool
    await Storage.init()
    binding = await _get_db()
    results = await asyncio.gather(*[binding.fetch_one("SELECT COUNT(*) FROM channel_bindings") for _ in range(20)])
    assert results == [(0,)] * 20
    assert await runtime.get("review-key") == {"ok": True}
    await close_binding_db()
    await knowledge.close()
    assert pool.get_idle_size() == pool.get_size()


async def test_synchronous_reader_reuses_its_loop_and_pool(pg_backend):
    from flocks.db.sync_store import PostgresSyncReader, close_sync_reader
    await Storage.init()
    await Storage.set("sync-probe", {"value": 7})
    reader = PostgresSyncReader(pg_backend)
    async def backend_pid(connection):
        return await connection.fetchval("SELECT pg_backend_pid()")
    try:
        first = await asyncio.to_thread(reader._run, backend_pid)
        second = await asyncio.to_thread(reader._run, backend_pid)
        assert first == second
        pool = await pool_for(pg_backend)
        async with pool.acquire() as connection:
            assert await backend_pid(connection) != first
        reads = await asyncio.gather(*[asyncio.to_thread(reader.get, "sync-probe") for _ in range(12)])
        assert reads == [{"value": 7}] * 12
    finally:
        await asyncio.to_thread(close_sync_reader)


async def test_storage_shutdown_closes_both_loop_pools(pg_backend):
    from flocks.db import sync_store
    await Storage.init()
    await Storage.set("shutdown-probe", {"ok": True})
    reader = sync_store.PostgresSyncReader(pg_backend)
    assert await asyncio.to_thread(reader.get, "shutdown-probe") == {"ok": True}
    pool = await pool_for(pg_backend)
    await Storage.shutdown()
    assert pool.is_closing()
    assert sync_store._reader_loop is None


async def test_workflow_trim_and_delete_are_atomic(pg_backend):
    import asyncpg
    await WorkflowStore.init()
    for i in range(3):
        await WorkflowStore.upsert_execution({"id": f"run-{i}", "workflow_id": "wf", "status": "completed", "startedAt": i + 1})
        await WorkflowStore.record_step(f"run-{i}", 0, {"node_id": "one"})
    assert set(await WorkflowStore.trim_executions("wf", keep=1)) == {"run-0", "run-1"}
    assert await WorkflowStore.get_execution("run-2") is not None
    db = await WorkflowStore.raw_db()
    # A real FK prevents the second deletion. The first deletion of steps
    # must be rolled back with it, rather than autocommitted separately.
    await db.execute("CREATE TABLE retained_run (id TEXT REFERENCES workflow_executions(id))")
    await db.execute("INSERT INTO retained_run VALUES (?)", ("run-2",))
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await WorkflowStore.delete_execution("run-2")
    steps, total = await WorkflowStore.list_steps("run-2")
    assert total == len(steps) == 1
    assert await WorkflowStore.get_execution("run-2") is not None


async def test_document_hook_and_storage_transaction_rollback(pg_backend):
    await Storage.init()
    with pytest.raises(RuntimeError, match="abort"):
        async with Storage.transaction() as tx:
            await tx.execute("CREATE TABLE rolled_back_table (id TEXT PRIMARY KEY)")
            await tx.commit()
            raise RuntimeError("abort")
    async with Storage.transaction() as tx:
        assert await tx.fetch_one("SELECT to_regclass('rolled_back_table')") == (None,)
    async def rollback_hook(tx):
        await tx.rollback()
    await Storage.mutate_many(set_entries=[("hook-probe", {"v": 1}, "json")], transaction_hook=rollback_hook)
    assert await Storage.get("hook-probe") is None
