"""
Agent-safe slash command dispatcher tool.
"""

from __future__ import annotations

from flocks.command.direct import (
    build_tools_catalog_summary,
    list_agent_safe_direct_commands,
    run_direct_command,
)
from flocks.command.help import format_help
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log

log = Log.create(service="tool.slash_command")

_READ_ONLY_ARGUMENT_HINTS = {
    "help": "",
    "tools": "[list|info `name`]",
    "skills": "[list]",
    "agents": "",
    "workflows": "",
    "mcp": "[list|status|tools]",
}


def _build_agent_safe_commands() -> list:
    return list_agent_safe_direct_commands()


def _build_command_enum() -> list[str]:
    return [command.name for command in _build_agent_safe_commands()]


def build_run_slash_command_description() -> str:
    commands = _build_agent_safe_commands()
    command_lines = [
        f"- {command.name}: {command.description}"
        for command in commands
    ]
    return (
        "Execute an agent-safe slash command for read-only inspection.\n"
        "Only direct commands that return text without UI or session side effects are exposed.\n\n"
        "Available commands:\n"
        + "\n".join(command_lines)
    )


def refresh_run_slash_command_metadata() -> None:
    tool = ToolRegistry.get("run_slash_command")
    if not tool:
        return

    tool.info.description = build_run_slash_command_description()
    for parameter in tool.info.parameters:
        if parameter.name == "command":
            parameter.enum = _build_command_enum()


def _normalize_arguments(command: str, arguments: str) -> tuple[bool, str]:
    args = (arguments or "").strip()
    if command == "help":
        return (not args, "")
    if command == "skills":
        return (args in {"", "list"}, args)
    if command == "agents":
        return (not args, "")
    if command == "workflows":
        return (not args, "")
    if command == "tools":
        if not args or args == "list":
            return (True, "")
        if args.startswith("info "):
            return (bool(args[len("info "):].strip()), args)
        return (False, args)
    if command == "mcp":
        return (args in {"", "list", "status", "tools"}, args)
    return (False, args)


def _usage_for_command(command: str) -> str:
    suffix = _READ_ONLY_ARGUMENT_HINTS.get(command, "")
    if suffix:
        return f"Usage: /{command} {suffix}"
    return f"Usage: /{command}"


@ToolRegistry.register_function(
    name="run_slash_command",
    description=build_run_slash_command_description(),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="command",
            type=ParameterType.STRING,
            description="The agent-safe slash command to run (without the / prefix)",
            required=True,
            enum=_build_command_enum(),
        ),
        ToolParameter(
            name="arguments",
            type=ParameterType.STRING,
            description="Optional command arguments for read-only subcommands, such as `info read_file` or `status`.",
            required=False,
            default="",
        ),
    ],
)
async def run_slash_command_tool(
    ctx: ToolContext,
    command: str,
    arguments: str = "",
) -> ToolResult:
    """Execute an agent-safe slash command."""
    del ctx
    refresh_run_slash_command_metadata()

    command = (command or "").strip().lstrip("/").lower()
    log.info("slash_command.run", {"command": command, "arguments": arguments})

    available_commands = {item.name: item for item in _build_agent_safe_commands()}
    if command not in available_commands:
        log.warn("slash_command.unknown", {"command": command})
        return ToolResult(success=False, error=f"Unknown agent-safe slash command: {command}")

    valid_args, normalized_args = _normalize_arguments(command, arguments)
    if not valid_args:
        return ToolResult(
            success=False,
            error=(
                f"Unsupported arguments for /{command}. "
                f"This tool only exposes read-only direct variants. {_usage_for_command(command)}"
            ),
        )

    if command == "help":
        return ToolResult(
            success=True,
            output=format_help(surface="webui"),
        )

    if command == "tools" and not normalized_args:
        return ToolResult(success=True, output=build_tools_catalog_summary())

    result = await run_direct_command(command, args=normalized_args)
    if not result.handled:
        return ToolResult(success=False, error=f"Unhandled slash command: {command}")
    if result.prompt is not None or result.clear_screen or result.clear_history:
        return ToolResult(
            success=False,
            error=f"Slash command /{command} is not agent-safe in this context.",
        )
    if not result.success:
        return ToolResult(success=False, error=result.text or f"Slash command /{command} failed.")
    return ToolResult(success=True, output=result.text or "")


refresh_run_slash_command_metadata()
