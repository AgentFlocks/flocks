"""Instance.dispose_all must finish promptly at shutdown.

Regression for the self-deadlock (dispose_all held ``Instance._lock`` while
``dispose()`` re-acquired it) that hung every SIGTERM until the supervisor
killed the runtime after 10s, and for the new bounded wait per instance.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from flocks.project import instance as instance_mod
from flocks.project.instance import Instance


@pytest.fixture
def fake_project(monkeypatch):
    async def fake_from_directory(cls, requested_directory):
        return {"project": SimpleNamespace(id="project"), "sandbox": requested_directory}

    monkeypatch.setattr(
        "flocks.project.instance.Project.from_directory",
        classmethod(fake_from_directory),
    )
    Instance._cache.clear()
    yield
    Instance._cache.clear()


@pytest.mark.asyncio
async def test_dispose_all_does_not_deadlock(fake_project):
    directory = "/tmp/flocks-instance-dispose-a"
    assert await Instance.provide(directory=directory, fn=lambda: "ok") == "ok"
    assert directory in Instance._cache

    # The old implementation never returned here (lock re-entry).
    await asyncio.wait_for(Instance.dispose_all(), timeout=2)

    assert Instance._cache == {}
    assert not Instance._lock.locked()


@pytest.mark.asyncio
async def test_dispose_all_bounds_a_stuck_state_disposer(fake_project, monkeypatch):
    directory = "/tmp/flocks-instance-dispose-b"
    errors: list[tuple[str, dict]] = []
    monkeypatch.setattr(instance_mod.log, "error", lambda msg, extra=None: errors.append((msg, extra or {})))
    monkeypatch.setattr(Instance, "DISPOSE_TIMEOUT_S", 0.3)

    async def stuck_disposer(_state) -> None:
        await asyncio.sleep(30)

    accessor = Instance.state(init=lambda: object(), dispose=stuck_disposer)
    # touch the state inside the instance context so it is registered for `directory`
    await Instance.provide(directory=directory, fn=accessor)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.wait_for(Instance.dispose_all(), timeout=2)
    elapsed = loop.time() - started

    assert elapsed < 1.5, elapsed
    assert Instance._cache == {}
    assert any(msg == "dispose_all.timeout" and extra.get("directory") == directory for msg, extra in errors), errors
    # a second shutdown call is a no-op and must not hang either
    await asyncio.wait_for(Instance.dispose_all(), timeout=1)
