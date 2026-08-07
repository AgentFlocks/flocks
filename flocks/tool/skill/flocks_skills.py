"""
flocks_skills tool — Skill management for Rex.

Wraps the `flocks skills` CLI so Rex can search, install, check status,
and manage agent skills without composing raw bash commands.

Design principle: one tool, all subcommands.  Rex sees the full command
surface in the tool description and picks the right subcommand for each
situation.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Optional

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log


log = Log.create(service="tool.flocks_skills")

_TIMEOUT_SEC = 120
_MAX_OUTPUT = 8_000  # chars — keep responses concise for the model

_DESCRIPTION = (
    "Manage skills from external registries. Actions: find by query, install from "
    "source, show dependency status, install a skill's dependencies, or remove a "
    "user-managed skill. Use skill_load instead to load an installed skill."
)

# Allowed subcommands — enforced to prevent arbitrary command execution.
# Ordered for consistent display in tool schema enum and error messages.
_ALLOWED_SUBCOMMANDS = frozenset(
    ["find", "install", "status", "install-deps", "remove"]
)
_SUBCOMMAND_ENUM = ["find", "install", "status", "install-deps", "remove"]

# Read-only registry / discovery — no shell side effects; skip bash permission gate.
_READ_ONLY_SUBCOMMANDS = frozenset({"find", "status"})


def _flocks_executable() -> Optional[str]:
    """Locate the `flocks` CLI on PATH."""
    return shutil.which("flocks")


@ToolRegistry.register_function(
    name="flocks_skills",
    description=_DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="subcommand",
            type=ParameterType.STRING,
            description=(
                "Skill management subcommand: "
                "find | install | status | install-deps | remove"
            ),
            required=True,
            enum=_SUBCOMMAND_ENUM,
        ),
        ToolParameter(
            name="query",
            type=ParameterType.STRING,
            description="Registry search query for subcommand=find.",
            required=False,
        ),
        ToolParameter(
            name="source",
            type=ParameterType.STRING,
            description=(
                "Skill source for subcommand=install, such as "
                "github:owner/repo/skill, clawhub:name, skills-sh:owner/repo/skill, "
                "safeskill://..., or an HTTPS SKILL.md URL."
            ),
            required=False,
        ),
        ToolParameter(
            name="skill_name",
            type=ParameterType.STRING,
            description="Installed skill name for install-deps or remove.",
            required=False,
        ),
        ToolParameter(
            name="scope",
            type=ParameterType.STRING,
            description="Installation scope for subcommand=install.",
            required=False,
            default="global",
            enum=["global", "project"],
        ),
    ],
)
async def flocks_skills(
    ctx: ToolContext,
    subcommand: str,
    query: Optional[str] = None,
    source: Optional[str] = None,
    skill_name: Optional[str] = None,
    scope: str = "global",
) -> ToolResult:
    """Execute a `flocks skills <subcommand>` command and return its output."""
    if subcommand not in _ALLOWED_SUBCOMMANDS:
        return ToolResult(
            success=False,
            error=(
                f"Unknown subcommand: {subcommand!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_SUBCOMMANDS))}"
            ),
        )

    if subcommand == "install":
        if not source:
            return ToolResult(
                success=False,
                error="install requires a source, e.g. github:owner/repo/skill-name or safeskill://...",
            )
        if scope not in {"global", "project"}:
            return ToolResult(
                success=False,
                error="install --scope must be 'global' or 'project'",
            )
        await ctx.ask(
            permission="bash",
            patterns=[
                f"flocks skills install {source} "
                f"--scope {scope} --yes"
            ],
            always=["*flocks skills *"],
            metadata={"subcommand": subcommand},
        )
        try:
            from flocks.skill.installer import SkillInstaller

            result = await SkillInstaller.install_from_source(
                source,
                scope=scope,
                yes=True,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Skill install failed: {exc}",
                title="flocks skills install",
            )
        if not result.success:
            return ToolResult(
                success=False,
                error=result.error or result.message or "Skill install failed",
                title="flocks skills install",
            )
        return ToolResult(
            success=True,
            output={
                "message": result.message,
                "skill_name": result.skill_name,
                "location": result.location,
            },
            title="flocks skills install",
        )

    flocks_bin = _flocks_executable()
    if flocks_bin is None:
        return ToolResult(
            success=False,
            error=(
                "The `flocks` CLI was not found on PATH. "
                "Make sure Flocks is installed and activated in the current environment."
            ),
        )

    command_args: list[str] = []
    if subcommand == "find":
        if not query:
            return ToolResult(success=False, error="find requires query")
        command_args.append(query)
    elif subcommand in {"install-deps", "remove"}:
        if not skill_name:
            return ToolResult(
                success=False,
                error=f"{subcommand} requires skill_name",
            )
        command_args.append(skill_name)

    # Build the command list — no shell interpolation, safe from injection.
    cmd: list[str] = [flocks_bin, "skills", subcommand]
    cmd.extend(command_args)
    # `skills add` (downstream of install for skills-sh sources) and remove
    # both prompt interactively. Auto-add --yes so non-interactive agent
    # calls don't hang.
    if subcommand in ("install", "remove") and "--yes" not in cmd and "-y" not in cmd:
        cmd.append("--yes")

    log.info("flocks_skills.run", {"cmd": cmd})

    # Mutating subcommands need bash approval. Read-only (find/status) runs
    # without prompting — same trust model as listing skills in the UI.
    #
    # For install/remove/install-deps, always-patterns must match the *full*
    # argv string (e.g. "/opt/flocks/bin/flocks skills install ..."); a bare
    # "flocks skills *" fails fnmatch and never auto-approved.
    if subcommand not in _READ_ONLY_SUBCOMMANDS:
        await ctx.ask(
            permission="bash",
            patterns=[" ".join(cmd)],
            always=["*flocks skills *"],
            metadata={"subcommand": subcommand},
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            # Drain pipes so the process can exit cleanly and avoid zombies.
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:
                pass
            return ToolResult(
                success=False,
                error=f"Command timed out after {_TIMEOUT_SEC}s: {' '.join(cmd)}",
            )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Failed to start flocks CLI: {exc}",
        )

    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")
    output = (stdout + stderr).strip()

    # Truncate very long output so we don't flood the context window.
    if len(output) > _MAX_OUTPUT:
        output = output[:_MAX_OUTPUT] + f"\n\n[… output truncated at {_MAX_OUTPUT} chars]"

    exit_code = proc.returncode
    success = exit_code == 0

    if success:
        return ToolResult(
            success=True,
            output=output or f"flocks skills {subcommand}: completed (no output)",
            title=f"flocks skills {subcommand}",
        )

    return ToolResult(
        success=False,
        error=output or f"flocks skills {subcommand} failed (exit {exit_code})",
        title=f"flocks skills {subcommand}",
    )
