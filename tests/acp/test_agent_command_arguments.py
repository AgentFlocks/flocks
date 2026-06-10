from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.acp.agent import ACPAgent
from flocks.acp.types import ACPConfig


class _DummySessionManager:
    def __init__(self, state):
        self._state = state

    def get(self, session_id: str):
        assert session_id == self._state.id
        return self._state

    def set_model(self, session_id: str, model):
        assert session_id == self._state.id
        self._state.model = model


@pytest.mark.asyncio
async def test_prompt_command_parses_json_arguments_for_acp():
    session_state = SimpleNamespace(
        id="ses_acp_json",
        cwd="/tmp/project",
        model=None,
        mode_id="rex",
    )
    sdk = SimpleNamespace(
        session=SimpleNamespace(
            prompt=AsyncMock(),
            command=AsyncMock(),
        )
    )
    agent = ACPAgent(SimpleNamespace(), ACPConfig(sdk=sdk))
    agent._session_manager = _DummySessionManager(session_state)
    agent._get_default_model = AsyncMock(
        return_value={"providerID": "anthropic", "modelID": "claude-test"}
    )

    with patch(
        "flocks.agent.registry.Agent.default_agent",
        new=AsyncMock(return_value="rex"),
    ):
        result = await agent.prompt(
            {
                "sessionId": "ses_acp_json",
                "prompt": [{"type": "text", "text": '/bug {"scope":"acp","retry":1}'}],
            }
        )

    assert result == {"stopReason": "end_turn", "_meta": {}}
    sdk.session.prompt.assert_not_called()
    sdk.session.command.assert_awaited_once_with(
        session_id="ses_acp_json",
        command="bug",
        arguments='{"scope":"acp","retry":1}',
        arguments_json={"scope": "acp", "retry": 1},
        model="anthropic/claude-test",
        agent="rex",
        directory="/tmp/project",
    )


@pytest.mark.asyncio
async def test_prompt_command_keeps_legacy_string_arguments_when_not_json():
    session_state = SimpleNamespace(
        id="ses_acp_string",
        cwd="/tmp/project",
        model={"providerID": "anthropic", "modelID": "claude-test"},
        mode_id="rex",
    )
    sdk = SimpleNamespace(
        session=SimpleNamespace(
            prompt=AsyncMock(),
            command=AsyncMock(),
        )
    )
    agent = ACPAgent(SimpleNamespace(), ACPConfig(sdk=sdk))
    agent._session_manager = _DummySessionManager(session_state)

    with patch(
        "flocks.agent.registry.Agent.default_agent",
        new=AsyncMock(return_value="rex"),
    ):
        result = await agent.prompt(
            {
                "sessionId": "ses_acp_string",
                "prompt": [{"type": "text", "text": "/bug investigate routing"}],
            }
        )

    assert result == {"stopReason": "end_turn", "_meta": {}}
    sdk.session.prompt.assert_not_called()
    sdk.session.command.assert_awaited_once_with(
        session_id="ses_acp_string",
        command="bug",
        arguments="investigate routing",
        model="anthropic/claude-test",
        agent="rex",
        directory="/tmp/project",
        arguments_json=None,
    )


@pytest.mark.asyncio
async def test_prompt_command_falls_back_when_json_parse_fails():
    session_state = SimpleNamespace(
        id="ses_acp_invalid_json",
        cwd="/tmp/project",
        model={"providerID": "anthropic", "modelID": "claude-test"},
        mode_id="rex",
    )
    sdk = SimpleNamespace(
        session=SimpleNamespace(
            prompt=AsyncMock(),
            command=AsyncMock(),
        )
    )
    agent = ACPAgent(SimpleNamespace(), ACPConfig(sdk=sdk))
    agent._session_manager = _DummySessionManager(session_state)

    with patch(
        "flocks.agent.registry.Agent.default_agent",
        new=AsyncMock(return_value="rex"),
    ):
        result = await agent.prompt(
            {
                "sessionId": "ses_acp_invalid_json",
                "prompt": [{"type": "text", "text": '/bug {"scope":'}],
            }
        )

    assert result == {"stopReason": "end_turn", "_meta": {}}
    sdk.session.prompt.assert_not_called()
    sdk.session.command.assert_awaited_once_with(
        session_id="ses_acp_invalid_json",
        command="bug",
        arguments='{"scope":',
        model="anthropic/claude-test",
        agent="rex",
        directory="/tmp/project",
        arguments_json=None,
    )
