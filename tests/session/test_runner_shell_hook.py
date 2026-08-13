from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.session.actions import run_session_shell
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
        "flocks.session.actions.Session.get_by_id",
        AsyncMock(return_value=SimpleNamespace(directory=str(tmp_path))),
    )
    monkeypatch.setattr(
        "flocks.session.actions.Message.create",
        AsyncMock(
            side_effect=[
                SimpleNamespace(id="msg_user"),
                SimpleNamespace(id="msg_assistant"),
            ]
        ),
    )
    monkeypatch.setattr(
        "flocks.session.actions.asyncio.create_subprocess_shell",
        create_process,
    )
    HookPipeline.register("capture.action", CaptureAction())

    result = await run_session_shell(
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
