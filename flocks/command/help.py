"""
Shared help text generation for slash commands.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from flocks.command.command import API_SURFACES, Command, CommandInfo, CommandSurface

_DEFAULT_HINTS: dict[str, str] = {
    "tools": "- /tools [list|refresh|info `name`|create `requirement`]",
    "skills": "- /skills [list|refresh]",
    "mcp": "- /mcp [list|status|tools|refresh `server`]",
}

_AGENT_SAFE_HINTS: dict[str, str] = {
    "tools": "- /tools [list|info `name`]",
    "skills": "- /skills [list]",
    "agents": "- /agents",
    "workflows": "- /workflows",
    "mcp": "- /mcp [list|status|tools]",
}


def list_help_commands(surface: Optional[CommandSurface] = None) -> list[CommandInfo]:
    """Return commands that should appear in help for a surface."""
    if surface is not None:
        return Command.list(surface=surface)
    return Command.list_for_surfaces(API_SURFACES)


def _select_hints(command_names: Iterable[str], *, agent_safe: bool) -> list[str]:
    hints = _AGENT_SAFE_HINTS if agent_safe else _DEFAULT_HINTS
    names = set(command_names)
    selected = [hints[name] for name in hints if name in names]
    return selected


def _append_command_group(lines: list[str], heading: str, commands: Sequence[CommandInfo]) -> None:
    if not commands:
        return

    lines.extend([heading, ""])
    for command in commands:
        lines.append(f"- /{command.name}: {command.description}")
    lines.append("")


def format_help(
    surface: Optional[CommandSurface] = None,
    *,
    commands: Optional[Sequence[CommandInfo]] = None,
    agent_safe: bool = False,
    title: str = "Available / commands:",
) -> str:
    """Format a slash-command help block from shared registry metadata."""
    help_commands = list(commands) if commands is not None else list_help_commands(surface)
    lines = [title, ""]

    direct_commands = [command for command in help_commands if command.execution_kind == "direct"]
    deferred_commands = [command for command in help_commands if command.execution_kind != "direct"]

    _append_command_group(lines, "Direct commands:", direct_commands)

    if deferred_commands:
        deferred_heading = "Other commands (handled through the normal assistant/session flow):"
        _append_command_group(lines, deferred_heading, deferred_commands)

    tip_lines = _select_hints((command.name for command in help_commands), agent_safe=agent_safe)
    if tip_lines:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(["Tips:", *tip_lines])

    return "\n".join(lines).rstrip()
