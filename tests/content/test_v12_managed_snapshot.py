"""The managed-content layer (flocks.content.managed): a snapshot of what the
deployment's sources list, which loaders lay over their local files.

* a refresh that fails keeps the last good snapshot, so the managed items stay
  protected instead of letting local copies take their place,
* the first load at startup must succeed for every kind, or the start fails,
* a task keeps the version of an item it first received.
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

import pytest

from flocks.content import ContentBody, ContentEntry, ContentRef, register_source, reset_sources
from flocks.content.managed import (
    ManagedContent,
    ManagedContentUnavailable,
    is_managed_location,
    managed_location,
    parse_managed_location,
)


class DeploymentSource:
    """An in-memory stand-in for a deployment's content source."""

    def __init__(self) -> None:
        self.latest: Dict[Tuple[str, str], str] = {}
        self.texts: Dict[Tuple[str, str, str], str] = {}
        self.failing: set[str] = set()
        self.list_calls = 0

    @property
    def name(self) -> str:
        return "deployment"

    def publish(self, kind: str, content_id: str, version: str, text: str) -> None:
        self.latest[(kind, content_id)] = version
        self.texts[(kind, content_id, version)] = text

    def supports(self, kind: str) -> bool:
        return kind in {"agent", "skill", "workflow"}

    async def list_entries(self, kind: str) -> Sequence[ContentEntry]:
        self.list_calls += 1
        if kind in self.failing:
            raise RuntimeError("core unavailable")
        return [
            ContentEntry(ref=ContentRef(kind=k, content_id=cid, version=v), name=cid, summary=f"{cid} summary")
            for (k, cid), v in sorted(self.latest.items())
            if k == kind
        ]

    async def fetch(self, ref: ContentRef) -> Optional[ContentBody]:
        version = ref.version or self.latest.get((ref.kind, ref.content_id))
        text = self.texts.get((ref.kind, ref.content_id, version or ""))
        if text is None:
            return None
        return ContentBody(ref=ContentRef(kind=ref.kind, content_id=ref.content_id, version=version), text=text)


@pytest.fixture
def source():
    reset_sources()
    ManagedContent.reset()
    deployment = DeploymentSource()
    register_source(deployment, priority=0, exclusive_kinds=("agent", "skill", "workflow"))
    yield deployment
    reset_sources()
    ManagedContent.reset()


async def test_refresh_snapshots_listings_and_the_bodies_loaders_need(source):
    source.publish("agent", "analyst", "1", "---\nname: analyst\n---\nprompt v1")
    source.publish("skill", "triage", "1", "skill body")
    source.publish("workflow", "nightly", "1", "start: a\nnodes: []\nedges: []\n")
    before = ManagedContent.generation()

    assert await ManagedContent.refresh() is True

    assert ManagedContent.ids("agent") == {"analyst"}
    assert ManagedContent.ids("skill") == {"triage"}
    assert ManagedContent.body("agent", "analyst").text.endswith("prompt v1")
    assert ManagedContent.body("workflow", "nightly") is not None
    # Skill bodies are fetched on use, not held.
    assert ManagedContent.body("skill", "triage") is None
    assert ManagedContent.generation() != before
    # Nothing changed: same generation, so loaders keep their caches.
    generation = ManagedContent.generation()
    assert await ManagedContent.refresh() is False
    assert ManagedContent.generation() == generation


async def test_a_failed_refresh_keeps_the_last_good_snapshot(source):
    source.publish("agent", "analyst", "1", "---\nname: analyst\n---\nprompt")
    await ManagedContent.refresh()
    source.failing.add("agent")

    await ManagedContent.refresh()

    assert ManagedContent.ids("agent") == {"analyst"}


async def test_a_listed_body_that_cannot_be_served_fails_the_whole_kind(source):
    source.publish("agent", "analyst", "1", "---\nname: analyst\n---\nprompt")
    await ManagedContent.refresh()
    # Listed, but the body is gone: a partial snapshot would drop the item's
    # protection, so the kind keeps its previous one.
    source.latest[("agent", "ghost")] = "9"

    await ManagedContent.refresh()

    assert ManagedContent.ids("agent") == {"analyst"}


async def test_startup_load_retries_then_refuses(source):
    source.publish("skill", "triage", "1", "body")
    source.failing.add("workflow")

    with pytest.raises(ManagedContentUnavailable, match="workflow"):
        await ManagedContent.ensure_loaded(attempts=3, delay=0)

    # Only the failing kind was retried.
    assert source.list_calls == 3 + 2
    source.failing.clear()
    await ManagedContent.ensure_loaded(attempts=1, delay=0)
    assert ManagedContent.ids("skill") == {"triage"}


async def test_startup_load_is_a_no_op_without_a_deployment_source():
    reset_sources()
    ManagedContent.reset()
    try:
        await ManagedContent.ensure_loaded(attempts=1, delay=0)
        assert ManagedContent.ids("agent") == frozenset()
    finally:
        ManagedContent.reset()


async def test_a_task_keeps_the_version_it_first_received(source):
    source.publish("skill", "triage", "1", "body v1")
    await ManagedContent.refresh()
    first = await ManagedContent.fetch_pinned("skill", "triage", scope="session-1")
    source.publish("skill", "triage", "2", "body v2")

    again = await ManagedContent.fetch_pinned("skill", "triage", scope="session-1")
    other = await ManagedContent.fetch_pinned("skill", "triage", scope="session-2")
    unscoped = await ManagedContent.fetch_pinned("skill", "triage", scope=None)

    assert (first.text, again.text) == ("body v1", "body v1")
    assert other.text == "body v2"
    assert unscoped.text == "body v2"


def test_managed_locations_round_trip():
    location = managed_location("skill", "triage")
    assert is_managed_location(location)
    assert parse_managed_location(location) == ("skill", "triage")
    assert not is_managed_location("/opt/flocks/skills/triage/SKILL.md")
    assert parse_managed_location("content://skill") is None



async def test_a_same_version_body_change_is_a_change(source):
    source.publish("agent", "analyst", "1", "---\nname: analyst\n---\nprompt A")
    await ManagedContent.refresh()
    generation = ManagedContent.generation()

    source.publish("agent", "analyst", "1", "---\nname: analyst\n---\nprompt B")

    assert await ManagedContent.refresh() is True
    assert ManagedContent.generation() != generation
    assert ManagedContent.body("agent", "analyst").text.endswith("prompt B")


async def test_racing_first_fetches_in_one_task_settle_on_one_version(source):
    import asyncio

    source.publish("skill", "triage", "1", "body v1")
    await ManagedContent.refresh()
    real_fetch = source.fetch
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def _interleaved(ref):
        # The first fetch reads v1, then is held back until a second fetch in
        # the same task has read v2 and finished.
        nonlocal calls
        calls += 1
        body = await real_fetch(ref)
        if calls == 1:
            first_started.set()
            await release_first.wait()
        return body

    source.fetch = _interleaved
    first = asyncio.create_task(ManagedContent.fetch_pinned("skill", "triage", scope="s-race"))
    await first_started.wait()
    source.publish("skill", "triage", "2", "body v2")
    second = await ManagedContent.fetch_pinned("skill", "triage", scope="s-race")
    release_first.set()
    first_body = await first

    assert first_body.text == second.text == "body v2"


async def test_a_body_comes_only_from_the_source_that_listed_it():
    from flocks.content import ContentNotFound

    reset_sources()
    ManagedContent.reset()
    lister = DeploymentSource()
    lister.publish("skill", "triage", "1", "managed body")
    lister.texts.clear()  # listed, but cannot serve it
    class OtherSource(DeploymentSource):
        @property
        def name(self) -> str:
            return "other"

    other = OtherSource()
    other.publish("skill", "triage", "1", "some other body")
    try:
        register_source(lister, priority=0)  # not exclusive
        register_source(other, priority=500)
        await ManagedContent.refresh()

        with pytest.raises(ContentNotFound):
            await ManagedContent.fetch_pinned("skill", "triage", scope="s-1")
    finally:
        reset_sources()
        ManagedContent.reset()



async def test_a_pinned_version_must_come_back_as_that_version(source):
    from flocks.content import ContentNotFound

    source.publish("skill", "triage", "1", "body v1")
    await ManagedContent.refresh()
    assert (await ManagedContent.fetch_pinned("skill", "triage", scope="s-strict")).text == "body v1"

    async def _unversioned(ref):
        return ContentBody(ref=ContentRef(kind=ref.kind, content_id=ref.content_id, version=None), text="body v2")

    source.fetch = _unversioned

    with pytest.raises(ContentNotFound):
        await ManagedContent.fetch_pinned("skill", "triage", scope="s-strict")
