from pathlib import Path

import pytest
from fastapi import status
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.storage.storage import Storage


@pytest.mark.asyncio
async def test_project_crud_uses_registry_without_project_database_rows(
    client: AsyncClient,
    tmp_path: Path,
):
    worktree = tmp_path / "labs"
    worktree.mkdir()

    create_response = await client.post(
        "/api/project",
        json={"name": "Labs", "worktree": str(worktree)},
    )
    assert create_response.status_code == status.HTTP_200_OK
    project = create_response.json()
    assert project["id"].startswith("prj_")
    assert await Storage.list_keys(prefix="project/") == []

    list_response = await client.get("/api/project")
    assert list_response.status_code == status.HTTP_200_OK
    projects = list_response.json()
    assert projects[0]["id"] == "default"
    assert projects[0]["name"] == "默认"
    assert projects[0]["isDefault"] is True
    assert [item["id"] for item in projects[1:]] == [project["id"]]

    rename_response = await client.patch(
        f"/api/project/{project['id']}",
        json={"name": "Security Labs"},
    )
    assert rename_response.status_code == status.HTTP_200_OK
    assert rename_response.json()["name"] == "Security Labs"

    delete_response = await client.delete(f"/api/project/{project['id']}")
    assert delete_response.status_code == status.HTTP_200_OK
    assert worktree.exists()
    assert [item["id"] for item in (await client.get("/api/project")).json()] == ["default"]


@pytest.mark.asyncio
async def test_create_project_creates_missing_directory(
    client: AsyncClient,
    tmp_path: Path,
):
    worktree = tmp_path / "workspace" / "created-by-project"

    response = await client.post(
        "/api/project",
        json={"name": "Created Project", "worktree": str(worktree)},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["worktree"] == str(worktree.resolve())
    assert worktree.is_dir()


@pytest.mark.asyncio
async def test_duplicate_project_directory_returns_existing_project(
    client: AsyncClient,
    tmp_path: Path,
):
    worktree = tmp_path / "same"
    worktree.mkdir()

    first = await client.post(
        "/api/project",
        json={"name": "First", "worktree": str(worktree)},
    )
    second = await client.post(
        "/api/project",
        json={"name": "Second", "worktree": str(worktree / ".")},
    )

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert second.json()["id"] == first.json()["id"]
    assert len((await client.get("/api/project")).json()) == 2


@pytest.mark.asyncio
async def test_folder_browser_lists_directories_and_blocks_outside_root(
    client: AsyncClient,
    tmp_path: Path,
):
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)

    response = await client.get("/api/project/folders", params={"path": str(parent)})

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["path"] == str(parent.resolve())
    assert {entry["name"] for entry in payload["entries"]} == {"child"}

    outside = await client.get("/api/project/folders", params={"path": str(Path.home())})
    assert outside.status_code == status.HTTP_403_FORBIDDEN

    escape_link = parent / "escape"
    escape_link.symlink_to(Path.home(), target_is_directory=True)
    escaped_listing = await client.get(
        "/api/project/folders",
        params={"path": str(escape_link)},
    )
    assert escaped_listing.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_project_counts_and_session_lists_are_user_scoped(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import project as project_routes
    from flocks.server.routes import session as session_routes

    alice = AuthUser(id="usr_alice", username="alice", role="member", status="active")
    bob = AuthUser(id="usr_bob", username="bob", role="member", status="active")

    monkeypatch.setattr(session_routes, "require_user", lambda _request: alice)
    alice_session = await client.post("/api/session", json={"title": "Alice session"})
    assert alice_session.status_code == status.HTTP_200_OK

    monkeypatch.setattr(session_routes, "require_user", lambda _request: bob)
    bob_session = await client.post("/api/session", json={"title": "Bob session"})
    assert bob_session.status_code == status.HTTP_200_OK

    monkeypatch.setattr(session_routes, "require_user", lambda _request: alice)
    monkeypatch.setattr(project_routes, "require_user", lambda _request: alice)

    projects = (await client.get("/api/project")).json()
    assert projects[0]["id"] == "default"
    assert projects[0]["sessionCount"] == 1

    second_alice_session = await client.post(
        "/api/session",
        json={"title": "Second Alice session"},
    )
    assert second_alice_session.status_code == status.HTTP_200_OK
    refreshed_projects = (await client.get("/api/project")).json()
    assert refreshed_projects[0]["sessionCount"] == 2

    sessions = (
        await client.get(
            "/api/session",
            params={"view": "list", "manager": "true", "roots": "true"},
        )
    ).json()
    assert {item["title"] for item in sessions} == {
        "Alice session",
        "Second Alice session",
    }
