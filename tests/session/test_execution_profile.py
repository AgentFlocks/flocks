"""Tests for session execution-profile path resolution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from flocks.session.execution_profile import profile_from_session
from flocks.workspace.manager import WorkspaceManager


@pytest.fixture(autouse=True)
def _isolated_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path):
    workspace_dir = tmp_path / "workspace"
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace_dir))
    WorkspaceManager._instance = None
    yield
    WorkspaceManager._instance = None


def test_execution_profile_separates_workspace_root_and_project_root() -> None:
    session = SimpleNamespace(
        id="session-1",
        project_id="project-1",
        directory="/projects/current",
        agent="rex",
        owner_username="alice",
        metadata={},
    )

    profile = profile_from_session(session)

    assert profile["workspace_dir"].endswith("/workspace")
    assert profile["project_root"] == "/projects/current"
