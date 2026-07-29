from __future__ import annotations

from typing import Any

import pytest

from flocks.hooks.pipeline import HookBase, HookContext, HookPipeline
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolParameter,
    ToolResult,
)


@pytest.fixture(autouse=True)
def _reset_hooks() -> None:
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield
    HookPipeline.reset()


_COMMAND_CASES = (
    (
        "bash",
        {"command": "git status", "workdir": "/tmp/work"},
    ),
    (
        "ssh_host_cmd",
        {"host": "host-a", "command": "uname -a", "password": "opaque-secret"},
    ),
    (
        "ssh_run_script",
        {"host": "host-a", "script_path": "/tmp/triage.sh"},
    ),
)


def _tool(name: str, arguments: dict[str, Any], calls: list[dict[str, Any]]) -> Tool:
    async def handler(_ctx: ToolContext, **kwargs: Any) -> ToolResult:
        calls.append(kwargs)
        return ToolResult(success=True, output="ok")

    return Tool(
        info=ToolInfo(
            name=name,
            description=f"{name} hook contract",
            category=ToolCategory.TERMINAL,
            parameters=[
                ToolParameter(
                    name=key,
                    type=(
                        ParameterType.INTEGER
                        if isinstance(value, int)
                        else ParameterType.STRING
                    ),
                )
                for key, value in arguments.items()
            ],
        ),
        handler=handler,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "arguments"), _COMMAND_CASES)
async def test_command_family_reaches_neutral_action_hook_with_raw_arguments(
    name: str,
    arguments: dict[str, Any],
) -> None:
    observed: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    class Recorder(HookBase):
        async def action_before(self, ctx) -> None:  # noqa: ANN001
            observed.append(dict(ctx.input))

    HookPipeline.register("command-contract-recorder", Recorder())
    result = await _tool(name, arguments, calls).execute(
        ToolContext(
            session_id="session-1",
            message_id="message-1",
            agent="rex",
            extra={"opaque": "carrier"},
        ),
        **arguments,
    )

    assert result.success is True
    assert calls == [arguments]
    assert observed[0]["operation"] == "tool.execute"
    assert observed[0]["tool"] == {"name": name, "input": arguments}
    assert observed[0]["tool_context_extra"] == {"opaque": "carrier"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "arguments"), _COMMAND_CASES)
async def test_command_family_behavior_is_unchanged_without_extensions(
    name: str,
    arguments: dict[str, Any],
) -> None:
    calls: list[dict[str, Any]] = []

    result = await _tool(name, arguments, calls).execute(
        ToolContext(session_id="session-1", message_id="message-1"),
        **arguments,
    )

    assert result.success is True
    assert result.output == "ok"
    assert calls == [arguments]


@pytest.mark.asyncio
async def test_execution_stop_cannot_be_cleared_by_a_later_hook() -> None:
    """The generic stop control is monotonic across independently ordered hooks."""

    class StopHook(HookBase):
        async def action_before(self, ctx: HookContext) -> None:
            ctx.output["execution"] = {"stop": True, "detail": "blocked"}

    class ClearHook(HookBase):
        async def action_before(self, ctx: HookContext) -> None:
            ctx.output["execution"] = {"stop": False}

    calls: list[dict[str, Any]] = []
    arguments = {"command": "safe"}
    HookPipeline.register("test.stop", StopHook(), order=10)
    HookPipeline.register("test.clear", ClearHook(), order=20)

    result = await _tool("bash", arguments, calls).execute(
        ToolContext(session_id="s-1", message_id="m-1"),
        **arguments,
    )

    assert result.success is False
    assert result.error == "blocked"
    assert calls == []
