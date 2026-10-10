from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from flocks.server.routes import project


def _entries():
    return [
        SimpleNamespace(id="normal", scope="workbench"),
        SimpleNamespace(id="legacy", scope=None),
        SimpleNamespace(id="ext-a", scope="extension-a"),
        SimpleNamespace(id="ext-b", scope="extension-b"),
    ]


@pytest.mark.asyncio
async def test_workbench_lists_only_its_own_projects(monkeypatch):
    monkeypatch.setattr(project, "require_user", lambda request: SimpleNamespace(id="owner"))
    monkeypatch.setattr(project, "_list_project_summaries", AsyncMock(return_value=_entries()))

    listed = await project.list_projects(None, search=None, scope="workbench")

    assert [p.id for p in listed] == ["normal", "legacy"]


@pytest.mark.asyncio
async def test_extension_scope_sees_workbench_folders_but_not_other_extensions(monkeypatch):
    monkeypatch.setattr(project, "require_user", lambda request: SimpleNamespace(id="owner"))
    monkeypatch.setattr(project, "_list_project_summaries", AsyncMock(return_value=_entries()))

    listed = await project.list_projects(None, search=None, scope="extension-a")

    assert [p.id for p in listed] == ["normal", "legacy", "ext-a"]


@pytest.mark.parametrize("scope", ["", "Upper", "has space", "-leading", "x" * 65])
def test_create_request_rejects_malformed_scope(scope):
    with pytest.raises(ValidationError):
        project.ProjectCreateRequest(worktree="/tmp/demo", scope=scope)


@pytest.mark.asyncio
async def test_create_conflict_across_scopes_is_rejected(monkeypatch):
    existing = SimpleNamespace(scope="workbench", name="Demo")
    monkeypatch.setattr(project, "require_user", lambda request: SimpleNamespace(id="owner"))
    monkeypatch.setattr(
        project.Project,
        "create",
        AsyncMock(side_effect=project.ProjectPathConflictError(existing)),
    )

    with pytest.raises(HTTPException) as caught:
        await project.create_project(None, project.ProjectCreateRequest(worktree="/tmp/demo", scope="extension-a"))

    assert caught.value.status_code == 409
