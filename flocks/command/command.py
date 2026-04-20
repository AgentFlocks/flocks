"""
Command definitions and management.

Ported from original command/index.ts Command namespace.
Handles slash command registration and execution.
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field


@dataclass
class CommandInfo:
    """Command definition"""
    name: str
    description: str
    template: str
    agent: Optional[str] = None
    model: Optional[str] = None
    subtask: Optional[bool] = None
    hidden: bool = False


class Command:
    """
    Command namespace.
    
    Manages slash commands like /init, /help, /model, etc.
    Ported from original Command namespace.
    """
    
    # Default commands
    _commands: Dict[str, CommandInfo] = {}
    
    # Default command names
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
        if cls._commands:
            return
        
        # Register built-in commands
        cls.register(CommandInfo(
            name="init",
            description="Analyze and create AGENTS.md for the project",
            template="Analyze this codebase and create an AGENTS.md file with project-specific configurations. $ARGUMENTS",
            agent="rex",
        ))
        
        cls.register(CommandInfo(
            name="help",
            description="Show available commands",
            template="List all available slash commands and their descriptions.",
            agent="rex",
        ))

        cls.register(CommandInfo(
            name="tools",
            description="List available tools",
            template="List all available tools with their names, categories, and descriptions.",
            agent="rex",
        ))

        cls.register(CommandInfo(
            name="skills",
            description="List available skills",
            template="List all available skills with their names and descriptions.",
            agent="rex",
        ))

        cls.register(CommandInfo(
            name="workflows",
            description="List available workflows",
            template="List all available workflows with their names, descriptions, and file paths.",
            agent="rex",
        ))
        
        cls.register(CommandInfo(
            name="model",
            description="Change the current model",
            template="Switch to model: $1",
            hidden=True,
        ))
        
        cls.register(CommandInfo(
            name="compact",
            description="Summarize the conversation (optionally /compact <focus>)",
            template="Summarize this conversation while preserving key context and decisions.",
            agent="rex",
        ))
        
        cls.register(CommandInfo(
            name="clear",
            description="Clear screen output",
            template="Clear the conversation history and start fresh.",
            hidden=False,
        ))

        cls.register(CommandInfo(
            name="restart",
            description="Restart agent session",
            template="Restart the current session and clear history.",
            agent="rex",
        ))
        
        cls.register(CommandInfo(
            name="bug",
            description="Report a bug or issue",
            template="I found a bug: $ARGUMENTS",
            agent="rex",
        ))
        
        cls.register(CommandInfo(
            name="plan",
            description="Create a plan for a task",
            template="Create a detailed plan for: $ARGUMENTS",
            agent="plan",
        ))
        
        cls.register(CommandInfo(
            name="ask",
            description="Ask a question without making changes",
            template="$ARGUMENTS",
            agent="ask",
        ))

        cls.register(CommandInfo(
            name="tasks",
            description="Show task center overview",
            template="Use the task_list tool to show the current task center overview including running, queued, and recently completed tasks. Present the results clearly.",
        ))

        cls.register(CommandInfo(
            name="queue",
            description="Show task queue status",
            template="Use the task_list tool with status filter to show the current task queue status: running tasks, queued tasks, and queue configuration. Present the results clearly.",
        ))

        # Load external commands (Flocks/Claude-compatible)
        try:
            from flocks.command.command_loader import discover_commands
            discovered = discover_commands()
            for cmd in discovered.values():
                cls.register(cmd)
        except Exception:
            # Avoid failing command registry if external discovery fails
            pass
    
    @classmethod
    def register(cls, command: CommandInfo) -> None:
        """
        Register a command.
        
        Args:
            command: Command to register
        """
        cls._commands[command.name] = command
    
    @classmethod
    def get(cls, name: str) -> Optional[CommandInfo]:
        """
        Get a command by name.
        
        Args:
            name: Command name
            
        Returns:
            Command info or None
        """
        cls._ensure_defaults()
        return cls._commands.get(name)
    
    @classmethod
    def list(cls) -> List[CommandInfo]:
        """
        List all registered commands.
        
        Returns:
            List of commands
        """
        cls._ensure_defaults()
        return [c for c in cls._commands.values() if not c.hidden]
    
    @classmethod
    def list_all(cls) -> List[CommandInfo]:
        """
        List all commands including hidden ones.
        
        Returns:
            List of all commands
        """
        cls._ensure_defaults()
        return list(cls._commands.values())
