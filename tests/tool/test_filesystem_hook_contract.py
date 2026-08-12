from __future__ import annotations

from typing import Any

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
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


def _search_tool(name: str) -> Tool:
    async def handler(_ctx: ToolContext, **_kwargs: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")

    return Tool(
        info=ToolInfo(
            name=name,
            description=f"{name} filesystem hook contract",
            category=ToolCategory.SEARCH,
            parameters=[
                ToolParameter(name="pattern", type=ParameterType.STRING),
                ToolParameter(name="path", type=ParameterType.STRING, required=False),
            ],
        ),
        handler=handler,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["glob", "grep"])
async def test_search_file_tools_reach_tool_execute_hook(tool_name: str) -> None:
    observed: list[dict[str, Any]] = []

    class Recorder(HookBase):
        async def tool_before(self, ctx) -> None:  # noqa: ANN001
            observed.append(dict(ctx.input["tool_execution"]))

    HookPipeline.register("filesystem-recorder", Recorder())
    context = ToolContext(
        session_id="session-1",
        message_id="message-1",
        agent="rex",
        extra={
            "agent_execution_session": True,
            "session_execution_profile": {
                "workspace_dir": "/projects/current",
                "project_root": "/projects/current",
                "project_id": "project-1",
                "permission_mode": "require-confirm",
                "runtime_mode": "dev-mode",
            },
        },
    )

    result = await _search_tool(tool_name).execute(
        context,
        pattern="needle",
        path="/projects/current",
    )

    assert result.success is True
    assert observed[0]["tool"]["name"] == tool_name
    assert observed[0]["tool"]["validated_input"]["path"] == "/projects/current"
    assert observed[0]["safety_mode"]["runtime_mode"] == "dev-mode"
