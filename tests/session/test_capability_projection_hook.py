from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.session.callable_schema import list_session_callable_tool_infos
from flocks.tool.registry import ToolCategory, ToolInfo


def _tool(name: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} description",
        category=ToolCategory.CUSTOM,
        enabled=True,
    )


@pytest.fixture(autouse=True)
def reset_pipeline() -> None:
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield
    HookPipeline.reset()


@pytest.mark.asyncio
async def test_capability_projection_preserves_candidates_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_tool("read"), _tool("write")]
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"read", "write"}),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_always_load_tool_names",
        lambda: set(),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema._resolve_dynamic_always_load_tool_names",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.resolve_callable_tool_infos",
        lambda _names: (candidates, len(candidates)),
    )

    result = await list_session_callable_tool_infos("session-1")

    assert result.tool_infos == candidates
    assert result.tool_infos[0] is candidates[0]


@pytest.mark.asyncio
async def test_capability_projection_accepts_opaque_candidate_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Projection(HookBase):
        async def capability_filter(self, _ctx):
            return {"candidates": [{"name": "write"}]}

    candidates = [_tool("read"), _tool("write")]
    HookPipeline.register("projection", Projection())
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_session_callable_tools",
        AsyncMock(return_value={"read", "write"}),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.get_always_load_tool_names",
        lambda: set(),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema._resolve_dynamic_always_load_tool_names",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "flocks.session.callable_schema.resolve_callable_tool_infos",
        lambda _names: (candidates, len(candidates)),
    )

    result = await list_session_callable_tool_infos("session-1")

    assert [tool.name for tool in result.tool_infos] == ["write"]
