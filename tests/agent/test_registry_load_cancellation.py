"""The cached agent load survives callers that are cancelled."""

import asyncio

import pytest

from flocks.agent import Agent
from flocks.agent.agent import AgentInfo
from flocks.agent.registry import Agent as AgentRegistry


@pytest.fixture(autouse=True)
def no_settings_sync(monkeypatch):
    """``Agent.state()`` first checks the user's skill and delegation settings
    files and drops the cache when they changed; keep the real files out."""
    monkeypatch.setattr(AgentRegistry, "_sync_skill_settings_cache", classmethod(lambda cls: None))
    monkeypatch.setattr(AgentRegistry, "_sync_delegatable_settings_cache", classmethod(lambda cls: None))


@pytest.fixture
def held_load(monkeypatch):
    """Replace the agent load with one the test releases.

    The real load reads the user's config and plugins; these tests are only
    about how the cached load task behaves.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    loads = []

    async def load():
        loads.append(1)
        started.set()
        await release.wait()
        return {"rex": AgentInfo(name="rex", mode="primary")}

    AgentRegistry.invalidate_cache()
    monkeypatch.setattr(AgentRegistry, "_load_agents", staticmethod(load))
    yield started, release, loads
    AgentRegistry.invalidate_cache()


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_fail_the_others(held_load):
    """E.g. a client disconnecting from /context-usage during the first load
    while the session loop is waiting for the same load."""
    started, release, loads = held_load
    leaving = asyncio.create_task(Agent.get("rex"))
    staying = asyncio.create_task(Agent.get("rex"))
    await started.wait()

    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving
    release.set()

    agent = await asyncio.wait_for(staying, timeout=5)
    assert agent is not None and agent.name == "rex"
    assert len(loads) == 1


@pytest.mark.asyncio
async def test_a_cancelled_load_is_started_again(held_load):
    """A load cancelled directly (loop shutdown) is not served from the cache."""
    started, release, loads = held_load
    load = AgentRegistry._state_accessor()
    await started.wait()
    load.cancel()
    with pytest.raises(asyncio.CancelledError):
        await load
    release.set()

    agent = await asyncio.wait_for(Agent.get("rex"), timeout=5)
    assert agent is not None and agent.name == "rex"
    assert len(loads) == 2


@pytest.mark.asyncio
async def test_a_load_left_on_a_closed_loop_is_started_again(monkeypatch):
    """E.g. a workflow thread's own event loop that ended during the load."""
    calls = []

    async def load():
        calls.append(1)
        if len(calls) == 1:
            await asyncio.sleep(3600)
        return {"rex": AgentInfo(name="rex", mode="primary")}

    AgentRegistry.invalidate_cache()
    monkeypatch.setattr(AgentRegistry, "_load_agents", staticmethod(load))

    def start_load_on_a_loop_that_then_closes():
        loop = asyncio.new_event_loop()

        async def start():
            pending = AgentRegistry._state_accessor()
            await asyncio.sleep(0)
            return pending

        try:
            return loop.run_until_complete(start())
        finally:
            loop.close()

    try:
        stale = await asyncio.to_thread(start_load_on_a_loop_that_then_closes)
        assert not stale.done() and stale.get_loop().is_closed()

        agent = await asyncio.wait_for(Agent.get("rex"), timeout=5)
        assert agent is not None and agent.name == "rex"
        assert len(calls) == 2
    finally:
        AgentRegistry.invalidate_cache()
