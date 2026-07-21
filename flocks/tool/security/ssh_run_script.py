"""Neutral SSH script execution primitive.

Flocks supplies file handling and remote execution only.  Script analysis,
approval, audit semantics, and enforcement are FlocksPro responsibilities.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
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


DEFAULT_TIMEOUT_S = 60
MAX_TIMEOUT_S = 600
MAX_OUTPUT_BYTES = 80_000


def _extract_sections(output: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_section = "HEADER"
    current_lines: list[str] = []
    for line in output.splitlines():
        if line.startswith("### ") and line.endswith(" ###"):
            sections[current_section] = "\n".join(current_lines).strip()
            current_section = line[4:-4].strip()
            current_lines = []
        else:
            current_lines.append(line)
    sections[current_section] = "\n".join(current_lines).strip()
    return sections


def _truncate_output(output: str, max_bytes: int = MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return output, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]
    return truncated + "\n\n[... output truncated, remaining data omitted ...]", True


async def execute_ssh_script_content(
    ctx: ToolContext,
    *,
    host: str,
    script_content: str,
    script_label: str,
    username: Optional[str] = None,
    port: int = 22,
    key_path: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_S,
    script_path: str | None = None,
) -> ToolResult:
    """Execute supplied script content without rereading or interpreting it."""

    if not script_content.strip():
        return ToolResult(success=False, error="Script content is empty.")

    start_ms = int(time.time() * 1000)
    username, key_path, password = resolve_ssh_credentials(
        username, key_path, password
    )
    timeout = min(max(10, timeout), MAX_TIMEOUT_S)
    try:
        exit_code, stdout, stderr = await execute_ssh_command(
            host=host,
            command=script_content,
            username=username,
            port=port,
            key_path=key_path,
            password=password,
            timeout_s=timeout,
            session_id=ctx.session_id,
        )
    except Exception as exc:
        error = (
            f"Script '{script_label}' timed out after {timeout}s"
            if isinstance(exc, asyncio.TimeoutError)
            else f"SSH connection failed: {exc}"
        )
        return ToolResult(success=False, error=error)

    elapsed = int(time.time() * 1000) - start_ms
    raw_output = stdout + (f"\n[stderr]\n{stderr}" if stderr else "")
    truncated_output, was_truncated = _truncate_output(raw_output)
    sections = _extract_sections(truncated_output)
    summary = (
        "=== SCRIPT EXECUTION SUMMARY ===\n"
        f"Script: {script_label} | Host: {host} | User: {username} | Elapsed: {elapsed}ms\n"
        f"Exit code: {exit_code} | Sections collected: {len([k for k in sections if k not in ('HEADER', '')])}\n\n"
        "=== FULL OUTPUT ===\n"
    )
    return ToolResult(
        success=exit_code == 0,
        output=summary + truncated_output,
        error=None if exit_code == 0 else f"Script exited with code {exit_code}",
        metadata={
            "host": host,
            "username": username,
            "port": port,
            "script": script_label,
            "script_path": script_path,
            "exit_code": exit_code,
            "elapsed_ms": elapsed,
            "output_bytes_raw": len(raw_output.encode()),
            "output_truncated": was_truncated,
            "sections_collected": [key for key in sections if key not in ("HEADER", "")],
        },
    )


@ToolRegistry.register_function(
    name="ssh_run_script",
    description="Execute a local shell script on a remote Linux host via SSH.",
    category=ToolCategory.TERMINAL,
    parameters=[
        ToolParameter(
            name="host",
            type=ParameterType.STRING,
            description="Target host IP address or hostname",
        ),
        ToolParameter(
            name="script_path",
            type=ParameterType.STRING,
            description="Local script path",
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
    ],
)
async def ssh_run_script(
    ctx: ToolContext,
    host: str,
    script_path: str,
    username: Optional[str] = None,
    port: int = 22,
    key_path: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> ToolResult:
    """Read a script once and pass its content to the neutral executor."""

    resolved_path = Path(script_path).expanduser()
    if not resolved_path.is_absolute():
        resolved_path = Path.cwd() / resolved_path
    try:
        if not resolved_path.is_file():
            return ToolResult(success=False, error=f"Script not found: {resolved_path}")
        script_content = resolved_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return ToolResult(success=False, error=f"Cannot read script: {exc}")

    return await execute_ssh_script_content(
        ctx,
        host=host,
        script_content=script_content,
        script_label=resolved_path.name,
        username=username,
        port=port,
        key_path=key_path,
        password=password,
        timeout=timeout,
        script_path=str(resolved_path),
    )


__all__ = ["execute_ssh_script_content", "ssh_run_script"]
