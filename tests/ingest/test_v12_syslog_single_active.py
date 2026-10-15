"""Instances sharing one database: one of them listens for a workflow's syslog,
the others stand by and take over when it goes away -- a sender's messages
are never consumed twice, and never dropped for long."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock

import pytest

from flocks.ingest.syslog import manager as syslog_manager

WORKFLOW = "wf-single-active"


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def syslog_config(monkeypatch: pytest.MonkeyPatch) -> dict:
    config = {
        "workflowId": WORKFLOW, "enabled": True, "protocol": "udp", "host": "127.0.0.1",
        "port": _free_udp_port(), "format": "auto", "inputKey": "syslog_message",
    }
    monkeypatch.setattr(syslog_manager.WorkflowStore, "get_config", AsyncMock(return_value=config))
    monkeypatch.setattr(
        syslog_manager,
        "read_workflow_from_fs",
        lambda wid: {"id": wid, "workflowJson": {
            "start": "n1", "nodes": [{"id": "n1", "type": "python", "code": "result = 1"}], "edges": [],
        }},
    )
    monkeypatch.setattr(syslog_manager, "_LEASE_CHECK_INTERVAL_S", 0.1)
    return config


@pytest.fixture
def no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two "hosts" in one process: listening is simulated, the lease is real."""

    async def _listen(self, workflow_id, config, queue, abort, ready):
        self._listener_status[workflow_id] = {
            **(self._listener_status.get(workflow_id) or {}), "state": "listening", "error": None,
        }
        ready.set()
        await abort.wait()

    monkeypatch.setattr(syslog_manager.SyslogManager, "_listener_loop", _listen)


async def _until_state(manager: syslog_manager.SyslogManager, state: str) -> str:
    for _ in range(100):
        current = manager.get_listener_status(WORKFLOW)["state"]
        if current == state:
            return current
        await asyncio.sleep(0.05)
    return manager.get_listener_status(WORKFLOW)["state"]


async def test_one_instance_listens_and_another_takes_over_when_it_stops(syslog_config):
    first, second = syslog_manager.SyslogManager(), syslog_manager.SyslogManager()
    try:
        assert (await first.restart_workflow(WORKFLOW))["state"] == "listening"
        assert (await second.restart_workflow(WORKFLOW))["state"] == "standby"

        await first.stop_workflow(WORKFLOW)

        assert await _until_state(second, "listening") == "listening"
    finally:
        await first.stop_workflow(WORKFLOW)
        await second.stop_workflow(WORKFLOW)


async def test_a_listener_that_loses_its_lease_stops_listening(syslog_config, no_sockets):
    first, second = syslog_manager.SyslogManager(), syslog_manager.SyslogManager()
    try:
        assert (await first.restart_workflow(WORKFLOW))["state"] == "listening"
        assert (await second.restart_workflow(WORKFLOW))["state"] == "standby"

        # The holder's database connection dies: its lock is gone with it.
        await first._leases[WORKFLOW]._connection.close()

        assert await _until_state(second, "listening") == "listening"
        assert await _until_state(first, "standby") == "standby"
        assert first._tasks.get(WORKFLOW) is None
    finally:
        await first.stop_workflow(WORKFLOW)
        await second.stop_workflow(WORKFLOW)


async def test_a_holder_that_cannot_bind_lets_another_instance_listen(syslog_config, monkeypatch):
    broken: list[syslog_manager.SyslogManager] = []

    async def _listen(self, workflow_id, config, queue, abort, ready):
        if self in broken:
            self._listener_status[workflow_id] = {
                **(self._listener_status.get(workflow_id) or {}), "state": "failed", "error": "port busy here",
            }
            ready.set()
            return
        self._listener_status[workflow_id] = {
            **(self._listener_status.get(workflow_id) or {}), "state": "listening", "error": None,
        }
        ready.set()
        await abort.wait()

    monkeypatch.setattr(syslog_manager.SyslogManager, "_listener_loop", _listen)
    first, second = syslog_manager.SyslogManager(), syslog_manager.SyslogManager()
    broken.append(first)
    try:
        assert (await second.restart_workflow(WORKFLOW))["state"] == "listening"
        await second.stop_workflow(WORKFLOW)
        # Second stopped; first (which cannot bind) gets the lease, fails, and
        # must give it back rather than sit on it.
        assert (await first.restart_workflow(WORKFLOW))["state"] == "failed"
        assert WORKFLOW not in first._leases
        assert (await second.restart_workflow(WORKFLOW))["state"] == "listening"
    finally:
        await first.stop_workflow(WORKFLOW)
        await second.stop_workflow(WORKFLOW)


async def test_concurrent_restarts_leave_one_lease_and_nothing_behind(syslog_config, no_sockets):
    first, second = syslog_manager.SyslogManager(), syslog_manager.SyslogManager()
    try:
        states = await asyncio.gather(*(first.restart_workflow(WORKFLOW) for _ in range(3)))
        assert states[-1]["state"] == "listening"
        assert list(first._leases) == [WORKFLOW]

        await first.stop_workflow(WORKFLOW)

        # Nothing of the earlier restarts kept the lease: another instance gets it at once.
        assert (await second.restart_workflow(WORKFLOW))["state"] == "listening"
    finally:
        await first.stop_workflow(WORKFLOW)
        await second.stop_workflow(WORKFLOW)


async def test_stop_all_also_stops_an_instance_standing_by(syslog_config, no_sockets):
    first, second = syslog_manager.SyslogManager(), syslog_manager.SyslogManager()
    try:
        assert (await first.restart_workflow(WORKFLOW))["state"] == "listening"
        assert (await second.restart_workflow(WORKFLOW))["state"] == "standby"

        await second.stop_all()
        await first.stop_workflow(WORKFLOW)
        await asyncio.sleep(0.5)

        assert second.get_listener_status(WORKFLOW)["state"] != "listening", "a stopped instance never takes over"
        assert not second._lease_tasks and not second._leases
    finally:
        await first.stop_workflow(WORKFLOW)
        await second.stop_workflow(WORKFLOW)


async def test_a_listener_that_dies_later_gives_the_lease_back(syslog_config, monkeypatch):
    die = asyncio.Event()

    async def _listen(self, workflow_id, config, queue, abort, ready):
        self._listener_status[workflow_id] = {
            **(self._listener_status.get(workflow_id) or {}), "state": "listening", "error": None,
        }
        ready.set()
        if self is first:
            await die.wait()
            raise OSError("socket closed under us")
        await abort.wait()

    monkeypatch.setattr(syslog_manager.SyslogManager, "_listener_loop", _listen)
    first, second = syslog_manager.SyslogManager(), syslog_manager.SyslogManager()
    try:
        assert (await first.restart_workflow(WORKFLOW))["state"] == "listening"
        assert (await second.restart_workflow(WORKFLOW))["state"] == "standby"

        die.set()

        assert await _until_state(second, "listening") == "listening"
        assert await _until_state(first, "failed") == "failed"
        assert WORKFLOW not in first._leases
    finally:
        await first.stop_workflow(WORKFLOW)
        await second.stop_workflow(WORKFLOW)


async def test_stop_all_stops_a_restart_still_reading_its_config(syslog_config, no_sockets, monkeypatch):
    """Review round 2 (batch 4): a restart under way had registered nothing yet."""
    reading = asyncio.Event()
    release = asyncio.Event()

    async def _slow_config(workflow_id, kind=None):
        reading.set()
        await release.wait()
        return syslog_config

    monkeypatch.setattr(syslog_manager.WorkflowStore, "get_config", _slow_config)
    manager = syslog_manager.SyslogManager()
    restart = asyncio.create_task(manager.restart_workflow(WORKFLOW))
    await reading.wait()

    stopping = asyncio.create_task(manager.stop_all())
    await asyncio.sleep(0.05)
    release.set()
    result = await restart
    await stopping

    assert result["state"] == "stopped"
    assert manager.get_listener_status(WORKFLOW)["state"] == "stopped"
    assert not manager._leases and not manager._tasks and not manager._lease_tasks
