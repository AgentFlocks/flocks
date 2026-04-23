"""
Command definitions and management.

Ported from original command/index.ts Command namespace.
Handles slash command registration and execution.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Literal, Optional, Tuple

CommandSurface = Literal["webui", "tui", "channel", "cli", "acp"]
CommandExecutionKind = Literal["direct", "llm", "session_control"]

API_SURFACES: Tuple[CommandSurface, ...] = ("webui", "tui", "acp")
ALL_SURFACES: Tuple[CommandSurface, ...] = ("webui", "tui", "channel", "cli", "acp")


@dataclass
class CommandDef:
    """Structured slash command definition."""

    name: str
    description: str
    template: str
    agent: Optional[str] = None
    model: Optional[str] = None
    subtask: Optional[bool] = None
    hidden: bool = False
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    visible_surfaces: Tuple[CommandSurface, ...] = ("webui", "tui", "acp", "cli")
    execution_kind: CommandExecutionKind = "llm"
    allow_attachments: bool = True
    requires_existing_session: bool = True
    channel_safe: bool = False

    @property
    def canonical_name(self) -> str:
        return self.name

    def is_visible_on(self, surface: CommandSurface) -> bool:
        return surface in self.visible_surfaces


# Backwards-compatible alias used across the codebase.
CommandInfo = CommandDef


class CommandRegistry:
    """Single source of truth for slash command metadata."""

    def __init__(self) -> None:
        self._commands: Dict[str, CommandDef] = {}
        self._lookup: Dict[str, CommandDef] = {}

    def register(self, command: CommandDef) -> None:
        canonical = command.name.lower()
        self._commands[canonical] = command
        self._rebuild_lookup()

    def get(self, name: str) -> Optional[CommandDef]:
        return self.resolve(name)

    def resolve(self, name: Optional[str]) -> Optional[CommandDef]:
        if not name:
            return None
        return self._lookup.get(name.lower().lstrip("/"))

    def list(
        self,
        *,
        surface: Optional[CommandSurface] = None,
        include_hidden: bool = False,
    ) -> List[CommandDef]:
        commands = list(self._commands.values())
        if surface is not None:
            commands = [cmd for cmd in commands if cmd.is_visible_on(surface)]
        if not include_hidden:
            commands = [cmd for cmd in commands if not cmd.hidden]
        return commands

    def list_for_surfaces(
        self,
        surfaces: Iterable[CommandSurface],
        *,
        include_hidden: bool = False,
    ) -> List[CommandDef]:
        surface_set = {surface for surface in surfaces}
        commands = [
            cmd for cmd in self._commands.values()
            if set(cmd.visible_surfaces) & surface_set
        ]
        if not include_hidden:
            commands = [cmd for cmd in commands if not cmd.hidden]
        return commands

    def list_all(self) -> List[CommandDef]:
        return list(self._commands.values())

    def _rebuild_lookup(self) -> None:
        lookup: Dict[str, CommandDef] = {}
        for command in self._commands.values():
            lookup[command.name.lower()] = command
            for alias in command.aliases:
                lookup[alias.lower().lstrip("/")] = command
        self._lookup = lookup


class Command:
    """
    Command namespace.

    Manages slash commands like /init, /help, /model, etc.
    Ported from original Command namespace.
    """

    _registry = CommandRegistry()
    _commands: Dict[str, CommandInfo] = {}
    _defaults_loaded = False

    class Default:
        """Default command names"""

        INIT = "init"
        HELP = "help"
        MODEL = "model"
        COMPACT = "compact"
        CLEAR = "clear"
        BUG = "bug"

    @classmethod
    def _ensure_defaults(cls) -> None:
        """Ensure default commands are registered"""
        if cls._registry._commands is not cls._commands:
            cls._registry._commands = cls._commands
            cls._registry._rebuild_lookup()
        if not cls._commands:
            cls._defaults_loaded = False
        if cls._defaults_loaded:
            return

        builtins = [
            CommandDef(
                name="init",
                description="Analyze and create AGENTS.md for the project",
                template="Analyze this codebase and create an AGENTS.md file with project-specific configurations. $ARGUMENTS",
                agent="rex",
                execution_kind="llm",
                allow_attachments=True,
            ),
            CommandDef(
                name="help",
                description="Show available commands",
                template="List all available slash commands and their descriptions.",
                agent="rex",
                execution_kind="direct",
                allow_attachments=False,
                visible_surfaces=ALL_SURFACES,
                channel_safe=True,
            ),
            CommandDef(
                name="tools",
                description="List available tools",
                template="List all available tools with their names, categories, and descriptions.",
                agent="rex",
                execution_kind="direct",
                allow_attachments=False,
                visible_surfaces=("webui", "tui", "acp", "cli"),
            ),
            CommandDef(
                name="skills",
                description="List available skills",
                template="List all available skills with their names and descriptions.",
                agent="rex",
                execution_kind="direct",
                allow_attachments=False,
                visible_surfaces=("webui", "tui", "acp", "cli"),
            ),
            CommandDef(
                name="workflows",
                description="List available workflows",
                template="List all available workflows with their names, descriptions, and file paths.",
                agent="rex",
                execution_kind="direct",
                allow_attachments=False,
                visible_surfaces=("webui", "tui", "acp", "cli"),
            ),
            CommandDef(
                name="mcp",
                description="Inspect or refresh MCP servers",
                template="Inspect MCP servers and tools.",
                agent="rex",
                execution_kind="direct",
                allow_attachments=False,
                visible_surfaces=("webui", "tui", "acp", "cli"),
            ),
            CommandDef(
                name="model",
                description="Change or inspect the current model",
                template="Switch to model: $1",
                execution_kind="session_control",
                allow_attachments=False,
                hidden=False,
                visible_surfaces=("channel",),
                channel_safe=True,
            ),
            CommandDef(
                name="status",
                description="Show current session status",
                template="Show the current session status.",
                execution_kind="session_control",
                allow_attachments=False,
                hidden=False,
                visible_surfaces=("channel",),
                channel_safe=True,
            ),
            CommandDef(
                name="new",
                description="Start a fresh conversation session",
                template="Start a fresh conversation.",
                execution_kind="session_control",
                allow_attachments=False,
                hidden=False,
                aliases=("reset",),
                visible_surfaces=("channel",),
                channel_safe=True,
            ),
            CommandDef(
                name="compact",
                description="Summarize the conversation (optionally /compact <focus>)",
                template="Summarize this conversation while preserving key context and decisions.",
                agent="rex",
                execution_kind="session_control",
                allow_attachments=False,
                visible_surfaces=("webui", "tui", "acp"),
            ),
            CommandDef(
                name="clear",
                description="Clear screen output",
                template="Clear the current UI output only.",
                execution_kind="direct",
                allow_attachments=False,
            ),
            CommandDef(
                name="bug",
                description="Report a bug or issue",
                template="I found a bug: $ARGUMENTS",
                agent="rex",
                execution_kind="llm",
                allow_attachments=True,
            ),
            CommandDef(
                name="plan",
                description="Create a plan for a task",
                template="Create a detailed plan for: $ARGUMENTS",
                agent="plan",
                execution_kind="llm",
                allow_attachments=True,
            ),
            CommandDef(
                name="ask",
                description="Ask a question without making changes",
                template="$ARGUMENTS",
                agent="ask",
                execution_kind="llm",
                allow_attachments=True,
            ),
            CommandDef(
                name="tasks",
                description="Show task center overview",
                template="Use the task_list tool to show the current task center overview including running, queued, and recently completed tasks. Present the results clearly.",
                execution_kind="llm",
                allow_attachments=True,
            ),
            CommandDef(
                name="queue",
                description="Show task queue status",
                template="Use the task_list tool with status filter to show the current task queue status: running tasks, queued tasks, and queue configuration. Present the results clearly.",
                execution_kind="llm",
                allow_attachments=True,
            ),
        ]

        for command in builtins:
            cls.register(command)

        try:
            from flocks.command.command_loader import discover_commands

            discovered = discover_commands()
            for cmd in discovered.values():
                cls.register(cmd)
        except Exception:
            pass

        cls._defaults_loaded = True

    @classmethod
    def register(cls, command: CommandInfo) -> None:
        """Register a command."""
        cls._registry.register(command)
        cls._commands = cls._registry._commands

    @classmethod
    def resolve(cls, name: Optional[str]) -> Optional[CommandInfo]:
        cls._ensure_defaults()
        return cls._registry.resolve(name)

    @classmethod
    def get(cls, name: str) -> Optional[CommandInfo]:
        cls._ensure_defaults()
        return cls._registry.get(name)

    @classmethod
    def list(
        cls,
        *,
        surface: Optional[CommandSurface] = None,
        include_hidden: bool = False,
    ) -> List[CommandInfo]:
        cls._ensure_defaults()
        return cls._registry.list(surface=surface, include_hidden=include_hidden)

    @classmethod
    def list_for_surfaces(
        cls,
        surfaces: Iterable[CommandSurface],
        *,
        include_hidden: bool = False,
    ) -> List[CommandInfo]:
        cls._ensure_defaults()
        return cls._registry.list_for_surfaces(surfaces, include_hidden=include_hidden)

    @classmethod
    def list_all(cls) -> List[CommandInfo]:
        cls._ensure_defaults()
        return cls._registry.list_all()
