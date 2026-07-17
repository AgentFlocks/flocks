from unittest.mock import AsyncMock, patch

import pytest

from flocks.project.project import (
    Project,
    ProjectDeletionError,
    ProjectInfo,
    ProjectNameConflictError,
    ProjectTime,
)
from flocks.storage.storage import Storage


@pytest.mark.asyncio
async def test_list_projects_reads_project_storage_entries():
    project = ProjectInfo(
        id="prj_labs",
        worktree="/tmp/flocks",
        name="Labs",
        time=ProjectTime(created=1, updated=2),
    )

    with patch.object(
        Storage,
        "list_entries",
        new=AsyncMock(return_value=[("project/prj_labs", project)]),
    ) as list_entries:
        result = await Project.list()

    assert result == [project]
    list_entries.assert_awaited_once_with(prefix="project/", model=ProjectInfo)


@pytest.mark.asyncio
async def test_create_rejects_duplicate_name_in_same_worktree():
    existing = ProjectInfo(
        id="project-existing",
        worktree="/tmp/workspace",
        name="Test",
        time=ProjectTime(created=1, updated=1),
    )

    with patch.object(
        Storage,
        "list_entries",
        new=AsyncMock(return_value=[("project/project-existing", existing)]),
    ), patch.object(Storage, "write", new=AsyncMock()) as write:
        with pytest.raises(ProjectNameConflictError):
            await Project.create(name=" test ", worktree="/tmp/workspace")

    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_allows_same_name_for_current_project():
    existing = ProjectInfo(
        id="project-existing",
        worktree="/tmp/workspace",
        name="Test",
        time=ProjectTime(created=1, updated=1),
    )

    with patch.object(Project, "get", new=AsyncMock(return_value=existing)), patch.object(
        Storage,
        "list_entries",
        new=AsyncMock(return_value=[("project/project-existing", existing)]),
    ), patch.object(Storage, "write", new=AsyncMock()) as write:
        updated = await Project.update("project-existing", name=" Test ")

    assert updated.name == "Test"
    write.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_empty_user_managed_project():
    project = ProjectInfo(
        id="prj_labs",
        worktree="/tmp/workspace",
        name="Labs",
        time=ProjectTime(created=1, updated=1),
    )

    with patch.object(Project, "get", new=AsyncMock(return_value=project)), patch.object(
        Storage,
        "list_entries",
        new=AsyncMock(return_value=[]),
    ), patch.object(Storage, "delete", new=AsyncMock(return_value=True)) as delete:
        result = await Project.delete(project.id)

    assert result is True
    delete.assert_awaited_once_with("project/prj_labs")


@pytest.mark.asyncio
async def test_delete_rejects_default_project():
    project = ProjectInfo(
        id="project-default",
        worktree="/tmp/workspace",
        name=None,
        time=ProjectTime(created=1, updated=1),
    )

    with patch.object(Project, "get", new=AsyncMock(return_value=project)):
        with pytest.raises(ProjectDeletionError):
            await Project.delete(project.id)
