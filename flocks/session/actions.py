"""Session actions that are independent of the agent execution loop."""

import asyncio
import os
from collections.abc import Mapping
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

    cwd = session.directory or os.getcwd()

    async def _effect(
        execution_command: str = command,
        execution_cwd: str = cwd,
    ) -> dict[str, Any]:
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
                execution_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=execution_cwd,
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
                    log.debug("session.shell.kill_failed", {"error": str(exc)})
        except Exception as exc:
            output = f"Error executing command: {exc}"
            exit_code = -1

        log.info(
            "session.shell",
            {
                "session_id": session_id,
                "command": execution_command[:50],
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
                        "input": {"command": execution_command},
                        "output": output,
                    },
                },
            ],
        }

    from flocks.session.tool_execution import (
        build_session_tool_execution_payload,
        run_tool_execution_lifecycle,
    )

    payload = await build_session_tool_execution_payload(
        session_id=session_id,
        message_id=Identifier.create("message"),
        agent=agent,
        tool_name="shell",
        tool_input={"command": command, "workdir": cwd},
        validated_input={"command": command, "workdir": cwd},
        tool_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "workdir": {"type": "string"},
            },
            "required": ["command"],
        },
        tool_context_extra={
            "tool_source": "session_actions",
            "tool_category": "command",
            "workspace_dir": cwd,
            "session_execution_profile": {
                "entry": "session.shell",
                "workspace_dir": cwd,
            },
        },
    )

    async def _patched_effect(patch: Mapping[str, Any]) -> dict[str, Any]:
        patched_command = patch.get("command", command)
        patched_cwd = patch.get("workdir", cwd)
        if not isinstance(patched_command, str) or not isinstance(patched_cwd, str):
            raise ValueError("Shell hook patch must contain string command and workdir")
        return await _effect(patched_command, patched_cwd)

    return await run_tool_execution_lifecycle(
        payload,
        _effect,
        patched_effect=_patched_effect,
    )
