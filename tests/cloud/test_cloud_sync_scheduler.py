from __future__ import annotations

import pytest

from flocks.cloud import scheduler as scheduler_mod


pytestmark = pytest.mark.asyncio


async def test_tick_once_runs_heartbeat_and_profile_when_due(monkeypatch: pytest.MonkeyPatch):
    storage_values: dict[str, int] = {}
    called = {"hb": 0, "sync": 0}

    async def _get(key: str):
        return storage_values.get(key)

    async def _set(key: str, value, _type: str):
        storage_values[key] = int(value)

    async def _heartbeat():
        called["hb"] += 1
        return {"ok": True}

    async def _sync(*, source: str = "scheduled", force: bool = False):
        _ = force
        assert source == "scheduled"
        called["sync"] += 1
        return {"ok": True}

    monkeypatch.setattr(scheduler_mod.Storage, "get", _get)
    monkeypatch.setattr(scheduler_mod.Storage, "set", _set)
    monkeypatch.setattr(scheduler_mod.CloudBindingService, "send_heartbeat", _heartbeat)
    monkeypatch.setattr(scheduler_mod.CloudBindingService, "sync_node_profile", _sync)
    monkeypatch.setattr(scheduler_mod.time, "time", lambda: 1700000000)

    await scheduler_mod.CloudSyncScheduler._tick_once()

    assert called["hb"] == 1
    assert called["sync"] == 1
    assert storage_values[scheduler_mod._HEARTBEAT_TS_KEY] == 1700000000
    assert storage_values[scheduler_mod._PROFILE_TS_KEY] == 1700000000


async def test_tick_once_skips_when_intervals_not_elapsed(monkeypatch: pytest.MonkeyPatch):
    now_ts = 1700001000
    storage_values = {
        scheduler_mod._HEARTBEAT_TS_KEY: now_ts - 300,
        scheduler_mod._PROFILE_TS_KEY: now_ts - 3600,
    }
    called = {"hb": 0, "sync": 0}

    async def _get(key: str):
        return storage_values.get(key)

    async def _set(key: str, value, _type: str):
        storage_values[key] = int(value)

    async def _heartbeat():
        called["hb"] += 1
        return {"ok": True}

    async def _sync(*, source: str = "scheduled", force: bool = False):
        _ = (source, force)
        called["sync"] += 1
        return {"ok": True}

    monkeypatch.setattr(scheduler_mod.Storage, "get", _get)
    monkeypatch.setattr(scheduler_mod.Storage, "set", _set)
    monkeypatch.setattr(scheduler_mod.CloudBindingService, "send_heartbeat", _heartbeat)
    monkeypatch.setattr(scheduler_mod.CloudBindingService, "sync_node_profile", _sync)
    monkeypatch.setattr(scheduler_mod.time, "time", lambda: now_ts)

    await scheduler_mod.CloudSyncScheduler._tick_once()

    assert called["hb"] == 0
    assert called["sync"] == 0
