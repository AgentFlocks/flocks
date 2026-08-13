"""SSH command execution primitive with OSS static blacklist guard."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
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


class SafetyDecision:
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"


_BLOCKED_PATTERN_SOURCES = [
    r"(?<![a-z])rm\b",
    r"\brmdir\b",
    r"\bmkdir\b",
    r"\btouch\b",
    r"\bcp\b\s",
    r"\bmv\b\s",
    r"\bln\b\s",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bchattr\b",
    r"\btruncate\b",
    r"(?<![<>])>>?\s",
    r"\btee\b",
    r"(?<![a-z])sudo\b",
    r"(?<![a-z])su\b(\s|$)",
    r"(?<![a-z])kill\b",
    r"\bkillall\b",
    r"\bpkill\b",
    r"\bapt(?:-get)?\s+install\b",
    r"\byum\s+install\b",
    r"\bdnf\s+install\b",
    r"\bpip\s+install\b",
    r"\bnpm\s+install\b",
    r"\bsnap\s+install\b",
    r"\bbrew\s+install\b",
    r"\bsystemctl\s+(start|stop|restart|enable|disable|mask|unmask)\b",
    r"\bservice\s+\S+\s+(start|stop|restart)\b",
    r"\bwget\b",
    r"\bcurl\b.*(?:-o\b|--output\b)",
    r"\bsed\b.*-i\b",
    r"\bfind\b.*-exec\s+(?:rm|mv|cp|chmod|chown)\b",
    r"\bfind\b.*-delete\b",
]
_BLOCKED_PATTERNS = [re.compile(p) for p in _BLOCKED_PATTERN_SOURCES]
_BLOCKED_BASE_COMMANDS = {
    "passwd",
    "usermod",
    "useradd",
    "userdel",
    "newgrp",
    "visudo",
    "vipw",
    "vigr",
}


def _strip_quoted(text: str) -> str:
    return re.sub(r"""'[^']*'|"[^"]*\"""", " ", text)


def _unescape_unquoted(text: str) -> str:
    """Normalize shell escapes that form executable names or operators."""
    return re.sub(r"\\(.)", r"\1", text, flags=re.DOTALL)


def _split_pipeline(command: str) -> list[str]:
    in_single = False
    in_double = False
    segments: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(command):
        c = command[i]
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
        elif not in_single and not in_double:
            if c in {"|", ";"}:
                segments.append("".join(current).strip())
                current = []
            elif c == "&" and i + 1 < len(command) and command[i + 1] == "&":
                segments.append("".join(current).strip())
                current = []
                i += 1
            else:
                current.append(c)
        else:
            current.append(c)
        i += 1
    segments.append("".join(current).strip())
    return [segment for segment in segments if segment]


def _get_base_command(segment: str) -> str:
    segment = re.sub(r"^\s*(?:\w+=\S+\s+)+", "", segment).strip()
    tokens = segment.split()
    if not tokens:
        return ""
    return Path(tokens[0]).name


def classify_command(command: str) -> tuple[str, str]:
    decisions: list[tuple[str, str]] = []
    for segment in _split_pipeline(command):
        stripped = _unescape_unquoted(_strip_quoted(segment))
        if any(pattern.search(stripped) for pattern in _BLOCKED_PATTERNS):
            decisions.append((SafetyDecision.BLOCKED, segment))
            continue
        base = _get_base_command(segment)
        if base in _BLOCKED_BASE_COMMANDS:
            decisions.append((SafetyDecision.BLOCKED, segment))
            continue
        decisions.append((SafetyDecision.ALLOWED, segment))
    if any(decision == SafetyDecision.BLOCKED for decision, _ in decisions):
        blocked_segments = [segment for decision, segment in decisions if decision == SafetyDecision.BLOCKED]
        return SafetyDecision.BLOCKED, f"blocked segment(s): {blocked_segments}"
    return SafetyDecision.ALLOWED, "static-rule"


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
    """Execute the supplied SSH command after OSS blacklist validation."""

    start_ms = int(time.time() * 1000)
    username, key_path, password = resolve_ssh_credentials(
        username, key_path, password
    )
    timeout = min(max(1, timeout), MAX_TIMEOUT_S)
    safety_decision, safety_reason = classify_command(command)

    if safety_decision == SafetyDecision.BLOCKED:
        return ToolResult(
            success=False,
            error=f"[BLOCKED] Command rejected by OSS safety blacklist: {safety_reason}",
            metadata={"safety_decision": safety_decision, "safety_reason": safety_reason},
        )

    if dry_run:
        return ToolResult(
            success=True,
            output={
                "dry_run": True,
                "command": command,
                "safety_decision": safety_decision,
                "reason": safety_reason,
            },
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
    """Registry handler for SSH execution with OSS blacklist guard."""

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
