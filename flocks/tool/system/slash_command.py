"""
Slash command dispatcher tool.

Handles /help, /tools, /skills and other meta-commands that let agents
inspect or control the Flocks runtime from within a conversation.
"""

from collections import defaultdict

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

_COMMANDS = ["tools", "skills", "workflows", "help", "tasks", "queue", "compact", "plan", "ask"]

_COMMAND_DESCRIPTIONS = {
    "tools":     "List all available tools grouped by category",
    "skills":    "List all available skills with descriptions",
    "workflows": "List all available workflows with descriptions and file paths",
    "help":      "Show available commands",
    "tasks":     "Show task center overview",
    "queue":     "Show task queue status",
    "compact":   "Summarize the conversation",
    "plan":      "Create a plan for a task",
    "ask":       "Ask a question without making changes",
}

_TOOL_DESCRIPTION = (
    "Execute a slash command to perform common operations.\n"
    "Use when the user wants to run a command like /tools, /skills, /help, etc.\n\n"
    "Available commands:\n"
    + "\n".join(f"- {cmd}: {desc}" for cmd, desc in _COMMAND_DESCRIPTIONS.items())
)

_HELP_TEXT = (
    "Available Slash Commands:\n"
    + "\n".join(f"- /{cmd}: {desc}" for cmd, desc in _COMMAND_DESCRIPTIONS.items())
)

_TOOL_CATEGORY_ORDER = ["file", "code", "search", "browser", "terminal", "system", "custom"]


def _truncate_text(text: str, max_chars: int) -> str:
    normalized = " ".join((text or "").split())
    if not normalized:
        return "No description provided."
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 3)].rstrip() + "..."


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


def format_tools_catalog_summary(
    tools: list,
    max_description_chars: int = 100,
    include_tip: bool = True,
) -> str:
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for tool in tools:
        if tool.name in {"invalid", "_noop"} or not getattr(tool, "enabled", True):
            continue
        category = getattr(tool.category, "value", str(tool.category))
        grouped[category].append((
            tool.name,
            _truncate_text(getattr(tool, "description", ""), max_description_chars),
        ))

    lines = ["Available Tools (grouped by category):", ""]
    seen: set[str] = set()
    for cat in _TOOL_CATEGORY_ORDER:
        if cat in grouped:
            lines.append(f"**{cat}**")
            for name, description in sorted(grouped[cat], key=lambda item: item[0]):
                lines.append(f"- {name}: {description}")
            lines.append("")
            seen.add(cat)

    for cat, items in grouped.items():
        if cat not in seen:
            lines.append(f"**{cat}**")
            for name, description in sorted(items, key=lambda item: item[0]):
                lines.append(f"- {name}: {description}")
            lines.append("")

    if include_tip:
        lines.append("Tip: use /tools info <name> for full details (UI only)")

    return "\n".join(lines).strip()


@ToolRegistry.register_function(
    name="run_slash_command",
    description=_TOOL_DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="command",
            type=ParameterType.STRING,
            description="The slash command to run (without the / prefix)",
            required=True,
            enum=_COMMANDS,
        ),
    ],
)
async def run_slash_command_tool(ctx: ToolContext, command: str) -> ToolResult:
    """Execute a slash command."""
    log.info("slash_command.run", {"command": command})

    if command == "help":
        return ToolResult(success=True, output=_HELP_TEXT)

    if command == "tools":
        return ToolResult(success=True, output=build_tools_catalog_summary())

    if command == "skills":
        from flocks.skill.skill import Skill
        skills = await Skill.all()
        if not skills:
            return ToolResult(success=True, output="No skills available.")
        lines = ["Available Skills:", ""]
        for i, skill in enumerate(skills, 1):
            lines.append(f"{i}. {skill.name}: {skill.description}")
        return ToolResult(success=True, output="\n".join(lines))

    if command == "workflows":
        from flocks.workflow.center import format_workflow_entries, scan_skill_workflows
        try:
            entries = await scan_skill_workflows()
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to scan workflows: {e}")
        if not entries:
            return ToolResult(success=True, output="No workflows found in .flocks/workflow/ directories.")
        body = format_workflow_entries(entries, markdown=True)
        output = (
            "Available Workflows:\n\n"
            + body
            + '\n\nUsage: run_workflow(workflow="<path>", inputs={...})'
        )
        return ToolResult(success=True, output=output)

    ui_only = {
        "tasks":   "Use /tasks in the UI to view task center",
        "queue":   "Use /queue in the UI to view task queue",
        "compact": "Use /compact in the UI to summarize conversation",
        "plan":    "Use /plan in the UI to enter plan mode",
        "ask":     "Use /ask in the UI for read-only analysis",
    }
    if command in ui_only:
        return ToolResult(success=True, output=ui_only[command])

    log.warn("slash_command.unknown", {"command": command})
    return ToolResult(success=False, error=f"Unknown command: {command}")
