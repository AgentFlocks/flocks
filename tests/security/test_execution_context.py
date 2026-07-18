from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks.security.execution_context as execution_context


@pytest.mark.asyncio
async def test_root_execution_context_binds_current_capability_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.security.execution_context import build_root_execution_security_context
    from flocks.session.callable_schema import CallableSchemaResult

    schema_result = CallableSchemaResult(
        tool_infos=[],
        metadata={},
        capability_ceiling={
            "tools": ["read"],
            "permission_mode": "readonly",
        },
    )
    captured_call: dict[str, object] = {}

    async def list_tools_side_effect(**kwargs):  # noqa: ANN003
        captured_call.update(deepcopy(kwargs))
        return schema_result

    list_tools = AsyncMock(side_effect=list_tools_side_effect)
    monkeypatch.setattr(
        "flocks.security.execution_context.list_session_callable_tool_infos",
        list_tools,
    )
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=SimpleNamespace(tools=["read", "write"])),
    )

    context = await build_root_execution_security_context(
        session_id="root-session",
        agent_name="rex",
        workspace="/tmp/workspace",
        supplied_context={"entry": "webui"},
    )

    assert context["entry"] == "webui"
    assert context["parent_ceiling"] == schema_result.capability_ceiling
    assert context["_capability_pool"] == schema_result.capability_ceiling
    assert list_tools.await_count == 1
    assert captured_call == {
        "session_id": "root-session",
        "declared_tool_names": ["read", "write"],
        "capability_context": {
            "entry": "webui",
            "agent": "rex",
            "workspace": "/tmp/workspace",
            "sessionID": "root-session",
        },
    }


@pytest.mark.asyncio
async def test_execution_agent_prefers_a_valid_explicit_request_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=SimpleNamespace()),
    )

    assert (
        await execution_context.resolve_execution_agent("worker", "rex")
        == "worker"
    )


@pytest.mark.asyncio
async def test_execution_agent_rejects_an_unknown_explicit_request_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=None),
    )

    with pytest.raises(ValueError, match="Unknown execution agent"):
        await execution_context.resolve_execution_agent("missing", "rex")
