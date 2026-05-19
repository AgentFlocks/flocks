"""
Slash command handlers for core commands.
"""

from typing import Awaitable, Callable, Optional

from flocks.command.command import Command, CommandSurface
from flocks.command.direct import run_direct_command


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
    result = await run_direct_command(name, args=args, surface=surface)
    if not result.handled:
        return False

    if result.prompt is not None:
        await send_prompt(result.prompt)
        return True

    if result.clear_screen:
        if clear_screen:
            await clear_screen()
        else:
            await send_text(result.text or "Screen cleared.")
        return True

    await send_text(result.text or "")
    return True
