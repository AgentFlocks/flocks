"""Regressions for the multi-project session manager listing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.project.project import Project
from flocks.server.routes import session as session_routes
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session, SessionInfo


async def _create_project(client: AsyncClient, tmp_path, name: str) -> str:
    response = await client.post(
        "/api/project", json={"name": name, "worktree": str(tmp_path / name)}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _create_session(client: AsyncClient, title: str, **kwargs) -> str:
    response = await client.post("/api/session", json={"title": title, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_grouped_matches_single_project_pages_and_reads_sessions_once(
    client: AsyncClient, tmp_path, monkeypatch
):
    project_a = await _create_project(client, tmp_path, "project-a")
    project_b = await _create_project(client, tmp_path, "project-b")
    for i in range(4):
        await _create_session(client, f"A {i}", projectID=project_a)
    for i in range(3):
        await _create_session(client, f"B {i}", projectID=project_b)
    root = await _create_session(client, "Ungrouped")
    await _create_session(client, "Child", parentID=root)
    await _create_session(client, "Task", category="task")
    archived = await _create_session(client, "Archived")
    archive_response = await client.post(f"/api/session/{archived}/archive")
    assert archive_response.status_code == 200, archive_response.text

    groups = [
        {"projectID": project_a, "limit": 2, "offset": 1},
        {"projectID": project_b, "limit": 1, "offset": 0},
        {"projectID": "tasks", "limit": 10, "offset": 0},
        {"projectID": "default", "limit": 1, "offset": 0},
        {"projectID": project_a, "limit": 1, "offset": 0},
        {"projectID": "not-a-project", "limit": 2, "offset": 0},
    ]
    expected = []
    for group in groups:
        response = await client.get("/api/session", params={
            "view": "list", "manager": "true", "roots": "true", **group,
        })
        assert response.status_code == 200, response.text
        expected.append(response.json())

    original_list = Session.list_all_unfiltered
    reads = 0

    async def count_list(cls):
        nonlocal reads
        reads += 1
        return await original_list()

    monkeypatch.setattr(Session, "list_all_unfiltered", classmethod(count_list))
    response = await client.post("/api/session/list-grouped", json={"projects": groups})
    assert response.status_code == 200, response.text
    assert [item["projectID"] for item in response.json()["groups"]] == [g["projectID"] for g in groups]
    assert [item["items"] for item in response.json()["groups"]] == expected
    assert reads == 1
    assert [row["id"] for row in expected[2]] == [root]


@pytest.mark.asyncio
async def test_grouped_list_filters_access_hidden_children_and_status(client, monkeypatch):
    viewer = AuthUser(id="usr_viewer", username="viewer", role="member", status="active")
    monkeypatch.setattr(session_routes, "require_user", lambda request: viewer)
    monkeypatch.setattr(session_routes, "get_current_auth_user", lambda: viewer)
    monkeypatch.setattr(Project, "visible_project_ids", classmethod(lambda cls, owner_id: {"p1"}))
    monkeypatch.setattr(Project, "shared_project_ids", classmethod(lambda cls: set()))
    monkeypatch.setattr(Project, "registered_project_names", classmethod(lambda cls: {"p1": "Project"}))

    def entry(session_id: str, owner: str, **kwargs):
        return SessionInfo(
            id=session_id, projectID="p1", directory="/tmp", title=session_id,
            ownerUserID=owner, ownerUsername=owner, **kwargs,
        )

    entries = [
        entry("own", "usr_viewer"),
        entry("shared", "usr_other", metadata={"shared_read_access_user_ids": ["usr_viewer"]}),
        entry("private", "usr_other"),
        entry("hidden", "usr_viewer", metadata={"hideFromSessionManager": True}),
        entry("child", "usr_viewer", parentID="own"),
        entry("archived", "usr_viewer", status="archived"),
        entry("task", "usr_viewer", category="task"),
    ]
    monkeypatch.setattr(Session, "list_all_unfiltered", AsyncMock(return_value=entries))
    monkeypatch.setattr(session_routes, "_latest_channel_bindings", AsyncMock(return_value={}))

    response = await client.post("/api/session/list-grouped", json={
        "projects": [{"projectID": "p1", "limit": 50}]
    })
    assert response.status_code == 200, response.text
    rows = response.json()["groups"][0]["items"]
    assert [row["id"] for row in rows] == ["own", "shared"]
    assert rows[0]["canWrite"] is True
    assert rows[1]["canWrite"] is False


@pytest.mark.asyncio
async def test_grouped_search_matches_derived_channel_title_before_pagination(
    client: AsyncClient, monkeypatch
):
    from flocks.channel.inbound.session_binding import SessionBindingService

    channel_id = await _create_session(client, "[Wecom] room-1")
    await Message.create(session_id=channel_id, role=MessageRole.USER, content="你是谁")
    await _create_session(client, "Unrelated newer session")

    async def list_bindings(self, *, session_ids):
        if channel_id not in session_ids:
            return []
        return [SimpleNamespace(
            session_id=channel_id, channel_id="wecom", chat_id="room-1", chat_type="group",
        )]

    monkeypatch.setattr(SessionBindingService, "list_bindings", list_bindings)
    params = {"view": "list", "manager": "true", "roots": "true", "projectID": "tasks", "search": "你是谁", "limit": 1}
    single = await client.get("/api/session", params=params)
    grouped = await client.post("/api/session/list-grouped", json={
        "projects": [{"projectID": "tasks", "limit": 1}], "search": "你是谁",
    })
    assert single.status_code == grouped.status_code == 200
    rows = grouped.json()["groups"][0]["items"]
    assert rows == single.json()
    assert [(row["id"], row["title"]) for row in rows] == [(channel_id, "[Wecom] 你是谁")]


@pytest.mark.asyncio
async def test_grouped_empty_and_invalid_pagination(client, monkeypatch):
    read = AsyncMock(side_effect=AssertionError("empty requests should not load sessions"))
    monkeypatch.setattr(Session, "list_all_unfiltered", read)
    response = await client.post("/api/session/list-grouped", json={"projects": []})
    assert response.status_code == 200 and response.json() == {"groups": []}
    read.assert_not_awaited()
    for group in (
        {"projectID": "", "limit": 1},
        {"projectID": "tasks", "limit": 0},
        {"projectID": "tasks", "limit": 10001},
        {"projectID": "tasks", "limit": 1, "offset": -1},
    ):
        response = await client.post("/api/session/list-grouped", json={"projects": [group]})
        assert response.status_code == 422
    response = await client.post("/api/session/list-grouped", json={
        "projects": [{"projectID": "tasks", "limit": 5001}, {"projectID": "tasks", "limit": 5000}]
    })
    assert response.status_code == 422
    response = await client.post("/api/session/list-grouped", json={
        "projects": [{"projectID": str(i), "limit": 1} for i in range(513)]
    })
    assert response.status_code == 422
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_list_can_skip_session_statistics(client, tmp_path, monkeypatch):
    project_id = await _create_project(client, tmp_path, "quick-project")
    await _create_session(client, "In project", projectID=project_id)
    full = await client.get("/api/project")
    assert full.status_code == 200 and full.json()[0]["sessionCount"] == 1

    read = AsyncMock(side_effect=AssertionError("light project listing must not load sessions"))
    monkeypatch.setattr(Session, "list_all_unfiltered", read)
    fast = await client.get("/api/project", params={"includeSessionStats": "false"})
    assert fast.status_code == 200, fast.text
    assert [row["id"] for row in fast.json()] == [project_id]
    assert fast.json()[0]["worktree"] == full.json()[0]["worktree"]
    assert fast.json()[0]["canWrite"] == full.json()[0]["canWrite"]
    read.assert_not_awaited()
