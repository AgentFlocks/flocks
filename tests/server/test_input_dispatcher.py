from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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

    def test_reads_structured_arguments_from_metadata(self):
        parsed = parse_slash_command(
            '/bug {"scope":"acp"}',
            {"commandArgumentsJson": {"scope": "acp"}},
        )
        assert parsed is not None
        assert parsed.args == '{"scope":"acp"}'
        assert parsed.args_json == {"scope": "acp"}

    def test_removed_restart_command_no_longer_resolves(self):
        parsed = parse_slash_command("/restart")
        assert parsed is not None
        assert parsed.command_name == "restart"
        assert parsed.command_def is None

    def test_goal_command_resolves(self):
        parsed = parse_slash_command("/goal fix tests")
        assert parsed is not None
        assert parsed.command_name == "goal"
        assert parsed.canonical_name == "goal"
        assert parsed.args == "fix tests"
        assert parsed.command_def is not None
        assert parsed.command_def.execution_kind == "direct"


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
    async def test_clear_uses_history_callback_without_direct_response(self):
        direct = []
        llm = []
        clear_history_calls = []
        sink = CallbackOutputSink(
            "webui",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append(llm, (prompt, display)),
            clear_history=lambda: _append(clear_history_calls, "cleared"),
        )
        event = UserInputEvent(
            source_type="webui",
            sessionID="ses_test",
            text="/clear",
            parts=[{"type": "text", "text": "/clear"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "direct"
        assert clear_history_calls == ["cleared"]
        assert not direct
        assert not llm

    @pytest.mark.asyncio
    async def test_clear_is_allowed_on_channel_surface(self):
        direct = []
        llm = []
        clear_history_calls = []
        sink = CallbackOutputSink(
            "channel",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append(llm, (prompt, display)),
            clear_history=lambda: _append(clear_history_calls, "cleared"),
        )
        event = UserInputEvent(
            source_type="wecom",
            sessionID="ses_test",
            text="/clear",
            parts=[{"type": "text", "text": "/clear"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "direct"
        assert clear_history_calls == ["cleared"]
        assert not direct
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
            text="/bug investigate routing",
            parts=[{"type": "text", "text": "/bug investigate routing"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "llm"
        assert llm == [("/bug investigate routing", "/bug investigate routing")]
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
    async def test_goal_requires_existing_session(self):
        direct = []
        sink = CallbackOutputSink(
            "webui",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append([], (prompt, display)),
        )
        event = UserInputEvent(
            source_type="webui",
            text="/goal fix tests",
            parts=[{"type": "text", "text": "/goal fix tests"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "rejected"
        assert "需要先有一个会话" in direct[0]

    @pytest.mark.asyncio
    async def test_goal_set_runs_llm_without_direct_ack(self, monkeypatch):
        direct = []
        llm = []
        sink = CallbackOutputSink(
            "webui",
            direct_response=lambda _event, text: _append(direct, text),
            run_llm=lambda _event, prompt, display: _append(llm, (prompt, display)),
        )
        monkeypatch.setattr(
            "flocks.command.direct.GoalManager.set_goal",
            AsyncMock(return_value=SimpleNamespace(objective="fix tests", max_turns=20)),
        )
        monkeypatch.setattr(
            "flocks.command.direct.GoalManager.goal_prompt",
            MagicMock(return_value="goal prompt"),
        )
        event = UserInputEvent(
            source_type="webui",
            sessionID="ses_goal_dispatch",
            text="/goal fix tests",
            parts=[{"type": "text", "text": "/goal fix tests"}],
        )

        result = await dispatch_user_input(event, sink)

        assert result.action == "llm"
        assert direct == []
        assert llm == [("goal prompt", "/goal fix tests")]

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
    async def test_goal_mode_publishes_active_goal_before_llm(self, monkeypatch):
        from flocks.input.events import UserInputEvent
        from flocks.server.routes import session as session_routes

        order = []
        goal_state = SimpleNamespace(
            status="active",
            objective="fix tests",
            last_reason=None,
        )

        async def publish(event_type, _properties):
            if event_type == "session.goal.updated":
                order.append("goal")

        async def process(*_args, **_kwargs):
            order.append("llm")

        monkeypatch.setattr(
            "flocks.command.direct.GoalManager.set_goal",
            AsyncMock(return_value=goal_state),
        )
        monkeypatch.setattr(
            "flocks.command.direct.GoalManager.goal_prompt",
            MagicMock(return_value="goal prompt"),
        )
        monkeypatch.setattr(
            "flocks.session.goal.GoalManager.get",
            AsyncMock(return_value=goal_state),
        )
        monkeypatch.setattr(
            "flocks.server.routes.event.publish_event",
            publish,
        )
        monkeypatch.setattr(
            session_routes,
            "_process_session_message",
            process,
        )

        await session_routes._dispatch_sse_input(
            "ses_goal_mode",
            SimpleNamespace(id="ses_goal_mode"),
            UserInputEvent(
                source_type="webui",
                sessionID="ses_goal_mode",
                text="/goal fix tests",
                parts=[{"type": "text", "text": "fix tests"}],
                display_text="fix tests",
                executionMode="goal",
            ),
            "/tmp/project",
        )

        assert order == ["goal", "llm"]

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
            parts=[{"type": "text", "text": "/bug investigate"}],
        )

        resp = await session_routes.send_session_message_async(session_id, request)
        assert resp["status"] == "accepted"
        await asyncio.sleep(0)
        dispatch_mock.assert_awaited_once()
        event = dispatch_mock.await_args.args[2]
        assert event.text == "/bug investigate"

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
        request = session_routes.CommandRequest(command="bug", arguments="investigate")

        resp = await session_routes.send_session_command(session_id, request)
        assert resp["status"] == "accepted"
        await asyncio.sleep(0)
        dispatch_mock.assert_awaited_once()
        event = dispatch_mock.await_args.args[2]
        assert event.text == "/bug investigate"
        assert event.display_text == "/bug investigate"


class TestPromptQueueRoutes:
    def test_materialize_queued_data_url_returns_readable_file_uri(self, monkeypatch, tmp_path):
        from flocks.server.routes import session as session_routes
        from flocks.session.utils.file_extractor import read_file_part_bytes

        monkeypatch.setattr(
            "flocks.workspace.manager.WorkspaceManager.get_instance",
            lambda: (_ for _ in ()).throw(PermissionError("workspace access denied")),
        )
        monkeypatch.setattr(
            "flocks.config.config.Config.get_data_path",
            staticmethod(lambda: tmp_path),
        )
        data_url = "data:image/png;base64," + base64.b64encode(b"png-bytes").decode()

        url = session_routes._materialize_data_url_part(
            "ses_windows_uri",
            data_url,
            "image/png",
            "screenshot.png",
        )

        assert url.startswith("file://")
        assert read_file_part_bytes(url) == b"png-bytes"

    def test_session_upload_path_rejects_traversal(self, monkeypatch, tmp_path):
        from flocks.server.routes import session as session_routes

        monkeypatch.setattr(
            "flocks.config.config.Config.get_data_path",
            staticmethod(lambda: tmp_path),
        )

        with pytest.raises(ValueError, match="Invalid session ID"):
            session_routes._session_uploads_dir("../outside")

    @pytest.mark.asyncio
    async def test_prompt_async_queues_when_session_running_without_creating_message(self, monkeypatch):
        from flocks.server.routes import session as session_routes
        from flocks.session.interaction_queue import InteractionQueue

        session_id = "ses_prompt_queue_running"
        await InteractionQueue.clear(session_id)

        message_create = AsyncMock()
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(return_value=SimpleNamespace(id=session_id, directory="/tmp/project")),
        )
        monkeypatch.setattr("flocks.session.session_loop.SessionLoop.is_running", lambda _sid: True)
        monkeypatch.setattr("flocks.session.message.Message.create", message_create)
        monkeypatch.setattr(session_routes, "_publish_prompt_queue", AsyncMock())

        request = session_routes.PromptRequest(parts=[{"type": "text", "text": "second question"}])

        resp = await session_routes.send_session_message_async(session_id, request)

        assert resp["status"] == "queued"
        assert resp["queueID"]
        items = await InteractionQueue.list(session_id)
        assert len(items) == 1
        assert items[0].parts[0]["text"] == "second question"
        message_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_prompt_queue_rejects_when_full(self, monkeypatch):
        from fastapi import HTTPException

        from flocks.server.routes import session as session_routes
        from flocks.session.interaction_queue import InteractionQueue, MAX_QUEUE_SIZE

        session_id = "ses_prompt_queue_full"
        await InteractionQueue.clear(session_id)
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(return_value=SimpleNamespace(id=session_id, directory="/tmp/project")),
        )
        monkeypatch.setattr("flocks.session.session_loop.SessionLoop.is_running", lambda _sid: True)
        monkeypatch.setattr(session_routes, "_publish_prompt_queue", AsyncMock())

        for idx in range(MAX_QUEUE_SIZE):
            await InteractionQueue.enqueue(
                session_id,
                parts=[{"type": "text", "text": f"queued {idx}"}],
            )

        request = session_routes.PromptRequest(parts=[{"type": "text", "text": "overflow"}])
        with pytest.raises(HTTPException) as exc_info:
            await session_routes.send_session_message_async(session_id, request)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_run_now_aborts_and_schedules_drain(self, monkeypatch):
        from flocks.server.routes import session as session_routes
        from flocks.session.interaction_queue import InteractionQueue

        session_id = "ses_prompt_queue_run_now"
        await InteractionQueue.clear(session_id)
        item = await InteractionQueue.enqueue(
            session_id,
            parts=[{"type": "text", "text": "run this now"}],
        )

        abort_mock = AsyncMock(return_value=True)
        wait_mock = AsyncMock()
        drain_mock = AsyncMock()
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(return_value=SimpleNamespace(id=session_id, directory="/tmp/project")),
        )
        monkeypatch.setattr("flocks.session.session_loop.SessionLoop.is_running", lambda _sid: True)
        monkeypatch.setattr(session_routes, "abort_session", abort_mock)
        monkeypatch.setattr(session_routes, "_wait_for_session_idle", wait_mock)
        monkeypatch.setattr(session_routes, "_schedule_prompt_queue_drain", drain_mock)
        monkeypatch.setattr(session_routes, "_publish_prompt_queue", AsyncMock())

        resp = await session_routes.run_prompt_queue_item_now(session_id, item.id)

        assert resp["status"] == "accepted"
        abort_mock.assert_awaited_once_with(session_id)
        wait_mock.assert_awaited_once_with(session_id)
        drain_mock.assert_awaited_once_with(session_id, "/tmp/project")

    @pytest.mark.asyncio
    async def test_scheduled_drain_retries_until_session_idle(self, monkeypatch):
        from flocks.server.routes import session as session_routes
        from flocks.session.interaction_queue import InteractionQueue

        session_id = "ses_prompt_queue_retry"
        await InteractionQueue.clear(session_id)
        await InteractionQueue.enqueue(
            session_id,
            parts=[{"type": "text", "text": "run after idle"}],
        )

        running_states = [True, True, False]
        dispatch_mock = AsyncMock()
        original_sleep = asyncio.sleep

        async def fake_provide(*, directory, init, fn):
            await fn()

        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(return_value=SimpleNamespace(id=session_id, directory="/tmp/project")),
        )
        monkeypatch.setattr("flocks.project.instance.Instance.provide", fake_provide)
        monkeypatch.setattr(session_routes, "_dispatch_sse_input", dispatch_mock)
        monkeypatch.setattr(session_routes, "_publish_prompt_queue", AsyncMock())
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(
            "flocks.session.session_loop.SessionLoop.is_running",
            lambda _sid: running_states.pop(0) if running_states else False,
        )

        await session_routes._schedule_prompt_queue_drain(session_id, "/tmp/project")
        await original_sleep(0)
        await original_sleep(0)

        dispatch_mock.assert_awaited_once()
        assert await InteractionQueue.list(session_id) == []


async def _append(target: list, value):
    target.append(value)
