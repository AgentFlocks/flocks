from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

import flocks.cli.commands.session as session_cmd
from flocks.session.session import SessionInfo, SessionTime

runner = CliRunner()


async def _noop_storage_init() -> None:
    return None


def _build_session(
    session_id: str = "ses_session_cli",
    project_id: str = "proj_cli",
    title: str = "CLI Session",
) -> SessionInfo:
    return SessionInfo(
        id=session_id,
        projectID=project_id,
        directory="/tmp/session-cli",
        title=title,
        time=SessionTime(created=1_000, updated=2_000),
    )


def test_session_list_uses_global_sessions(monkeypatch) -> None:
    session = _build_session()

    async def fake_list_all():
        return [session]

    monkeypatch.setattr(session_cmd.Storage, "init", _noop_storage_init)
    monkeypatch.setattr(session_cmd.Session, "list_all", fake_list_all)

    result = runner.invoke(session_cmd.session_app, ["list"])

    assert result.exit_code == 0
    assert session.id in result.stdout
    assert session.title in result.stdout


def test_session_show_uses_get_by_id_without_project(monkeypatch) -> None:
    session = _build_session()

    async def fake_get_by_id(session_id: str):
        assert session_id == session.id
        return session

    async def fake_get_message_count(session_id: str):
        assert session_id == session.id
        return 3

    monkeypatch.setattr(session_cmd.Storage, "init", _noop_storage_init)
    monkeypatch.setattr(session_cmd.Session, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(session_cmd.Session, "get_message_count", fake_get_message_count)

    result = runner.invoke(session_cmd.session_app, ["show", session.id])

    assert result.exit_code == 0
    assert f"Session: {session.id}" in result.stdout
    assert "Messages: 3" in result.stdout


@pytest.mark.asyncio
async def test_delete_session_resolves_project_from_session(monkeypatch) -> None:
    session = _build_session(project_id="proj_delete", title="Delete Me")

    async def fake_get_by_id(session_id: str):
        assert session_id == session.id
        return session

    async def fake_delete(project_id: str, session_id: str):
        assert project_id == session.project_id
        assert session_id == session.id
        return True

    monkeypatch.setattr(session_cmd.Storage, "init", _noop_storage_init)
    monkeypatch.setattr(session_cmd.Session, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(session_cmd.Session, "delete", fake_delete)

    await session_cmd._delete_session(session.id, None, force=True)


@pytest.mark.asyncio
async def test_delete_session_confirmation_calls_out_permanent_deletion(monkeypatch) -> None:
    session = _build_session(project_id="proj_delete_prompt", title="Delete Forever")
    prompts: list[str] = []

    async def fake_get_by_id(_session_id: str):
        return session

    def fake_confirm(prompt: str, *, default: bool):
        prompts.append(prompt)
        assert default is False
        return False

    monkeypatch.setattr(session_cmd.Storage, "init", _noop_storage_init)
    monkeypatch.setattr(session_cmd.Session, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(session_cmd.typer, "confirm", fake_confirm)

    await session_cmd._delete_session(session.id, None, force=False)

    assert prompts == [
        "Permanently delete session 'Delete Forever' and all of its messages and history?"
    ]


@pytest.mark.asyncio
async def test_archive_session_resolves_project_from_session(monkeypatch) -> None:
    session = _build_session(project_id="proj_archive")

    async def fake_get_by_id(session_id: str):
        assert session_id == session.id
        return session

    async def fake_archive(project_id: str, session_id: str):
        assert project_id == session.project_id
        assert session_id == session.id
        return True

    monkeypatch.setattr(session_cmd.Storage, "init", _noop_storage_init)
    monkeypatch.setattr(session_cmd.Session, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(session_cmd.Session, "archive", fake_archive)

    await session_cmd._archive_session(session.id, None)


@pytest.mark.asyncio
async def test_restore_session_resolves_project_from_session(monkeypatch) -> None:
    session = _build_session(project_id="proj_restore").model_copy(
        update={"owner_user_id": "usr_cli"}
    )

    async def fake_get_by_id(session_id: str):
        assert session_id == session.id
        return session

    async def fake_unarchive(project_id: str, session_id: str):
        assert project_id == session.project_id
        assert session_id == session.id
        return True

    monkeypatch.setattr(session_cmd.Storage, "init", _noop_storage_init)
    monkeypatch.setattr(session_cmd.Session, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(session_cmd.Session, "unarchive", fake_unarchive)
    restore_project = AsyncMock(return_value=None)
    monkeypatch.setattr(session_cmd.Project, "restore", restore_project)

    await session_cmd._restore_session(session.id, None)

    restore_project.assert_awaited_once_with(session.project_id, owner_id="usr_cli")
