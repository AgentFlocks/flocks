"""Regression tests for `flocks session / export / import` defects found in the 2026-09 full test pass.

Covers:
- ``session list --format`` rejects unknown formats; ``-n`` must be >= 1
- ``session list`` hides archived sessions; ``session restore`` refuses non-archived sessions
- ``flocks export <id> --no-pretty`` / ``-o`` (options after the positional) reach the callback
- ``export -o`` to an unwritable path reports a readable error (no traceback)
- the interactive export prompt goes to stderr so stdout stays a clean JSON stream
- ``flocks import <file> -p <project>`` (option after the positional) is accepted
- share URLs must match the whole string (``.../share/a b`` is rejected, no request made)
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import flocks.cli.commands.export as export_cmd
import flocks.cli.commands.import_ as import_cmd
import flocks.cli.commands.session as session_cmd
from flocks.session.session import SessionInfo, SessionTime

runner = CliRunner(mix_stderr=False)


async def _noop_storage_init() -> None:
    return None


def _session(session_id: str, *, archived: int | None = None, parent_id: str | None = None, updated: int = 2) -> SessionInfo:
    return SessionInfo(
        id=session_id,
        projectID="proj_cli",
        directory="/tmp/session-cli",
        title=f"Title {session_id}",
        parentID=parent_id,
        status="archived" if archived else "active",
        time=SessionTime(created=1, updated=updated, archived=archived),
    )


@pytest.fixture(autouse=True)
def _no_storage(monkeypatch):
    monkeypatch.setattr(session_cmd.Storage, "init", _noop_storage_init)
    monkeypatch.setattr(export_cmd.Storage, "init", _noop_storage_init)
    monkeypatch.setattr(import_cmd.Storage, "init", _noop_storage_init)


# --------------------------------------------------------------------------- session list
def _list_of(monkeypatch, sessions):
    async def fake_list_all():
        return list(sessions)

    monkeypatch.setattr(session_cmd.Session, "list_all", fake_list_all)


def test_session_list_rejects_unknown_format(monkeypatch) -> None:
    _list_of(monkeypatch, [_session("ses_a")])

    result = runner.invoke(session_cmd.session_app, ["list", "--format", "xml"])

    assert result.exit_code == 2
    assert "table" in result.stderr and "json" in result.stderr


@pytest.mark.parametrize("value", ["0", "-1"])
def test_session_list_rejects_non_positive_limit(monkeypatch, value) -> None:
    _list_of(monkeypatch, [_session("ses_a")])

    result = runner.invoke(session_cmd.session_app, ["list", "-n", value])

    assert result.exit_code == 2
    assert "x>=1" in result.stderr


def test_session_list_hides_archived_sessions(monkeypatch) -> None:
    _list_of(monkeypatch, [_session("ses_live", updated=5), _session("ses_gone", archived=123, updated=9)])

    result = runner.invoke(session_cmd.session_app, ["list"])

    assert result.exit_code == 0
    assert "ses_live" in result.stdout
    assert "ses_gone" not in result.stdout
    assert "1 session(s)" in result.stdout


def test_session_restore_refuses_active_session(monkeypatch) -> None:
    active = _session("ses_active")
    calls: list[str] = []

    async def fake_get_by_id(session_id: str):
        return active

    async def fake_restore(*_args, **_kwargs):
        calls.append("restore")
        return True

    monkeypatch.setattr(session_cmd.Session, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(session_cmd.Session, "restore", fake_restore)

    result = runner.invoke(session_cmd.session_app, ["restore", active.id])

    assert result.exit_code == 1
    assert "Failed to restore session (not found or not archived): ses_active" in result.stdout
    assert calls == []


# --------------------------------------------------------------------------- export
def _export_fixture(monkeypatch, sessions):
    async def fake_get_by_id(session_id: str):
        return next((s for s in sessions if s.id == session_id), None)

    async def fake_list_all():
        return list(sessions)

    async def fake_list_with_parts(session_id: str, include_archived: bool = False):
        return []

    monkeypatch.setattr(export_cmd.Session, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(export_cmd.Session, "list_all", fake_list_all)
    monkeypatch.setattr(export_cmd.Message, "list_with_parts", fake_list_with_parts)


def test_export_accepts_options_after_session_id(monkeypatch, tmp_path) -> None:
    session = _session("ses_export")
    _export_fixture(monkeypatch, [session])

    compact = runner.invoke(export_cmd.export_app, [session.id, "--no-pretty"])
    assert compact.exit_code == 0, compact.output
    assert compact.stdout.count("\n") == 1
    assert json.loads(compact.stdout)["info"]["id"] == session.id

    target = tmp_path / "out.json"
    to_file = runner.invoke(export_cmd.export_app, [session.id, "-o", str(target)])
    assert to_file.exit_code == 0, to_file.output
    assert json.loads(target.read_text(encoding="utf-8"))["info"]["id"] == session.id
    assert to_file.stdout == ""


def test_export_reports_unwritable_output_path(monkeypatch, tmp_path) -> None:
    session = _session("ses_export")
    _export_fixture(monkeypatch, [session])

    result = runner.invoke(export_cmd.export_app, [session.id, "-o", str(tmp_path / "missing" / "out.json")])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Failed to write export file" in result.stderr
    # Rich soft-wraps long paths at the runner's 80 columns
    assert str(tmp_path / "missing" / "out.json") in "".join(result.stderr.split())


def test_export_interactive_prompt_keeps_stdout_clean(monkeypatch) -> None:
    sessions = [_session("ses_new", updated=9), _session("ses_old", updated=1)]
    _export_fixture(monkeypatch, sessions)

    result = runner.invoke(export_cmd.export_app, [], input="1\n")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["info"]["id"] == "ses_new"
    assert "Enter number or session ID" in result.stderr


# --------------------------------------------------------------------------- import
def _fake_storage(monkeypatch, existing: dict[str, object] | None = None) -> dict[str, object]:
    stored: dict[str, object] = dict(existing or {})

    async def fake_mutate_many(*, set_entries=(), **kwargs):
        for key, value, category in set_entries:
            stored[key] = value

    async def fake_get(key: str, default=None):
        return stored.get(key, default)

    monkeypatch.setattr(import_cmd.Storage, "mutate_many", fake_mutate_many)
    monkeypatch.setattr(import_cmd.Storage, "get", fake_get)
    return stored


def _export_payload(session_id: str = "ses_imp", *, directory: str = "/Users/elsewhere/proj", title: str = "Imported") -> dict:
    return {
        "info": {"id": session_id, "title": title, "projectID": "proj_old", "directory": directory},
        "messages": [
            {"info": {"id": "msg_1", "sessionID": session_id, "role": "user"}, "parts": [{"type": "text", "text": "hi"}]},
        ],
    }


def test_import_accepts_project_option_after_file(monkeypatch, tmp_path) -> None:
    stored = _fake_storage(monkeypatch)
    payload = {"info": {"id": "ses_imp", "title": "Imported", "projectID": "proj_old", "directory": "/tmp/x"}, "messages": []}
    source = tmp_path / "s.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(import_cmd.import_app, [str(source), "-p", "proj_new"])

    assert result.exit_code == 0, result.output
    assert "session:proj_new:ses_imp" in stored


@pytest.mark.parametrize("url", ["http://opncd.ai/share/a b", "https://opncd.ai/share/abc/../x", "https://opncd.ai/share/abc?x=1"])
def test_import_rejects_share_urls_that_do_not_fully_match(monkeypatch, url) -> None:
    def _no_network(*_args, **_kwargs):
        raise AssertionError("no request must be made for an invalid share URL")

    monkeypatch.setattr("httpx.AsyncClient.get", _no_network, raising=True)

    result = runner.invoke(import_cmd.import_app, [url])

    assert result.exit_code == 1
    assert "Invalid URL format" in result.stdout


# --------------------------------------------------------------------------- import: conflicts, --new-id, directory relocation (E2E-0010)
def test_import_refuses_to_overwrite_existing_session_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    existing = {"session:proj_x:ses_imp": {"id": "ses_imp", "title": "Old title"}}
    stored = _fake_storage(monkeypatch, existing)
    source = tmp_path / "s.json"
    source.write_text(json.dumps(_export_payload(title="New title")), encoding="utf-8")

    result = runner.invoke(import_cmd.import_app, [str(source), "-p", "proj_x"])

    assert result.exit_code == 1, result.output
    assert "Session already exists: ses_imp" in result.stdout
    assert "--force" in result.stdout and "--new-id" in result.stdout
    assert stored["session:proj_x:ses_imp"]["title"] == "Old title", "existing session must not be touched"
    assert "message:ses_imp" not in stored


def test_import_force_overwrites_existing_session(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    stored = _fake_storage(monkeypatch, {"session:proj_x:ses_imp": {"id": "ses_imp", "title": "Old title"}})
    source = tmp_path / "s.json"
    source.write_text(json.dumps(_export_payload(title="New title")), encoding="utf-8")

    result = runner.invoke(import_cmd.import_app, [str(source), "-p", "proj_x", "--force"])

    assert result.exit_code == 0, result.output
    assert stored["session:proj_x:ses_imp"]["title"] == "New title"
    assert len(stored["message:ses_imp"]) == 1


def test_import_new_id_creates_a_fresh_session_and_rewrites_message_session_ids(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    stored = _fake_storage(monkeypatch, {"session:proj_x:ses_imp": {"id": "ses_imp", "title": "Old title"}})
    source = tmp_path / "s.json"
    source.write_text(json.dumps(_export_payload()), encoding="utf-8")

    result = runner.invoke(import_cmd.import_app, [str(source), "-p", "proj_x", "--new-id"])

    assert result.exit_code == 0, result.output
    new_keys = [k for k in stored if k.startswith("session:proj_x:") and k != "session:proj_x:ses_imp"]
    assert len(new_keys) == 1
    new_id = stored[new_keys[0]]["id"]
    assert new_id != "ses_imp" and new_id.startswith("ses")
    assert "Original ID: ses_imp" in result.stdout
    assert stored["session:proj_x:ses_imp"]["title"] == "Old title"
    assert stored[f"message:{new_id}"][0]["sessionID"] == new_id
    assert f"message_parts:{new_id}:msg_1" in stored


def test_import_relocates_directory_to_current_project_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    stored = _fake_storage(monkeypatch)
    source = tmp_path / "s.json"
    source.write_text(json.dumps(_export_payload(directory="/Users/elsewhere/proj")), encoding="utf-8")

    result = runner.invoke(import_cmd.import_app, [str(source), "-p", "proj_x"])

    assert result.exit_code == 0, result.output
    assert stored["session:proj_x:ses_imp"]["directory"] == str(tmp_path)
    assert "Directory:" in result.stdout and "(was /Users/elsewhere/proj)" in result.stdout


def test_import_keep_directory_preserves_source_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    stored = _fake_storage(monkeypatch)
    source = tmp_path / "s.json"
    source.write_text(json.dumps(_export_payload(directory="/Users/elsewhere/proj")), encoding="utf-8")

    result = runner.invoke(import_cmd.import_app, [str(source), "-p", "proj_x", "--keep-directory"])

    assert result.exit_code == 0, result.output
    assert stored["session:proj_x:ses_imp"]["directory"] == "/Users/elsewhere/proj"
    assert "(was " not in result.stdout
