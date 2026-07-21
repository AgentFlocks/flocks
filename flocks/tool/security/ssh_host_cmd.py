"""Neutral SSH command execution primitive.

This OSS module intentionally contains no command safety classification,
approval, allowlist, LLM evaluation, or security audit policy.  FlocksPro
attaches those controls through the generic execution lifecycle hook.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.tool.security.ssh_utils import execute_ssh_command, resolve_ssh_credentials


MAX_TIMEOUT_S = 120
DEFAULT_TIMEOUT_S = 30


async def execute_ssh_host_command(
    ctx: ToolContext,
    *,
    host: str,
    command: str,
    username: Optional[str] = None,
    port: int = 22,
    key_path: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_S,
    dry_run: bool = False,
) -> ToolResult:
    """Execute the supplied SSH command without interpreting its safety."""

    start_ms = int(time.time() * 1000)
    username, key_path, password = resolve_ssh_credentials(
        username, key_path, password
    )
    timeout = min(max(1, timeout), MAX_TIMEOUT_S)

    if dry_run:
        return ToolResult(
            success=True,
            output={"dry_run": True, "command": command},
            metadata={"host": host, "username": username, "port": port},
        )

    try:
        exit_code, stdout, stderr = await execute_ssh_command(
            host=host,
            command=command,
            username=username,
            port=port,
            key_path=key_path,
            password=password,
            timeout_s=timeout,
            session_id=ctx.session_id,
        )
    except Exception as exc:
        error = (
            f"Command timed out after {timeout}s"
            if isinstance(exc, asyncio.TimeoutError)
            else f"SSH connection failed: {exc}"
        )
        return ToolResult(success=False, error=error)

    elapsed = int(time.time() * 1000) - start_ms
    output = stdout
    if stderr:
        output += f"\n[stderr]\n{stderr}"
    return ToolResult(
        success=exit_code == 0,
        output=output or "(no output)",
        error=None if exit_code == 0 else f"Command exited with code {exit_code}",
        metadata={
            "host": host,
            "username": username,
            "port": port,
            "exit_code": exit_code,
            "elapsed_ms": elapsed,
        },
    )


@ToolRegistry.register_function(
    name="ssh_host_cmd",
    description="Execute a command on a remote Linux host via SSH.",
    category=ToolCategory.TERMINAL,
    parameters=[
        ToolParameter(
            name="host",
            type=ParameterType.STRING,
            description="Target host IP address or hostname",
        ),
        ToolParameter(
            name="command",
            type=ParameterType.STRING,
            description="Command to execute on the remote host",
        ),
        ToolParameter(
            name="username",
            type=ParameterType.STRING,
            description="SSH username",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="port",
            type=ParameterType.INTEGER,
            description="SSH port number",
            required=False,
            default=22,
        ),
        ToolParameter(
            name="key_path",
            type=ParameterType.STRING,
            description="Path to SSH private key",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="password",
            type=ParameterType.STRING,
            description="SSH password",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="timeout",
            type=ParameterType.INTEGER,
            description="Timeout in seconds",
            required=False,
            default=DEFAULT_TIMEOUT_S,
        ),
        ToolParameter(
            name="dry_run",
            type=ParameterType.BOOLEAN,
            description="Return the request without executing it",
            required=False,
            default=False,
        ),
    ],
)
async def ssh_host_cmd(
    ctx: ToolContext,
    host: str,
    command: str,
    username: Optional[str] = None,
    port: int = 22,
    key_path: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_S,
    dry_run: bool = False,
) -> ToolResult:
    """Registry handler for the neutral SSH execution primitive."""

    return await execute_ssh_host_command(
        ctx,
        host=host,
        command=command,
        username=username,
        port=port,
        key_path=key_path,
        password=password,
        timeout=timeout,
        dry_run=dry_run,
    )


__all__ = ["execute_ssh_host_command", "ssh_host_cmd"]
