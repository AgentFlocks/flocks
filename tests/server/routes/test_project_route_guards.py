"""Project route guards: blank rename -> 400, default project delete -> 409, creation with blank name uses the directory name."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import status
from httpx import AsyncClient

from flocks.project.project import DEFAULT_PROJECT_ID


async def test_rename_with_blank_name_is_a_client_error(client: AsyncClient, tmp_path: Path):
    worktree = tmp_path / "guard-labs"
    worktree.mkdir()
    created = await client.post("/api/project", json={"name": "Guard Labs", "worktree": str(worktree)})
    assert created.status_code == status.HTTP_200_OK, created.text
    project_id = created.json()["id"]

    for blank in ("", "   "):
        resp = await client.patch(f"/api/project/{project_id}", json={"name": blank})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, (blank, resp.text)
        assert "Project name cannot be empty" in resp.text

    still = await client.get(f"/api/project/{project_id}")
    assert still.status_code == status.HTTP_200_OK and still.json()["name"] == "Guard Labs"


async def test_default_project_cannot_be_deleted(client: AsyncClient):
    resp = await client.delete(f"/api/project/{DEFAULT_PROJECT_ID}")
    assert resp.status_code == status.HTTP_409_CONFLICT, resp.text
    assert "default project cannot be deleted" in resp.text


async def test_create_with_blank_name_falls_back_to_directory_name(client: AsyncClient, tmp_path: Path):
    worktree = tmp_path / "dir-name-project"
    worktree.mkdir()
    created = await client.post("/api/project", json={"name": "   ", "worktree": str(worktree)})
    assert created.status_code == status.HTTP_200_OK, created.text
    assert created.json()["name"] == "dir-name-project"
