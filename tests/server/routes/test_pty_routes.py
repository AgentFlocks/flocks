from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from flocks.hooks.execution import current_execution_context
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.identity import Subject, get_current_subject
from flocks.pty.pty import CreateInput, Pty, PtyInfo, PtyStatus
from flocks.server.routes import pty as pty_routes


class _FakeWebSocket:
    def __init__(self) -> None:
        self.close_code = None
        self.close_reason = None
        self.accepted = False
        self.state = SimpleNamespace()

    async def close(self, code: int, reason: str = "") -> None:
        self.close_code = code
        self.close_reason = reason

    async def accept(self) -> None:
        self.accepted = True


@pytest.fixture(autouse=True)
def _reset_pipeline() -> None:
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield
    HookPipeline.reset()


@pytest.mark.asyncio
async def test_pty_websocket_authenticates_before_session_lookup(monkeypatch: pytest.MonkeyPatch):
    websocket = _FakeWebSocket()

    async def _reject(_websocket):
        raise HTTPException(status_code=401, detail="missing auth")

    get_session = Mock()
    monkeypatch.setattr(pty_routes, "apply_auth_for_request", _reject)
    monkeypatch.setattr(pty_routes.Pty, "get", get_session)

    await pty_routes.connect_session(websocket, "pty_missing")

    assert websocket.close_code == 4401
    assert websocket.close_reason == "missing auth"
    assert websocket.accepted is False
    get_session.assert_not_called()


@pytest.mark.asyncio
async def test_public_pty_create_uses_neutral_action_lifecycle_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, dict]] = []

    class _Recorder(HookBase):
        async def action_before(self, ctx) -> None:
            observed.append((ctx.stage, dict(ctx.input)))

        async def action_after(self, ctx) -> None:
            observed.append((ctx.stage, dict(ctx.input)))

    HookPipeline.register("pty-lifecycle-recorder", _Recorder())
    input_data = CreateInput(
        command="/usr/local/bin/custom-shell",
        args=["--custom-interactive-flag"],
        cwd="/tmp/pty-workspace",
        env={"SHELL_STARTUP_FILE": "/tmp/startup"},
    )
    created = PtyInfo(
        id="pty_created",
        title="Terminal",
        command=input_data.command or "",
        args=input_data.args or [],
        cwd=input_data.cwd or "",
        status=PtyStatus.RUNNING,
        pid=123,
    )
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(Pty, "_create", create)

    assert (await Pty.create(input_data)).id == "pty_created"

    assert [stage for stage, _ in observed] == ["action.before", "action.after"]
    before = observed[0][1]
    after = observed[1][1]
    assert before["action"] == "pty.open"
    assert before["resource"] == {"type": "pty"}
    assert before["action_input"] == input_data.model_dump()
    assert "tool" not in before
    assert after["action"] == "pty.open"
    assert after["resource"] == {"type": "pty"}
    assert after["action_input"] == input_data.model_dump()
    assert after["outcome"] == "success"
    create.assert_awaited_once_with(input_data)


@pytest.mark.asyncio
async def test_public_pty_write_uses_neutral_action_lifecycle_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, dict]] = []

    class _Recorder(HookBase):
        async def action_before(self, ctx) -> None:
            observed.append((ctx.stage, dict(ctx.input)))

        async def action_after(self, ctx) -> None:
            observed.append((ctx.stage, dict(ctx.input)))

    HookPipeline.register("pty-lifecycle-recorder", _Recorder())
    write = Mock()
    monkeypatch.setattr(Pty, "_write", write)

    await Pty.write("pty_123", "first raw input")

    assert [stage for stage, _ in observed] == ["action.before", "action.after"]
    before = observed[0][1]
    after = observed[1][1]
    assert before["action"] == "pty.input"
    assert before["resource"] == {"type": "pty", "id": "pty_123"}
    assert before["action_input"] == {"data": "first raw input"}
    assert "tool" not in before
    assert after["action"] == "pty.input"
    assert after["resource"] == {"type": "pty", "id": "pty_123"}
    assert after["action_input"] == {"data": "first raw input"}
    assert after["outcome"] == "success"
    write.assert_called_once_with("pty_123", "first raw input")


@pytest.mark.asyncio
async def test_pty_websocket_keeps_authenticated_context_for_full_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str | None, dict]] = []

    class _InputWebSocket(_FakeWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self._messages = iter(["raw terminal input"])

        async def receive_text(self) -> str:
            try:
                return next(self._messages)
            except StopIteration as exc:
                from fastapi import WebSocketDisconnect

                raise WebSocketDisconnect() from exc

    async def _authenticate(websocket: _FakeWebSocket):
        websocket.state.subject = Subject(
            subject_id="websocket-user",
            subject_type="human",
        )
        websocket.state.extension_context = {"opaque_transfer": "verified"}
        return False, object(), None

    async def _on_message(_data: str) -> None:
        subject = get_current_subject()
        observed.append(
            (
                subject.subject_id if subject is not None else None,
                current_execution_context(),
            )
        )

    websocket = _InputWebSocket()
    monkeypatch.setattr(pty_routes, "apply_auth_for_request", _authenticate)
    monkeypatch.setattr(pty_routes, "clear_auth_context", Mock())
    monkeypatch.setattr(pty_routes.Pty, "get", Mock(return_value=object()))
    monkeypatch.setattr(
        pty_routes.Pty,
        "connect",
        AsyncMock(return_value={"onMessage": _on_message, "onClose": lambda: None}),
    )

    await pty_routes.connect_session(websocket, "pty_123")

    assert observed == [("websocket-user", {"opaque_transfer": "verified"})]
