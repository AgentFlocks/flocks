from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.session.runner import SessionRunner


@pytest.fixture(autouse=True)
def reset_pipeline() -> None:
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield
    HookPipeline.reset()


@pytest.mark.asyncio
async def test_session_shell_runs_through_neutral_action_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Direct shell execution exposes facts to extensions without OSS policy."""

    observed: list[dict] = []

    class CaptureAction(HookBase):
        async def action_before(self, ctx):
            observed.append(ctx.input)

    process = SimpleNamespace(
        communicate=AsyncMock(return_value=(b"ok\n", b"")),
        returncode=0,
    )
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(
        "flocks.session.runner.Session.get_by_id",
        AsyncMock(return_value=SimpleNamespace(directory=str(tmp_path))),
    )
    monkeypatch.setattr(
        "flocks.session.runner.Message.create",
        AsyncMock(
            side_effect=[
                SimpleNamespace(id="msg_user"),
                SimpleNamespace(id="msg_assistant"),
            ]
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

    assert observed == [
        {
            "operation": "session.shell",
            "session_id": "ses_1",
            "agent": "build",
            "execution_domain": "execution_runtime",
            "resource": {"type": "command", "id": "session.shell"},
            "tool": {
                "name": "shell",
                "input": {"command": "echo ok", "workdir": str(tmp_path)},
            },
        }
    ]
    create_process.assert_awaited_once_with(
        "echo ok",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(tmp_path),
    )
    assert result["parts"][0]["state"]["output"] == "ok\n"
