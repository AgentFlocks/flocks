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
    assert run_after.await_args.args[0]["permission_checked"] is False
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
    assert after_payload["permission_checked"] is False


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
            side_effect=[
                SimpleNamespace(
                    output={
                        "decision": {
                            "action": "constrain",
                            "updated_input": {"value": "rewritten"},
                        }
                    }
                ),
                SimpleNamespace(output={"decision": {"action": "allow"}}),
            ]
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


@pytest.mark.asyncio
async def test_registry_fails_closed_on_malformed_active_policy_decision(
    monkeypatch: pytest.MonkeyPatch,
):
    ToolRegistry.init()
    tool_name = "b3_registry_hook_malformed_policy"
    executed = {"handler": False}

    async def _handler(ctx: ToolContext, value: str) -> ToolResult:
        executed["handler"] = True
        return ToolResult(success=True, output=value)

    _register_test_tool(tool_name, _handler)
    try:
        run_before = AsyncMock(
            return_value=SimpleNamespace(
                output={
                    "policy_engine_present": True,
                    "decision": {"action": "typo"},
                }
            )
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
    assert result.error == "invalid_policy_decision"
    assert result.metadata["decision"]["action"] == "deny"
    assert result.metadata["blocked_by_policy"] is True
    assert executed["handler"] is False
    assert run_after.await_count == 0
    assert audit_emit.await_count == 1
    assert audit_emit.await_args_list[0].args[1]["permission_checked"] is True


@pytest.mark.asyncio
async def test_registry_forwards_tool_policy_constraint_to_hook_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ToolRegistry.init()
    tool_name = "b3_registry_hook_constraint_forward"
    constraint = {
        "allowed": False,
        "tool": tool_name,
        "source": "sandbox.tool_policy",
    }
    extra = {"tool_policy_constraint": constraint, "sandbox": {"enabled": True}}

    async def _handler(ctx: ToolContext, value: str) -> ToolResult:
        return ToolResult(success=True, output=value)

    _register_test_tool(tool_name, _handler)
    try:
        run_before = AsyncMock(return_value=SimpleNamespace(output={"decision": {"action": "allow"}}))
        monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_before", run_before)
        monkeypatch.setattr(
            "flocks.hooks.pipeline.HookPipeline.run_tool_after",
            AsyncMock(return_value=SimpleNamespace(output={})),
        )
        monkeypatch.setattr(registry_mod, "_emit_tool_audit", AsyncMock())

        ctx = ToolContext(session_id="s-hook", message_id="m-hook", extra=extra)
        result = await ToolRegistry.execute(tool_name, ctx=ctx, value="hello")
    finally:
        ToolRegistry.unregister(tool_name)

    assert result.success is True
    hook_payload = run_before.await_args.args[0]
    assert hook_payload["tool_policy_constraint"] == constraint
    assert ctx.extra == extra


@pytest.mark.asyncio
async def test_registry_returns_pending_metadata_for_approval_mode(monkeypatch: pytest.MonkeyPatch):
    ToolRegistry.init()
    tool_name = "b3_registry_hook_approval_pending"

    async def _handler(ctx: ToolContext, value: str) -> ToolResult:
        return ToolResult(success=True, output=value)

    _register_test_tool(tool_name, _handler)
    try:
        run_before = AsyncMock(
            return_value=SimpleNamespace(
                output={
                    "decision": {
                        "action": "ask",
                        "mode": "approval",
                        "reason": "approval_required",
                        "grant_ref": "approval_123",
                        "policy_version": "b3-v1",
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
            value="hello",
        )
    finally:
        ToolRegistry.unregister(tool_name)

    assert result.success is False
    assert result.metadata["pending"] is True
    assert result.metadata["pending_mode"] == "approval"
    assert result.metadata["pending_approval"]["request_id"] == "approval_123"
