from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from flocks.cli.session_runner import CLISessionRunner
from flocks.session.session import Session, SessionInfo, SessionTime


def _session(session_id: str, directory: Path, *, updated: int) -> SessionInfo:
    return SessionInfo(
        id=session_id,
        projectID="default",
        directory=str(directory),
        title=session_id,
        time=SessionTime(created=updated, updated=updated),
    )


@pytest.mark.asyncio
async def test_continue_uses_latest_session_from_current_worktree(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    session_a = _session("ses-a", project_a, updated=2_000)
    session_b = _session("ses-b", project_b, updated=1_000)

    async def fake_list(project_id: str):
        assert project_id == "default"
        return [session_a, session_b]

    monkeypatch.setattr(Session, "list", fake_list)
    runner = CLISessionRunner(
        console=Console(file=StringIO()),
        directory=project_b,
    )

    resumed = await runner._get_or_create_session(
        project_id="default",
        continue_session=True,
    )

    assert resumed.id == session_b.id
