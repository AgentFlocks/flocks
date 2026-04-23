from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.command.command import Command, CommandDef
from flocks.input.dispatcher import dispatch_user_input, parse_slash_command
from flocks.input.events import UserInputEvent
from flocks.input.output import CallbackOutputSink


class TestParseSlashCommand:
    def test_resolves_alias_to_canonical_name(self):
        parsed = parse_slash_command("/reset")
        assert parsed is not None
        assert parsed.command_name == "reset"
        assert parsed.canonical_name == "new"

    def test_removed_restart_command_no_longer_resolves(self):
        parsed = parse_slash_command("/restart")
        assert parsed is not None
        assert parsed.command_name == "restart"
        assert parsed.command_def is None


class TestDispatchUserInput:
    @pytest.mark.asyncio
    async def test_direct_command_uses_direct_response(self):
        direct = []
        llm = []
        sink = CallbackOutputSink(
            "webui",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append(llm, (prompt, display)),
        )
        event = UserInputEvent(
            source_type="webui",
            sessionID="ses_test",
            text="/help",
            parts=[{"type": "text", "text": "/help"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "direct"
        assert direct and "Available / commands:" in direct[0]
        assert not llm

    @pytest.mark.asyncio
    async def test_llm_command_routes_raw_slash_text(self):
        direct = []
        llm = []
        sink = CallbackOutputSink(
            "webui",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append(llm, (prompt, display)),
        )
        event = UserInputEvent(
            source_type="webui",
            sessionID="ses_test",
            text="/plan investigate routing",
            parts=[{"type": "text", "text": "/plan investigate routing"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "llm"
        assert llm == [("/plan investigate routing", "/plan investigate routing")]
        assert not direct

    @pytest.mark.asyncio
    async def test_removed_restart_command_falls_back_to_llm(self):
        direct = []
        llm = []
        sink = CallbackOutputSink(
            "webui",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append(llm, (prompt, display)),
        )
        event = UserInputEvent(
            source_type="webui",
            sessionID="ses_test",
            text="/restart",
            parts=[{"type": "text", "text": "/restart"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "llm"
        assert llm == [("/restart", "/restart")]
        assert not direct

    @pytest.mark.asyncio
    async def test_known_command_rejected_on_wrong_surface(self):
        direct = []
        sink = CallbackOutputSink(
            "webui",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append([], (prompt, display)),
        )
        event = UserInputEvent(
            source_type="webui",
            sessionID="ses_test",
            text="/model anthropic/claude-sonnet-4-5",
            parts=[{"type": "text", "text": "/model anthropic/claude-sonnet-4-5"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "rejected"
        assert "当前入口不可用" in direct[0]

    @pytest.mark.asyncio
    async def test_command_rejects_attachments_when_not_allowed(self):
        direct = []
        sink = CallbackOutputSink(
            "webui",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append([], (prompt, display)),
        )
        event = UserInputEvent(
            source_type="webui",
            sessionID="ses_test",
            text="/help",
            parts=[
                {"type": "text", "text": "/help"},
                {"type": "file", "url": "file:///tmp/demo.txt"},
            ],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "rejected"
        assert "不支持附件" in direct[0]

    @pytest.mark.asyncio
    async def test_channel_unsafe_command_is_rejected(self):
        Command.register(
            CommandDef(
                name="channel-unsafe-test",
                description="unsafe",
                template="unsafe",
                hidden=True,
                execution_kind="direct",
                allow_attachments=False,
                visible_surfaces=("channel",),
                channel_safe=False,
            )
        )
        direct = []
        sink = CallbackOutputSink(
            "channel",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append([], (prompt, display)),
        )
        event = UserInputEvent(
            source_type="wecom",
            sessionID="ses_test",
            text="/channel-unsafe-test",
            parts=[{"type": "text", "text": "/channel-unsafe-test"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "rejected"
        assert "不支持在渠道会话中执行" in direct[0]


class TestSessionRoutesUseDispatcher:
    @pytest.mark.asyncio
    async def test_prompt_async_routes_through_dispatcher(self, monkeypatch):
        from flocks.server.routes import session as session_routes

        dispatch_mock = AsyncMock()
        session_id = "ses_dispatcher"

        async def fake_provide(*, directory, init, fn):
            await fn()

        monkeypatch.setattr(session_routes, "_dispatch_sse_input", dispatch_mock)
        monkeypatch.setattr("flocks.project.instance.Instance.provide", fake_provide)
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(
                return_value=SimpleNamespace(
                    id=session_id,
                    directory="/tmp/project",
                )
            ),
        )
        request = session_routes.PromptRequest(
            parts=[{"type": "text", "text": "/plan investigate"}],
        )

        resp = await session_routes.send_session_message_async(session_id, request)
        assert resp["status"] == "accepted"
        await asyncio.sleep(0)
        dispatch_mock.assert_awaited_once()
        event = dispatch_mock.await_args.args[2]
        assert event.text == "/plan investigate"

    @pytest.mark.asyncio
    async def test_command_route_routes_through_dispatcher(self, monkeypatch):
        from flocks.server.routes import session as session_routes

        dispatch_mock = AsyncMock()
        session_id = "ses_dispatcher"

        async def fake_provide(*, directory, init, fn):
            await fn()

        monkeypatch.setattr(session_routes, "_dispatch_sse_input", dispatch_mock)
        monkeypatch.setattr("flocks.project.instance.Instance.provide", fake_provide)
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(
                return_value=SimpleNamespace(
                    id=session_id,
                    directory="/tmp/project",
                )
            ),
        )
        request = session_routes.CommandRequest(command="plan", arguments="investigate")

        resp = await session_routes.send_session_command(session_id, request)
        assert resp["status"] == "accepted"
        await asyncio.sleep(0)
        dispatch_mock.assert_awaited_once()
        event = dispatch_mock.await_args.args[2]
        assert event.text == "/plan investigate"
        assert event.display_text == "/plan investigate"


async def _append(target: list, value):
    target.append(value)
