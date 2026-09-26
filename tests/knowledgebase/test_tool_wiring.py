from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.knowledgebase.errors import KnowledgebaseError
from flocks.tool.knowledgebase import rag_retrieve_tool


def _context(agent="helper"):
    return SimpleNamespace(agent=agent, session_id="session-1", ask=AsyncMock())


@pytest.mark.asyncio
async def test_tool_is_rejected_when_the_agent_does_not_list_it(monkeypatch):
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=SimpleNamespace(tools=["bash"], permission=None)),
    )
    result = await rag_retrieve_tool(_context(), "question")
    assert result.success is False
    assert result.metadata["code"] == "tool_not_allowed_for_agent"


@pytest.mark.asyncio
async def test_tool_retrieves_through_the_current_session(monkeypatch):
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=SimpleNamespace(tools=["rag_retrieve"], permission=None)),
    )
    monkeypatch.setattr("flocks.tool.knowledgebase.get_client", lambda: object())
    retrieve = AsyncMock(return_value={"chunks": [{"content": "hit"}], "total": 1})
    monkeypatch.setattr("flocks.tool.knowledgebase.retrieve_for_session", retrieve)
    result = await rag_retrieve_tool(_context(), "question", dataset=["dataset-a"])
    assert result.success is True
    assert result.output["chunks"][0]["content"] == "hit"
    retrieve.assert_awaited()
    assert retrieve.await_args.kwargs["dataset"] == ["dataset-a"]


def test_tool_is_registered():
    from flocks.tool.registry import ToolRegistry

    ToolRegistry.init()
    assert any(tool.name == "rag_retrieve" for tool in ToolRegistry.list_tools())
