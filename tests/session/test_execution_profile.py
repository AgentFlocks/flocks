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


@pytest.mark.asyncio
async def test_a_profile_update_keeps_a_metadata_update_that_landed_while_it_waited(tmp_path, monkeypatch) -> None:
    """The profile was merged into the metadata read before the lock and written back whole."""
    import asyncio

    from flocks.session.execution_profile import PROFILE_METADATA_KEY, upsert_session_execution_profile
    from flocks.session.session import Session

    session = await Session.create(project_id="p", directory=str(tmp_path), agent="rex")
    read = asyncio.Event()
    real_get_by_id = Session.get_by_id

    async def get_by_id(session_id, *args, **kwargs):
        found = await real_get_by_id(session_id, *args, **kwargs)
        read.set()
        return found

    monkeypatch.setattr(Session, "get_by_id", get_by_id)
    async with Session.lifecycle_lock(session.id):
        profile_update = asyncio.create_task(
            upsert_session_execution_profile(session.id, patch={"permission_mode": "default"}, source="test")
        )
        await asyncio.wait_for(read.wait(), timeout=10)
        # Another writer holding the lock: the knowledge base picker, contextFolders, ...
        await Session._update_locked(
            session.project_id, session.id, metadata={**(session.metadata or {}), "contextFolders": ["docs"]},
        )
    profile = await asyncio.wait_for(profile_update, timeout=10)

    stored = await Session.get(session.project_id, session.id)
    assert stored.metadata["contextFolders"] == ["docs"]
    assert profile is not None and stored.metadata[PROFILE_METADATA_KEY]["permission_mode"] == profile["permission_mode"]
