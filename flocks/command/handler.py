"""
Slash command handlers for core commands.

Provides shared, code-driven handling for /help, /tools, /skills.
"""

from typing import Awaitable, Callable, Optional

from flocks.command.command import API_SURFACES, Command, CommandSurface
from flocks.tool.registry import ToolRegistry
from flocks.skill.skill import Skill


SendText = Callable[[str], Awaitable[None]]
SendPrompt = Callable[[str], Awaitable[None]]
ClearScreen = Callable[[], Awaitable[None]]


async def handle_slash_command(
    content: str,
    *,
    send_text: SendText,
    send_prompt: SendPrompt,
    clear_screen: Optional[ClearScreen] = None,
    surface: Optional[CommandSurface] = None,
) -> bool:
    """
    Handle supported slash commands.

    Returns True if handled.
    """
    stripped = content.strip()
    if not stripped.startswith("/"):
        return False

    cmd_parts = stripped[1:].split(None, 1)
    if not cmd_parts:
        return False

    resolved = Command.resolve(cmd_parts[0].lower())
    if not resolved or resolved.execution_kind != "direct":
        return False

    name = resolved.name
    args = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""

    if name == "help":
        if surface:
            commands = Command.list(surface=surface)
        else:
            commands = Command.list_for_surfaces(API_SURFACES)
        lines = ["Available / commands:", ""]
        for command in commands:
            lines.append(f"- /{command.name}: {command.description}")
        lines.extend([
            "",
            "Tips:",
            "- /clear clears the screen",
            "- /tools [list|refresh|info <name>|create <requirement>]",
            "- /skills [list|refresh]",
            "- /workflows — list all available workflows",
            "- /mcp [list|status|tools|refresh <server>]",
        ])
        await send_text("\n".join(lines))
        return True

    if name == "clear":
        if clear_screen:
            await clear_screen()
        else:
            await send_text("Screen cleared.")
        return True

    if name == "tools":
        if not args or args == "list":
            ToolRegistry.init()
            tools = ToolRegistry.list_tools()
            lines = ["Available tools (summary):", ""]
            for i, tool in enumerate(tools, 1):
                desc = (tool.description or "").strip().splitlines()[0]
                lines.append(f"{i}. {tool.name}: {desc}")
            lines.append("")
            lines.append("Tip: use /tools info <name> for details")
            await send_text("\n".join(lines))
            return True

        if args == "refresh":
            ToolRegistry.refresh_dynamic_tools()
            ToolRegistry.init()
            tools = ToolRegistry.list_tools()
            lines = ["Dynamic tools refreshed. Current summary:", ""]
            for i, tool in enumerate(tools, 1):
                desc = (tool.description or "").strip().splitlines()[0]
                lines.append(f"{i}. {tool.name}: {desc}")
            lines.append("")
            lines.append("Tip: use /tools info <name> for details")
            await send_text("\n".join(lines))
            return True

        if args.startswith("info"):
            name = args[len("info"):].strip()
            if not name:
                await send_text("Usage: /tools info <name>")
                return True

            ToolRegistry.init()
            tool = ToolRegistry.get(name)
            if not tool:
                await send_text(f'Tool not found: "{name}"')
                return True

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
                    lines.append(f"- {param.name} ({param.type.value}, {required}){enum}: {param.description}")
            else:
                lines.append("Parameters: none")

            await send_text("\n".join(lines))
            return True

        if args.startswith("create"):
            requirement = args[len("create"):].strip()
            if not requirement:
                await send_text("Usage: /tools create <requirement>")
                return True

            skill = await Skill.get("tool-builder")
            if not skill:
                await send_text('Skill not found: "tool-builder". Check whether skills are loaded.')
                return True

            try:
                with open(skill.location, "r", encoding="utf-8") as f:
                    skill_content = f.read().strip()
            except Exception as e:
                await send_text(f"Failed to load skill: {str(e)}")
                return True

            create_prompt = "\n\n".join([
                "Please follow this skill exactly to create the tool:",
                skill_content,
                f"User requirement: {requirement}",
            ])
            await send_prompt(create_prompt)
            return True

        await send_text("Usage: /tools [list|refresh|info <name>|create <requirement>]")
        return True

    if name == "skills":
        if not args or args == "list":
            skills = await Skill.all()
            lines = ["Available skills:", ""]
            for i, skill in enumerate(skills, 1):
                lines.append(f"{i}. {skill.name}: {skill.description}")
            await send_text("\n".join(lines))
            return True

        if args == "refresh":
            skills = await Skill.refresh()
            lines = ["Skills refreshed. Current list:", ""]
            for i, skill in enumerate(skills, 1):
                lines.append(f"{i}. {skill.name}: {skill.description}")
            await send_text("\n".join(lines))
            return True

        await send_text("Usage: /skills [list|refresh]")
        return True

    if name == "workflows":
        from flocks.workflow.center import format_workflow_entries, scan_skill_workflows
        try:
            entries = await scan_skill_workflows()
        except Exception as e:
            await send_text(f"Error scanning workflows: {e}")
            return True

        if not entries:
            await send_text(
                "No workflows found.\n"
                "Create a workflow.json in .flocks/workflow/<name>/ to get started."
            )
            return True

        body = format_workflow_entries(entries)
        await send_text(
            "Available Workflows:\n\n"
            + body
            + "\n\nTip: use run_workflow tool with the path shown above to execute a workflow."
        )
        return True

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
                        lines.append(f"{status_icon} {server_name}: {info.status.value} ({info.tools_count} tools, {info.resources_count} resources)")
                    lines.append("")
                    lines.append("Tip: use /mcp tools to list all available MCP tools")
                await send_text("\n".join(lines))
                return True
            except Exception as e:
                await send_text(f"Error listing MCP servers: {str(e)}")
                return True
        
        if args == "status":
            try:
                status = await MCP.status()
                if not status:
                    await send_text("No MCP servers configured.")
                    return True
                
                lines = ["MCP Server Status:", ""]
                for server_name, info in status.items():
                    lines.append(f"Server: {server_name}")
                    lines.append(f"  Status: {info.status.value}")
                    lines.append(f"  Tools: {info.tools_count}")
                    lines.append(f"  Resources: {info.resources_count}")
                    if info.error:
                        lines.append(f"  Error: {info.error}")
                    lines.append("")
                await send_text("\n".join(lines))
                return True
            except Exception as e:
                await send_text(f"Error getting MCP status: {str(e)}")
                return True
        
        if args == "tools":
            try:
                from flocks.mcp import McpToolRegistry
                
                all_servers = McpToolRegistry.get_all_servers()
                if not all_servers:
                    await send_text("No MCP tools available. Connect to an MCP server first.")
                    return True
                
                lines = ["MCP Tools:", ""]
                for server_name in all_servers:
                    tools = McpToolRegistry.get_server_tools(server_name)
                    lines.append(f"From {server_name}: ({len(tools)} tools)")
                    for tool_name in tools:
                        source = McpToolRegistry.get_source(tool_name)
                        if source:
                            lines.append(f"  - {tool_name} (original: {source.mcp_tool})")
                    lines.append("")
                
                lines.append("Tip: use /tools info <name> to see tool details")
                await send_text("\n".join(lines))
                return True
            except Exception as e:
                await send_text(f"Error listing MCP tools: {str(e)}")
                return True
        
        if args.startswith("refresh"):
            server_name = args[len("refresh"):].strip()
            if not server_name:
                await send_text("Usage: /mcp refresh <server_name>")
                return True
            
            try:
                count = await MCP.refresh_tools(server_name)
                await send_text(f"Refreshed {count} tools from {server_name}")
                return True
            except Exception as e:
                await send_text(f"Error refreshing MCP tools: {str(e)}")
                return True
        
        await send_text("Usage: /mcp [list|status|tools|refresh <server>]")
        return True

    return False
