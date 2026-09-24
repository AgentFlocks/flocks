from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.session.message import Message, ToolPart
from flocks.session.runner import SessionRunner
from flocks.session.tool_execution import build_session_tool_execution_payload


@pytest.fixture(autouse=True)
def reset_pipeline() -> None:
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield
    HookPipeline.reset()


@pytest.mark.asyncio
async def test_session_shell_uses_tool_execute_hook_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Direct shell execution shares the canonical tool lifecycle."""

    observed: list[dict] = []

    class CaptureAction(HookBase):
        async def tool_before(self, ctx):
            observed.append(ctx.input)

    process = SimpleNamespace(
        communicate=AsyncMock(return_value=(b"ok\n", b"")),
        returncode=0,
    )
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(
        "flocks.session.runner.Session.get_by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                directory=str(tmp_path),
                project_id="project_shell_hook",
            )
        ),
    )
    monkeypatch.setattr(
        "flocks.session.runner.asyncio.create_subprocess_shell",
        create_process,
    )
    HookPipeline.register("capture.action", CaptureAction())

    result = await SessionRunner.shell(
        session_id="ses_1",
        agent="build",
        command="echo ok",
    )

    assert observed[0]["operation"] == "tool.execute"
    assert observed[0]["tool_execution"]["tool"]["name"] == "shell"
    assert observed[0]["tool_execution"]["tool"]["validated_input"] == {
        "command": "echo ok",
        "workdir": str(tmp_path),
    }
    create_process.assert_awaited_once_with(
        "echo ok",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(tmp_path),
    )
    assert result["parts"][0]["state"]["output"] == "ok\n"


@pytest.mark.asyncio
async def test_session_shell_persists_completed_tool_and_publishes_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The HTTP result and reloaded message history share one terminal tool part."""

    process = SimpleNamespace(
        communicate=AsyncMock(return_value=(b"PERSISTED\n", b"")),
        returncode=0,
    )
    monkeypatch.setattr(
        "flocks.session.runner.Session.get_by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                directory=str(tmp_path),
                project_id="project_shell_persistence",
            )
        ),
    )
    monkeypatch.setattr(
        "flocks.session.runner.asyncio.create_subprocess_shell",
        AsyncMock(return_value=process),
    )
    publish_event = AsyncMock()

    result = await SessionRunner.shell(
        session_id="ses_shell_persistence",
        agent="rex",
        command="printf PERSISTED",
        event_publish_callback=publish_event,
    )

    assistant_id = result["info"]["id"]
    Message.invalidate_cache("ses_shell_persistence")
    restored = await Message.get_with_parts_lazy(
        "ses_shell_persistence",
        assistant_id,
    )

    assert restored is not None
    assert restored.info.finish == "stop"
    assert restored.info.time["completed"] >= restored.info.time["created"]
    tool_parts = [part for part in restored.parts if isinstance(part, ToolPart)]
    assert len(tool_parts) == 1
    tool_part = tool_parts[0]
    assert tool_part.tool == "bash"
    assert tool_part.state.status == "completed"
    assert tool_part.state.input == {
        "command": "printf PERSISTED",
        "workdir": str(tmp_path),
    }
    assert tool_part.state.output == "PERSISTED\n"
    assert tool_part.state.metadata["exitCode"] == 0
    assert result["parts"] == [tool_part.model_dump(mode="json", by_alias=True)]

    event_names = [call.args[0] for call in publish_event.await_args_list]
    assert event_names.count("message.updated") == 3
    assert event_names.count("message.part.updated") == 3
    final_part_event = publish_event.await_args_list[-2].args
    final_message_event = publish_event.await_args_list[-1].args
    assert final_part_event[0] == "message.part.updated"
    assert final_part_event[1]["part"]["state"]["status"] == "completed"
    assert final_message_event[0] == "message.updated"
    assert final_message_event[1]["info"]["finish"] == "stop"


@pytest.mark.asyncio
async def test_tool_execution_payload_falls_back_to_session_owner_subject() -> None:
    payload = await build_session_tool_execution_payload(
        session_id="ses_owner",
        message_id="msg_owner",
        agent="rex",
        tool_name="shell",
        tool_input={"command": "echo ok"},
        tool_schema={},
        tool_context_extra={
            "session_execution_profile": {
                "entry": "webui",
                "permission_mode": "require-confirm",
                "runtime_mode": "dev-mode",
                "owner_user_id": "usr_owner_1",
            }
        },
        validated_input={"command": "echo ok"},
    )

    actor_subject = payload["tool_execution"]["actor"]["subject"]
    assert actor_subject["id"] == "usr_owner_1"
    assert actor_subject["type"] == "human"
