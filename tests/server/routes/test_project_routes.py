from pathlib import Path

import pytest
from fastapi import status
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.project.project import Project
from flocks.session.session import Session
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
    assert [item["id"] for item in projects] == [project["id"]]
    assert all(item["isDefault"] is False for item in projects)

    rename_response = await client.patch(
        f"/api/project/{project['id']}",
        json={"name": "Security Labs"},
    )
    assert rename_response.status_code == status.HTTP_200_OK
    assert rename_response.json()["name"] == "Security Labs"

    retained_file = worktree / "keep.txt"
    retained_file.write_text("project files stay", encoding="utf-8")
    session_response = await client.post(
        "/api/session",
        json={"title": "Delete with project", "projectID": project["id"]},
    )
    assert session_response.status_code == status.HTTP_200_OK
    session_id = session_response.json()["id"]

    delete_response = await client.delete(f"/api/project/{project['id']}")
    assert delete_response.status_code == status.HTTP_200_OK
    assert worktree.exists()
    assert retained_file.read_text(encoding="utf-8") == "project files stay"
    assert (await client.get("/api/project")).json() == []
    assert (await client.get(f"/api/session/{session_id}")).status_code == status.HTTP_404_NOT_FOUND


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
    assert len((await client.get("/api/project")).json()) == 1


@pytest.mark.asyncio
async def test_folder_browser_starts_at_home_when_path_is_omitted(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    home = Path.home().resolve()
    monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(home))

    response = await client.get("/api/project/folders")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["path"] == str(home)


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
async def test_project_local_sharing_includes_existing_and_future_sessions(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import project as project_routes
    from flocks.server.routes import session as session_routes

    owner = AuthUser(id="usr_owner", username="owner", role="member", status="active")
    viewer = AuthUser(id="usr_viewer", username="viewer", role="member", status="active")
    worktree = tmp_path / "shared-project"
    worktree.mkdir()
    project = await Project.create(
        owner_id=owner.id,
        name="Shared Project",
        worktree=str(worktree),
    )
    existing = await Session.create(
        project_id=project.id,
        directory=str(worktree),
        title="Existing session",
        owner_user_id=owner.id,
        owner_username=owner.username,
    )

    monkeypatch.setattr(project_routes, "require_user", lambda _request: owner)
    share_response = await client.post(f"/api/project/{project.id}/share-local")

    assert share_response.status_code == status.HTTP_200_OK
    assert share_response.json()["isShared"] is True

    future = await Session.create(
        project_id=project.id,
        directory=str(worktree),
        title="Future session",
        owner_user_id=owner.id,
        owner_username=owner.username,
    )

    monkeypatch.setattr(project_routes, "require_user", lambda _request: viewer)
    project_list = (await client.get("/api/project")).json()
    shared_project = next(item for item in project_list if item["id"] == project.id)
    assert shared_project["isShared"] is True
    assert shared_project["canWrite"] is False
    assert shared_project["canDelete"] is False

    non_owner_share = await client.post(f"/api/project/{project.id}/share-local")
    assert non_owner_share.status_code == status.HTTP_404_NOT_FOUND

    monkeypatch.setattr(session_routes, "require_user", lambda _request: viewer)
    session_list = (
        await client.get(
            "/api/session",
            params={"view": "list", "manager": True, "projectID": project.id},
        )
    ).json()
    assert {item["id"] for item in session_list} == {existing.id, future.id}
    assert all(item["effectiveProjectID"] == project.id for item in session_list)
    assert all(item["isShared"] is True for item in session_list)
    assert all(item["canWrite"] is False for item in session_list)

    monkeypatch.setattr(project_routes, "require_user", lambda _request: owner)
    unshare_response = await client.post(f"/api/project/{project.id}/unshare-local")
    assert unshare_response.status_code == status.HTTP_200_OK
    assert unshare_response.json()["isShared"] is False

    monkeypatch.setattr(project_routes, "require_user", lambda _request: viewer)
    assert project.id not in {item["id"] for item in (await client.get("/api/project")).json()}
    monkeypatch.setattr(session_routes, "require_user", lambda _request: viewer)
    assert (
        await client.get(
            "/api/session",
            params={"view": "list", "manager": True, "projectID": project.id},
        )
    ).json() == []


@pytest.mark.asyncio
async def test_project_counts_and_session_lists_are_user_scoped(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from flocks.server.routes import project as project_routes
    from flocks.server.routes import session as session_routes

    alice = AuthUser(id="usr_alice", username="alice", role="member", status="active")
    bob = AuthUser(id="usr_bob", username="bob", role="member", status="active")

    alice_worktree = tmp_path / "alice-project"
    alice_worktree.mkdir()
    monkeypatch.setattr(project_routes, "require_user", lambda _request: alice)
    project_response = await client.post(
        "/api/project",
        json={"name": "Alice Project", "worktree": str(alice_worktree)},
    )
    alice_project = project_response.json()

    monkeypatch.setattr(session_routes, "require_user", lambda _request: alice)
    alice_session = await client.post(
        "/api/session",
        json={"title": "Alice session", "projectID": alice_project["id"]},
    )
    assert alice_session.status_code == status.HTTP_200_OK

    monkeypatch.setattr(session_routes, "require_user", lambda _request: bob)
    bob_session = await client.post("/api/session", json={"title": "Bob session"})
    assert bob_session.status_code == status.HTTP_200_OK

    monkeypatch.setattr(session_routes, "require_user", lambda _request: alice)
    projects = (await client.get("/api/project")).json()
    assert projects[0]["id"] == alice_project["id"]
    assert projects[0]["sessionCount"] == 1

    second_alice_session = await client.post(
        "/api/session",
        json={"title": "Second Alice session", "projectID": alice_project["id"]},
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


@pytest.mark.asyncio
async def test_session_title_update_invalidates_project_search_stats(
    client: AsyncClient,
    tmp_path: Path,
):
    worktree = tmp_path / "search-project"
    worktree.mkdir()
    project = (
        await client.post(
            "/api/project",
            json={"name": "Search Project", "worktree": str(worktree)},
        )
    ).json()
    created = await client.post(
        "/api/session",
        json={"title": "Initial title", "projectID": project["id"]},
    )
    assert created.status_code == status.HTTP_200_OK

    initial = await client.get("/api/project", params={"search": "triage"})
    assert initial.status_code == status.HTTP_200_OK
    assert initial.json()[0]["matchedSessionCount"] == 0

    updated = await client.patch(
        f"/api/session/{created.json()['id']}",
        json={"title": "Triage findings"},
    )
    assert updated.status_code == status.HTTP_200_OK

    refreshed = await client.get("/api/project", params={"search": "triage"})
    assert refreshed.status_code == status.HTTP_200_OK
    assert refreshed.json()[0]["matchedSessionCount"] == 1
