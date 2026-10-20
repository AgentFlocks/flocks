"""Scheduled times and webhook deliveries run once across instances (G13).

* a scheduled time is claimed in the database: two instances firing at once,
  or one firing after the other's run already ended, run it once;
* a webhook delivery is bound to the run it starts in that run's start
  transaction: one claimed by a process that died before starting the run is
  taken over by a retry, and the late original then starts nothing.
"""

from __future__ import annotations

import asyncio
import os
from collections import Counter

import pytest

from flocks.workflow import poller_manager as poller_module
from flocks.workflow.execution_store import create_execution_record, for_webhook_delivery
from flocks.workflow.ownership import WorkflowOwnershipError
from flocks.workflow.poller_manager import WorkflowPollerManager
from flocks.workflow.store import WorkflowStore

pytestmark = pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="isolated PostgreSQL required")


async def test_two_instances_claiming_one_scheduled_time_get_it_once():
    results = await asyncio.gather(*[
        WorkflowStore.claim_schedule_slot("wf-g13", "sched", 1_792_000_000_000, f"node-{n}") for n in range(6)
    ])
    assert results.count(True) == 1
    assert await WorkflowStore.claim_schedule_slot("wf-g13", "sched", 1_792_000_060_000, "node-0")
    # Another schedule of the same workflow has its own times.
    assert await WorkflowStore.claim_schedule_slot("wf-g13", "other", 1_792_000_000_000, "node-0")


async def test_two_pollers_run_each_interval_once_even_when_runs_end_at_once(monkeypatch):
    """Both instances fire for every interval; runs finish immediately, so a
    no-overlap lease alone would let the later one start the same time again."""
    claimed: list[tuple[str, int]] = []
    ran: list[str] = []

    def instance(name: str) -> WorkflowPollerManager:
        manager = WorkflowPollerManager()
        real_claim = manager._claim

        async def claim(workflow_id, config, slot_ms):
            # The time a run is for is the one claimed: a run that starts late
            # (a busy host) is still that time's, whatever the clock says then.
            won = await real_claim(workflow_id, config, slot_ms)
            if won:
                claimed.append((name, slot_ms))
            return won

        async def fake_run(workflow_id, workflow_json, config):
            ran.append(name)
            return {}

        monkeypatch.setattr(manager, "_claim", claim)
        monkeypatch.setattr(manager, "_execute_run", fake_run)
        return manager

    config = {"workflowId": "wf-g13-poll", "intervalSeconds": 1, "timeoutSeconds": 5, "noOverlap": True,
              "inputs": {}, "triggerId": "sched", "cronExpression": None}
    stop = asyncio.Event()
    loops = [
        asyncio.create_task(instance(name)._poller_loop("wf-g13-poll", {"start": "a"}, dict(config), stop))
        for name in ("A", "B")
    ]
    await asyncio.sleep(3.2)
    stop.set()
    await asyncio.gather(*loops)

    await asyncio.sleep(0.2)  # the last claim's run gets its turn
    per_slot = Counter(slot for _name, slot in claimed)
    assert len(per_slot) >= 3, claimed
    assert set(per_slot.values()) == {1}, f"a scheduled time was taken more than once: {claimed}"
    assert Counter(ran) == Counter(name for name, _slot in claimed), f"one run per claim: {ran} vs {claimed}"


async def test_a_cron_time_another_instance_claimed_is_skipped_here(monkeypatch):
    manager = WorkflowPollerManager()
    ran = []

    async def fake_run(workflow_id, workflow_json, config):
        ran.append(config)
        return {}

    monkeypatch.setattr(manager, "_execute_run", fake_run)
    config = {"workflowId": "wf-g13-cron", "intervalSeconds": 30, "timeoutSeconds": 5, "noOverlap": True,
              "inputs": {}, "triggerId": "sched", "cronExpression": "* * * * *"}
    next_at = manager._compute_next_run_at_ms(config)
    assert await WorkflowStore.claim_schedule_slot("wf-g13-cron", "sched", next_at, "the-other-node")
    # This time is due now; the one after is an hour away.
    times = iter([next_at, next_at + 3_600_000])
    monkeypatch.setattr(manager, "_compute_next_run_at_ms", lambda _config, **_kw: next(times))
    monkeypatch.setattr(poller_module, "_now_ms", lambda: next_at)
    stop = asyncio.Event()
    loop = asyncio.create_task(manager._poller_loop("wf-g13-cron", {}, config, stop))
    await asyncio.sleep(0.3)
    stop.set()
    await loop
    assert ran == []
    assert manager.get_status("wf-g13-cron")["lastError"] == "scheduled_time_claimed_elsewhere"


async def test_a_delivery_is_bound_to_the_run_it_starts():
    key = "wf-g13/hook/id:d-bind"
    assert await WorkflowStore.claim_webhook_delivery(key, "d-bind", 300, "claim-1") is None
    with for_webhook_delivery(key, "claim-1"):
        run = await create_execution_record("wf-g13", input_params={"n": 1})
    # A retry -- even after the takeover time -- is a duplicate: a run handles it.
    WorkflowStore.WEBHOOK_TAKEOVER_S, saved = 0.0, WorkflowStore.WEBHOOK_TAKEOVER_S
    try:
        assert await WorkflowStore.claim_webhook_delivery(key, "d-bind", 300, "claim-2") == "d-bind"
    finally:
        WorkflowStore.WEBHOOK_TAKEOVER_S = saved
    db = await WorkflowStore._db()
    async with db.transaction() as tx:
        row = await tx.fetch_one("SELECT exec_id FROM workflow_webhook_deliveries WHERE key = ?", (key,))
    assert row[0] == run["id"]


async def test_a_delivery_whose_process_died_before_the_run_is_taken_over(monkeypatch):
    key = "wf-g13/hook/id:d-crash"
    monkeypatch.setattr(WorkflowStore, "WEBHOOK_TAKEOVER_S", 0.2)
    assert await WorkflowStore.claim_webhook_delivery(key, "d-crash", 300, "claim-dead") is None
    # Retried at once: still the first claim's (it may yet start its run).
    assert await WorkflowStore.claim_webhook_delivery(key, "d-crash", 300, "claim-early") == "d-crash"
    await asyncio.sleep(0.3)
    # Retried after the restart: no run was ever bound, so the retry runs it.
    assert await WorkflowStore.claim_webhook_delivery(key, "d-crash", 300, "claim-retry") is None
    with for_webhook_delivery(key, "claim-retry"):
        await create_execution_record("wf-g13", input_params={"n": 2})

    # The original, only slow after all, starts nothing now.
    with for_webhook_delivery(key, "claim-dead"):
        with pytest.raises(WorkflowOwnershipError, match="taken over"):
            await create_execution_record("wf-g13", input_params={"n": 3})
    db = await WorkflowStore._db()
    async with db.transaction() as tx:
        runs = await tx.fetch_one(
            "SELECT count(*) FROM workflow_executions WHERE workflow_id = ? AND input_params LIKE ?",
            ("wf-g13", '%"n": 3%'),
        )
    assert runs[0] == 0, "the refused start left no run behind"
