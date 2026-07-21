"""
Shared execution logic for direct slash commands.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from flocks.agent.agent import AvailableAgent
from flocks.agent.registry import Agent
from flocks.command.command import Command, CommandInfo, CommandSurface
from flocks.command.help import format_help
from flocks.session.goal import GoalManager
from flocks.skill.skill import Skill
from flocks.tool.registry import ToolRegistry

AGENT_SAFE_DIRECT_COMMANDS = frozenset({"help", "tools", "skills", "agents", "workflows", "mcp"})
_TOOL_CATEGORY_ORDER = ["file", "code", "search", "browser", "terminal", "system", "api", "mcp", "device", "plugin_py", "plugin_yaml", "custom"]
_SOURCE_AWARE_GROUPS = frozenset({"api", "mcp", "device", "plugin_py", "plugin_yaml"})


@dataclass
class DirectCommandResult:
    handled: bool
    success: bool = True
    text: Optional[str] = None
    prompt: Optional[str] = None
    clear_screen: bool = False
    clear_history: bool = False


def is_agent_safe_direct_command(command: CommandInfo) -> bool:
    return (
        command.execution_kind == "direct"
        and command.name in AGENT_SAFE_DIRECT_COMMANDS
    )


def list_agent_safe_direct_commands() -> list[CommandInfo]:
    return [cmd for cmd in Command.list_all() if is_agent_safe_direct_command(cmd)]


def _truncate_text(text: str, max_chars: int) -> str:
    normalized = " ".join((text or "").split())
    if not normalized:
        return "No description provided."
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 3)].rstrip() + "..."


def _tool_catalog_group_key(tool: object) -> str:
    source = str(getattr(tool, "source", "") or "").strip().lower()
    if source in _SOURCE_AWARE_GROUPS:
        return source
    category = getattr(getattr(tool, "category", None), "value", getattr(tool, "category", "custom"))
    return str(category)


def format_tools_catalog_summary(
    tools: list,
    max_description_chars: int = 100,
    include_tip: bool = True,
) -> str:
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for tool in tools:
        if tool.name in {"invalid", "_noop"} or not getattr(tool, "enabled", True):
            continue
        group_key = _tool_catalog_group_key(tool)
        grouped[group_key].append((
            tool.name,
            _truncate_text(getattr(tool, "description", ""), max_description_chars),
        ))

    lines = ["Available Tools (grouped by category/source):", ""]
    seen: set[str] = set()
    for category in _TOOL_CATEGORY_ORDER:
        if category not in grouped:
            continue
        lines.append(f"**{category}**")
        for name, description in sorted(grouped[category], key=lambda item: item[0]):
            lines.append(f"- {name}: {description}")
        lines.append("")
        seen.add(category)

    for category, items in grouped.items():
        if category in seen:
            continue
        lines.append(f"**{category}**")
        for name, description in sorted(items, key=lambda item: item[0]):
            lines.append(f"- {name}: {description}")
        lines.append("")

    if include_tip:
        lines.append("Tip: use /tools info `name` for full details.")

    return "\n".join(lines).strip()


def build_tools_catalog_summary(
    max_description_chars: int = 100,
    include_tip: bool = True,
) -> str:
    ToolRegistry.init()
    tools = ToolRegistry.list_tools()
    return format_tools_catalog_summary(
        tools=tools,
        max_description_chars=max_description_chars,
        include_tip=include_tip,
    )


def format_available_agents_summary(agents: list[AvailableAgent]) -> str:
    if not agents:
        return "No available agents."

    lines = ["Available agents:", ""]
    for agent in agents:
        metadata_bits = []
        if agent.metadata.category:
            metadata_bits.append(agent.metadata.category)
        if agent.metadata.cost:
            metadata_bits.append(agent.metadata.cost)
        metadata_suffix = f" ({', '.join(metadata_bits)})" if metadata_bits else ""
        description = agent.description.strip() or "No description provided."
        lines.append(f"- `{agent.name}`{metadata_suffix}: {description}")
    return "\n".join(lines)


async def run_direct_command(
    name: str,
    *,
    args: str = "",
    args_json: Optional[Any] = None,
    surface: Optional[CommandSurface] = None,
    session_id: Optional[str] = None,
) -> DirectCommandResult:
    """Execute a direct command and return its result."""
    resolved = Command.resolve(name)
    if not resolved or resolved.execution_kind != "direct":
        return DirectCommandResult(handled=False)

    name = resolved.name
    args = (args or "").strip()
    _ = args_json

    if name == "help":
        return DirectCommandResult(handled=True, text=format_help(surface=surface))

    if name == "clear":
        return DirectCommandResult(handled=True, clear_history=True)

    if name == "goal":
        if not session_id:
            return DirectCommandResult(
                handled=True,
                success=False,
                text="Usage: /goal requires an active session.",
            )

        try:
            state = await GoalManager.set_goal(session_id, args)
        except ValueError:
            return DirectCommandResult(
                handled=True,
                success=False,
                text="Usage: /goal <objective>",
            )
        return DirectCommandResult(
            handled=True,
            prompt=GoalManager.goal_prompt(state.objective),
        )

    if name == "tools":
        if not args or args == "list":
            return DirectCommandResult(handled=True, text=build_tools_catalog_summary())

        if args == "refresh":
            ToolRegistry.refresh_dynamic_tools()
            summary = build_tools_catalog_summary()
            text = "Dynamic tools refreshed. Current summary:\n\n" + summary.split("\n", 1)[1]
            return DirectCommandResult(handled=True, text=text)

        if args.startswith("info"):
            tool_name = args[len("info"):].strip()
            if not tool_name:
                return DirectCommandResult(handled=True, text="Usage: /tools info `name`")

            ToolRegistry.init()
            tool = ToolRegistry.get(tool_name)
            if not tool:
                return DirectCommandResult(handled=True, text=f'Tool not found: "{tool_name}"')

            info = tool.info
            lines = [
                f"Tool details: {info.name}",
                "",
                f"- Category: {info.category.value}",
                f"- Description: {info.description.strip()}",
                f"- Enabled: {info.enabled}",
                f"- Requires confirmation: {info.requires_confirmation}",
                "",
            ]
            if info.parameters:
                lines.append("Parameters:")
                for param in info.parameters:
                    required = "required" if param.required else "optional"
                    enum = f" Values: {param.enum}" if param.enum else ""
                    lines.append(
                        f"- {param.name} ({param.type.value}, {required}){enum}: {param.description}"
                    )
            else:
                lines.append("Parameters: none")

            return DirectCommandResult(handled=True, text="\n".join(lines))

        if args.startswith("create"):
            requirement = args[len("create"):].strip()
            if not requirement:
                return DirectCommandResult(
                    handled=True,
                    text="Usage: /tools create `requirement`",
                )

            skill = await Skill.get("tool-builder")
            if not skill:
                return DirectCommandResult(
                    handled=True,
                    text='Skill not found: "tool-builder". Check whether skills are loaded.',
                )

            try:
                with open(skill.location, "r", encoding="utf-8") as file:
                    skill_content = file.read().strip()
            except Exception as exc:
                return DirectCommandResult(handled=True, text=f"Failed to load skill: {str(exc)}")

            create_prompt = "\n\n".join([
                "Please follow this skill exactly to create the tool:",
                skill_content,
                f"User requirement: {requirement}",
            ])
            return DirectCommandResult(handled=True, prompt=create_prompt)

        return DirectCommandResult(
            handled=True,
            text="Usage: /tools [list|refresh|info `name`|create `requirement`]",
        )

    if name == "skills":
        # `surface` distinguishes user-driven calls (CLI/TUI/WebUI input
        # dispatcher passes its sink surface) from agent-driven calls (the
        # `slash_command` tool calls `run_direct_command` without a surface).
        # We want the agent path to be strictly "enabled-only" so a toggled
        # off skill cannot be rediscovered through this listing, while keeping
        # the user path showing the full inventory with a `[disabled]` marker
        # so users can tell at a glance what they have turned off.
        is_user_surface = surface is not None
        if not args or args == "list":
            if is_user_surface:
                skills = await Skill.all()
                disabled = Skill.load_disabled()
            else:
                skills = await Skill.list_enabled()
                disabled = set()
            if not skills:
                return DirectCommandResult(handled=True, text="No skills available.")
            lines = ["Available skills:", ""]
            for index, skill in enumerate(skills, 1):
                suffix = " [disabled]" if skill.name in disabled else ""
                lines.append(f"{index}. {skill.name}{suffix}: {skill.description}")
            return DirectCommandResult(handled=True, text="\n".join(lines))

        if args == "refresh":
            # `Skill.refresh()` repopulates the discovery cache; the user
            # path then re-lists everything with the `[disabled]` marker so
            # the refresh output matches what the user actually sees.
            await Skill.refresh()
            if is_user_surface:
                skills = await Skill.all()
                disabled = Skill.load_disabled()
            else:
                skills = await Skill.list_enabled()
                disabled = set()
            lines = ["Skills refreshed. Current list:", ""]
            for index, skill in enumerate(skills, 1):
                suffix = " [disabled]" if skill.name in disabled else ""
                lines.append(f"{index}. {skill.name}{suffix}: {skill.description}")
            return DirectCommandResult(handled=True, text="\n".join(lines))

        return DirectCommandResult(handled=True, text="Usage: /skills [list|refresh]")

    if name == "agents":
        if args:
            return DirectCommandResult(handled=True, text="Usage: /agents")
        agents = await Agent.list_available_agents()
        return DirectCommandResult(
            handled=True,
            text=format_available_agents_summary(agents),
        )

    if name == "workflows":
        from flocks.workflow.center import format_workflow_entries, scan_skill_workflows

        try:
            entries = await scan_skill_workflows()
        except Exception as exc:
            return DirectCommandResult(
                handled=True,
                success=False,
                text=f"Error scanning workflows: {exc}",
            )

        if not entries:
            return DirectCommandResult(
                handled=True,
                text=(
                    "No workflows found.\n"
                    "Create a workflow.json in .flocks/workflow/<name>/ to get started."
                ),
            )

        body = format_workflow_entries(entries)
        return DirectCommandResult(
            handled=True,
            text=(
                "Available Workflows:\n\n"
                + body
                + "\n\nTip: use run_workflow with the path shown above to execute a workflow."
            ),
        )

    if name == "mcp":
        from flocks.mcp import MCP, McpStatus

        if not args or args == "list":
            try:
                status = await MCP.status()
                if not status:
                    lines = [
                        "No MCP servers configured.",
                        "",
                        "To add servers, configure them in ~/.flocks/config/flocks.json under the 'mcp' key.",
                        "Example:",
                        '  "mcp": {',
                        '    "my_server": {',
                        '      "type": "remote",',
                        '      "url": "https://example.com/mcp",',
                        '      "enabled": true',
                        '    }',
                        '  }',
                    ]
                else:
                    lines = ["MCP Servers:", ""]
                    for server_name, info in status.items():
                        status_icon = "✓" if info.status == McpStatus.CONNECTED else "✗"
                        lines.append(
                            f"{status_icon} {server_name}: {info.status.value} "
                            f"({info.tools_count} tools, {info.resources_count} resources)"
                        )
                    lines.append("")
                    lines.append("Tip: use /mcp tools to list all available MCP tools")
                return DirectCommandResult(handled=True, text="\n".join(lines))
            except Exception as exc:
                return DirectCommandResult(
                    handled=True,
                    success=False,
                    text=f"Error listing MCP servers: {str(exc)}",
                )

        if args == "status":
            try:
                status = await MCP.status()
                if not status:
                    return DirectCommandResult(handled=True, text="No MCP servers configured.")

                lines = ["MCP Server Status:", ""]
                for server_name, info in status.items():
                    lines.append(f"Server: {server_name}")
                    lines.append(f"  Status: {info.status.value}")
                    lines.append(f"  Tools: {info.tools_count}")
                    lines.append(f"  Resources: {info.resources_count}")
                    if info.error:
                        lines.append(f"  Error: {info.error}")
                    lines.append("")
                return DirectCommandResult(handled=True, text="\n".join(lines))
            except Exception as exc:
                return DirectCommandResult(
                    handled=True,
                    success=False,
                    text=f"Error getting MCP status: {str(exc)}",
                )

        if args == "tools":
            try:
                from flocks.mcp import McpToolRegistry

                all_servers = McpToolRegistry.get_all_servers()
                if not all_servers:
                    return DirectCommandResult(
                        handled=True,
                        text="No MCP tools available. Connect to an MCP server first.",
                    )

                lines = ["MCP Tools:", ""]
                for server_name in all_servers:
                    tools = McpToolRegistry.get_server_tools(server_name)
                    lines.append(f"From {server_name}: ({len(tools)} tools)")
                    for tool_name in tools:
                        source = McpToolRegistry.get_source(tool_name)
                        if source:
                            lines.append(f"  - {tool_name} (original: {source.mcp_tool})")
                    lines.append("")

                lines.append("Tip: use /tools info `name` to see tool details")
                return DirectCommandResult(handled=True, text="\n".join(lines))
            except Exception as exc:
                return DirectCommandResult(
                    handled=True,
                    success=False,
                    text=f"Error listing MCP tools: {str(exc)}",
                )

        if args.startswith("refresh"):
            server_name = args[len("refresh"):].strip()
            if not server_name:
                return DirectCommandResult(handled=True, text="Usage: /mcp refresh `server`")

            try:
                count = await MCP.refresh_tools(server_name)
                return DirectCommandResult(
                    handled=True,
                    text=f"Refreshed {count} tools from {server_name}",
                )
            except Exception as exc:
                return DirectCommandResult(
                    handled=True,
                    success=False,
                    text=f"Error refreshing MCP tools: {str(exc)}",
                )

        return DirectCommandResult(
            handled=True,
            text="Usage: /mcp [list|status|tools|refresh `server`]",
        )

    return DirectCommandResult(handled=False)
