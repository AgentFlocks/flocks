"""Session actions that are independent of the agent execution loop."""

import asyncio
import os
from typing import Any, Optional

from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.utils.id import Identifier
from flocks.utils.log import Log


log = Log.create(service="session.actions")


async def render_session_command(
    session_id: str,
    command: str,
    arguments: str = "",
) -> dict[str, Any]:
    """Resolve and render one slash-command template."""
    from flocks.command.command import Command

    command_info = Command.get(command)
    if not command_info:
        raise ValueError(f"Command '{command}' not found")
    template = command_info.template.replace("$ARGUMENTS", arguments)
    log.info(
        "session.command",
        {
            "session_id": session_id,
            "command": command,
            "arguments": arguments[:50] if arguments else "",
        },
    )
    return {
        "command": command,
        "arguments": arguments,
        "template": template,
    }


async def run_session_shell(
    session_id: str,
    agent: str,
    command: str,
) -> dict[str, Any]:
    """Execute one explicit user shell action and return its tool part."""
    session = await Session.get_by_id(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    user_message = await Message.create(
        session_id=session_id,
        role=MessageRole.USER,
        content="The following tool was executed by the user",
        agent=agent,
    )
    assistant_message = await Message.create(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content="",
        agent=agent,
        parent_id=user_message.id,
    )

    started_at = asyncio.get_event_loop().time()
    process: Optional[asyncio.subprocess.Process] = None
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=session.directory or os.getcwd(),
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=300,
        )
        output = (
            (stdout_bytes or b"").decode("utf-8", errors="replace")
            + (stderr_bytes or b"").decode("utf-8", errors="replace")
        )
        exit_code = process.returncode or 0
    except asyncio.TimeoutError:
        output = "Command timed out after 300 seconds"
        exit_code = -1
        if process is not None:
            try:
                process.kill()
            except Exception as exc:
                log.debug(
                    "session.shell.kill_failed",
                    {"error": str(exc)},
                )
    except Exception as exc:
        output = f"Error executing command: {exc}"
        exit_code = -1

    log.info(
        "session.shell",
        {
            "session_id": session_id,
            "command": command[:50],
            "exit_code": exit_code,
            "duration_ms": int(
                (asyncio.get_event_loop().time() - started_at) * 1000,
            ),
        },
    )
    return {
        "info": {
            "id": assistant_message.id,
            "sessionID": session_id,
            "role": "assistant",
            "agent": agent,
        },
        "parts": [
            {
                "id": Identifier.create("part"),
                "messageID": assistant_message.id,
                "sessionID": session_id,
                "type": "tool",
                "tool": "bash",
                "state": {
                    "status": "completed",
                    "input": {"command": command},
                    "output": output,
                },
            },
        ],
    }
