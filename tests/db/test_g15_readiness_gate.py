"""Readiness and background work follow the database (G15).

* not ready while the database is down, a standby or a read-only session;
* back only once it stayed writable for the recovery window;
* the task queue and workflow schedules start nothing meanwhile.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from flocks.db import health
from flocks.db.health import check_database, execution_allowed


@pytest.fixture(autouse=True)
def fresh_tracker(monkeypatch):
    health.reset_database_health()
    monkeypatch.setenv("FLOCKS_READY_RECOVERY_STABLE_S", "10")
    yield
    health.reset_database_health()


def _database(answers: list):
    """database_ready answering from *answers* in turn (an exception raises it)."""

    async def fake():
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return fake


async def test_a_database_that_comes_back_is_let_in_only_after_it_stayed_up(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(health.time, "monotonic", lambda: clock[0])
    answers = [(True, "database primary")]
    monkeypatch.setattr(health, "database_ready", _database(answers))

    assert await check_database() == (True, "database primary"), "at startup the first good answer counts"
    assert execution_allowed()

    answers[:] = [ConnectionRefusedError("down")]
    ok, detail = await check_database()
    assert not ok and "unreachable" in detail and not execution_allowed()

    answers[:] = [(True, "database primary")]
    clock[0] += 3
    ok, detail = await check_database()  # writable again from here
    assert not ok and "recovering" in detail and not execution_allowed()
    clock[0] += 9
    assert not (await check_database())[0], "9s of the 10s: not yet"
    clock[0] += 2
    assert (await check_database())[0] and execution_allowed()

    # A blip during the window starts it over.
    answers[:] = [(False, "database is a read-only standby")]
    assert not (await check_database())[0]
    answers[:] = [(True, "database primary")]
    clock[0] += 9
    assert not (await check_database())[0]


async def test_a_read_only_session_is_not_ready(monkeypatch):
    class _Tx:
        async def fetch_one(self, sql, params=()):
            assert "transaction_read_only" in sql
            return (False, "on")

    class _Ctx:
        async def __aenter__(self):
            return _Tx()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("flocks.storage.storage.Storage.transaction", lambda *args, **kwargs: _Ctx())
    ok, detail = await health.database_ready()
    assert not ok and "read-only" in detail


@pytest.mark.skipif(not os.environ.get("FLOCKS_TEST_PG_DSN"), reason="needs FLOCKS_TEST_PG_DSN")
async def test_the_test_database_is_a_writable_primary():
    assert await check_database() == (True, "database primary")


async def test_the_task_queue_starts_nothing_while_the_database_is_not_usable(monkeypatch):
    from flocks.task.manager import TaskManager

    manager = TaskManager()
    manager._poll_interval = 0.01
    dequeued = []

    async def dequeue():
        dequeued.append(1)
        return None

    monkeypatch.setattr(manager.queue, "dequeue", dequeue)
    for name in ("_process_retry_queue", "_recover_stale_active_executions", "_recover_orphaned_queued_executions"):
        async def _noop(*args, **kwargs):
            return 0

        monkeypatch.setattr(manager, name, _noop)
    monkeypatch.setattr(health, "database_ready", _database([ConnectionRefusedError("down")]))
    await check_database()

    manager._running = True
    loop = asyncio.create_task(manager._execution_loop())
    await asyncio.sleep(0.1)
    assert dequeued == []

    health.reset_database_health()  # usable again (no failure observed since)
    await asyncio.sleep(0.1)
    manager._running = False
    await asyncio.wait_for(loop, 1)
    assert dequeued, "work is picked up once the database is usable"


@pytest.mark.skipif(not os.environ.get("FLOCKS_TEST_PG_DSN"), reason="needs FLOCKS_TEST_PG_DSN")
async def test_a_schedule_does_not_claim_a_time_it_cannot_run(monkeypatch):
    from flocks.workflow.poller_manager import WorkflowPollerManager
    from flocks.workflow.store import WorkflowStore

    monkeypatch.setattr(health, "database_ready", _database([(False, "database is a read-only standby")]))
    await check_database()
    manager = WorkflowPollerManager()
    config = {"workflowId": "wf-g15", "intervalSeconds": 60, "triggerId": "sched"}

    assert await manager._claim("wf-g15", config, 1_792_000_000_000) is False
    assert manager.get_status("wf-g15")["lastError"] == "database_not_ready"
    # Left for an instance whose database is usable.
    assert await WorkflowStore.claim_schedule_slot("wf-g15", "sched", 1_792_000_000_000, "the-other-node")
