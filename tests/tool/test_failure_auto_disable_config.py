from __future__ import annotations

import pytest

from flocks.config.config import Config, ConfigInfo, ToolFailureConfig
from flocks.tool.registry import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolRegistry,
    ToolResult,
)


def _failing_tool(name: str = "failing_custom_tool") -> Tool:
    async def handler(_ctx: ToolContext, **_kwargs) -> ToolResult:
        return ToolResult(success=False, error="synthetic repeated failure")

    return Tool(
        info=ToolInfo(
            name=name,
            description="Always fails for repeated-failure tests",
            category=ToolCategory.CUSTOM,
            source="plugin_py",
        ),
        handler=handler,
    )


@pytest.fixture
def isolated_failure_tracking(monkeypatch: pytest.MonkeyPatch):
    tool = _failing_tool()
    monkeypatch.setattr(ToolRegistry, "_initialized", True)
    monkeypatch.setattr(ToolRegistry, "_tools", {tool.info.name: tool})
    monkeypatch.setattr(ToolRegistry, "_failure_state", {})
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo())
    return tool


def test_tool_failure_config_defaults_to_enabled() -> None:
    section = ToolFailureConfig()

    assert section.disable_on_repeated_failure is True


def test_tool_failure_config_accepts_camel_and_snake_case() -> None:
    camel = ConfigInfo.model_validate(
        {"toolFailure": {"disableOnRepeatedFailure": False}}
    )
    snake = ConfigInfo.model_validate(
        {"tool_failure": {"disable_on_repeated_failure": False}}
    )

    assert camel.tool_failure is not None
    assert camel.tool_failure.disable_on_repeated_failure is False
    assert snake.tool_failure is not None
    assert snake.tool_failure.disable_on_repeated_failure is False


@pytest.mark.asyncio
async def test_repeated_failures_disable_tool_by_default(
    isolated_failure_tracking: Tool,
) -> None:
    tool = isolated_failure_tracking

    for _ in range(ToolRegistry._failure_disable_threshold):
        result = await ToolRegistry.execute(tool.info.name, query="same")

    assert tool.info.enabled is False
    assert result.metadata == {
        "disabled": True,
        "disabled_reason": "repeated_error",
    }


@pytest.mark.asyncio
async def test_config_can_turn_off_repeated_failure_auto_disable(
    isolated_failure_tracking: Tool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = isolated_failure_tracking
    monkeypatch.setattr(
        Config,
        "_cached_config",
        ConfigInfo.model_validate(
            {"toolFailure": {"disableOnRepeatedFailure": False}}
        ),
    )

    for _ in range(ToolRegistry._failure_disable_threshold + 1):
        result = await ToolRegistry.execute(tool.info.name, query="same")

    assert result.success is False
    assert "disabled" not in result.metadata
    assert tool.info.enabled is True
    assert ToolRegistry._failure_state == {}
