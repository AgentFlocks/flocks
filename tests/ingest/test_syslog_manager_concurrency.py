"""Restart and stop of one syslog workflow never leave a listener nothing tracks."""

from __future__ import annotations

import asyncio

import pytest

import flocks.ingest.syslog.manager as syslog_manager

WORKFLOW = "wf-concurrent-restart"
CONFIG = {
    "workflowId": WORKFLOW, "enabled": True, "protocol": "udp",
    "host": "127.0.0.1", "port": 15198, "format": "auto", "inputKey": "syslog_message",
}


@pytest.fixture
def listeners(monkeypatch: pytest.MonkeyPatch):
    """The abort events of the listeners serving right now."""
    serving: list[asyncio.Event] = []

    async def _serve(host, port, format_hint, on_message, *, abort_event, on_bound=None):
        serving.append(abort_event)
        try:
            if on_bound is not None:
                on_bound()
            await abort_event.wait()
        finally:
            serving.remove(abort_event)

    monkeypatch.setattr(syslog_manager, "run_udp_syslog_server", _serve)
    monkeypatch.setattr(syslog_manager, "_BIND_WAIT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(
        syslog_manager, "read_workflow_from_fs",
        lambda wid: {"id": wid, "workflowJson": {"start": "n1", "nodes": [{"id": "n1", "type": "python", "code": "result = {}"}], "edges": []}},
    )
    return serving


def _slow_config(monkeypatch: pytest.MonkeyPatch, gate: asyncio.Event) -> None:
    async def _get_config(workflow_id, *, kind):
        await gate.wait()
        return dict(CONFIG)

    monkeypatch.setattr(syslog_manager.WorkflowStore, "get_config", _get_config)


@pytest.mark.asyncio
async def test_two_restarts_at_once_leave_one_listener(monkeypatch, listeners) -> None:
    """Two saves of the config at once: both stop before either starts unless they take turns."""
    gate = asyncio.Event()
    _slow_config(monkeypatch, gate)
    manager = syslog_manager.SyslogManager()
    try:
        both = asyncio.gather(manager.restart_workflow(WORKFLOW), manager.restart_workflow(WORKFLOW))
        await asyncio.sleep(0.05)
        gate.set()
        statuses = await both

        assert [status["state"] for status in statuses] == ["listening", "listening"]
        assert len(listeners) == 1
    finally:
        await manager.stop_workflow(WORKFLOW)
    assert listeners == []
    assert manager.get_listener_status(WORKFLOW)["state"] == "stopped"


@pytest.mark.asyncio
async def test_stop_all_during_a_restart_starts_nothing(monkeypatch, listeners) -> None:
    """Stopped while a restart still reads its config: no listener after that."""
    gate = asyncio.Event()
    _slow_config(monkeypatch, gate)
    manager = syslog_manager.SyslogManager()
    restarting = asyncio.create_task(manager.restart_workflow(WORKFLOW))
    await asyncio.sleep(0.05)

    stopping = asyncio.create_task(manager.stop_all())
    await asyncio.sleep(0.05)
    gate.set()
    status = await restarting
    await stopping

    assert status["state"] == "stopped"
    assert listeners == []
    assert manager.get_listener_status(WORKFLOW)["state"] == "stopped"
