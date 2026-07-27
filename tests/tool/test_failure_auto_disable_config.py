from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from flocks.config.config import Config, ConfigInfo, ToolFailureConfig
from flocks.config.config_writer import ConfigWriter
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
    monkeypatch.setattr(ToolRegistry, "_revision", 41)
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo())
    monkeypatch.setattr(ConfigWriter, "set_tool_setting", lambda _name, _setting: None)
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
    assert ToolRegistry.revision() == 42


def test_concurrent_failures_commit_one_disable_transition(
    isolated_failure_tracking: Tool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = isolated_failure_tracking
    params = {"query": "same"}
    error = "synthetic repeated failure"
    ToolRegistry._failure_state[tool.info.name] = {
        "key": ToolRegistry._failure_key(tool.info.name, params, error),
        "count": ToolRegistry._failure_disable_threshold - 1,
    }
    persist_setting = Mock()
    monkeypatch.setattr(ConfigWriter, "set_tool_setting", persist_setting)

    with ThreadPoolExecutor(max_workers=8) as executor:
        transitions = list(executor.map(
            lambda _: ToolRegistry._record_failure(tool, params, error),
            range(8),
        ))

    assert transitions.count(True) == 1
    assert tool.info.enabled is False
    assert ToolRegistry.revision() == 42
    persist_setting.assert_called_once_with(tool.info.name, {"enabled": False})


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
