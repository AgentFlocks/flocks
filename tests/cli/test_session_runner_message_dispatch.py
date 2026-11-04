"""Plain CLI text and dispatched prompts must reach the shared session loop."""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rich.console import Console

from flocks.cli.session_runner import CLISessionRunner
from flocks.session.message import Message, MessageRole


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_commands", [True, False])
async def test_plain_text_persists_and_reaches_session_loop(monkeypatch, tmp_path, dispatch_commands):
    from flocks.agent.registry import Agent
    from flocks.provider.provider import Provider
    from flocks.session.lifecycle.title import SessionTitle
    from flocks.session.session_loop import SessionLoop

    runner = CLISessionRunner(
        console=Console(file=StringIO()), directory=tmp_path,
        agent="rex", model="test-provider/test-model", auto_confirm=True,
    )
    runner._session = SimpleNamespace(id="cli-regression-session")
    monkeypatch.setattr(Agent, "get", AsyncMock(return_value=SimpleNamespace(name="rex", require_dedicated_session=False)))
    monkeypatch.setattr(Provider, "get", Mock(return_value=SimpleNamespace(is_configured=lambda: True)))
    create_message = AsyncMock()
    run_loop = AsyncMock(return_value=SimpleNamespace(action="stop", message="done"))
    monkeypatch.setattr(Message, "create", create_message)
    monkeypatch.setattr(SessionLoop, "run", run_loop)
    monkeypatch.setattr(SessionTitle, "generate_title_after_first_message", AsyncMock(return_value=None))

    await runner._process_message("plain text", dispatch_commands=dispatch_commands)

    create_message.assert_awaited_once_with(
        session_id="cli-regression-session", role=MessageRole.USER,
        content="plain text", agent="rex",
        model={"providerID": "test-provider", "modelID": "test-model"},
    )
    run_loop.assert_awaited_once()
    assert run_loop.await_args.kwargs["session_id"] == "cli-regression-session"
