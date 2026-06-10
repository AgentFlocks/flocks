from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from flocks.command.handler import handle_slash_command
from flocks.input.events import ParsedCommand


@pytest.mark.asyncio
async def test_handle_slash_command_forwards_structured_arguments():
    send_text = AsyncMock()
    send_prompt = AsyncMock()

    with patch("flocks.command.handler.run_direct_command", new=AsyncMock()) as run_mock:
        run_mock.return_value.handled = True
        run_mock.return_value.prompt = None
        run_mock.return_value.clear_screen = False
        run_mock.return_value.clear_history = False
        run_mock.return_value.text = "ok"

        handled = await handle_slash_command(
            '/agents {"team":"blue"}',
            parsed_command=ParsedCommand(
                raw_text='/agents {"team":"blue"}',
                command_name="agents",
                canonical_name="agents",
                args='{"team":"blue"}',
                args_json={"team": "blue"},
            ),
            send_text=send_text,
            send_prompt=send_prompt,
        )

    assert handled is True
    run_mock.assert_awaited_once_with(
        "agents",
        args='{"team":"blue"}',
        args_json={"team": "blue"},
        surface=None,
    )
    send_text.assert_awaited_once_with("ok")
    send_prompt.assert_not_called()
