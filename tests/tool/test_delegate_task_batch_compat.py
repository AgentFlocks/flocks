from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry


def _make_ctx() -> ToolContext:
    return ToolContext(session_id="test-session", message_id="test-message", agent="rex")


class TestDelegateTaskTolerance:
    def test_delegate_task_schema_allows_omitting_optional_fields(self):
        schema = ToolRegistry.get_schema("delegate_task")
        assert schema is not None
        assert "prompt" in schema.required
        assert "load_skills" not in schema.required
        assert "description" not in schema.required

    @pytest.mark.asyncio
    async def test_delegate_task_derives_description_and_ignores_blank_skills(self):
        manager = SimpleNamespace(
            launch=AsyncMock(return_value=SimpleNamespace(
                id="task-1",
                description="Investigate threatbook.cn assets",
                agent="asset-survey",
                status="running",
                session_id="ses-child",
            ))
        )
        with patch("flocks.tool.agent.delegate_task._find_completed_delegate", AsyncMock(return_value=None)),              patch("flocks.tool.agent.delegate_task.Config.get", AsyncMock(return_value=SimpleNamespace(categories=None))),              patch("flocks.tool.agent.delegate_task.is_delegatable", return_value=True),              patch("flocks.tool.agent.delegate_task.get_background_manager", return_value=manager),              patch("flocks.tool.agent.delegate_task.Skill.get", AsyncMock()) as skill_get:
            result = await ToolRegistry.execute(
                "delegate_task",
                ctx=_make_ctx(),
                subagent_type="asset-survey",
                prompt="Investigate threatbook.cn assets",
                run_in_background=True,
                load_skills=["", "   "],
            )

        assert result.success is True
        assert result.title == "Investigate threatbook.cn assets"
        assert result.metadata["sessionId"] == "ses-child"
        skill_get.assert_not_awaited()
        manager.launch.assert_awaited_once()
        launch_input = manager.launch.await_args.args[0]
        assert launch_input.description == "Investigate threatbook.cn assets"


class TestBatchCompatibility:
    def test_batch_schema_allows_legacy_commands_alias(self):
        schema = ToolRegistry.get_schema("batch")
        assert schema is not None
        assert "tool_calls" not in schema.required
        assert "commands" in schema.properties

    @pytest.mark.asyncio
    async def test_batch_accepts_legacy_commands_args(self):
        result = await ToolRegistry.execute(
            "batch",
            ctx=_make_ctx(),
            commands=[{"tool": "echo", "args": {"message": "hello"}}],
        )

        assert result.success is True
        assert result.metadata["tools"] == ["echo"]
        assert result.metadata["successful"] == 1
