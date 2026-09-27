import asyncio

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.knowledgebase import runtime
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
    runtime.publish(SimpleNamespace(close=AsyncMock()), runtime.publication_epoch())
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
    assert any(tool.name == "rag_retrieve" and tool.group == "检索" for tool in ToolRegistry.list_tools())


@pytest.mark.parametrize("outcome", ["success", "cancel", "denied"])
async def test_tool_lease_covers_permission_wait_and_retrieval(monkeypatch, outcome):
    entered, finish = asyncio.Event(), asyncio.Event()
    old = SimpleNamespace(close=AsyncMock())
    new = SimpleNamespace(close=AsyncMock())
    runtime.publish(old, runtime.publication_epoch())
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=SimpleNamespace(tools=["rag_retrieve"], permission=None)),
    )
    retrieve = AsyncMock(return_value={"chunks": [], "total": 0})
    monkeypatch.setattr("flocks.tool.knowledgebase.retrieve_for_session", retrieve)

    async def ask(**kwargs):
        entered.set()
        await finish.wait()
        if outcome == "denied":
            raise KnowledgebaseError(403, "permission_denied", "Denied.")

    ctx = _context()
    ctx.ask = ask
    pending = asyncio.create_task(rag_retrieve_tool(ctx, "question"))
    await asyncio.wait_for(entered.wait(), 1)
    runtime.publish(new, runtime.publication_epoch())
    old.close.assert_not_awaited()
    if outcome == "cancel":
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        finish.set()
        result = await pending
        assert result.success is (outcome == "success")
    if outcome == "success":
        assert retrieve.await_args.kwargs["client"] is old
    else:
        retrieve.assert_not_awaited()
    old.close.assert_awaited_once()
    assert runtime.get_client() is new
    new.close.assert_not_awaited()
