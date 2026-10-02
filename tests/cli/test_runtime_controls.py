from unittest.mock import AsyncMock

import pytest
from flocks.session.runtime_controls import RuntimeControls, runtime_controls


@pytest.mark.parametrize("mode,name,allowed", [
    ("default", "read", True), ("default", "bash", False),
    ("acceptEdits", "edit", True), ("acceptEdits", "bash", False),
    ("plan", "write", False), ("bypassPermissions", "bash", True),
    ("dontAsk", "question", False),
])
def test_permission_modes(mode, name, allowed):
    assert (RuntimeControls(permission_mode=mode).denial(name) is None) == allowed


async def test_registry_enforces_deny_and_sandbox_before_execution(monkeypatch):
    from types import SimpleNamespace
    from flocks.tool.registry import ToolRegistry, ToolContext, ToolResult
    executed = AsyncMock(return_value=ToolResult(success=True, output="ran"))
    tool = SimpleNamespace(info=SimpleNamespace(source="builtin", enabled=True), execute=executed)
    monkeypatch.setattr(ToolRegistry, "get", lambda name: tool)
    monkeypatch.setattr(ToolRegistry, "_reset_failure_state", lambda name: None)
    controls = RuntimeControls(allowed_tools=("*",), disallowed_tools=("write",))
    token = runtime_controls.set(controls)
    try:
        denied = await ToolRegistry.execute("write")
        assert not denied.success
        executed.assert_not_called()
        allowed = await ToolRegistry.execute("read")
        assert allowed.success
        assert executed.await_count == 1
        controls.sandbox_required = True
        denied = await ToolRegistry.execute("bash", ctx=ToolContext("s", "m"))
        assert not denied.success and "sandbox" in denied.error
        assert executed.await_count == 1
        assert controls.denial("delegate_task")
    finally:
        runtime_controls.reset(token)


async def test_context_inheritance_and_isolation():
    import asyncio
    controls = RuntimeControls(disallowed_tools=("bash",))
    async def child():
        return runtime_controls.get().denial("bash")
    token = runtime_controls.set(controls)
    task = asyncio.create_task(child())
    runtime_controls.reset(token)
    assert await task
    assert runtime_controls.get() is None


def test_budget_uses_existing_cost_calculator_and_rejects_unknown_currency():
    from flocks.provider.types import PriceConfig
    price = PriceConfig(input=1, output=2, unit=1000, currency="USD")
    controls = RuntimeControls(max_budget=0.01)
    controls.check_budget(price, require_pricing=True)
    controls.record_usage({"prompt_tokens": 5, "completion_tokens": 5}, price)
    assert controls.total_cost == 0.015
    with pytest.raises(RuntimeError, match="budget"):
        controls.check_budget(price, require_pricing=True)
    with pytest.raises(RuntimeError, match="known USD"):
        RuntimeControls(max_budget=1).check_budget(None, require_pricing=True)
    with pytest.raises(RuntimeError, match="known USD"):
        RuntimeControls(max_budget=1).check_budget(PriceConfig(input=1, output=1, currency="CNY"), require_pricing=True)
