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
from unittest.mock import AsyncMock, MagicMock

import pytest

from flocks.ingest.syslog import manager as syslog_manager


def _find_busy_udp_port() -> tuple[socket.socket, int]:
    """Bind a UDP socket on a free port and return it (still bound)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    return sock, port


@pytest.mark.asyncio
async def test_start_all_logs_stale_workflow_config_as_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "removed-workflow"
    config = {"workflowId": workflow_id, "enabled": True}
    info_log = MagicMock()
    warning_log = MagicMock()

    monkeypatch.setattr(
        syslog_manager.WorkflowStore,
        "list_configs",
        AsyncMock(return_value=[(workflow_id, config)]),
    )
    monkeypatch.setattr(
        syslog_manager.WorkflowStore,
        "get_config",
        AsyncMock(return_value=config),
    )
    monkeypatch.setattr(syslog_manager, "read_workflow_from_fs", lambda _workflow_id: None)
    monkeypatch.setattr(syslog_manager.log, "info", info_log)
    monkeypatch.setattr(syslog_manager.log, "warning", warning_log)

    manager = syslog_manager.SyslogManager()
    await manager.start_all()

    assert manager.get_listener_status(workflow_id) == {
        "state": "stopped",
        "error": "workflow_not_found",
    }
    info_log.assert_called_once_with(
        "syslog.workflow_not_found_on_start",
        {
            "workflow_id": workflow_id,
            "action": "stale_config_skipped",
        },
    )
    warning_log.assert_not_called()


@pytest.mark.asyncio
async def test_start_all_continues_after_one_workflow_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restarted: list[str] = []
    warning_log = MagicMock()

    monkeypatch.setattr(
        syslog_manager.WorkflowStore,
        "list_configs",
        AsyncMock(
            return_value=[
                ("broken-workflow", {"enabled": True}),
                ("healthy-workflow", {"enabled": True}),
            ]
        ),
    )

    async def _restart(workflow_id: str, *, startup: bool = False) -> dict:
        assert startup is True
        restarted.append(workflow_id)
        if workflow_id == "broken-workflow":
            raise ValueError("invalid config")
        return {"state": "listening", "error": None}

    manager = syslog_manager.SyslogManager()
    monkeypatch.setattr(manager, "restart_workflow", _restart)
    monkeypatch.setattr(syslog_manager.log, "warning", warning_log)

    await manager.start_all()

    assert restarted == ["broken-workflow", "healthy-workflow"]
    warning_log.assert_called_once_with(
        "syslog.start_failed",
        {"workflow_id": "broken-workflow", "error": "invalid config"},
    )


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
        monkeypatch.setattr(
            syslog_manager.WorkflowStore,
            "get_config",
            AsyncMock(return_value=config),
        )
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

    monkeypatch.setattr(
        syslog_manager.WorkflowStore,
        "get_config",
        AsyncMock(return_value=config),
    )

    manager = syslog_manager.SyslogManager()
    status = await manager.restart_workflow(workflow_id)
    assert status == {"state": "stopped", "error": None}
    assert manager.get_listener_status(workflow_id) == {"state": "stopped", "error": None}
