"""Regression test for the bind-failure path of ``SyslogManager.restart_workflow``.

The HTTP ``POST /api/workflow/{id}/syslog-config`` endpoint relies on
``restart_workflow`` synchronously reporting the listener's terminal state so
the route can return ``409 Conflict`` instead of falsely claiming success.

We reproduce the failure by binding our own UDP socket on a chosen port and
then asking ``SyslogManager`` to start a listener for the same host/port; the
``OSError`` raised inside ``_listener_loop`` must surface as
``state == "failed"`` in the returned status.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from flocks.ingest.syslog import manager as syslog_manager


def _find_busy_udp_port() -> tuple[socket.socket, int]:
    """Bind a UDP socket on a free port and return it (still bound)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    return sock, port


@pytest.mark.asyncio
async def test_restart_workflow_reports_failure_on_port_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restarting a listener on a busy port must yield state="failed"."""
    busy_sock, busy_port = _find_busy_udp_port()
    try:
        workflow_id = "wf-bind-fail"
        config = {
            "workflowId": workflow_id,
            "enabled": True,
            "protocol": "udp",
            "host": "127.0.0.1",
            "port": busy_port,
            "format": "auto",
            "inputKey": "syslog_message",
        }

        async def _fake_storage_read(key: str):  # noqa: ANN001
            if key == syslog_manager.SyslogManager._config_key(workflow_id):
                return config
            return None

        def _fake_read_workflow_from_fs(wid: str):  # noqa: ANN001
            return {
                "id": wid,
                "workflowJson": {
                    "start": "n1",
                    "nodes": [{"id": "n1", "type": "python", "code": "result = {'ok': True}"}],
                    "edges": [],
                },
            }

        # Patch the *module-level* names ``manager.py`` looks up at call time.
        monkeypatch.setattr(syslog_manager.Storage, "read", _fake_storage_read)
        monkeypatch.setattr(syslog_manager, "read_workflow_from_fs", _fake_read_workflow_from_fs)

        manager = syslog_manager.SyslogManager()
        try:
            status = await manager.restart_workflow(workflow_id)
            assert status["state"] == "failed", (
                f"expected state='failed' on busy port, got {status!r}"
            )
            assert status.get("error"), "failed status must include an error message"
            assert status["port"] == busy_port
        finally:
            await manager.stop_workflow(workflow_id)
    finally:
        busy_sock.close()


@pytest.mark.asyncio
async def test_restart_workflow_returns_stopped_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A saved-but-disabled config must report state="stopped"."""
    workflow_id = "wf-disabled"
    config = {
        "workflowId": workflow_id,
        "enabled": False,
        "protocol": "udp",
        "host": "127.0.0.1",
        "port": 9999,
        "format": "auto",
        "inputKey": "syslog_message",
    }

    async def _fake_storage_read(key: str):  # noqa: ANN001
        return config

    monkeypatch.setattr(syslog_manager.Storage, "read", _fake_storage_read)

    manager = syslog_manager.SyslogManager()
    status = await manager.restart_workflow(workflow_id)
    assert status == {"state": "stopped", "error": None}
    assert manager.get_listener_status(workflow_id) == {"state": "stopped", "error": None}
