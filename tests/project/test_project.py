import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from flocks.project.project import (
    DEFAULT_PROJECT_ID,
    Project,
    ProjectDeletionError,
    ProjectNameConflictError,
    ProjectPathConflictError,
)
from flocks.storage.storage import Storage


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    root = tmp_path / ".flocks"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.setenv("FLOCKS_DEFAULT_PROJECT_DIR", str(workspace))
    return tmp_path


@pytest.mark.asyncio
async def test_list_projects_uses_json_registry_and_virtual_default(project_root):
    labs = project_root / "labs"
    labs.mkdir()
    created = await Project.create(owner_id="user-1", name="Labs", worktree=str(labs))

    with patch.object(Storage, "list_entries", new=AsyncMock()) as list_entries:
        projects = await Project.list(owner_id="user-1")

    assert [project.id for project in projects] == [DEFAULT_PROJECT_ID, created.id]
    assert projects[0].name == "默认"
    assert projects[0].is_default is True
    assert projects[1].worktree == str(labs.resolve())
    list_entries.assert_not_awaited()

    registry = json.loads(Project.registry_path("user-1").read_text(encoding="utf-8"))
    assert registry["defaultWorktree"] == str((project_root / "workspace").resolve())
    assert registry["projects"][0]["id"].startswith("prj_")
    uuid.UUID(registry["projects"][0]["id"].removeprefix("prj_"))


@pytest.mark.asyncio
async def test_create_rejects_duplicate_canonical_directory(project_root):
    labs = project_root / "labs"
    labs.mkdir()
    existing = await Project.create(owner_id="user-1", name="Labs", worktree=str(labs))

    with pytest.raises(ProjectPathConflictError) as exc_info:
        await Project.create(owner_id="user-1", name="Other", worktree=str(labs / "."))

    assert exc_info.value.project.id == existing.id
    assert len((await Project.list(owner_id="user-1"))) == 2


@pytest.mark.asyncio
async def test_create_rejects_duplicate_name_across_directories(project_root):
    first = project_root / "first"
    second = project_root / "second"
    first.mkdir()
    second.mkdir()
    await Project.create(owner_id="user-1", name="Labs", worktree=str(first))

    with pytest.raises(ProjectNameConflictError):
        await Project.create(owner_id="user-1", name=" labs ", worktree=str(second))


@pytest.mark.asyncio
async def test_create_project_creates_a_missing_worktree(project_root):
    worktree = project_root / "new" / "nested-project"

    project = await Project.create(
        owner_id="user-1",
        name="Nested Project",
        worktree=str(worktree),
    )

    assert worktree.is_dir()
    assert project.worktree == str(worktree.resolve())


@pytest.mark.asyncio
async def test_create_project_does_not_create_outside_allowed_roots(project_root, monkeypatch):
    allowed_root = project_root / "allowed"
    allowed_root.mkdir()
    outside = project_root / "outside" / "project"
    monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(allowed_root))

    with pytest.raises(ValueError, match="outside the allowed roots"):
        await Project.create(
            owner_id="user-1",
            name="Outside",
            worktree=str(outside),
        )

    assert not outside.exists()


@pytest.mark.asyncio
async def test_update_renames_registered_project(project_root):
    labs = project_root / "labs"
    labs.mkdir()
    created = await Project.create(owner_id="user-1", name="Labs", worktree=str(labs))

    updated = await Project.update(created.id, owner_id="user-1", name=" Security ")

    assert updated.name == "Security"
    assert (await Project.get(created.id, owner_id="user-1")).name == "Security"


@pytest.mark.asyncio
async def test_delete_only_removes_registry_entry(project_root):
    labs = project_root / "labs"
    labs.mkdir()
    created = await Project.create(owner_id="user-1", name="Labs", worktree=str(labs))

    with patch.object(Storage, "delete", new=AsyncMock()) as storage_delete:
        result = await Project.delete(created.id, owner_id="user-1")

    assert result is True
    assert labs.exists()
    assert await Project.get(created.id, owner_id="user-1") is None
    storage_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_rejects_default_project(project_root):
    await Project.ensure_registry("user-1")

    with pytest.raises(ProjectDeletionError):
        await Project.delete(DEFAULT_PROJECT_ID, owner_id="user-1")


@pytest.mark.asyncio
async def test_effective_project_maps_legacy_and_removed_projects_to_default(project_root):
    labs = project_root / "labs"
    labs.mkdir()
    created = await Project.create(owner_id="user-1", name="Labs", worktree=str(labs))

    assert Project.effective_project_id("user-1", created.id) == created.id
    assert Project.effective_project_id("user-1", "old-git-project") == DEFAULT_PROJECT_ID

    await Project.delete(created.id, owner_id="user-1")
    assert Project.effective_project_id("user-1", created.id) == DEFAULT_PROJECT_ID


@pytest.mark.asyncio
async def test_from_directory_never_writes_project_storage(project_root):
    with patch.object(Storage, "write", new=AsyncMock()) as storage_write:
        result = await Project.from_directory(str(project_root))

    assert result["project"].id == DEFAULT_PROJECT_ID
    storage_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_project_creates_keep_every_registry_entry(project_root):
    worktrees = []
    for index in range(8):
        worktree = project_root / f"project-{index}"
        worktree.mkdir()
        worktrees.append(worktree)

    created = await asyncio.gather(*(
        Project.create(
            owner_id="user-1",
            name=f"Project {index}",
            worktree=str(worktree),
        )
        for index, worktree in enumerate(worktrees)
    ))

    projects = await Project.list(owner_id="user-1")
    assert {project.id for project in projects[1:]} == {project.id for project in created}


@pytest.mark.asyncio
async def test_registry_writes_do_not_create_backup(project_root):
    labs = project_root / "labs"
    labs.mkdir()
    created = await Project.create(owner_id="user-1", name="Labs", worktree=str(labs))
    await Project.update(created.id, owner_id="user-1", name="Renamed Labs")

    registry_path = Project.registry_path("user-1")
    assert registry_path.exists()
    assert not registry_path.with_suffix(".json.bak").exists()


@pytest.mark.asyncio
async def test_corrupt_registry_only_returns_default(project_root):
    await Project.ensure_registry("user-1")
    registry_path = Project.registry_path("user-1")
    registry_path.write_text("{broken", encoding="utf-8")

    projects = await Project.list(owner_id="user-1")

    assert [project.id for project in projects] == [DEFAULT_PROJECT_ID]
    assert registry_path.read_text(encoding="utf-8") == "{broken"
