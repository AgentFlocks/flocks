from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.agent.task import task_tool
from flocks.tool.registry import ToolContext, ToolRegistry, ToolResult


def _make_ctx() -> ToolContext:
    return ToolContext(session_id="test-session", message_id="test-message", agent="rex")


class TestTaskCompatibilityAlias:
    def test_task_schema_does_not_expose_background_execution(self):
        schema = ToolRegistry.get_schema("task")
        assert schema is not None
        assert "run_in_background" not in schema.properties

    @pytest.mark.asyncio
    async def test_task_tool_rejects_background_execution_when_called_directly(self):
        result = await task_tool(
            _make_ctx(),
            description="delegate explore",
            prompt="Inspect the repository",
            subagent_type="explore",
            run_in_background=True,
        )

        assert result.success is False
        assert "Background subagent execution is disabled" in (result.error or "")

    @pytest.mark.asyncio
    async def test_task_tool_forwards_single_call_to_delegate_task(self):
        delegate_result = ToolResult(
            success=True,
            output="ok",
            metadata={"sessionId": "ses-child"},
        )

        with patch(
            "flocks.tool.agent.task.delegate_task_tool",
            AsyncMock(return_value=delegate_result),
        ) as delegate:
            result = await task_tool(
                _make_ctx(),
                description="delegate explore",
                prompt="Inspect the repository",
                subagent_type="explore",
                model="openai/gpt-5",
            )

        assert result is delegate_result
        delegate.assert_awaited_once()
        kwargs = delegate.await_args.kwargs
        assert kwargs["description"] == "delegate explore"
        assert kwargs["prompt"] == "Inspect the repository"
        assert kwargs["subagent_type"] == "explore"
        assert kwargs["run_in_background"] is False
        assert kwargs["model"] == "openai/gpt-5"

    @pytest.mark.asyncio
    async def test_task_tool_forwards_batch_to_delegate_task(self):
        delegate_result = ToolResult(
            success=True,
            output="batch launched",
            metadata={"parallel": True},
        )
        tasks = [
            {
                "description": "inspect api",
                "prompt": "Inspect API routes",
                "subagent_type": "explore",
            }
        ]

        with patch(
            "flocks.tool.agent.task.delegate_task_tool",
            AsyncMock(return_value=delegate_result),
        ) as delegate:
            result = await task_tool(
                _make_ctx(),
                tasks=tasks,
                run_in_background=False,
            )

        assert result is delegate_result
        delegate.assert_awaited_once()
        assert delegate.await_args.kwargs["tasks"] == tasks
