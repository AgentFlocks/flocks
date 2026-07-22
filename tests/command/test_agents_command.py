from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from flocks.agent.agent import AgentPromptMetadata, AvailableAgent
from flocks.command.command import Command
from flocks.command.handler import handle_slash_command


def _make_available_agent(
    name: str,
    description: str,
    *,
    category: str = "exploration",
    cost: str = "CHEAP",
) -> AvailableAgent:
    return AvailableAgent(
        name=name,
        description=description,
        metadata=AgentPromptMetadata(category=category, cost=cost),
    )


async def _collect_text(content: str) -> tuple[list[str], bool]:
    texts: list[str] = []

    async def send_text(text: str) -> None:
        texts.append(text)

    async def send_prompt(_text: str) -> None:
        raise AssertionError("send_prompt should not be called for /agents")

    handled = await handle_slash_command(content, send_text=send_text, send_prompt=send_prompt)
    return texts, handled


class TestAgentsCommandRegistration:
    def setup_method(self):
        Command._commands = {}

    def test_agents_command_registered(self):
        cmd = Command.get("agents")
        assert cmd is not None
        assert cmd.name == "agents"
        assert cmd.execution_kind == "direct"


class TestAgentsCommandHandler:
    _LIST_AVAILABLE = "flocks.agent.registry.Agent.list_available_agents"

    @pytest.mark.asyncio
    async def test_agents_command_lists_available_agents(self):
        agents = [
            _make_available_agent("explore", "Explore the codebase."),
            _make_available_agent("oracle", "Answer deep questions.", category="advisor", cost="EXPENSIVE"),
        ]
        with patch(self._LIST_AVAILABLE, new_callable=AsyncMock, return_value=agents):
            texts, handled = await _collect_text("/agents")

        assert handled
        output = "\n".join(texts)
        assert "Available agents:" in output
        assert "`explore`" in output
        assert "`oracle`" in output
        assert "advisor" in output
        assert "EXPENSIVE" in output

    @pytest.mark.asyncio
    async def test_agents_command_handles_empty_list(self):
        with patch(self._LIST_AVAILABLE, new_callable=AsyncMock, return_value=[]):
            texts, handled = await _collect_text("/agents")

        assert handled
        assert texts == ["No available agents."]

    @pytest.mark.asyncio
    async def test_agents_command_rejects_arguments(self):
        texts, handled = await _collect_text("/agents extra")

        assert handled
        assert texts == ["Usage: /agents"]
