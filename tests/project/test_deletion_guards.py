from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.project import lifecycle
from flocks.project.project import Project, ProjectDeletionError


@pytest.mark.asyncio
async def test_direct_project_delete_preserves_registration_when_work_is_active(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.setattr(lifecycle, "_deletion_guards", {})
    project = await Project.create(owner_id="owner", name="Guarded", worktree=str(tmp_path / "project"))

    async def active(project, owner):
        assert owner == "owner"
        raise ProjectDeletionError("active extension task")

    lifecycle.register_project_deletion_guard("test", active)
    with pytest.raises(ProjectDeletionError, match="active extension task"):
        await Project.delete(project.id, owner_id="owner")
    assert await Project.get(project.id, owner_id="owner") is not None
    lifecycle.unregister_project_deletion_guard("test")
    assert await Project.delete(project.id, owner_id="owner")


@pytest.mark.asyncio
async def test_project_guard_failure_is_fail_closed_and_registration_is_replaceable(monkeypatch):
    monkeypatch.setattr(lifecycle, "_deletion_guards", {})
    failing = AsyncMock(side_effect=RuntimeError("private exception detail"))
    lifecycle.register_project_deletion_guard("test", failing)
    with pytest.raises(ProjectDeletionError, match="extension is unavailable"):
        await lifecycle.require_project_deletion_allowed(SimpleNamespace(id="p"), "owner")
    replacement = AsyncMock()
    lifecycle.register_project_deletion_guard("test", replacement)
    await lifecycle.require_project_deletion_allowed(SimpleNamespace(id="p"), "owner")
    assert failing.await_count == 1 and replacement.await_count == 1
