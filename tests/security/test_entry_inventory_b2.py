from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import flocks.tool.registry as registry_mod
from flocks.server.routes.tool import _execute_with_http_context
from flocks.tool.registry import ParameterType, ToolContext, ToolParameter, ToolRegistry, ToolResult
from flocks.workflow.tools_adapter import FlocksToolAdapter


def _register_entry_test_tool(name: str):
    async def _handler(ctx: ToolContext, value: str) -> ToolResult:
        return ToolResult(success=True, output=f"ok:{value}")

    ToolRegistry.register_function(
        name=name,
        description="entry inventory test tool",
        parameters=[ToolParameter(name="value", type=ParameterType.STRING, required=True)],
    )(_handler)


@pytest.mark.asyncio
async def test_entry_inventory_registry_direct_emits_before_execute(monkeypatch: pytest.MonkeyPatch):
    ToolRegistry.init()
    tool_name = "b2_entry_direct"
    _register_entry_test_tool(tool_name)
    try:
        audit_emit = AsyncMock()
        monkeypatch.setattr(registry_mod, "_emit_tool_audit", audit_emit)

        result = await ToolRegistry.execute(
            tool_name,
            ctx=ToolContext(session_id="entry-direct", message_id="msg-1", call_id="trace-direct"),
            value="a",
        )
    finally:
        ToolRegistry.unregister(tool_name)

    assert result.success is True
    assert [call.args[0] for call in audit_emit.await_args_list] == [
        "tool.before_execute",
        "tool.after_execute",
    ]
    assert audit_emit.await_args_list[0].args[1]["trace_id"] == "trace-direct"
    assert audit_emit.await_args_list[1].args[1]["trace_id"] == "trace-direct"


@pytest.mark.asyncio
async def test_entry_inventory_http_route_emits_before_execute(monkeypatch: pytest.MonkeyPatch):
    ToolRegistry.init()
    tool_name = "b2_entry_http"
    _register_entry_test_tool(tool_name)
    try:
        audit_emit = AsyncMock()
        monkeypatch.setattr(registry_mod, "_emit_tool_audit", audit_emit)
        tool = ToolRegistry.get(tool_name)
        assert tool is not None

        result = await _execute_with_http_context(
            tool_name=tool_name,
            tool_info=tool.info,
            params={"value": "b"},
            session_id=None,
            message_id=None,
            agent="rex",
        )
    finally:
        ToolRegistry.unregister(tool_name)

    assert result.success is True
    assert "tool.before_execute" in [call.args[0] for call in audit_emit.await_args_list]


def test_entry_inventory_workflow_adapter_emits_before_execute(monkeypatch: pytest.MonkeyPatch):
    ToolRegistry.init()
    tool_name = "b2_entry_workflow"
    _register_entry_test_tool(tool_name)
    try:
        audit_emit = AsyncMock()
        monkeypatch.setattr(registry_mod, "_emit_tool_audit", audit_emit)
        adapter = FlocksToolAdapter(
            tool_context=ToolContext(session_id="entry-workflow", message_id="msg-3", call_id="trace-workflow")
        )

        output = adapter.run(tool_name, value="c")
    finally:
        ToolRegistry.unregister(tool_name)

    assert output == "ok:c"
    assert [call.args[0] for call in audit_emit.await_args_list][:1] == ["tool.before_execute"]
