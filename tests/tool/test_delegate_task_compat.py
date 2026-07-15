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
        assert "run_in_background" not in schema.properties
        # Legacy batch shape is gone: tasks=[...] is no longer a public option.
        assert "tasks" not in schema.properties

    @pytest.mark.asyncio
    async def test_delegate_task_derives_description_and_ignores_blank_skills(self):
        parent_session = SimpleNamespace(
            id="test-session",
            project_id="proj",
            directory="/tmp/project",
            provider=None,
            model=None,
        )
        child_session = SimpleNamespace(id="ses-child")
        with (
            patch("flocks.tool.agent.delegate_task._find_completed_delegate", AsyncMock(return_value=None)),
            patch("flocks.tool.agent.delegate_task.Config.get", AsyncMock(return_value=SimpleNamespace(categories=None))),
            patch("flocks.tool.agent.delegate_task.is_delegatable", return_value=True),
            patch("flocks.tool.agent.delegate_task.Skill.get", AsyncMock()) as skill_get,
            patch("flocks.tool.agent.delegate_task.Session.get_by_id", AsyncMock(return_value=parent_session)),
            patch("flocks.tool.agent.delegate_task.Session.create", AsyncMock(return_value=child_session)) as create_session,
            patch("flocks.tool.agent.delegate_task.Message.create", AsyncMock()),
            patch("flocks.tool.agent.delegate_task.SessionLoop.run", AsyncMock(return_value=SimpleNamespace(
                action="stop",
                error=None,
                last_message=None,
            ))),
        ):
            result = await ToolRegistry.execute(
                "delegate_task",
                ctx=_make_ctx(),
                subagent_type="asset-survey",
                prompt="Investigate threatbook.cn assets",
                load_skills=["", "   "],
            )

        assert result.success is True
        assert result.title == "Investigate threatbook.cn assets"
        assert result.metadata["sessionId"] == "ses-child"
        skill_get.assert_not_awaited()
        permissions = create_session.await_args.kwargs["permission"]
        denied_permissions = {rule.permission for rule in permissions if rule.action == "deny"}
        assert "delegate_task" not in denied_permissions
        assert "task" not in denied_permissions

    @pytest.mark.asyncio
    async def test_delegate_task_persists_and_forwards_a_narrowed_parent_ceiling(self):
        parent_session = SimpleNamespace(
            id="test-session",
            project_id="proj",
            directory="/tmp/project",
            provider=None,
            model=None,
        )
        child_session = SimpleNamespace(id="ses-child")
        parent_ceiling = {
            "tools": ["read"],
            "permission_mode": "readonly",
            "data_domains": ["tenant-a"],
        }
        with (
            patch("flocks.tool.agent.delegate_task._find_completed_delegate", AsyncMock(return_value=None)),
            patch("flocks.tool.agent.delegate_task.Config.get", AsyncMock(return_value=SimpleNamespace(categories=None))),
            patch("flocks.tool.agent.delegate_task.is_delegatable", return_value=True),
            patch("flocks.tool.agent.delegate_task.Skill.get", AsyncMock()),
            patch("flocks.tool.agent.delegate_task.Session.get_by_id", AsyncMock(return_value=parent_session)),
            patch("flocks.tool.agent.delegate_task.Session.create", AsyncMock(return_value=child_session)),
            patch("flocks.tool.agent.delegate_task.Message.create", AsyncMock()),
            patch("flocks.tool.agent.delegate_task._build_delegated_security_context", AsyncMock(return_value={
                "parent_ceiling": parent_ceiling,
            })),
            patch("flocks.tool.agent.delegate_task.store_delegation_security_context", AsyncMock()) as store_context,
            patch("flocks.tool.agent.delegate_task.SessionLoop.run", AsyncMock(return_value=SimpleNamespace(
                action="stop",
                error=None,
                last_message=None,
            ))) as loop_run,
        ):
            result = await ToolRegistry.execute(
                "delegate_task",
                ctx=ToolContext(
                    session_id="test-session",
                    message_id="test-message",
                    agent="rex",
                    extra={"_capability_pool": {"tools": ["read"], "secret": "must-not-leak"}},
                ),
                subagent_type="asset-survey",
                prompt="Investigate assets",
            )

        assert result.success is True
        store_context.assert_awaited_once_with("ses-child", {"parent_ceiling": parent_ceiling})
        assert loop_run.await_args.kwargs["security_context"] == {"parent_ceiling": parent_ceiling}

    @pytest.mark.asyncio
    async def test_delegate_task_category_model_uses_runtime_override_without_pinning(self):
        parent_session = SimpleNamespace(
            id="test-session",
            project_id="proj",
            directory="/tmp/project",
            provider=None,
            model=None,
        )
        child_session = SimpleNamespace(id="ses-quick")
        cfg = SimpleNamespace(categories={
            "quick": {
                "model": "anthropic/claude-haiku-4-5",
                "prompt_append": None,
            }
        })

        with patch("flocks.tool.agent.delegate_task._find_completed_delegate", AsyncMock(return_value=None)), \
             patch("flocks.tool.agent.delegate_task.Config.get", AsyncMock(return_value=cfg)), \
             patch("flocks.tool.agent.delegate_task._validate_category_model", return_value={
                 "providerID": "anthropic",
                 "modelID": "claude-haiku-4-5",
             }), \
             patch("flocks.tool.agent.delegate_task.Session.get_by_id", AsyncMock(return_value=parent_session)), \
             patch("flocks.tool.agent.delegate_task.Session.create", AsyncMock(return_value=child_session)) as create_session, \
             patch("flocks.tool.agent.delegate_task.Message.create", AsyncMock()), \
             patch("flocks.tool.agent.delegate_task.SessionLoop.run", AsyncMock(return_value=SimpleNamespace(
                 action="stop",
                 error=None,
                 last_message=None,
             ))) as loop_run:
            result = await ToolRegistry.execute(
                "delegate_task",
                ctx=_make_ctx(),
                category="quick",
                prompt="Summarize the diff",
                description="quick task",
            )

        assert result.success is True
        assert create_session.await_args.kwargs["provider"] == "anthropic"
        assert create_session.await_args.kwargs["model"] == "claude-haiku-4-5"
        assert create_session.await_args.kwargs["model_pinned"] is False
        assert loop_run.await_args.kwargs["provider_id"] == "anthropic"
        assert loop_run.await_args.kwargs["model_id"] == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_delegate_task_rejects_background_execution(self):
        # run_in_background is not in the public schema, so the registry
        # rejects it with an "unknown parameters" error before the function
        # body runs. This guards the schema-level ban.
        result = await ToolRegistry.execute(
            "delegate_task",
            ctx=_make_ctx(),
            subagent_type="asset-survey",
            prompt="Investigate threatbook.cn assets",
            run_in_background=True,
        )

        assert result.success is False
        assert "unknown parameters: run_in_background" in (result.error or "")

    @pytest.mark.asyncio
    async def test_delegate_task_function_body_guard_rejects_background(self):
        # Direct (in-process) callers that bypass the registry still hit the
        # function-body guard. This is the second line of defense.
        from flocks.tool.agent.delegate_task import delegate_task_tool

        result = await delegate_task_tool(
            _make_ctx(),
            subagent_type="asset-survey",
            prompt="Investigate threatbook.cn assets",
            run_in_background=True,
        )

        assert result.success is False
        assert "Background subagent execution is disabled" in (result.error or "")

    @pytest.mark.asyncio
    async def test_delegate_task_rejects_legacy_batch_tasks_param(self):
        # tasks=[...] has been removed from the schema; passing it now
        # surfaces a schema-level "unknown parameters" error.
        result = await ToolRegistry.execute(
            "delegate_task",
            ctx=_make_ctx(),
            tasks=[{"prompt": "x", "subagent_type": "explore"}],
        )

        assert result.success is False
        assert "unknown parameters: tasks" in (result.error or "")

    @pytest.mark.asyncio
    async def test_delegate_task_sync_continue_fails_when_last_message_missing(self):
        session = SimpleNamespace(
            id="ses-child",
            agent="asset-survey",
        )

        with patch("flocks.tool.agent.delegate_task.Config.get", AsyncMock(return_value=SimpleNamespace(categories=None))), \
             patch("flocks.tool.agent.delegate_task.Session.get_by_id", AsyncMock(return_value=session)), \
             patch("flocks.tool.agent.delegate_task.Message.create", AsyncMock()), \
             patch("flocks.tool.agent.delegate_task.SessionLoop.run", AsyncMock(return_value=SimpleNamespace(
                 action="stop",
                 error=None,
                 last_message=None,
             ))):
            result = await ToolRegistry.execute(
                "delegate_task",
                ctx=_make_ctx(),
                session_id="ses-child",
                prompt="Continue investigating",
            )

        assert result.success is True
        assert result.metadata["sessionId"] == "ses-child"
        assert result.metadata["emptyOutput"] is True
        assert "without producing a final assistant message" in (result.output or "")
