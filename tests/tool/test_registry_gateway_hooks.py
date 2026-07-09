from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks.tool.registry as registry_mod
from flocks.tool.registry import ParameterType, ToolContext, ToolParameter, ToolRegistry, ToolResult


def _register_test_tool(name: str, handler):
    ToolRegistry.register_function(
        name=name,
        description="b2 registry hook test tool",
        parameters=[ToolParameter(name="value", type=ParameterType.STRING, required=True)],
    )(handler)


@pytest.mark.asyncio
async def test_registry_executes_before_after_hooks_and_emits_audit(monkeypatch: pytest.MonkeyPatch):
    ToolRegistry.init()
    tool_name = "b2_registry_hook_allow"

    async def _handler(ctx: ToolContext, value: str) -> ToolResult:
        return ToolResult(success=True, output=f"ok:{value}")

    _register_test_tool(tool_name, _handler)
    try:
        run_before = AsyncMock(return_value=SimpleNamespace(output={"decision": {"action": "allow"}}))
        run_after = AsyncMock(return_value=SimpleNamespace(output={}))
        audit_emit = AsyncMock()
        monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_before", run_before)
        monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_after", run_after)
        monkeypatch.setattr(registry_mod, "_emit_tool_audit", audit_emit)

        result = await ToolRegistry.execute(
            tool_name,
            ctx=ToolContext(session_id="s-hook", message_id="m-hook", call_id="call-123"),
            value="hello",
        )
    finally:
        ToolRegistry.unregister(tool_name)

    assert result.success is True
    assert result.output == "ok:hello"
    assert run_before.await_count == 1
    assert run_after.await_count == 1
    assert audit_emit.await_count == 2
    assert audit_emit.await_args_list[0].args[0] == "tool.before_execute"
    assert audit_emit.await_args_list[1].args[0] == "tool.after_execute"
    assert audit_emit.await_args_list[0].args[1]["trace_id"] == "call-123"
    assert audit_emit.await_args_list[1].args[1]["trace_id"] == "call-123"
    before_payload = audit_emit.await_args_list[0].args[1]
    after_payload = audit_emit.await_args_list[1].args[1]
    assert "input" not in before_payload["tool"]
    assert "input_hash" in before_payload["tool"]
    assert "result" not in after_payload
    assert "output_hash" in after_payload


@pytest.mark.asyncio
async def test_registry_respects_constrain_decision(monkeypatch: pytest.MonkeyPatch):
    ToolRegistry.init()
    tool_name = "b2_registry_hook_constrain"
    observed: dict[str, str] = {}

    async def _handler(ctx: ToolContext, value: str) -> ToolResult:
        observed["value"] = value
        return ToolResult(success=True, output=value)

    _register_test_tool(tool_name, _handler)
    try:
        run_before = AsyncMock(
            return_value=SimpleNamespace(
                output={
                    "decision": {
                        "action": "constrain",
                        "updated_input": {"value": "rewritten"},
                    }
                }
            )
        )
        monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_before", run_before)
        monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_after", AsyncMock(return_value=SimpleNamespace(output={})))
        monkeypatch.setattr(registry_mod, "_emit_tool_audit", AsyncMock())

        result = await ToolRegistry.execute(
            tool_name,
            ctx=ToolContext(session_id="s-hook", message_id="m-hook"),
            value="original",
        )
    finally:
        ToolRegistry.unregister(tool_name)

    assert result.success is True
    assert observed["value"] == "rewritten"


@pytest.mark.asyncio
async def test_registry_stops_execution_on_deny_decision(monkeypatch: pytest.MonkeyPatch):
    ToolRegistry.init()
    tool_name = "b2_registry_hook_deny"
    executed = {"handler": False}

    async def _handler(ctx: ToolContext, value: str) -> ToolResult:
        executed["handler"] = True
        return ToolResult(success=True, output=value)

    _register_test_tool(tool_name, _handler)
    try:
        run_before = AsyncMock(
            return_value=SimpleNamespace(output={"decision": {"action": "deny", "reason": "blocked"}})
        )
        run_after = AsyncMock(return_value=SimpleNamespace(output={}))
        audit_emit = AsyncMock()
        monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_before", run_before)
        monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_after", run_after)
        monkeypatch.setattr(registry_mod, "_emit_tool_audit", audit_emit)

        result = await ToolRegistry.execute(
            tool_name,
            ctx=ToolContext(session_id="s-hook", message_id="m-hook"),
            value="hello",
        )
    finally:
        ToolRegistry.unregister(tool_name)

    assert result.success is False
    assert result.error == "blocked"
    assert executed["handler"] is False
    assert run_after.await_count == 0
    assert audit_emit.await_count == 1
    assert audit_emit.await_args_list[0].args[0] == "tool.before_execute"
    assert "input" not in audit_emit.await_args_list[0].args[1]["tool"]
