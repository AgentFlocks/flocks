from unittest.mock import AsyncMock

import pytest

from flocks.bus.bus import Bus
from flocks.session.message import Message
from flocks.session.session import Session, SessionInfo
from flocks.session.session_loop import LoopResult, SessionLoop


@pytest.mark.asyncio
async def test_run_uses_runtime_working_directory(monkeypatch: pytest.MonkeyPatch):
    session = SessionInfo(
        id="ses_runtime_directory",
        projectID="legacy-project",
        directory="/missing/original",
        title="Legacy session",
    )
    run_loop = AsyncMock(return_value=LoopResult(action="stop"))

    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=session))
    monkeypatch.setattr(Session, "touch", AsyncMock())
    monkeypatch.setattr(Message, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(SessionLoop, "_run_loop", run_loop)
    monkeypatch.setattr(
        "flocks.session.orphan_tools.abort_orphan_running_parts",
        AsyncMock(),
    )
    monkeypatch.setattr(Bus, "publish", AsyncMock())

    result = await SessionLoop.run(
        session.id,
        provider_id="test-provider",
        model_id="test-model",
        working_directory="/available/default",
    )

    assert result.action == "stop"
    loop_context = run_loop.await_args.args[0]
    assert loop_context.session.directory == "/available/default"
    assert loop_context.session_ctx.directory == "/available/default"
    assert session.directory == "/missing/original"
