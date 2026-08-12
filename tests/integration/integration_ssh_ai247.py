"""Optional manual smoke test for the neutral OSS SSH transport.

This is intentionally excluded from the normal test suite.  It validates
connectivity only; command authorization, audit, and command safety belong to
FlocksPro and must be exercised by its local policy tests rather than here.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry


TARGET_HOST = "ai247"


def _is_host_reachable(host: str) -> bool:
    """Return whether the configured manual-test host accepts SSH."""

    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "echo ok"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def require_manual_host() -> None:
    if not _is_host_reachable(TARGET_HOST):
        pytest.skip(f"Host '{TARGET_HOST}' is not reachable — skipping manual SSH smoke")


def _run(command: str, **kwargs):
    import flocks.tool.security.ssh_host_cmd  # noqa: F401

    return asyncio.run(
        ToolRegistry.execute(
            "ssh_host_cmd",
            ToolContext(session_id="integration-test", message_id="msg-001"),
            host=TARGET_HOST,
            command=command,
            **kwargs,
        )
    )


def test_neutral_ssh_transport_executes_a_manual_smoke_command() -> None:
    result = _run("uname -a")
    assert result.success, result.error
    assert result.output


def test_neutral_ssh_transport_dry_run_does_not_execute() -> None:
    result = _run("uname -a", dry_run=True)
    assert result.success
    assert result.output["dry_run"] is True
