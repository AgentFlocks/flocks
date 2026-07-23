"""Tests for the neutral OSS SSH command transport primitive."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flocks.tool.registry import ToolContext
from flocks.tool.security.ssh_host_cmd import execute_ssh_host_command


@pytest.mark.asyncio
async def test_ssh_host_command_forwards_exact_request_to_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(return_value=(0, "ok", ""))
    monkeypatch.setattr(
        "flocks.tool.security.ssh_host_cmd.execute_ssh_command", execute
    )

    result = await execute_ssh_host_command(
        ToolContext(session_id="s-1", message_id="m-1"),
        host="host-a",
        command="printf 'exact command'",
        username="analyst",
        port=2222,
        timeout=15,
    )

    assert result.success is True
    assert result.output == "ok"
    assert execute.await_args.kwargs["command"] == "printf 'exact command'"
    assert execute.await_args.kwargs["host"] == "host-a"
    assert execute.await_args.kwargs["username"] == "analyst"
    assert execute.await_args.kwargs["port"] == 2222


@pytest.mark.asyncio
async def test_ssh_host_command_dry_run_does_not_open_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(
        "flocks.tool.security.ssh_host_cmd.execute_ssh_command", execute
    )

    result = await execute_ssh_host_command(
        ToolContext(session_id="s-1", message_id="m-1"),
        host="host-a",
        command="uname -a",
        dry_run=True,
    )

    assert result.success is True
    assert result.output == {
        "dry_run": True,
        "command": "uname -a",
        "safety_decision": "ALLOWED",
        "reason": "static-rule",
    }
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_ssh_host_command_blacklist_blocks_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(
        "flocks.tool.security.ssh_host_cmd.execute_ssh_command", execute
    )

    result = await execute_ssh_host_command(
        ToolContext(session_id="s-1", message_id="m-1"),
        host="host-a",
        command="sudo systemctl restart sshd",
    )

    assert result.success is False
    assert "OSS safety blacklist" in (result.error or "")
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_ssh_host_command_blacklist_blocks_escaped_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(
        "flocks.tool.security.ssh_host_cmd.execute_ssh_command", execute
    )

    result = await execute_ssh_host_command(
        ToolContext(session_id="s-1", message_id="m-1"),
        host="host-a",
        command=r"r\m -rf /tmp/test",
    )

    assert result.success is False
    assert "OSS safety blacklist" in (result.error or "")
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_ssh_host_command_blacklist_dry_run_still_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(
        "flocks.tool.security.ssh_host_cmd.execute_ssh_command", execute
    )

    result = await execute_ssh_host_command(
        ToolContext(session_id="s-1", message_id="m-1"),
        host="host-a",
        command="rm -rf /tmp/test",
        dry_run=True,
    )

    assert result.success is False
    assert "OSS safety blacklist" in (result.error or "")
    execute.assert_not_awaited()
