"""Regressions for Tool.execute argument prechecks and failure tracking (G3a, 2026-09-14)."""
from __future__ import annotations

import pytest

from flocks.config.config import Config, ConfigInfo
from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


def _ctx() -> ToolContext:
    return ToolContext(session_id="test", message_id="test")


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ToolRegistry, "_initialized", True)
    monkeypatch.setattr(ToolRegistry, "_tools", {})
    monkeypatch.setattr(ToolRegistry, "_failure_state", {})
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo())
    monkeypatch.setattr(ConfigWriter, "set_tool_setting", lambda _name, _setting: None)


async def test_execute_rejects_uncoercible_integer():
    """``limit="abc"`` used to reach the handler and blow up with a TypeError."""
    calls: list[dict] = []

    async def handler(_ctx: ToolContext, limit: int = 10) -> ToolResult:
        calls.append({"limit": limit})
        return ToolResult(success=True, output=str(limit + 1))

    tool = Tool(
        info=ToolInfo(
            name="int_probe",
            description="probe",
            category=ToolCategory.CUSTOM,
            parameters=[ToolParameter(name="limit", type=ParameterType.INTEGER, required=False)],
        ),
        handler=handler,
    )

    ok = await tool.execute(_ctx(), limit="5")
    assert ok.success and ok.output == "6" and calls == [{"limit": 5}]

    bad = await tool.execute(_ctx(), limit="abc")
    assert bad.success is False
    assert bad.error.startswith("Invalid arguments for int_probe: parameter 'limit' must be an integer (got 'abc')")
    assert bad.metadata["schema_precheck"]["invalid_types"]
    assert len(calls) == 1, "handler must not run with an uncoercible integer"


async def test_execute_rejects_uncoercible_number():
    async def handler(_ctx: ToolContext, timeout_s: float = 1.0) -> ToolResult:
        return ToolResult(success=True, output=str(timeout_s))

    tool = Tool(
        info=ToolInfo(
            name="num_probe",
            description="probe",
            category=ToolCategory.CUSTOM,
            parameters=[ToolParameter(name="timeout_s", type=ParameterType.NUMBER, required=False)],
        ),
        handler=handler,
    )

    assert (await tool.execute(_ctx(), timeout_s="1.5")).output == "1.5"
    bad = await tool.execute(_ctx(), timeout_s="x")
    assert bad.success is False and "must be a number" in bad.error


async def test_hook_denials_do_not_count_toward_auto_disable(isolated_registry, monkeypatch: pytest.MonkeyPatch):
    """A policy deny is not a tool malfunction: three refusals must not switch the tool off."""
    from flocks.hooks import tool_gate
    from flocks.hooks.contracts import HookDecisionAction, ToolDecision

    async def handler(_ctx: ToolContext, **_kwargs) -> ToolResult:
        return ToolResult(success=True, output="ran")

    tool = Tool(
        info=ToolInfo(name="policy_probe", description="probe", category=ToolCategory.CUSTOM, source="plugin_py"),
        handler=handler,
    )
    ToolRegistry._tools[tool.info.name] = tool

    async def fake_gate(*_args, **_kwargs):
        return tool_gate.ToolGateVerdict(
            decision=ToolDecision(action=HookDecisionAction.DENY, reason="policy"),
        )

    monkeypatch.setattr(tool_gate, "run_tool_gate", fake_gate)

    for _ in range(4):
        result = await ToolRegistry.execute("policy_probe", ctx=_ctx(), a=1)
        assert result.success is False
        assert result.metadata.get("blocked_by_hook") is True
        assert "disabled after" not in (result.error or "")

    assert tool.info.enabled is True
    assert ToolRegistry._failure_state.get("policy_probe") is None


def test_recognised_alias_is_dropped_when_canonical_key_present():
    from flocks.tool.registry import _remap_schema_kwargs

    remapped, aliases = _remap_schema_kwargs({"filePath": "a", "file_path": "b"}, ["filePath"])

    assert remapped == {"filePath": "a"}
    assert aliases == {"file_path": "filePath"}


def test_invalid_tool_is_registered_as_builtin():
    from flocks.tool.registry import ToolRegistry

    ToolRegistry.init()
    tool = ToolRegistry.get("invalid")
    assert tool is not None
    assert tool.info.category == ToolCategory.SYSTEM


async def test_execution_mode_denial_names_the_mode_value():
    from flocks.session.execution_mode import SessionExecutionMode
    from flocks.tool.registry import ToolRegistry

    ToolRegistry.init()
    ctx = ToolContext(session_id="s", message_id="m", extra={"execution_mode": SessionExecutionMode.BUILD})
    result = await ToolRegistry.execute("plan_exit", ctx=ctx)

    assert result.success is False
    assert result.error == "Tool 'plan_exit' is not available in 'build' execution mode."
