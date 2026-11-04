"""Listener lifecycle regressions: bind-before-listening and oversized TCP lines."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock

import pytest

from flocks.ingest.syslog import listener as syslog_listener
from flocks.ingest.syslog import manager as syslog_manager


def _free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.asyncio
async def test_udp_on_bound_fires_only_after_bind_succeeds() -> None:
    port = _free_port(socket.SOCK_DGRAM)
    abort = asyncio.Event()
    bound: list[bool] = []

    task = asyncio.create_task(
        syslog_listener.run_udp_syslog_server(
            "127.0.0.1", port, "auto", lambda _m: None, abort_event=abort, on_bound=lambda: bound.append(True)
        )
    )
    try:
        for _ in range(50):
            if bound:
                break
            await asyncio.sleep(0.01)
        assert bound == [True]
        # The socket really exists once on_bound fired: a second bind must fail.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            with pytest.raises(OSError):
                probe.bind(("127.0.0.1", port))
    finally:
        abort.set()
        await task


@pytest.mark.asyncio
async def test_on_bound_not_called_when_bind_fails() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        bound: list[bool] = []
        with pytest.raises(OSError):
            await syslog_listener.run_udp_syslog_server(
                "127.0.0.1", port, "auto", lambda _m: None,
                abort_event=asyncio.Event(), on_bound=lambda: bound.append(True),
            )
        assert bound == []


@pytest.mark.asyncio
async def test_manager_reports_listening_only_after_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow bind (DNS/getaddrinfo in the thread pool) must not be reported as listening."""
    workflow_id = "wf-slow-bind"
    config = {
        "workflowId": workflow_id, "enabled": True, "protocol": "udp",
        "host": "127.0.0.1", "port": _free_port(socket.SOCK_DGRAM), "format": "auto", "inputKey": "syslog_message",
    }
    monkeypatch.setattr(syslog_manager.WorkflowStore, "get_config", AsyncMock(return_value=config))
    monkeypatch.setattr(
        syslog_manager, "read_workflow_from_fs",
        lambda wid: {"id": wid, "workflowJson": {"start": "n1", "nodes": [{"id": "n1", "type": "python", "code": "result = {}"}], "edges": []}},
    )
    monkeypatch.setattr(syslog_manager, "_BIND_WAIT_TIMEOUT_S", 0.2)

    release = asyncio.Event()

    async def _slow_failing_bind(host, port, format_hint, on_message, *, abort_event, on_bound=None):
        # Simulate a resolver round-trip longer than one loop tick, then fail.
        await release.wait()
        raise OSError("could not bind to local_addr ('999.1.1.1', 15977): [Errno 49] Can't assign requested address")

    monkeypatch.setattr(syslog_manager, "run_udp_syslog_server", _slow_failing_bind)

    manager = syslog_manager.SyslogManager()
    try:
        status = await manager.restart_workflow(workflow_id)
        # Not bound yet -> must not claim "listening".
        assert status["state"] == "binding", status
        release.set()
        for _ in range(50):
            if manager.get_listener_status(workflow_id)["state"] == "failed":
                break
            await asyncio.sleep(0.01)
        final = manager.get_listener_status(workflow_id)
        assert final["state"] == "failed"
        assert "Errno 49" in final["error"]
    finally:
        await manager.stop_workflow(workflow_id)


@pytest.mark.asyncio
async def test_manager_slow_bind_turns_listening_once_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slow but successful bind: restart reports ``binding`` within the wait budget, then flips to ``listening``."""
    workflow_id = "wf-slow-bind-ok"
    config = {
        "workflowId": workflow_id, "enabled": True, "protocol": "udp",
        "host": "127.0.0.1", "port": _free_port(socket.SOCK_DGRAM), "format": "auto", "inputKey": "syslog_message",
    }
    monkeypatch.setattr(syslog_manager.WorkflowStore, "get_config", AsyncMock(return_value=config))
    monkeypatch.setattr(
        syslog_manager, "read_workflow_from_fs",
        lambda wid: {"id": wid, "workflowJson": {"start": "n1", "nodes": [{"id": "n1", "type": "python", "code": "result = {}"}], "edges": []}},
    )
    monkeypatch.setattr(syslog_manager, "_BIND_WAIT_TIMEOUT_S", 0.2)

    release = asyncio.Event()

    async def _slow_ok_bind(host, port, format_hint, on_message, *, abort_event, on_bound=None):
        await release.wait()
        if on_bound is not None:
            on_bound()
        await abort_event.wait()

    monkeypatch.setattr(syslog_manager, "run_udp_syslog_server", _slow_ok_bind)

    manager = syslog_manager.SyslogManager()
    try:
        status = await manager.restart_workflow(workflow_id)
        assert status["state"] == "binding" and status["error"] == "bind_pending_timeout", status
        release.set()
        for _ in range(50):
            if manager.get_listener_status(workflow_id)["state"] == "listening":
                break
            await asyncio.sleep(0.01)
        final = manager.get_listener_status(workflow_id)
        assert final["state"] == "listening" and final["error"] is None, final
    finally:
        await manager.stop_workflow(workflow_id)


@pytest.mark.asyncio
async def test_tcp_oversized_line_is_dropped_but_connection_survives() -> None:
    port = _free_port(socket.SOCK_STREAM)
    abort = asyncio.Event()
    bound = asyncio.Event()
    received: list[str] = []

    task = asyncio.create_task(
        syslog_listener.run_tcp_syslog_server(
            "127.0.0.1", port, "auto", lambda m: received.append(m["message"]),
            abort_event=abort, on_bound=bound.set,
        )
    )
    try:
        await asyncio.wait_for(bound.wait(), 5)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"<13>before\n")
        writer.write(b"<13>" + b"x" * (syslog_listener.TCP_MAX_LINE_BYTES + 5000) + b"\n")
        writer.write(b"<13>after\n")
        await writer.drain()
        for _ in range(100):
            if "after" in received:
                break
            await asyncio.sleep(0.02)
        writer.close()
        await writer.wait_closed()
        assert "before" in received and "after" in received, received
        assert not any(len(m) > syslog_listener.TCP_MAX_LINE_BYTES for m in received)
    finally:
        abort.set()
        await task
