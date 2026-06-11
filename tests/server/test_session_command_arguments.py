from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class TestSessionCommandArguments:
    @pytest.mark.asyncio
    async def test_command_route_preserves_arguments_json_metadata(self, monkeypatch):
        from flocks.server.routes import session as session_routes

        dispatch_mock = AsyncMock()
        session_id = "ses_json_args"

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

        payload = {"scope": "acp", "retry": 2}
        request = session_routes.CommandRequest(command="bug", argumentsJson=payload)

        resp = await session_routes.send_session_command(session_id, request)
        assert resp["status"] == "accepted"
        await asyncio.sleep(0)

        dispatch_mock.assert_awaited_once()
        event = dispatch_mock.await_args.args[2]
        assert event.text == f"/bug {json.dumps(payload, ensure_ascii=False)}"
        assert event.display_text == f"/bug {json.dumps(payload, ensure_ascii=False)}"
        assert event.metadata == {"commandArgumentsJson": payload}

    @pytest.mark.asyncio
    async def test_command_route_keeps_legacy_string_arguments_unchanged(self, monkeypatch):
        from flocks.server.routes import session as session_routes

        dispatch_mock = AsyncMock()
        session_id = "ses_string_args"

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

        request = session_routes.CommandRequest(command="bug", arguments="investigate routing")

        resp = await session_routes.send_session_command(session_id, request)
        assert resp["status"] == "accepted"
        await asyncio.sleep(0)

        dispatch_mock.assert_awaited_once()
        event = dispatch_mock.await_args.args[2]
        assert event.text == "/bug investigate routing"
        assert event.display_text == "/bug investigate routing"
        assert event.metadata == {}

    @pytest.mark.asyncio
    async def test_command_route_prefers_explicit_string_for_display_when_json_also_present(self, monkeypatch):
        from flocks.server.routes import session as session_routes

        dispatch_mock = AsyncMock()
        session_id = "ses_both_args"

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

        payload = {"scope": "acp"}
        request = session_routes.CommandRequest(
            command="bug",
            arguments="use this exact text",
            argumentsJson=payload,
        )

        resp = await session_routes.send_session_command(session_id, request)
        assert resp["status"] == "accepted"
        await asyncio.sleep(0)

        dispatch_mock.assert_awaited_once()
        event = dispatch_mock.await_args.args[2]
        assert event.text == "/bug use this exact text"
        assert event.display_text == "/bug use this exact text"
        assert event.metadata == {"commandArgumentsJson": payload}

    def test_build_prompt_request_from_event_attaches_metadata_to_text_part(self):
        from flocks.server.routes import session as session_routes
        from flocks.input.events import UserInputEvent

        event = UserInputEvent(
            source_type="webui",
            sessionID="ses_meta_parts",
            text='/bug {"scope":"acp"}',
            parts=[
                {"type": "text", "text": '/bug {"scope":"acp"}', "metadata": {"existing": True}},
                {"type": "file", "url": "file:///tmp/evidence.txt", "filename": "evidence.txt"},
            ],
            metadata={"commandArgumentsJson": {"scope": "acp"}},
            display_text='/bug {"scope":"acp"}',
        )

        request = session_routes._build_prompt_request_from_event(event, "/bug {\"scope\":\"acp\"}")

        assert request.parts[0]["type"] == "text"
        assert request.parts[0]["text"] == "/bug {\"scope\":\"acp\"}"
        assert request.parts[0]["metadata"] == {
            "existing": True,
            "commandArgumentsJson": {"scope": "acp"},
        }
        assert request.parts[1] == {
            "type": "file",
            "url": "file:///tmp/evidence.txt",
            "filename": "evidence.txt",
        }

    @pytest.mark.asyncio
    async def test_llm_command_path_passes_arguments_json_into_prompt_parts(self, monkeypatch):
        from flocks.server.routes import session as session_routes
        from flocks.input.events import UserInputEvent

        session_id = "ses_llm_metadata"
        event = UserInputEvent(
            source_type="webui",
            sessionID=session_id,
            text='/bug {"scope":"acp"}',
            parts=[{"type": "text", "text": '/bug {"scope":"acp"}'}],
            metadata={"commandArgumentsJson": {"scope": "acp"}},
            display_text='/bug {"scope":"acp"}',
        )
        session = SimpleNamespace(id=session_id, directory="/tmp/project")

        process_mock = AsyncMock()
        monkeypatch.setattr(session_routes, "_process_session_message", process_mock)

        await session_routes._dispatch_sse_input(session_id, session, event, "/tmp/project")

        process_mock.assert_awaited_once()
        request = process_mock.await_args.args[2]
        assert request.parts[0]["type"] == "text"
        assert request.parts[0]["text"] == '/bug {"scope":"acp"}'
        assert request.parts[0]["metadata"] == {
            "commandArgumentsJson": {"scope": "acp"},
        }
