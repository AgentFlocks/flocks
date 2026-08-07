from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry


def _make_ctx() -> ToolContext:
    return ToolContext(session_id="test-session", message_id="test-message", agent="rex")


class TestDelegateTaskTolerance:
    def test_delegate_description_requires_material_delegation_benefit(self):
        from flocks.tool.agent.delegate_task import DESCRIPTION

        assert "A specialized agent clearly matches the task" in DESCRIPTION
        assert "You need to explore code in parallel" in DESCRIPTION
        assert "Isolating research or noisy intermediate work" in DESCRIPTION
        assert "Do not delegate trivial edits" in DESCRIPTION
        assert "The task requires multiple steps or research" not in DESCRIPTION

    def test_delegate_schema_exposes_only_subagent_routing(self):
        schema = ToolRegistry.get_schema("delegate_task")
        assert schema is not None
        assert "prompt" in schema.required
        assert "subagent_type" in schema.properties
        assert "category" not in schema.properties
        assert "load_skills" not in schema.required
        assert "description" not in schema.required
        assert "run_in_background" not in schema.properties
        assert "command" in schema.properties
        assert "command" not in schema.required
        assert "tasks" not in schema.properties

    @pytest.mark.asyncio
    async def test_delegate_task_derives_description_and_ignores_blank_skills(self):
        ctx = _make_ctx()
        ctx.extra["workflow_temp_parent"] = True
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
                ctx=ctx,
                subagent_type="asset-survey",
                prompt="Investigate threatbook.cn assets",
                load_skills=["", "   "],
            )

        assert result.success is True
        assert result.title == "Investigate threatbook.cn assets"
        assert result.metadata["sessionId"] == "ses-child"
        assert ctx.extra["workflow_child_session_created"] is True
        skill_get.assert_not_awaited()
        permissions = create_session.await_args.kwargs["permission"]
        denied_permissions = {rule.permission for rule in permissions if rule.action == "deny"}
        assert "delegate_task" not in denied_permissions

    @pytest.mark.asyncio
    async def test_delegate_task_explicit_model_override_is_pinned(self):
        parent_session = SimpleNamespace(
            id="test-session",
            project_id="proj",
            directory="/tmp/project",
            provider=None,
            model=None,
        )
        child_session = SimpleNamespace(id="ses-child")

        with patch("flocks.tool.agent.delegate_task._find_completed_delegate", AsyncMock(return_value=None)), \
             patch("flocks.tool.agent.delegate_task.is_delegatable", return_value=True), \
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
                subagent_type="asset-survey",
                model="anthropic/claude-haiku-4-5",
                prompt="Summarize the diff",
                description="summary task",
            )

        assert result.success is True
        assert create_session.await_args.kwargs["provider"] == "anthropic"
        assert create_session.await_args.kwargs["model"] == "claude-haiku-4-5"
        assert create_session.await_args.kwargs["model_pinned"] is True
        assert loop_run.await_args.kwargs["provider_id"] == "anthropic"
        assert loop_run.await_args.kwargs["model_id"] == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_delegate_task_rejects_removed_category_parameter(self):
        result = await ToolRegistry.execute(
            "delegate_task",
            ctx=_make_ctx(),
            category="quick",
            prompt="Summarize the diff",
        )

        assert result.success is False
        assert "unknown parameters: category" in (result.error or "")

    @pytest.mark.asyncio
    async def test_delegate_task_accepts_deprecated_command_parameter(self):
        result = await ToolRegistry.execute(
            "delegate_task",
            ctx=_make_ctx(),
            command="legacy-tracking-command",
            prompt="Summarize the diff",
        )

        assert result.success is False
        assert "unknown parameters: command" not in (result.error or "")
        assert "subagent_type or session_id" in (result.error or "")

    @pytest.mark.asyncio
    async def test_delegate_task_requires_subagent_for_new_task(self):
        result = await ToolRegistry.execute(
            "delegate_task",
            ctx=_make_ctx(),
            prompt="Summarize the diff",
        )

        assert result.success is False
        assert "subagent_type or session_id" in (result.error or "")

    @pytest.mark.asyncio
    async def test_delegate_task_rejects_restricted_subagent(self):
        with patch("flocks.tool.agent.delegate_task.is_delegatable", return_value=False):
            result = await ToolRegistry.execute(
                "delegate_task",
                ctx=_make_ctx(),
                subagent_type="restricted-agent",
                prompt="Summarize the diff",
            )

        assert result.success is False
        assert 'Agent "restricted-agent" cannot be delegated to' in (result.error or "")

    @pytest.mark.asyncio
    async def test_delegate_task_rejects_unknown_subagent(self, monkeypatch):
        from flocks.agent import registry
        from flocks.agent.agent import AgentInfo

        known_agent = AgentInfo(
            name="known-agent",
            mode="subagent",
            delegatable=True,
        )
        monkeypatch.setattr(registry, "_agents_ref", {"known-agent": known_agent})

        result = await ToolRegistry.execute(
            "delegate_task",
            ctx=_make_ctx(),
            subagent_type="explroe",
            prompt="Summarize the diff",
        )

        assert result.success is False
        assert 'Agent "explroe" cannot be delegated to' in (result.error or "")

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

        with patch("flocks.tool.agent.delegate_task.Session.get_by_id", AsyncMock(return_value=session)), \
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

    @pytest.mark.asyncio
    async def test_delegate_task_sync_continue_reports_normalized_abort(self):
        session = SimpleNamespace(
            id="ses-child-aborted",
            agent="asset-survey",
        )

        with (
            patch(
                "flocks.tool.agent.delegate_task.Session.get_by_id",
                AsyncMock(return_value=session),
            ),
            patch("flocks.tool.agent.delegate_task.Message.create", AsyncMock()),
            patch(
                "flocks.tool.agent.delegate_task.SessionLoop.run",
                AsyncMock(
                    return_value=SimpleNamespace(
                        action="stop",
                        error=None,
                        last_message=None,
                        metadata={"aborted": True},
                    )
                ),
            ),
        ):
            result = await ToolRegistry.execute(
                "delegate_task",
                ctx=_make_ctx(),
                session_id="ses-child-aborted",
                prompt="Continue investigating",
            )

        assert result.success is False
        assert result.metadata["sessionId"] == "ses-child-aborted"
        assert result.metadata["status"] == "interrupted"
        assert "Sub-agent execution was interrupted" in (result.error or "")
        assert "session_id: ses-child-aborted" in (result.error or "")

    @pytest.mark.asyncio
    async def test_new_delegate_reports_normalized_abort_to_parent_metadata(self):
        ctx = _make_ctx()
        metadata_callback = MagicMock()
        ctx.metadata = metadata_callback
        parent_session = SimpleNamespace(
            id="test-session",
            project_id="proj",
            directory="/tmp/project",
            provider=None,
            model=None,
        )
        child_session = SimpleNamespace(id="ses-new-child-aborted")

        with (
            patch(
                "flocks.tool.agent.delegate_task._find_completed_delegate",
                AsyncMock(return_value=None),
            ),
            patch("flocks.tool.agent.delegate_task.is_delegatable", return_value=True),
            patch(
                "flocks.tool.agent.delegate_task.Session.get_by_id",
                AsyncMock(return_value=parent_session),
            ),
            patch(
                "flocks.tool.agent.delegate_task.Session.create",
                AsyncMock(return_value=child_session),
            ),
            patch("flocks.tool.agent.delegate_task.Message.create", AsyncMock()),
            patch(
                "flocks.tool.agent.delegate_task.SessionLoop.run",
                AsyncMock(
                    return_value=SimpleNamespace(
                        action="stop",
                        error=None,
                        last_message=None,
                        metadata={"aborted": True},
                    )
                ),
            ),
        ):
            result = await ToolRegistry.execute(
                "delegate_task",
                ctx=ctx,
                subagent_type="asset-survey",
                prompt="Investigate the interruption",
            )

        assert result.success is False
        assert result.metadata["sessionId"] == "ses-new-child-aborted"
        assert result.metadata["status"] == "interrupted"
        final_parent_metadata = metadata_callback.call_args_list[-1].args[0]
        assert final_parent_metadata["metadata"]["status"] == "interrupted"
