from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.tool.agent.delegate_task import delegate_task_tool
from flocks.tool.registry import ToolContext, ToolResult


@pytest.fixture(autouse=True)
def reset_pipeline() -> None:
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield
    HookPipeline.reset()


@pytest.mark.asyncio
async def test_delegation_emits_child_lifecycle_with_parent_and_child_ids() -> None:
    observed: list[tuple[str, dict]] = []

    class ChildLifecycle(HookBase):
        async def session_child_before(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

        async def session_child_after(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

    HookPipeline.register("child-lifecycle", ChildLifecycle())
    parent = SimpleNamespace(id="parent-1", project_id="project-1", directory="/tmp/project")
    child = SimpleNamespace(id="child-1")
    forwarder = SimpleNamespace(
        final_metadata={},
        build_callbacks=lambda **_kwargs: SimpleNamespace(),
    )
    context = ToolContext(session_id="parent-1", message_id="message-1", agent="rex")

    with (
        patch("flocks.tool.agent.delegate_task._find_completed_delegate", AsyncMock(return_value=None)),
        patch("flocks.tool.agent.delegate_task.Config.get", AsyncMock(return_value=SimpleNamespace(categories=None))),
        patch("flocks.tool.agent.delegate_task.is_delegatable", return_value=True),
        patch("flocks.tool.agent.delegate_task.Session.get_by_id", AsyncMock(return_value=parent)),
        patch("flocks.tool.agent.delegate_task.Session.create", AsyncMock(return_value=child)),
        patch("flocks.tool.agent.delegate_task.Message.create", AsyncMock()),
        patch("flocks.tool.agent.delegate_task.SessionLoop.run", AsyncMock(return_value=SimpleNamespace())),
        patch("flocks.session.features.activity_forwarder.ActivityForwarder", return_value=forwarder),
        patch(
            "flocks.tool.agent.delegate_task.format_sync_subagent_result",
            AsyncMock(return_value=ToolResult(success=True, output="complete")),
        ),
    ):
        result = await delegate_task_tool(
            context,
            subagent_type="asset-survey",
            prompt="Inspect the workspace",
        )

    assert result.success is True
    assert [stage for stage, _payload in observed] == [
        "session.child.before",
        "session.child.after",
    ]
    for _stage, payload in observed:
        assert payload["parent_session_id"] == "parent-1"
        assert payload["child_session_id"] == "child-1"
