"""
Slash command handlers for core commands.
"""

from typing import Awaitable, Callable, Optional

from flocks.command.command import Command, CommandSurface
from flocks.command.direct import run_direct_command
from flocks.input.events import ParsedCommand


SendText = Callable[[str], Awaitable[None]]
SendPrompt = Callable[[str], Awaitable[None]]
ClearScreen = Callable[[], Awaitable[None]]
ClearHistory = Callable[[], Awaitable[None]]


async def handle_slash_command(
    content: str,
    *,
    parsed_command: Optional[ParsedCommand] = None,
    send_text: SendText,
    send_prompt: SendPrompt,
    clear_screen: Optional[ClearScreen] = None,
    clear_history: Optional[ClearHistory] = None,
    surface: Optional[CommandSurface] = None,
    session_id: Optional[str] = None,
) -> bool:
    """
    Handle supported slash commands.

    Returns True if handled.
    """
    parsed = parsed_command
    if parsed is None:
        stripped = content.strip()
        if not stripped.startswith("/"):
            return False

        cmd_parts = stripped[1:].split(None, 1)
        if not cmd_parts:
            return False
        parsed = ParsedCommand(
            raw_text=stripped,
            command_name=cmd_parts[0].lower(),
            canonical_name=cmd_parts[0].lower(),
            args=cmd_parts[1].strip() if len(cmd_parts) > 1 else "",
        )

    resolved = Command.resolve(parsed.command_name)
    if not resolved or resolved.execution_kind != "direct":
        return False

    name = resolved.name
    result = await run_direct_command(
        name,
        args=parsed.args,
        args_json=parsed.args_json,
        surface=surface,
        session_id=session_id,
    )
    if not result.handled:
        return False

    if result.prompt is not None:
        if result.text:
            await send_text(result.text)
        await send_prompt(result.prompt)
        return True

    if result.clear_screen:
        if clear_screen:
            await clear_screen()
        else:
            await send_text(result.text or "Screen cleared.")
        return True

    if result.clear_history:
        if clear_history:
            await clear_history()
        else:
            await send_text(result.text or "Conversation history could not be cleared on this surface.")
        return True

    await send_text(result.text or "")
    return True
