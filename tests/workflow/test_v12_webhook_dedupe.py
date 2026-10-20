"""Webhook replays are recognised through the database, so a replay that
reaches another instance is still a replay."""

from __future__ import annotations

import asyncio
import os

import pytest

from flocks.workflow.store import WorkflowStore

pytestmark = pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="isolated PostgreSQL required")


async def test_two_copies_at_once_record_exactly_one_delivery():
    results = await asyncio.gather(*[
        WorkflowStore.claim_webhook_delivery("wf/t1/id:d-1", f"delivery-{n}", 300) for n in range(8)
    ])

    winners = [r for r in results if r is None]
    assert len(winners) == 1
    first = next(f"delivery-{n}" for n, r in enumerate(results) if r is None)
    assert all(r == first for r in results if r is not None)


async def test_a_released_delivery_may_be_sent_again():
    assert await WorkflowStore.claim_webhook_delivery("wf/t1/id:d-2", "a", 300, "claim-a") is None
    await WorkflowStore.release_webhook_delivery("wf/t1/id:d-2", "claim-a")
    assert await WorkflowStore.claim_webhook_delivery("wf/t1/id:d-2", "b", 300, "claim-b") is None


async def test_a_stale_request_cannot_release_a_newer_claim():
    """Same client delivery id, two claims: the first one's release leaves the second."""
    assert await WorkflowStore.claim_webhook_delivery("wf/t1/id:d-4", "same", 0.05, "claim-old") is None
    await asyncio.sleep(0.1)
    # The window ran out; a retry of the same delivery claims it anew and runs.
    assert await WorkflowStore.claim_webhook_delivery("wf/t1/id:d-4", "same", 300, "claim-new") is None

    # The old request, refused late, releases what it claimed -- and only that.
    await WorkflowStore.release_webhook_delivery("wf/t1/id:d-4", "claim-old")

    assert await WorkflowStore.claim_webhook_delivery("wf/t1/id:d-4", "same", 300, "claim-third") == "same"


async def test_a_delivery_past_its_window_is_new_again():
    assert await WorkflowStore.claim_webhook_delivery("wf/t1/id:d-3", "a", 0.05) is None
    assert await WorkflowStore.claim_webhook_delivery("wf/t1/id:d-3", "b", 0.05) == "a"
    await asyncio.sleep(0.1)
    assert await WorkflowStore.claim_webhook_delivery("wf/t1/id:d-3", "c", 300) is None
