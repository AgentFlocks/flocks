import asyncio
import json

import pytest
from typer.testing import CliRunner

import flocks.cli.commands.export as export_cmd
import flocks.cli.commands.import_ as import_cmd
import flocks.mcp.server as mcp_server
from flocks.session.message import Message, MessageWithParts, TextPart, UserMessageInfo
from flocks.session.session import SessionInfo, SessionTime
from flocks.storage.storage import Storage

if not hasattr(mcp_server, "get_manager"):
    mcp_server.get_manager = lambda: None

import httpx

from flocks.server.client import FlocksClient

runner = CliRunner()


async def _noop_storage_init() -> None:
    return None


def _build_session(project_id: str = "proj_source") -> SessionInfo:
    return SessionInfo(
        id="ses_export_test",
        projectID=project_id,
        directory="/tmp/flocks-project",
        title="Export Session",
        time=SessionTime(created=1, updated=2),
    )


def _build_message_with_parts(session_id: str) -> MessageWithParts:
    message = UserMessageInfo(
        id="msg_export_test",
        sessionID=session_id,
        role="user",
        time={"created": 1},
        agent="rex",
        model={"providerID": "anthropic", "modelID": "claude-sonnet"},
    )
    part = TextPart(
        id="part_export_test",
        sessionID=session_id,
        messageID=message.id,
        text="hello export",
    )
    return MessageWithParts(info=message, parts=[part])


def test_export_and_import_round_trip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(export_cmd.Storage, "init", _noop_storage_init)
    monkeypatch.setattr(import_cmd.Storage, "init", _noop_storage_init)

    session = _build_session()
    message = _build_message_with_parts(session.id)
    output_path = tmp_path / "session-export.json"

    async def fake_get_by_id(session_id: str):
        assert session_id == session.id
        return session

    async def fake_list_with_parts(session_id: str, include_archived: bool = False):
        assert session_id == session.id
        assert include_archived is False
        return [message]

    stored_entries: list[tuple[str, object, str | None]] = []

    async def fake_mutate_many(*, set_entries=(), transaction_hook=None, **kwargs):
        assert transaction_hook is not None
        stored_entries.extend(set_entries)

    monkeypatch.setattr(export_cmd.Session, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(export_cmd.Message, "list_with_parts", fake_list_with_parts)
    monkeypatch.setattr(import_cmd.Storage, "mutate_many", fake_mutate_many)

    async def fake_get(key: str, default=None):
        return default  # nothing imported yet: no conflict

    monkeypatch.setattr(import_cmd.Storage, "get", fake_get)

    export_result = runner.invoke(
        export_cmd.export_app,
        ["-o", str(output_path), "--no-pretty", session.id],
    )

    assert export_result.exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["info"]["id"] == session.id
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["info"]["id"] == message.info.id
    assert payload["messages"][0]["parts"][0]["id"] == "part_export_test"
    assert payload["messages"][0]["parts"][0]["text"] == "hello export"

    import_result = runner.invoke(
        import_cmd.import_app,
        ["-p", "proj_imported", str(output_path)],
    )

    assert import_result.exit_code == 0

    stored = {key: (value, category) for key, value, category in stored_entries}
    assert stored["session:proj_imported:ses_export_test"][0]["projectID"] == "proj_imported"
    assert stored["message:ses_export_test"][0][0]["id"] == "msg_export_test"
    assert "message_parts:ses_export_test" not in stored
    assert stored["message_parts:ses_export_test:msg_export_test"][0][0]["text"] == "hello export"
    assert stored["message_parts:ses_export_test:msg_export_test"][1] == "message_part"


@pytest.mark.asyncio
async def test_import_writes_messages_in_runtime_storage_format(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))

    session = _build_session()
    message = _build_message_with_parts(session.id)
    payload = {
        "info": session.model_dump(by_alias=True),
        "messages": [
            {
                "info": message.info.model_dump(by_alias=True),
                "parts": [part.model_dump() for part in message.parts],
            }
        ],
    }
    input_path = tmp_path / "session-import.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    # force=True: the shared Storage singleton may still hold this session id from an
    # earlier test in the same process, and this test is about part normalization.
    await import_cmd._import_session(str(input_path), "proj_imported", force=True)

    Message.invalidate_cache(session.id)
    imported = await Message.list_with_parts(session.id)

    assert len(imported) == 1
    assert imported[0].info.id == "msg_export_test"
    assert len(imported[0].parts) == 1
    assert imported[0].parts[0].id == "part_export_test"
    assert imported[0].parts[0].text == "hello export"
    assert await Storage.get(f"message_parts:{session.id}") is None
    stored_parts = await Storage.get(f"message_parts:{session.id}:msg_export_test")
    assert stored_parts[0]["text"] == "hello export"
    recent, *_ = await Message.list_recent_with_parts(session.id)
    assert [item.info.id for item in recent] == [message.info.id]
    assert recent[0].parts[0].text == "hello export"

    # A deliberate replacement must invalidate an already warm message cache.
    payload["messages"][0]["parts"][0]["text"] = "updated import"
    await asyncio.to_thread(input_path.write_text, json.dumps(payload), encoding="utf-8")
    await import_cmd._import_session(str(input_path), "proj_imported", force=True)
    assert (await Message.list_with_parts(session.id))[0].parts[0].text == "updated import"


@pytest.mark.asyncio
async def test_import_deleted_session_refuses_without_partial_documents(tmp_path) -> None:
    import typer
    from flocks.session.session import Session

    session = _build_session("proj_imported")
    message = _build_message_with_parts(session.id)
    payload = {
        "info": session.model_dump(by_alias=True),
        "messages": [{"info": message.info.model_dump(), "parts": [part.model_dump() for part in message.parts]}],
    }
    input_path = tmp_path / "deleted-session-import.json"
    await asyncio.to_thread(input_path.write_text, json.dumps(payload), encoding="utf-8")
    await import_cmd._import_session(str(input_path), "proj_imported")
    assert await Session.delete("proj_imported", session.id)
    with pytest.raises(typer.Exit):
        await import_cmd._import_session(str(input_path), "proj_imported", force=True)
    assert await Storage.get(f"session:proj_imported:{session.id}") is None
    assert await Storage.get(f"message:{session.id}") is None
    assert await Storage.get(f"message_parts:{session.id}:{message.info.id}") is None

    await import_cmd._import_session(str(input_path), "proj_imported", new_id=True)
    entries = await Storage.list_entries(prefix="session:proj_imported:")
    assert len(entries) == 1 and entries[0][1]["id"] != session.id


@pytest.mark.asyncio
async def test_import_normalizes_legacy_reasoning_parts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))

    session = _build_session()
    payload = {
        "info": session.model_dump(by_alias=True),
        "messages": [
            {
                "info": {
                    "id": "msg_user",
                    "sessionID": session.id,
                    "role": "user",
                    "time": {"created": 1},
                    "agent": "rex",
                    "model": {"providerID": "anthropic", "modelID": "claude-sonnet"},
                },
                "parts": [
                    {
                        "id": "part_user",
                        "sessionID": session.id,
                        "messageID": "msg_user",
                        "type": "text",
                        "text": "hello",
                    }
                ],
            },
            {
                "info": {
                    "id": "msg_assistant",
                    "sessionID": session.id,
                    "role": "assistant",
                    "time": {"created": 2, "updated": 3},
                    "parentID": "msg_user",
                    "modelID": "claude-sonnet",
                    "providerID": "anthropic",
                    "mode": "rex",
                    "agent": "rex",
                    "path": {"cwd": "./", "root": ""},
                    "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
                "parts": [
                    {
                        "id": "part_reasoning",
                        "sessionID": session.id,
                        "messageID": "msg_assistant",
                        "type": "reasoning",
                        "text": "thinking",
                        "metadata": {},
                    }
                ],
            },
        ],
    }
    input_path = tmp_path / "legacy-session-import.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    # force=True: the shared Storage singleton may still hold this session id from an
    # earlier test in the same process, and this test is about part normalization.
    await import_cmd._import_session(str(input_path), "proj_imported", force=True)

    Message.invalidate_cache(session.id)
    imported = await Message.list_with_parts(session.id, include_archived=True)

    assert len(imported) == 2
    assert imported[1].parts[0].type == "reasoning"
    assert imported[1].parts[0].text == "thinking"
    assert imported[1].parts[0].time.start == 2
    assert imported[1].parts[0].time.end == 3


def test_export_help_does_not_show_project_option() -> None:
    result = runner.invoke(export_cmd.export_app, ["--help"])

    assert result.exit_code == 0
    assert "--project" not in result.stdout


@pytest.mark.asyncio
async def test_session_client_messages_returns_info_and_parts() -> None:
    """The client reads a session's messages from the service, as the service returns them."""
    session = _build_session()
    message = _build_message_with_parts(session.id)
    seen: list[tuple[str, str | None]] = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("Authorization")))
        return httpx.Response(200, json=[{
            "info": message.info.model_dump(by_alias=True, mode="json"),
            "parts": [part.model_dump(by_alias=True, mode="json") for part in message.parts],
        }])

    client = FlocksClient("http://127.0.0.1:8000", api_token="tok", transport=httpx.MockTransport(serve))
    try:
        result = await client.session.messages(session.id, "/tmp/flocks-project")
    finally:
        await client.close()

    assert seen == [(f"/api/session/{session.id}/message", "Bearer tok")]
    assert len(result) == 1
    assert result[0]["info"]["id"] == message.info.id
    assert result[0]["parts"][0]["id"] == "part_export_test"
    assert result[0]["parts"][0]["text"] == "hello export"
