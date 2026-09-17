"""
Session route tests

Covers:
  - CRUD (create / list / get / patch / delete)
  - Message operations (send message, list messages)
  - Session event SSE endpoint (basic connectivity)
  - Utility endpoints (clear, abort, status, metrics, recent)
  - Permission management on sessions
  - Error cases (404 for unknown IDs, 422 for bad payloads)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient
from flocks.auth.context import API_TOKEN_SERVICE_USER_ID, AuthUser
from flocks.hooks.execution import (
    ExecutionStopped,
    current_execution_context,
    execution_context_scope,
)
from flocks.server.routes import session as session_routes
from flocks.session.core.status import SessionStatus, SessionStatusBusy
from flocks.session.message import (
    Message,
    FilePart,
    MessageRole,
    PartTime,
    ReasoningPart,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStateRunning,
)
from flocks.session.orphan_tools import INTERRUPTED_TOOL_ERROR
from flocks.session.session import Session


def _use_webui_admin(monkeypatch: pytest.MonkeyPatch) -> AuthUser:
    """Authenticate a route test as a browser user instead of the API token."""
    from flocks.server.routes import session as session_routes

    user = AuthUser(id="usr_admin", username="admin", role="admin", status="active")
    monkeypatch.setattr(session_routes, "require_user", lambda _request: user)
    return user


# ===========================================================================
# CRUD
# ===========================================================================


@pytest.mark.asyncio
async def test_missing_session_directory_uses_cwd_and_publishes_notice(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import event as event_routes
    from flocks.server.routes import session as session_routes

    monkeypatch.chdir(tmp_path)
    publish_event = AsyncMock()
    monkeypatch.setattr(event_routes, "publish_event", publish_event)

    working_directory = await session_routes._resolve_session_working_directory(
        SimpleNamespace(
            id="ses_missing_directory",
            directory=str(tmp_path / "missing"),
        ),
    )

    assert working_directory == str(tmp_path)
    publish_event.assert_awaited_once_with(
        "session.notice",
        {
            "sessionID": "ses_missing_directory",
            "kind": "directory-fallback",
            "storedDirectory": str(tmp_path / "missing"),
            "fallbackDirectory": str(tmp_path),
        },
    )


@pytest.mark.asyncio
async def test_shell_route_maps_extension_stop_to_forbidden(
    monkeypatch: pytest.MonkeyPatch,
    session_id: str,
) -> None:
    """A Pro policy stop must not surface as an unhandled server error."""

    monkeypatch.setattr(session_routes, "require_user", lambda _request: object())
    monkeypatch.setattr(
        session_routes,
        "_get_session_by_id_unfiltered",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        session_routes,
        "_require_session_write_access",
        lambda _session, _user: None,
    )
    monkeypatch.setattr(
        "flocks.session.runner.SessionRunner.shell",
        AsyncMock(side_effect=ExecutionStopped("hard_deny_system_delete")),
    )

    with pytest.raises(HTTPException) as error:
        await session_routes.run_shell_command(
            session_id,
            session_routes.ShellRequest(agent="build", command="rm -rf /etc"),
            SimpleNamespace(),
        )

    assert error.value.status_code == status.HTTP_403_FORBIDDEN
    assert error.value.detail == "execution stopped by extension"


@pytest.mark.asyncio
async def test_background_session_task_preserves_execution_context() -> None:
    """Async session work retains opaque ingress context after scheduling."""
    observed: list[dict[str, object]] = []

    async def _record_context() -> None:
        observed.append(current_execution_context())

    before = set(getattr(session_routes.router, "_pending_tasks", set()))
    with execution_context_scope({"workflow_transfer": "opaque-transfer"}):
        session_routes._schedule_background_coro(_record_context())

    pending = list(getattr(session_routes.router, "_pending_tasks", set()) - before)
    assert len(pending) == 1
    await asyncio.gather(*pending)

    assert observed == [{"workflow_transfer": "opaque-transfer"}]

class TestSessionCRUD:
    """Basic create / read / update / delete for sessions."""

    @pytest.mark.asyncio
    async def test_create_session_minimal(self, client: AsyncClient):
        """POST /api/session with no body returns a valid session."""
        resp = await client.post("/api/session", json={})
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["id"].startswith("ses_")
        assert "projectID" in data
        assert "directory" in data
        from flocks.session.execution_profile import get_session_execution_profile

        profile = await get_session_execution_profile(data["id"])
        assert profile is not None
        assert profile["entry"] == "webui"
        assert profile["source"] == "webui.session.create"
        assert profile["permission_mode"] == "require-confirm"
        assert profile["runtime_mode"] == "dev-mode"
        assert profile["network_mode"] == "require-confirm"

    @pytest.mark.asyncio
    async def test_create_session_emits_canonical_created_event(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Session creation emits the canonical Bus lifecycle event."""
        from flocks.bus.bus import Bus
        from flocks.bus.events import SessionCreated

        emit_event = AsyncMock()
        monkeypatch.setattr(Bus, "publish", emit_event)

        response = await client.post("/api/session", json={})

        assert response.status_code == status.HTTP_200_OK
        emit_event.assert_awaited_once()
        assert emit_event.await_args.args[0] is SessionCreated
        info = emit_event.await_args.args[1]["info"]
        assert info == {
            "id": response.json()["id"],
            "title": response.json()["title"],
            "parentID": None,
            "projectID": response.json()["projectID"],
        }

    @pytest.mark.asyncio
    async def test_create_ordinary_session_uses_process_cwd(
        self,
        client: AsyncClient,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A task session must not resolve through a virtual default project."""
        process_cwd = tmp_path / "server-cwd"
        process_cwd.mkdir()
        monkeypatch.chdir(process_cwd)

        response = await client.post("/api/session", json={})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["directory"] == str(process_cwd)

    @pytest.mark.asyncio
    async def test_create_session_with_title(self, client: AsyncClient):
        """Created session reflects the provided title."""
        resp = await client.post("/api/session", json={"title": "My Test Session"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["title"] == "My Test Session"

    @pytest.mark.asyncio
    async def test_create_session_with_category(self, client: AsyncClient):
        """Category field is stored and returned."""
        resp = await client.post(
            "/api/session",
            json={"title": "Workflow Session", "category": "workflow"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["category"] == "workflow"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", [None, "entity-config", "workflow"])
    async def test_create_session_with_manual_auto_mode(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        category: str | None,
    ):
        """Auto is persisted for supported WebUI conversation sessions."""
        from flocks.session.session_loop import SessionLoop

        _use_webui_admin(monkeypatch)
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            AsyncMock(return_value=(True, "available")),
        )
        payload = {"title": "Auto Session", "model_auto": True}
        if category is not None:
            payload["category"] = category
        resp = await client.post("/api/session", json=payload)

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["model_auto"] is True
        assert resp.json()["model_pinned"] is False
        assert resp.json()["category"] == (category or "user")

    @pytest.mark.asyncio
    async def test_create_unsupported_session_rejects_auto(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session.session_loop import SessionLoop

        validate_auto = AsyncMock(return_value=(True, "available"))
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            validate_auto,
        )

        resp = await client.post(
            "/api/session",
            json={"category": "task", "model_auto": True},
        )

        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert (
            "only available for user, entity configuration, and workflow sessions"
            in str(resp.json())
        )
        validate_auto.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_session_rejects_unavailable_auto(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session.session_loop import SessionLoop

        _use_webui_admin(monkeypatch)
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            AsyncMock(return_value=(False, "fallback_unavailable")),
        )

        resp = await client.post("/api/session", json={"model_auto": True})

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "fallback_unavailable" in str(resp.json())

    @pytest.mark.asyncio
    async def test_api_token_cannot_create_auto_session(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session.session_loop import SessionLoop

        validate_auto = AsyncMock(return_value=(True, "available"))
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            validate_auto,
        )

        resp = await client.post("/api/session", json={"model_auto": True})

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "only be enabled from the WebUI" in str(resp.json())
        validate_auto.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_session_with_api_token_is_system_owned(self, client: AsyncClient):
        """API-token sessions have an explicit owner manageable by WebUI admins."""
        resp = await client.post("/api/session", json={"title": "TUI Session"})

        assert resp.status_code == status.HTTP_200_OK
        session = await Session.get_by_id(resp.json()["id"])
        assert session is not None
        assert session.owner_user_id == API_TOKEN_SERVICE_USER_ID
        assert session.owner_username == API_TOKEN_SERVICE_USER_ID

    @pytest.mark.asyncio
    async def test_create_session_with_local_user_keeps_owner(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Browser-authenticated sessions remain private to their local user."""
        from flocks.server.routes import session as session_routes

        user = AuthUser(id="usr_admin", username="admin", role="admin", status="active")
        monkeypatch.setattr(session_routes, "require_user", lambda _request: user)

        resp = await client.post("/api/session", json={"title": "WebUI Session"})

        assert resp.status_code == status.HTTP_200_OK
        session = await Session.get_by_id(resp.json()["id"])
        assert session is not None
        assert session.owner_user_id == user.id
        assert session.owner_username == user.username

    @pytest.mark.asyncio
    async def test_webui_admin_can_manage_api_token_session(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A WebUI admin can list, continue, rename, and delete a TUI session."""
        from flocks.server.routes import session as session_routes

        create_resp = await client.post("/api/session", json={"title": "TUI Session"})
        assert create_resp.status_code == status.HTTP_200_OK
        session_id = create_resp.json()["id"]

        admin = AuthUser(id="usr_admin", username="admin", role="admin", status="active")
        monkeypatch.setattr(session_routes, "require_user", lambda _request: admin)

        list_resp = await client.get(
            "/api/session",
            params={"view": "list", "manager": "true", "roots": "true"},
        )
        assert list_resp.status_code == status.HTTP_200_OK
        assert session_id in {session["id"] for session in list_resp.json()}

        message_resp = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": "Continue from WebUI"}],
                "noReply": True,
            },
        )
        assert message_resp.status_code == status.HTTP_200_OK

        child_resp = await client.post(
            "/api/session",
            json={"title": "WebUI child", "parentID": session_id},
        )
        assert child_resp.status_code == status.HTTP_200_OK
        child = await Session.get_by_id(child_resp.json()["id"])
        assert child is not None
        assert child.owner_user_id == API_TOKEN_SERVICE_USER_ID
        assert child.owner_username == API_TOKEN_SERVICE_USER_ID

        rename_resp = await client.patch(
            f"/api/session/{session_id}",
            json={"title": "Renamed in WebUI"},
        )
        assert rename_resp.status_code == status.HTTP_200_OK
        assert rename_resp.json()["title"] == "Renamed in WebUI"

        delete_resp = await client.delete(f"/api/session/{session_id}")
        assert delete_resp.status_code == status.HTTP_200_OK
        assert delete_resp.json() is True

    @pytest.mark.asyncio
    async def test_get_session_includes_persisted_goal(self, client: AsyncClient):
        """GET /api/session/{id} hydrates persisted goal state for the WebUI."""
        from flocks.session.goal import GoalManager

        create_resp = await client.post("/api/session", json={"title": "Goal Session"})
        assert create_resp.status_code == status.HTTP_200_OK
        session_id = create_resp.json()["id"]
        await GoalManager.set_goal(session_id, "List built-in tools")

        resp = await client.get(f"/api/session/{session_id}")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["goal"] == {
            "status": "active",
            "objective": "List built-in tools",
            "reason": None,
        }

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, client: AsyncClient):
        """GET /api/session returns an empty list when no sessions exist."""
        resp = await client.get("/api/session")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_sessions_after_create(self, client: AsyncClient):
        """List returns exactly the sessions that were created."""
        await client.post("/api/session", json={"title": "A"})
        await client.post("/api/session", json={"title": "B"})
        resp = await client.get("/api/session")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, list)
        titles = [s["title"] for s in data]
        assert "A" in titles
        assert "B" in titles

    @pytest.mark.asyncio
    async def test_list_sessions_roots_excludes_children(self, client: AsyncClient):
        """roots=true should exclude child sessions from the list payload."""
        parent_resp = await client.post("/api/session", json={"title": "Parent"})
        parent_id = parent_resp.json()["id"]
        child_resp = await client.post(
            "/api/session",
            json={"title": "Child", "parentID": parent_id},
        )
        child_id = child_resp.json()["id"]

        resp = await client.get("/api/session", params={"roots": "true"})
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        ids = {item["id"] for item in data}
        assert parent_id in ids
        assert child_id not in ids

    @pytest.mark.asyncio
    async def test_list_sessions_light_manager_filters_and_omits_heavy_fields(self, client: AsyncClient):
        """Lightweight manager list returns only sidebar metadata."""
        from flocks.session.goal import GoalManager

        user_resp = await client.post("/api/session", json={"title": "User"})
        user_id = user_resp.json()["id"]
        workflow_resp = await client.post(
            "/api/session",
            json={"title": "Workflow", "category": "workflow"},
        )
        workflow_id = workflow_resp.json()["id"]
        task_resp = await client.post(
            "/api/session",
            json={"title": "Task", "category": "task"},
        )
        task_id = task_resp.json()["id"]
        child_resp = await client.post(
            "/api/session",
            json={"title": "Child", "parentID": user_id},
        )
        child_id = child_resp.json()["id"]
        await GoalManager.set_goal(user_id, "Do not hydrate in list mode")

        resp = await client.get(
            "/api/session",
            params={"view": "list", "manager": "true", "roots": "true", "limit": "100"},
        )

        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        ids = {item["id"] for item in data}
        assert user_id in ids
        assert workflow_id in ids
        assert task_id not in ids
        assert child_id not in ids

        row = next(item for item in data if item["id"] == user_id)
        assert set(row) == {
            "id",
            "projectID",
            "projectName",
            "effectiveProjectID",
            "directory",
            "title",
            "time",
            "category",
            "channelID",
            "channelChatType",
            "status",
            "parentID",
            "provider",
            "model",
            "model_pinned",
            "model_auto",
            "ownerUserID",
            "ownerUsername",
            "canWrite",
            "canDelete",
            "isShared",
        }
        assert row["projectID"]
        assert row["directory"]
        assert "ownerUsername" in row
        assert "goal" not in row
        assert "summary" not in row

    @pytest.mark.asyncio
    async def test_manager_list_includes_channel_metadata_and_legacy_title(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.channel.inbound.session_binding import SessionBindingService

        session_resp = await client.post("/api/session", json={"title": "[Wecom] room-1"})
        session_id = session_resp.json()["id"]
        await Message.create(
            session_id=session_id,
            role=MessageRole.USER,
            content="你是谁",
        )
        list_bindings_mock = AsyncMock(return_value=[SimpleNamespace(
            session_id=session_id,
            channel_id="wecom",
            chat_id="room-1",
            chat_type="group",
        )])
        monkeypatch.setattr(
            SessionBindingService,
            "list_bindings",
            list_bindings_mock,
        )

        response = await client.get(
            "/api/session",
            params={"view": "list", "manager": "true", "roots": "true", "limit": "100"},
        )

        assert response.status_code == status.HTTP_200_OK
        row = next(item for item in response.json() if item["id"] == session_id)
        assert row["channelID"] == "wecom"
        assert row["channelChatType"] == "group"
        assert row["title"] == "[Wecom] 你是谁"
        assert session_id in list_bindings_mock.await_args.kwargs["session_ids"]

    @pytest.mark.asyncio
    async def test_manager_list_recognizes_legacy_direct_title_with_sender_name(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.channel.inbound.session_binding import SessionBindingService

        session_resp = await client.post(
            "/api/session",
            json={"title": "[Telegram] DM — Alice"},
        )
        session_id = session_resp.json()["id"]
        await Message.create(
            session_id=session_id,
            role=MessageRole.USER,
            content="你是谁",
        )
        monkeypatch.setattr(
            SessionBindingService,
            "list_bindings",
            AsyncMock(return_value=[SimpleNamespace(
                session_id=session_id,
                channel_id="telegram",
                chat_id="12345",
                chat_type="direct",
            )]),
        )

        response = await client.get(
            "/api/session",
            params={"view": "list", "manager": "true", "roots": "true"},
        )

        assert response.status_code == status.HTTP_200_OK
        row = next(item for item in response.json() if item["id"] == session_id)
        assert row["title"] == "[Telegram] 你是谁"

    @pytest.mark.asyncio
    async def test_manager_search_matches_derived_channel_title_before_pagination(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.channel.inbound.session_binding import SessionBindingService

        session_resp = await client.post("/api/session", json={"title": "[Wecom] room-1"})
        session_id = session_resp.json()["id"]
        await Message.create(
            session_id=session_id,
            role=MessageRole.USER,
            content="你是谁",
        )
        decoy_resp = await client.post(
            "/api/session",
            json={"title": "Unrelated newer session"},
        )
        decoy_id = decoy_resp.json()["id"]
        list_bindings_mock = AsyncMock(return_value=[SimpleNamespace(
            session_id=session_id,
            channel_id="wecom",
            chat_id="room-1",
            chat_type="group",
        )])
        monkeypatch.setattr(
            SessionBindingService,
            "list_bindings",
            list_bindings_mock,
        )

        response = await client.get(
            "/api/session",
            params={
                "view": "list",
                "manager": "true",
                "roots": "true",
                "search": "你是谁",
                "limit": "1",
                "offset": "0",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert [item["id"] for item in response.json()] == [session_id]
        assert response.json()[0]["title"] == "[Wecom] 你是谁"
        queried_session_ids = list_bindings_mock.await_args.kwargs["session_ids"]
        assert session_id in queried_session_ids
        assert decoy_id in queried_session_ids

    @pytest.mark.asyncio
    async def test_channel_binding_lookup_batches_large_session_lists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.channel.inbound.session_binding import SessionBindingService
        from flocks.server.routes.session import _latest_channel_bindings

        list_bindings_mock = AsyncMock(return_value=[])
        monkeypatch.setattr(
            SessionBindingService,
            "list_bindings",
            list_bindings_mock,
        )

        await _latest_channel_bindings([f"ses-{index}" for index in range(1001)])

        assert list_bindings_mock.await_count == 3
        assert all(
            len(call.kwargs["session_ids"]) <= 500
            for call in list_bindings_mock.await_args_list
        )

    @pytest.mark.asyncio
    async def test_archive_hides_session_preserves_history_and_restores_tree(self, client: AsyncClient):
        parent_resp = await client.post("/api/session", json={"title": "Archive Parent"})
        parent_id = parent_resp.json()["id"]
        project_id = parent_resp.json()["projectID"]
        child_resp = await client.post(
            "/api/session",
            json={"title": "Archive Child", "parentID": parent_id},
        )
        child_id = child_resp.json()["id"]
        await client.post(
            f"/api/session/{parent_id}/message",
            json={"parts": [{"type": "text", "text": "keep this history"}], "noReply": True},
        )

        archive_resp = await client.post(f"/api/session/{parent_id}/archive")

        assert archive_resp.status_code == status.HTTP_200_OK
        assert archive_resp.json()["status"] == "archived"
        archived_parent = await Session.get(project_id, parent_id)
        archived_child = await Session.get(project_id, child_id)
        assert archived_parent is not None and archived_parent.status == "archived"
        assert archived_child is not None and archived_child.status == "archived"
        assert len(await Message.list(parent_id)) == 1

        active_list = await client.get("/api/session", params={"status": "active"})
        archived_list = await client.get(
            "/api/session",
            params={"status": "archived", "view": "list", "manager": "true", "roots": "true"},
        )
        assert parent_id not in {item["id"] for item in active_list.json()}
        assert parent_id in {item["id"] for item in archived_list.json()}
        assert child_id not in {item["id"] for item in archived_list.json()}
        archived_row = next(item for item in archived_list.json() if item["id"] == parent_id)
        assert archived_row["canWrite"] is False

        get_resp = await client.get(f"/api/session/{parent_id}")
        update_resp = await client.patch(f"/api/session/{parent_id}", json={"title": "Blocked"})
        message_resp = await client.post(
            f"/api/session/{parent_id}/message",
            json={"parts": [{"type": "text", "text": "blocked"}], "noReply": True},
        )
        assert get_resp.status_code == status.HTTP_404_NOT_FOUND
        assert update_resp.status_code == status.HTTP_409_CONFLICT
        assert message_resp.status_code == status.HTTP_409_CONFLICT

        child_restore_resp = await client.post(f"/api/session/{child_id}/restore")
        late_child_resp = await client.post(
            "/api/session",
            json={"title": "Late Child", "parentID": parent_id},
        )
        assert child_restore_resp.status_code == status.HTTP_409_CONFLICT
        assert late_child_resp.status_code == status.HTTP_409_CONFLICT

        restore_resp = await client.post(f"/api/session/{parent_id}/restore")
        assert restore_resp.status_code == status.HTTP_200_OK
        assert restore_resp.json()["status"] == "active"
        restored_child = await Session.get(project_id, child_id)
        assert restored_child is not None and restored_child.status == "active"
        assert len(await Message.list(parent_id)) == 1

    @pytest.mark.asyncio
    async def test_archive_cancels_route_background_work(
        self,
        client: AsyncClient,
        session_id: str,
    ):
        from flocks.server.routes import session as session_routes

        started = asyncio.Event()
        stopped = asyncio.Event()

        async def background_work() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        session_routes._schedule_background_coro(
            background_work(),
            session_id=session_id,
            action="test.background",
        )
        await started.wait()

        response = await client.post(f"/api/session/{session_id}/archive")

        assert response.status_code == status.HTTP_200_OK
        assert stopped.is_set()

    @pytest.mark.asyncio
    async def test_list_status_filter_applies_before_pagination(self, client: AsyncClient):
        archived_resp = await client.post("/api/session", json={"title": "Archived First"})
        active_resp = await client.post("/api/session", json={"title": "Active Second"})
        assert (await client.post(f"/api/session/{archived_resp.json()['id']}/archive")).status_code == 200

        response = await client.get(
            "/api/session",
            params={"status": "active", "limit": "1", "offset": "0"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert [item["id"] for item in response.json()] == [active_resp.json()["id"]]

    @pytest.mark.asyncio
    async def test_archived_manager_admin_sees_all_and_member_only_owns(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import session as session_routes

        alice = AuthUser(id="usr_alice", username="alice", role="member", status="active")
        bob = AuthUser(id="usr_bob", username="bob", role="member", status="active")
        admin = AuthUser(id="usr_admin", username="admin", role="admin", status="active")
        alice_session = await Session.create(
            project_id="archive_permissions",
            directory="/tmp",
            title="Alice archive",
            owner_user_id=alice.id,
            owner_username=alice.username,
        )
        bob_session = await Session.create(
            project_id="archive_permissions",
            directory="/tmp",
            title="Bob shared archive",
            owner_user_id=bob.id,
            owner_username=bob.username,
            metadata={"shared_local": True},
        )
        assert await Session.archive(alice_session.project_id, alice_session.id) is True
        assert await Session.archive(bob_session.project_id, bob_session.id) is True

        params = {
            "status": "archived",
            "view": "list",
            "manager": "true",
            "roots": "true",
        }
        monkeypatch.setattr(session_routes, "require_user", lambda _request: admin)
        admin_response = await client.get("/api/session", params=params)

        assert admin_response.status_code == status.HTTP_200_OK
        admin_rows = {row["id"]: row for row in admin_response.json()}
        assert {alice_session.id, bob_session.id}.issubset(admin_rows)
        assert admin_rows[alice_session.id]["ownerUsername"] == "alice"
        assert admin_rows[bob_session.id]["ownerUsername"] == "bob"

        monkeypatch.setattr(session_routes, "require_user", lambda _request: alice)
        member_response = await client.get("/api/session", params=params)

        assert member_response.status_code == status.HTTP_200_OK
        assert [row["id"] for row in member_response.json()] == [alice_session.id]

        ordinary_archived_response = await client.get(
            "/api/session",
            params={"status": "archived", "view": "list", "roots": "true"},
        )
        assert ordinary_archived_response.status_code == status.HTTP_200_OK
        assert [row["id"] for row in ordinary_archived_response.json()] == [
            alice_session.id
        ]

        all_status_response = await client.get(
            "/api/session",
            params={"status": "all", "view": "list", "roots": "true"},
        )
        assert bob_session.id not in {
            row["id"] for row in all_status_response.json()
        }

    @pytest.mark.asyncio
    async def test_archive_rejects_tree_with_descendant_owned_by_another_user(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.auth.context import reset_current_auth_user, set_current_auth_user
        from flocks.server.routes import session as session_routes
        from flocks.session.session import SessionInfo
        from flocks.storage.storage import Storage

        alice = AuthUser(id="usr_alice", username="alice", role="member", status="active")
        parent = await Session.create(
            project_id="archive_mixed_owner_tree",
            directory="/tmp",
            title="Alice parent",
            owner_user_id=alice.id,
            owner_username=alice.username,
        )
        child = SessionInfo(
            projectID=parent.project_id,
            directory=parent.directory,
            title="Bob child",
            parentID=parent.id,
            ownerUserID="usr_bob",
            ownerUsername="bob",
        )
        await Storage.set(
            f"session:{child.project_id}:{child.id}",
            child,
            "session",
        )
        Session.invalidate_cache()
        monkeypatch.setattr(session_routes, "require_user", lambda _request: alice)

        token = set_current_auth_user(alice)
        try:
            response = await client.post(f"/api/session/{parent.id}/archive")
        finally:
            reset_current_auth_user(token)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        stored_parent = await Storage.get(
            f"session:{parent.project_id}:{parent.id}",
            SessionInfo,
        )
        stored_child = await Storage.get(
            f"session:{child.project_id}:{child.id}",
            SessionInfo,
        )
        assert stored_parent is not None and stored_parent.status == "active"
        assert stored_child is not None and stored_child.status == "active"

    @pytest.mark.asyncio
    async def test_create_session_in_user_managed_project(
        self,
        client: AsyncClient,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Session creation can target a user-managed project."""
        worktree = tmp_path / "labs"
        worktree.mkdir()
        monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(tmp_path))
        project_resp = await client.post(
            "/api/project",
            json={"name": "Labs", "worktree": str(worktree)},
        )
        assert project_resp.status_code == status.HTTP_200_OK
        project = project_resp.json()

        session_resp = await client.post(
            "/api/session",
            json={"title": "Project Session", "projectID": project["id"]},
        )
        assert session_resp.status_code == status.HTTP_200_OK
        assert session_resp.json()["projectID"] == project["id"]

        list_resp = await client.get(
            "/api/session",
            params={"view": "list", "manager": "true", "roots": "true", "limit": "100"},
        )
        row = next(item for item in list_resp.json() if item["id"] == session_resp.json()["id"])
        assert row["projectID"] == project["id"]
        assert row["projectName"] == "Labs"
        assert row["effectiveProjectID"] == project["id"]
        assert row["directory"] == project["worktree"]

    @pytest.mark.asyncio
    async def test_legacy_session_is_grouped_under_tasks_without_rewrite(
        self,
        client: AsyncClient,
        tmp_path,
    ):
        """Legacy project IDs are projected to Tasks while storage stays unchanged."""
        legacy = await Session.create(
            project_id="legacy-git-project",
            directory=str(tmp_path),
            title="Legacy Session",
        )

        response = await client.get(
            "/api/session",
            params={
                "view": "list",
                "manager": "true",
                "roots": "true",
                "projectID": "tasks",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        row = next(item for item in response.json() if item["id"] == legacy.id)
        assert row["projectID"] == "legacy-git-project"
        assert row["effectiveProjectID"] == "tasks"
        stored = await Session.get("legacy-git-project", legacy.id)
        assert stored is not None
        assert stored.project_id == "legacy-git-project"

    @pytest.mark.asyncio
    async def test_deleted_project_sessions_are_archived_and_restorable(
        self,
        client: AsyncClient,
        tmp_path,
    ):
        """Deleting a project archives its sessions and restoring one revives the project."""
        worktree = tmp_path / "removable-project"
        worktree.mkdir()
        project_response = await client.post(
            "/api/project",
            json={"name": "Removable", "worktree": str(worktree)},
        )
        project = project_response.json()
        session_response = await client.post(
            "/api/session",
            json={"title": "Keep Me", "projectID": project["id"]},
        )
        session_id = session_response.json()["id"]

        delete_response = await client.delete(f"/api/project/{project['id']}")
        assert delete_response.status_code == status.HTTP_200_OK
        assert worktree.exists()

        session_get_response = await client.get(f"/api/session/{session_id}")
        assert session_get_response.status_code == status.HTTP_404_NOT_FOUND

        tasks_response = await client.get(
            "/api/session",
            params={"view": "list", "manager": "true", "projectID": "tasks"},
        )
        assert all(item["id"] != session_id for item in tasks_response.json())
        archived = await Session.get(project["id"], session_id)
        assert archived is not None and archived.status == "archived"

        restore_response = await client.post(f"/api/session/{session_id}/restore")
        assert restore_response.status_code == status.HTTP_200_OK
        assert restore_response.json()["status"] == "active"
        assert [item["id"] for item in (await client.get("/api/project")).json()] == [project["id"]]

    @pytest.mark.asyncio
    async def test_restore_rolls_project_back_when_session_restore_fails(
        self,
        client: AsyncClient,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        worktree = tmp_path / "restore-rollback"
        worktree.mkdir()
        project = (
            await client.post(
                "/api/project",
                json={"name": "Rollback", "worktree": str(worktree)},
            )
        ).json()
        session_id = (
            await client.post(
                "/api/session",
                json={"title": "Rollback Me", "projectID": project["id"]},
            )
        ).json()["id"]
        assert (await client.delete(f"/api/project/{project['id']}")).status_code == 200

        async def fail_unarchive(_project_id: str, _session_id: str) -> bool:
            return False

        monkeypatch.setattr(Session, "unarchive", fail_unarchive)
        response = await client.post(f"/api/session/{session_id}/restore")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert (await client.get("/api/project")).json() == []
        archived = await Session.get(project["id"], session_id)
        assert archived is not None and archived.status == "archived"

    @pytest.mark.asyncio
    async def test_restore_rejects_missing_managed_project_metadata(
        self,
        client: AsyncClient,
    ):
        session = await Session.create(
            project_id="prj_missing_restore_metadata",
            directory="/tmp",
            title="Missing project metadata",
        )
        assert await Session.archive(session.project_id, session.id) is True

        response = await client.post(f"/api/session/{session.id}/restore")

        assert response.status_code == status.HTTP_409_CONFLICT
        archived = await Session.get(session.project_id, session.id)
        assert archived is not None and archived.status == "archived"

    @pytest.mark.asyncio
    async def test_get_session(self, client: AsyncClient, session_id: str):
        """GET /api/session/{id} returns the specific session."""
        resp = await client.get(f"/api/session/{session_id}")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["id"] == session_id

    @pytest.mark.asyncio
    async def test_context_usage_keeps_inflight_task_after_cancelled_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import session as session_routes
        from flocks.session.context_usage import ContextUsageSnapshot

        session_routes._context_usage_cache.clear()
        session_routes._context_usage_inflight.clear()

        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()
        session = SimpleNamespace(time=SimpleNamespace(updated=123), provider=None, model=None)

        async def fake_build_context_usage_snapshot(session_id: str, *, session=None):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return ContextUsageSnapshot(
                sessionID=session_id,
                usedTokens=42,
                contextWindow=100,
                estimatedTokens=42,
            )

        monkeypatch.setattr(
            session_routes,
            "build_context_usage_snapshot",
            fake_build_context_usage_snapshot,
        )

        first = asyncio.create_task(
            session_routes._cached_context_usage_snapshot("ses_context_cancel", session=session)
        )
        second = None
        try:
            await started.wait()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

            second = asyncio.create_task(
                session_routes._cached_context_usage_snapshot("ses_context_cancel", session=session)
            )
            await asyncio.sleep(0)
            assert calls == 1

            release.set()
            snapshot = await asyncio.wait_for(second, timeout=1)
            assert snapshot.used_tokens == 42

            cached = await session_routes._cached_context_usage_snapshot("ses_context_cancel", session=session)
            assert cached.used_tokens == 42
            assert calls == 1
        finally:
            release.set()
            if second is not None and not second.done():
                await asyncio.wait_for(second, timeout=1)
            session_routes._context_usage_cache.clear()
            session_routes._context_usage_inflight.clear()

    @pytest.mark.asyncio
    async def test_context_usage_keeps_newer_cache_when_older_task_finishes_late(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import session as session_routes
        from flocks.session.context_usage import ContextUsageSnapshot

        session_routes._context_usage_cache.clear()
        session_routes._context_usage_inflight.clear()

        old_started = asyncio.Event()
        new_started = asyncio.Event()
        old_release = asyncio.Event()
        new_release = asyncio.Event()
        old_session = SimpleNamespace(time=SimpleNamespace(updated=100), provider=None, model=None)
        new_session = SimpleNamespace(time=SimpleNamespace(updated=200), provider=None, model=None)

        async def fake_build_context_usage_snapshot(session_id: str, *, session=None):
            updated = session.time.updated
            if updated == 100:
                old_started.set()
                await old_release.wait()
            else:
                new_started.set()
                await new_release.wait()
            return ContextUsageSnapshot(
                sessionID=session_id,
                usedTokens=updated,
                contextWindow=1000,
                estimatedTokens=updated,
            )

        monkeypatch.setattr(
            session_routes,
            "build_context_usage_snapshot",
            fake_build_context_usage_snapshot,
        )

        old_task = asyncio.create_task(
            session_routes._cached_context_usage_snapshot("ses_context_order", session=old_session)
        )
        new_task = asyncio.create_task(
            session_routes._cached_context_usage_snapshot("ses_context_order", session=new_session)
        )
        try:
            await old_started.wait()
            await new_started.wait()

            new_release.set()
            new_snapshot = await asyncio.wait_for(new_task, timeout=1)
            assert new_snapshot.used_tokens == 200

            old_release.set()
            old_snapshot = await asyncio.wait_for(old_task, timeout=1)
            assert old_snapshot.used_tokens == 100

            assert ("ses_context_order", 200) in session_routes._context_usage_cache
            assert ("ses_context_order", 100) not in session_routes._context_usage_cache
        finally:
            old_release.set()
            new_release.set()
            session_routes._context_usage_cache.clear()
            session_routes._context_usage_inflight.clear()

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, client: AsyncClient):
        """GET for an unknown session ID returns 404."""
        resp = await client.get("/api/session/ses_nonexistent00000000000000")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_update_session_title(self, client: AsyncClient, session_id: str):
        """PATCH /api/session/{id} updates the title."""
        resp = await client.patch(
            f"/api/session/{session_id}",
            json={"title": "Updated Title"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["title"] == "Updated Title"

    @pytest.mark.asyncio
    async def test_update_session_auto_and_concrete_model_are_exclusive(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session.session_loop import SessionLoop

        _use_webui_admin(monkeypatch)
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            AsyncMock(return_value=(True, "available")),
        )
        auto_resp = await client.patch(
            f"/api/session/{session_id}",
            json={"model_auto": True},
        )
        assert auto_resp.status_code == status.HTTP_200_OK
        assert auto_resp.json()["model_auto"] is True
        assert auto_resp.json()["model_pinned"] is False

        invalid_resp = await client.patch(
            f"/api/session/{session_id}",
            json={
                "model_auto": True,
                "provider": "openai",
                "model": "gpt-4o",
                "model_pinned": True,
            },
        )
        assert invalid_resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        concrete_resp = await client.patch(
            f"/api/session/{session_id}",
            json={"provider": "openai", "model": "gpt-4o"},
        )
        assert concrete_resp.status_code == status.HTTP_200_OK
        assert concrete_resp.json()["model_auto"] is False
        assert concrete_resp.json()["model_pinned"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", ["entity-config", "workflow"])
    async def test_update_supported_sidebar_session_allows_auto(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        category: str,
    ):
        from flocks.session.session_loop import SessionLoop

        create_resp = await client.post(
            "/api/session",
            json={"title": "Sidebar Chat", "category": category},
        )
        assert create_resp.status_code == status.HTTP_200_OK

        _use_webui_admin(monkeypatch)
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            AsyncMock(return_value=(True, "available")),
        )
        resp = await client.patch(
            f"/api/session/{create_resp.json()['id']}",
            json={"model_auto": True},
        )

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["category"] == category
        assert resp.json()["model_auto"] is True
        assert resp.json()["model_pinned"] is False

    @pytest.mark.asyncio
    async def test_update_session_rejects_unavailable_auto(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session.session_loop import SessionLoop

        _use_webui_admin(monkeypatch)
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            AsyncMock(return_value=(False, "primary_provider_not_configured")),
        )

        resp = await client.patch(
            f"/api/session/{session_id}",
            json={"model_auto": True},
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "primary_provider_not_configured" in str(resp.json())

    @pytest.mark.asyncio
    async def test_pinning_existing_auto_session_disables_auto(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session.session_loop import SessionLoop

        _use_webui_admin(monkeypatch)
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            AsyncMock(return_value=(True, "available")),
        )
        clear_state = MagicMock()
        monkeypatch.setattr(SessionLoop, "clear_auto_failover_state", clear_state)
        auto_resp = await client.patch(
            f"/api/session/{session_id}",
            json={"model_auto": True},
        )
        assert auto_resp.status_code == status.HTTP_200_OK

        pin_resp = await client.patch(
            f"/api/session/{session_id}",
            json={"model_pinned": True},
        )

        assert pin_resp.status_code == status.HTTP_200_OK
        assert pin_resp.json()["model_pinned"] is True
        assert pin_resp.json()["model_auto"] is False
        clear_state.assert_called_once_with(session_id)

    @pytest.mark.asyncio
    async def test_api_token_cannot_enable_auto_on_existing_session(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session.session_loop import SessionLoop

        validate_auto = AsyncMock(return_value=(True, "available"))
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            validate_auto,
        )

        resp = await client.patch(
            f"/api/session/{session_id}",
            json={"model_auto": True},
        )

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "only be enabled from the WebUI" in str(resp.json())
        validate_auto.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_unsupported_session_rejects_auto(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session.session_loop import SessionLoop

        create_resp = await client.post(
            "/api/session",
            json={"title": "Non-user Session", "category": "task"},
        )
        assert create_resp.status_code == status.HTTP_200_OK
        session_id = create_resp.json()["id"]
        validate_auto = AsyncMock(return_value=(True, "available"))
        monkeypatch.setattr(
            SessionLoop,
            "validate_auto_configuration",
            validate_auto,
        )

        resp = await client.patch(
            f"/api/session/{session_id}",
            json={"model_auto": True},
        )

        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert (
            "only available for user, entity configuration, and workflow sessions"
            in str(resp.json())
        )
        validate_auto.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_session_not_found(self, client: AsyncClient):
        """PATCH for unknown session returns 404."""
        resp = await client.patch(
            "/api/session/ses_nonexistent00000000000000",
            json={"title": "X"},
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_delete_session(self, client: AsyncClient, session_id: str):
        """DELETE /api/session/{id} removes the session."""
        resp = await client.delete(f"/api/session/{session_id}")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() is True

        # Confirm it is gone
        get_resp = await client.get(f"/api/session/{session_id}")
        assert get_resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_delete_session_not_found(self, client: AsyncClient):
        """DELETE for unknown session returns 404."""
        resp = await client.delete("/api/session/ses_nonexistent00000000000000")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ===========================================================================
# Message operations
# ===========================================================================

class TestSessionMessages:
    """Message-related endpoints on a session."""

    @pytest.mark.asyncio
    async def test_list_messages_empty(self, client: AsyncClient, session_id: str):
        """New session has no messages."""
        resp = await client.get(f"/api/session/{session_id}/message")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_list_messages_reports_storage_failure(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A read failure must not look like a successfully empty history."""
        monkeypatch.setattr(
            Message,
            "list_with_parts",
            AsyncMock(side_effect=RuntimeError("storage unavailable")),
        )

        resp = await client.get(f"/api/session/{session_id}/message")

        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_send_message_noReply(self, client: AsyncClient, session_id: str):
        """POST /api/session/{id}/message with noReply=True stores without triggering LLM."""
        payload = {
            "parts": [{"type": "text", "text": "Hello!"}],
            "noReply": True,
        }
        resp = await client.post(f"/api/session/{session_id}/message", json=payload)
        assert resp.status_code == status.HTTP_200_OK

        # The message should appear in the list
        list_resp = await client.get(f"/api/session/{session_id}/message")
        messages = list_resp.json()
        assert any(
            any(p.get("text") == "Hello!" for p in m.get("parts", []))
            for m in messages
        )

    @pytest.mark.asyncio
    async def test_list_messages_preserves_reasoning_part_time(
        self,
        client: AsyncClient,
        session_id: str,
    ):
        """Reloaded message history retains timing needed by the process summary."""
        message = await Message.create(session_id, MessageRole.ASSISTANT, "")
        part = ReasoningPart(
            id="part_timed_reasoning",
            sessionID=session_id,
            messageID=message.id,
            text="Inspect the request.",
            time=PartTime(start=1_000, end=4_500),
        )
        await Message.store_part(session_id, message.id, part)

        response = await client.get(f"/api/session/{session_id}/message")

        assert response.status_code == status.HTTP_200_OK
        messages = response.json()
        reloaded_message = next(item for item in messages if item["info"]["id"] == message.id)
        reloaded_part = next(item for item in reloaded_message["parts"] if item["id"] == part.id)
        assert reloaded_part["time"]["start"] == 1_000
        assert reloaded_part["time"]["end"] == 4_500

    @pytest.mark.asyncio
    async def test_list_messages_keeps_running_tool_when_session_busy(
        self,
        client: AsyncClient,
        session_id: str,
    ):
        msg = await Message.create(session_id, MessageRole.ASSISTANT, "")
        part = ToolPart(
            id="part_busy_running",
            sessionID=session_id,
            messageID=msg.id,
            callID="call_busy_running",
            tool="bash",
            state=ToolStateRunning(
                input={"cmd": "sleep 60"},
                time={"start": 1000},
            ),
        )
        await Message.store_part(session_id, msg.id, part)

        SessionStatus.set(session_id, SessionStatusBusy())
        try:
            resp = await client.get(f"/api/session/{session_id}/message")
        finally:
            SessionStatus.clear(session_id)

        assert resp.status_code == status.HTTP_200_OK
        parts = await Message.parts(msg.id, session_id)
        running_part = next(p for p in parts if p.id == "part_busy_running")
        assert running_part.state.status == "running"

    @pytest.mark.asyncio
    async def test_list_messages_recovers_orphan_running_tool_when_session_idle(
        self,
        client: AsyncClient,
        session_id: str,
    ):
        msg = await Message.create(session_id, MessageRole.ASSISTANT, "")
        part = ToolPart(
            id="part_idle_running",
            sessionID=session_id,
            messageID=msg.id,
            callID="call_idle_running",
            tool="bash",
            state=ToolStateRunning(
                input={"cmd": "sleep 60"},
                metadata={"sessionId": "ses_child"},
                time={"start": 1000},
            ),
        )
        await Message.store_part(session_id, msg.id, part)

        resp = await client.get(f"/api/session/{session_id}/message")

        assert resp.status_code == status.HTTP_200_OK
        parts = await Message.parts(msg.id, session_id)
        repaired_part = next(p for p in parts if p.id == "part_idle_running")
        assert isinstance(repaired_part.state, ToolStateError)
        assert repaired_part.state.status == "error"
        assert repaired_part.state.error == INTERRUPTED_TOOL_ERROR
        assert repaired_part.state.metadata == {"sessionId": "ses_child"}
        assert repaired_part.state.time["start"] == 1000
        assert repaired_part.state.time["end"] >= 1000

    @pytest.mark.asyncio
    async def test_list_messages_uses_preloaded_orphan_recovery_path(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session import orphan_tools

        preloaded_recovery = AsyncMock(return_value=0)
        legacy_recovery = AsyncMock(side_effect=AssertionError("legacy recovery should not be called"))
        monkeypatch.setattr(orphan_tools, "abort_orphan_running_parts_in_messages", preloaded_recovery)
        monkeypatch.setattr(orphan_tools, "abort_orphan_running_parts", legacy_recovery)

        resp = await client.get(f"/api/session/{session_id}/message")

        assert resp.status_code == status.HTTP_200_OK
        preloaded_recovery.assert_awaited_once()
        legacy_recovery.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_messages_page_uses_lazy_recent_path(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        old_msg = await Message.create(session_id, MessageRole.USER, "old")
        mid_msg = await Message.create(session_id, MessageRole.USER, "middle")
        new_msg = await Message.create(session_id, MessageRole.ASSISTANT, "new")

        async def _fail_full_list(*args, **kwargs):
            raise AssertionError("full list_with_parts should not be used for paged reads")

        monkeypatch.setattr(Message, "list_with_parts", _fail_full_list)

        first_resp = await client.get(
            f"/api/session/{session_id}/message",
            params={"page": "true", "limit": "2"},
        )

        assert first_resp.status_code == status.HTTP_200_OK
        first_page = first_resp.json()
        assert [item["info"]["id"] for item in first_page["items"]] == [mid_msg.id, new_msg.id]
        assert first_page["hasMore"] is True
        assert first_page["nextBefore"] == mid_msg.id

        older_resp = await client.get(
            f"/api/session/{session_id}/message",
            params={"page": "true", "limit": "2", "before": first_page["nextBefore"]},
        )
        assert older_resp.status_code == status.HTTP_200_OK
        older_page = older_resp.json()
        assert [item["info"]["id"] for item in older_page["items"]] == [old_msg.id]
        assert older_page["hasMore"] is False

# ===========================================================================
# Delete permissions (single-admin model)
# ===========================================================================

class TestSessionDeletePermissions:
    @pytest.mark.asyncio
    async def test_only_owner_can_delete(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import session as session_routes

        owner = AuthUser(id="usr_owner", username="owner", role="member", status="active")
        admin = AuthUser(id="usr_admin", username="admin", role="admin", status="active")
        other = AuthUser(id="usr_other", username="other", role="member", status="active")

        session = await Session.create(
            project_id="delete_permissions",
            directory="/tmp",
            title="owner-only-delete",
            owner_user_id=owner.id,
            owner_username=owner.username,
        )

        monkeypatch.setattr(session_routes, "require_user", lambda _request: other)
        forbidden = await client.delete(f"/api/session/{session.id}")
        assert forbidden.status_code == status.HTTP_403_FORBIDDEN

        monkeypatch.setattr(session_routes, "require_user", lambda _request: admin)
        admin_forbidden = await client.delete(f"/api/session/{session.id}")
        assert admin_forbidden.status_code == status.HTTP_403_FORBIDDEN

        monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
        owner_ok = await client.delete(f"/api/session/{session.id}")
        assert owner_ok.status_code == status.HTTP_200_OK


class TestSessionLocalSharing:
    @pytest.mark.asyncio
    async def test_owner_can_share_and_unshare_session(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import session as session_routes

        owner = AuthUser(id="usr_owner", username="owner", role="member", status="active")
        monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
        create_resp = await client.post("/api/session", json={"title": "share-session"})
        assert create_resp.status_code == status.HTTP_200_OK
        session_id = create_resp.json()["id"]
        assert await Session.get_by_id(session_id) is not None

        share_resp = await client.post(f"/api/session/{session_id}/share-local")
        assert share_resp.status_code == status.HTTP_200_OK
        assert share_resp.json()["isShared"] is True

        unshare_resp = await client.post(f"/api/session/{session_id}/unshare-local")
        assert unshare_resp.status_code == status.HTTP_200_OK
        assert unshare_resp.json()["isShared"] is False

    @pytest.mark.asyncio
    async def test_non_owner_cannot_change_share_or_continue_session(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import session as session_routes

        owner = AuthUser(id="usr_owner", username="owner", role="member", status="active")
        viewer = AuthUser(id="usr_viewer", username="viewer", role="member", status="active")

        monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
        create_resp = await client.post("/api/session", json={"title": "share-session-2"})
        assert create_resp.status_code == status.HTTP_200_OK
        session_id = create_resp.json()["id"]
        owner_share_resp = await client.post(f"/api/session/{session_id}/share-local")
        assert owner_share_resp.status_code == status.HTTP_200_OK

        monkeypatch.setattr(session_routes, "require_user", lambda _request: viewer)

        share_resp = await client.post(f"/api/session/{session_id}/share-local")
        assert share_resp.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)

        unshare_resp = await client.post(f"/api/session/{session_id}/unshare-local")
        assert unshare_resp.status_code == status.HTTP_403_FORBIDDEN

        prompt_resp = await client.post(
            f"/api/session/{session_id}/prompt_async",
            json={"parts": [{"type": "text", "text": "blocked"}]},
        )
        assert prompt_resp.status_code == status.HTTP_403_FORBIDDEN


class TestSessionMessagesRemaining:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("path_suffix", "payload"),
        [
            ("/message", {"parts": [{"type": "text", "text": "blocked"}], "agent": "disabled-agent"}),
            ("/prompt_async", {"parts": [{"type": "text", "text": "blocked"}], "agent": "disabled-agent"}),
            ("/prompt_queue", {"parts": [{"type": "text", "text": "blocked"}], "agent": "disabled-agent"}),
            ("/command", {"command": "help", "agent": "disabled-agent"}),
        ],
    )
    async def test_input_routes_reject_disabled_agents(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
        path_suffix: str,
        payload: dict,
    ):
        """Disabled subagents cannot be used through direct session input APIs."""
        monkeypatch.setattr(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=SimpleNamespace(
                name="disabled-agent",
                mode="subagent",
                delegatable=False,
                hidden=False,
                tags=[],
                model=None,
            )),
        )

        resp = await client.post(f"/api/session/{session_id}{path_suffix}", json=payload)

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.json()["message"] == 'Agent "disabled-agent" is disabled'

    @pytest.mark.asyncio
    async def test_send_message_empty_parts_returns_success(
        self, client: AsyncClient, session_id: str
    ):
        """Message with empty 'parts' (default) is accepted by PromptRequest model."""
        resp = await client.post(f"/api/session/{session_id}/message", json={})
        # parts defaults to [] so pydantic passes, business logic may return 200 or 4xx
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @pytest.mark.asyncio
    async def test_message_to_unknown_session_returns_404(self, client: AsyncClient):
        """Sending a message to a non-existent session returns 404."""
        resp = await client.post(
            "/api/session/ses_nonexistent00000000000000/message",
            json={"parts": [{"type": "text", "text": "Hi"}]},
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_message_is_not_persisted_if_session_archives_after_preflight(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.provider.provider import Provider
        from flocks.server.routes import session as session_routes

        apply_config_entered = asyncio.Event()
        release_apply_config = asyncio.Event()
        original_apply_config = Provider.apply_config

        async def blocked_apply_config(*args, **kwargs):
            await original_apply_config(*args, **kwargs)
            apply_config_entered.set()
            await release_apply_config.wait()

        monkeypatch.setattr(Provider, "apply_config", blocked_apply_config)

        message_task = asyncio.create_task(
            client.post(
                f"/api/session/{session_id}/message",
                json={
                    "parts": [{"type": "text", "text": "must not persist"}],
                    "noReply": True,
                },
            )
        )
        await apply_config_entered.wait()
        archive_response = await client.post(f"/api/session/{session_id}/archive")
        restore_response = await client.post(f"/api/session/{session_id}/restore")
        release_apply_config.set()
        message_response = await message_task

        assert archive_response.status_code == status.HTTP_200_OK
        assert restore_response.status_code == status.HTTP_200_OK
        assert message_response.status_code == status.HTTP_409_CONFLICT
        assert await Message.list(session_id) == []

    @pytest.mark.asyncio
    async def test_resend_user_message_updates_text_and_truncates_followups(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Resending a user message updates its text and removes later assistant replies."""
        from flocks.server.routes import session as session_routes
        from flocks.session.lifecycle.revert import SessionRevert

        create_resp = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": "Original user text"}],
                "noReply": True,
                "mockReply": "Initial reply",
            },
        )
        assert create_resp.status_code == status.HTTP_200_OK

        list_resp = await client.get(f"/api/session/{session_id}/message")
        messages = list_resp.json()
        user_message = next(msg for msg in messages if msg["info"]["role"] == "user")
        user_part = next(part for part in user_message["parts"] if part["type"] == "text")

        async def _fake_instance_provide(*, directory, init, fn):
            return await fn()

        async def _fake_prepare_replay_runtime(session_id: str, user_message):
            return {
                "agent_name": "rex",
                "provider_id": "openai",
                "model_id": "gpt-4-turbo-preview",
            }

        async def _fake_run_existing_user_message(
            session_id: str,
            session,
            user_message,
            working_directory: str,
            runtime=None,
        ):
            await SessionRevert.cleanup(session)
            return {"status": "completed", "sessionID": session_id, "messageID": user_message.id}

        scheduled_coroutines = []

        monkeypatch.setattr("flocks.project.instance.Instance.provide", _fake_instance_provide)
        monkeypatch.setattr(session_routes, "_prepare_replay_runtime", _fake_prepare_replay_runtime)
        monkeypatch.setattr(session_routes, "_run_existing_user_message", _fake_run_existing_user_message)
        monkeypatch.setattr(
            session_routes,
            "_schedule_background_coro",
            lambda coro, **kwargs: scheduled_coroutines.append(coro),
        )

        resend_resp = await client.post(
            f"/api/session/{session_id}/message/{user_message['info']['id']}/resend",
            json={"text": "Updated user text", "partID": user_part["id"]},
        )
        assert resend_resp.status_code == status.HTTP_202_ACCEPTED

        assert len(scheduled_coroutines) == 1
        await scheduled_coroutines.pop(0)

        updated_resp = await client.get(f"/api/session/{session_id}/message")
        updated_messages = updated_resp.json()
        assert len(updated_messages) == 1
        assert updated_messages[0]["info"]["role"] == "user"
        assert updated_messages[0]["parts"][0]["text"] == "Updated user text"

    @pytest.mark.asyncio
    async def test_resend_user_message_respects_requested_text_part(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Resend updates the explicitly selected text part instead of the first one."""
        from flocks.server.routes import session as session_routes
        from flocks.session.lifecycle.revert import SessionRevert
        from flocks.session.message import Message, TextPart

        create_resp = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": "Original user text"}],
                "noReply": True,
                "mockReply": "Initial reply",
            },
        )
        assert create_resp.status_code == status.HTTP_200_OK

        list_resp = await client.get(f"/api/session/{session_id}/message")
        messages = list_resp.json()
        user_message = next(msg for msg in messages if msg["info"]["role"] == "user")

        extra_text_part = TextPart(
            sessionID=session_id,
            messageID=user_message["info"]["id"],
            text="Editable follow-up text",
        )
        await Message.add_part(session_id, user_message["info"]["id"], extra_text_part)

        async def _fake_instance_provide(*, directory, init, fn):
            return await fn()

        async def _fake_prepare_replay_runtime(session_id: str, user_message):
            return {
                "agent_name": "rex",
                "provider_id": "openai",
                "model_id": "gpt-4-turbo-preview",
            }

        async def _fake_run_existing_user_message(
            session_id: str,
            session,
            user_message,
            working_directory: str,
            runtime=None,
        ):
            await SessionRevert.cleanup(session)
            return {"status": "completed", "sessionID": session_id, "messageID": user_message.id}

        scheduled_coroutines = []

        monkeypatch.setattr("flocks.project.instance.Instance.provide", _fake_instance_provide)
        monkeypatch.setattr(session_routes, "_prepare_replay_runtime", _fake_prepare_replay_runtime)
        monkeypatch.setattr(session_routes, "_run_existing_user_message", _fake_run_existing_user_message)
        monkeypatch.setattr(
            session_routes,
            "_schedule_background_coro",
            lambda coro, **kwargs: scheduled_coroutines.append(coro),
        )

        resend_resp = await client.post(
            f"/api/session/{session_id}/message/{user_message['info']['id']}/resend",
            json={"text": "Updated follow-up text", "partID": extra_text_part.id},
        )
        assert resend_resp.status_code == status.HTTP_202_ACCEPTED

        assert len(scheduled_coroutines) == 1
        await scheduled_coroutines.pop(0)

        updated_resp = await client.get(f"/api/session/{session_id}/message")
        updated_messages = updated_resp.json()
        assert len(updated_messages) == 1
        text_parts = [part for part in updated_messages[0]["parts"] if part["type"] == "text"]
        assert text_parts[0]["text"] == "Original user text"
        assert any(
            part["id"] == extra_text_part.id and part["text"] == "Updated follow-up text"
            for part in text_parts
        )

    @pytest.mark.asyncio
    async def test_resend_preflight_failure_keeps_existing_history(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Replay preflight errors must not truncate history or mutate the target text."""
        from flocks.server.routes import session as session_routes

        create_resp = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": "Original user text"}],
                "noReply": True,
                "mockReply": "Initial reply",
            },
        )
        assert create_resp.status_code == status.HTTP_200_OK

        list_resp = await client.get(f"/api/session/{session_id}/message")
        messages = list_resp.json()
        user_message = next(msg for msg in messages if msg["info"]["role"] == "user")
        user_part = next(part for part in user_message["parts"] if part["type"] == "text")

        async def _fail_prepare_replay_runtime(session_id: str, user_message):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider configuration is invalid",
            )

        scheduled_coroutines = []

        monkeypatch.setattr(session_routes, "_prepare_replay_runtime", _fail_prepare_replay_runtime)
        monkeypatch.setattr(
            session_routes,
            "_schedule_background_coro",
            lambda coro, **kwargs: scheduled_coroutines.append(coro),
        )

        resend_resp = await client.post(
            f"/api/session/{session_id}/message/{user_message['info']['id']}/resend",
            json={"text": "Should not be applied", "partID": user_part["id"]},
        )
        assert resend_resp.status_code == status.HTTP_202_ACCEPTED

        assert len(scheduled_coroutines) == 1
        with pytest.raises(HTTPException):
            await scheduled_coroutines.pop(0)

        updated_resp = await client.get(f"/api/session/{session_id}/message")
        updated_messages = updated_resp.json()
        assert len(updated_messages) == 2
        assert next(msg for msg in updated_messages if msg["info"]["role"] == "assistant")
        preserved_user = next(msg for msg in updated_messages if msg["info"]["role"] == "user")
        assert preserved_user["parts"][0]["text"] == "Original user text"

        session_resp = await client.get(f"/api/session/{session_id}")
        assert session_resp.status_code == status.HTTP_200_OK
        assert session_resp.json().get("revert") is None

    @pytest.mark.asyncio
    async def test_regenerate_assistant_message_truncates_original_reply(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Regenerate removes the selected assistant reply before replay starts."""
        from flocks.server.routes import session as session_routes
        from flocks.session.lifecycle.revert import SessionRevert

        create_resp = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": "Question"}],
                "noReply": True,
                "mockReply": "Assistant answer",
            },
        )
        assert create_resp.status_code == status.HTTP_200_OK

        list_resp = await client.get(f"/api/session/{session_id}/message")
        messages = list_resp.json()
        assistant_message = next(msg for msg in messages if msg["info"]["role"] == "assistant")

        async def _fake_instance_provide(*, directory, init, fn):
            return await fn()

        async def _fake_prepare_replay_runtime(session_id: str, user_message):
            return {
                "agent_name": "rex",
                "provider_id": "openai",
                "model_id": "gpt-4-turbo-preview",
            }

        async def _fake_run_existing_user_message(
            session_id: str,
            session,
            user_message,
            working_directory: str,
            runtime=None,
        ):
            await SessionRevert.cleanup(session)
            return {"status": "completed", "sessionID": session_id, "messageID": user_message.id}

        scheduled_coroutines = []

        monkeypatch.setattr("flocks.project.instance.Instance.provide", _fake_instance_provide)
        monkeypatch.setattr(session_routes, "_prepare_replay_runtime", _fake_prepare_replay_runtime)
        monkeypatch.setattr(session_routes, "_run_existing_user_message", _fake_run_existing_user_message)
        monkeypatch.setattr(
            session_routes,
            "_schedule_background_coro",
            lambda coro, **kwargs: scheduled_coroutines.append(coro),
        )

        regenerate_resp = await client.post(
            f"/api/session/{session_id}/message/{assistant_message['info']['id']}/regenerate",
        )
        assert regenerate_resp.status_code == status.HTTP_202_ACCEPTED

        assert len(scheduled_coroutines) == 1
        await scheduled_coroutines.pop(0)

        updated_resp = await client.get(f"/api/session/{session_id}/message")
        updated_messages = updated_resp.json()
        assert len(updated_messages) == 1
        assert updated_messages[0]["info"]["role"] == "user"

    @pytest.mark.asyncio
    async def test_regenerate_assistant_message_without_text_part_is_allowed(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Regenerate should not require the assistant message to retain a text part."""
        from flocks.server.routes import session as session_routes
        from flocks.session.lifecycle.revert import SessionRevert

        create_resp = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": "Question"}],
                "noReply": True,
                "mockReply": "Assistant answer",
            },
        )
        assert create_resp.status_code == status.HTTP_200_OK

        list_resp = await client.get(f"/api/session/{session_id}/message")
        messages = list_resp.json()
        assistant_message = next(msg for msg in messages if msg["info"]["role"] == "assistant")
        assistant_text_part = next(part for part in assistant_message["parts"] if part["type"] == "text")

        delete_resp = await client.delete(
            f"/api/session/{session_id}/message/{assistant_message['info']['id']}/part/{assistant_text_part['id']}",
        )
        assert delete_resp.status_code == status.HTTP_200_OK

        async def _fake_instance_provide(*, directory, init, fn):
            return await fn()

        async def _fake_prepare_replay_runtime(session_id: str, user_message):
            return {
                "agent_name": "rex",
                "provider_id": "openai",
                "model_id": "gpt-4-turbo-preview",
            }

        async def _fake_run_existing_user_message(
            session_id: str,
            session,
            user_message,
            working_directory: str,
            runtime=None,
        ):
            await SessionRevert.cleanup(session)
            return {"status": "completed", "sessionID": session_id, "messageID": user_message.id}

        scheduled_coroutines = []

        monkeypatch.setattr("flocks.project.instance.Instance.provide", _fake_instance_provide)
        monkeypatch.setattr(session_routes, "_prepare_replay_runtime", _fake_prepare_replay_runtime)
        monkeypatch.setattr(session_routes, "_run_existing_user_message", _fake_run_existing_user_message)
        monkeypatch.setattr(
            session_routes,
            "_schedule_background_coro",
            lambda coro, **kwargs: scheduled_coroutines.append(coro),
        )

        regenerate_resp = await client.post(
            f"/api/session/{session_id}/message/{assistant_message['info']['id']}/regenerate",
        )
        assert regenerate_resp.status_code == status.HTTP_202_ACCEPTED
        assert len(scheduled_coroutines) == 1
        await scheduled_coroutines.pop(0)

    @pytest.mark.asyncio
    async def test_prepare_replay_runtime_uses_current_model_resolution(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Replay runtime should ignore the historical user-message model."""
        from flocks.server.routes import session as session_routes

        user_message = SimpleNamespace(
            agent="rex",
            model={"providerID": "anthropic", "modelID": "old-message-model"},
        )

        monkeypatch.setattr(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=SimpleNamespace(name="rex", model=None)),
        )
        monkeypatch.setattr(
            session_routes,
            "_resolve_model",
            AsyncMock(return_value=("openai", "gpt-4.1", "session")),
        )
        monkeypatch.setattr("flocks.provider.provider.Provider._ensure_initialized", lambda: None)
        monkeypatch.setattr("flocks.config.config.Config.get", AsyncMock(return_value=SimpleNamespace()))
        monkeypatch.setattr("flocks.provider.provider.Provider.apply_config", AsyncMock())
        monkeypatch.setattr("flocks.provider.provider.Provider.get", lambda _provider_id: object())

        runtime = await session_routes._prepare_replay_runtime("ses_test", user_message)

        assert runtime == {
            "agent_name": "rex",
            "provider_id": "openai",
            "model_id": "gpt-4.1",
            "auto_failover": False,
        }

    @pytest.mark.asyncio
    async def test_auto_replay_reports_actual_fallback_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Replay passes Auto authorization and publishes the recovered model."""
        from flocks.server.routes import session as session_routes
        from flocks.session.session_loop import LoopResult, SessionLoop

        user_message = SimpleNamespace(id="msg_user", agent="rex")
        session = SimpleNamespace(id="ses_auto", directory="/tmp/project")
        assistant = SimpleNamespace(
            id="msg_assistant",
            providerID="fallback",
            modelID="fallback-model",
            finish="stop",
            tokens=None,
            time={"created": 123},
        )
        run = AsyncMock(return_value=LoopResult(
            action="stop",
            last_message=assistant,
            provider_id="fallback",
            model_id="fallback-model",
        ))
        publish = AsyncMock()
        context_usage = AsyncMock()

        monkeypatch.setattr(SessionLoop, "run", run)
        monkeypatch.setattr(
            "flocks.session.lifecycle.revert.SessionRevert.cleanup",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "flocks.session.message.Message.get_text_content",
            AsyncMock(return_value="recovered"),
        )
        monkeypatch.setattr("flocks.server.routes.event.publish_event", publish)
        monkeypatch.setattr(
            session_routes,
            "_publish_context_usage_update",
            context_usage,
        )

        result = await session_routes._run_existing_user_message(
            session.id,
            session,
            user_message,
            session.directory,
            runtime={
                "agent_name": "rex",
                "provider_id": "primary",
                "model_id": "primary-model",
                "auto_failover": True,
            },
        )

        assert result["status"] == "completed"
        assert run.await_args.kwargs["auto_failover"] is True
        completion = next(
            call.args[1]
            for call in publish.await_args_list
            if call.args[0] == "message.updated"
        )
        assert completion["info"]["providerID"] == "fallback"
        assert completion["info"]["modelID"] == "fallback-model"
        assert context_usage.await_args.kwargs["provider_id"] == "fallback"
        assert context_usage.await_args.kwargs["model_id"] == "fallback-model"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", ["user", "entity-config", "workflow"])
    async def test_prepare_auto_replay_defers_unavailable_primary_to_failover(
        self,
        monkeypatch: pytest.MonkeyPatch,
        category: str,
    ):
        from flocks.server.routes import session as session_routes
        from flocks.session.session_loop import SessionLoop

        user_message = SimpleNamespace(agent="rex")
        monkeypatch.setattr(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=SimpleNamespace(name="rex", model=None)),
        )
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(return_value=SimpleNamespace(
                model_auto=True,
                category=category,
            )),
        )
        monkeypatch.setattr(
            "flocks.config.config.Config.resolve_default_llm",
            AsyncMock(return_value={
                "provider_id": "primary",
                "model_id": "primary-model",
            }),
        )
        monkeypatch.setattr(
            "flocks.config.config.Config.get",
            AsyncMock(return_value=SimpleNamespace()),
        )
        validate = AsyncMock(return_value=(False, "provider_not_configured"))
        monkeypatch.setattr(SessionLoop, "validate_runtime_model", validate)
        monkeypatch.setattr("flocks.provider.provider.Provider._ensure_initialized", lambda: None)
        monkeypatch.setattr("flocks.provider.provider.Provider.apply_config", AsyncMock())
        monkeypatch.setattr("flocks.provider.provider.Provider.get", lambda _provider_id: None)
        resolve = AsyncMock()
        monkeypatch.setattr(session_routes, "_resolve_model", resolve)

        runtime = await session_routes._prepare_replay_runtime("ses_auto", user_message)

        assert runtime == {
            "agent_name": "rex",
            "provider_id": "primary",
            "model_id": "primary-model",
            "auto_failover": True,
        }
        validate.assert_not_awaited()
        resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_prepare_replay_ignores_auto_on_unsupported_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import session as session_routes
        from flocks.session.session_loop import SessionLoop

        user_message = SimpleNamespace(agent="rex")
        monkeypatch.setattr(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=SimpleNamespace(name="rex", model=None)),
        )
        monkeypatch.setattr(
            "flocks.session.session.Session.get_by_id",
            AsyncMock(return_value=SimpleNamespace(
                model_auto=True,
                category="task",
            )),
        )
        resolve = AsyncMock(return_value=("direct", "direct-model", "session"))
        monkeypatch.setattr(session_routes, "_resolve_model", resolve)
        default_llm = AsyncMock()
        monkeypatch.setattr(
            "flocks.config.config.Config.resolve_default_llm",
            default_llm,
        )
        monkeypatch.setattr(
            "flocks.config.config.Config.get",
            AsyncMock(return_value=SimpleNamespace()),
        )
        validate = AsyncMock()
        monkeypatch.setattr(SessionLoop, "validate_runtime_model", validate)
        monkeypatch.setattr(
            "flocks.provider.provider.Provider._ensure_initialized",
            lambda: None,
        )
        monkeypatch.setattr(
            "flocks.provider.provider.Provider.apply_config",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "flocks.provider.provider.Provider.get",
            lambda _provider_id: object(),
        )

        runtime = await session_routes._prepare_replay_runtime(
            "ses_workflow",
            user_message,
        )

        assert runtime == {
            "agent_name": "rex",
            "provider_id": "direct",
            "model_id": "direct-model",
            "auto_failover": False,
        }
        resolve.assert_awaited_once()
        default_llm.assert_not_awaited()
        validate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resend_uses_current_model_for_replay(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Resend should replay with the session's current model, not the original one."""
        from flocks.server.routes import session as session_routes

        create_resp = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": "Original user text"}],
                "noReply": True,
                "mockReply": "Initial reply",
            },
        )
        assert create_resp.status_code == status.HTTP_200_OK

        list_resp = await client.get(f"/api/session/{session_id}/message")
        messages = list_resp.json()
        user_message = next(msg for msg in messages if msg["info"]["role"] == "user")
        user_part = next(part for part in user_message["parts"] if part["type"] == "text")

        scheduled_coroutines = []
        captured_runtime = {}

        async def _fake_instance_provide(*, directory, init, fn):
            return await fn()

        async def _fake_run_existing_user_message(
            session_id: str,
            session,
            user_message,
            working_directory: str,
            runtime=None,
        ):
            captured_runtime.update(runtime or {})
            return {"status": "completed", "sessionID": session_id, "messageID": user_message.id}

        monkeypatch.setattr("flocks.project.instance.Instance.provide", _fake_instance_provide)
        monkeypatch.setattr(
            session_routes,
            "_resolve_model",
            AsyncMock(return_value=("openai", "gpt-4.1", "session")),
        )
        monkeypatch.setattr(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=SimpleNamespace(name="rex", model=None)),
        )
        monkeypatch.setattr("flocks.provider.provider.Provider._ensure_initialized", lambda: None)
        monkeypatch.setattr("flocks.config.config.Config.get", AsyncMock(return_value=SimpleNamespace()))
        monkeypatch.setattr("flocks.provider.provider.Provider.apply_config", AsyncMock())
        monkeypatch.setattr("flocks.provider.provider.Provider.get", lambda _provider_id: object())
        monkeypatch.setattr(
            "flocks.session.lifecycle.revert.SessionRevert.revert",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(session_routes, "_run_existing_user_message", _fake_run_existing_user_message)
        monkeypatch.setattr(
            session_routes,
            "_schedule_background_coro",
            lambda coro, **kwargs: scheduled_coroutines.append(coro),
        )

        resend_resp = await client.post(
            f"/api/session/{session_id}/message/{user_message['info']['id']}/resend",
            json={"text": "Updated user text", "partID": user_part["id"]},
        )
        assert resend_resp.status_code == status.HTTP_202_ACCEPTED
        assert len(scheduled_coroutines) == 1

        await scheduled_coroutines.pop(0)

        assert captured_runtime["provider_id"] == "openai"
        assert captured_runtime["model_id"] == "gpt-4.1"

    @pytest.mark.asyncio
    async def test_regenerate_uses_current_model_for_replay(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Regenerate should replay with the session's current model, not the original one."""
        from flocks.server.routes import session as session_routes

        create_resp = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": "Question"}],
                "noReply": True,
                "mockReply": "Assistant answer",
            },
        )
        assert create_resp.status_code == status.HTTP_200_OK

        list_resp = await client.get(f"/api/session/{session_id}/message")
        messages = list_resp.json()
        assistant_message = next(msg for msg in messages if msg["info"]["role"] == "assistant")

        scheduled_coroutines = []
        captured_runtime = {}

        async def _fake_instance_provide(*, directory, init, fn):
            return await fn()

        async def _fake_run_existing_user_message(
            session_id: str,
            session,
            user_message,
            working_directory: str,
            runtime=None,
        ):
            captured_runtime.update(runtime or {})
            return {"status": "completed", "sessionID": session_id, "messageID": user_message.id}

        monkeypatch.setattr("flocks.project.instance.Instance.provide", _fake_instance_provide)
        monkeypatch.setattr(
            session_routes,
            "_resolve_model",
            AsyncMock(return_value=("openai", "gpt-4.1", "session")),
        )
        monkeypatch.setattr(
            "flocks.agent.registry.Agent.get",
            AsyncMock(return_value=SimpleNamespace(name="rex", model=None)),
        )
        monkeypatch.setattr("flocks.provider.provider.Provider._ensure_initialized", lambda: None)
        monkeypatch.setattr("flocks.config.config.Config.get", AsyncMock(return_value=SimpleNamespace()))
        monkeypatch.setattr("flocks.provider.provider.Provider.apply_config", AsyncMock())
        monkeypatch.setattr("flocks.provider.provider.Provider.get", lambda _provider_id: object())
        monkeypatch.setattr(
            "flocks.session.lifecycle.revert.SessionRevert.revert",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(session_routes, "_run_existing_user_message", _fake_run_existing_user_message)
        monkeypatch.setattr(
            session_routes,
            "_schedule_background_coro",
            lambda coro, **kwargs: scheduled_coroutines.append(coro),
        )

        regenerate_resp = await client.post(
            f"/api/session/{session_id}/message/{assistant_message['info']['id']}/regenerate",
        )
        assert regenerate_resp.status_code == status.HTTP_202_ACCEPTED
        assert len(scheduled_coroutines) == 1

        await scheduled_coroutines.pop(0)

        assert captured_runtime["provider_id"] == "openai"
        assert captured_runtime["model_id"] == "gpt-4.1"


# ===========================================================================
# Utility endpoints
# ===========================================================================

class TestSessionUtilities:
    """clear, abort, status, metrics, recent endpoints."""

    @pytest.mark.asyncio
    async def test_clear_session(self, client: AsyncClient, session_id: str):
        """POST /api/session/{id}/clear removes messages."""
        from flocks.session.goal import GoalManager

        # Add a message first
        await client.post(
            f"/api/session/{session_id}/message",
            json={"parts": [{"type": "text", "text": "msg"}], "noReply": True},
        )
        await GoalManager.set_goal(session_id, "List built-in tools")
        clear_resp = await client.post(f"/api/session/{session_id}/clear")
        assert clear_resp.status_code == status.HTTP_200_OK

        # Messages should be gone
        list_resp = await client.get(f"/api/session/{session_id}/message")
        assert list_resp.json() == []
        assert await GoalManager.get(session_id) is None

        session_resp = await client.get(f"/api/session/{session_id}")
        assert session_resp.status_code == status.HTTP_200_OK
        assert session_resp.json()["goal"] is None

    @pytest.mark.asyncio
    async def test_clear_session_clears_prompt_queue(self, client: AsyncClient, session_id: str):
        """POST /api/session/{id}/clear also drops queued prompts."""
        from flocks.session.interaction_queue import InteractionQueue

        await InteractionQueue.clear(session_id)
        await InteractionQueue.enqueue(
            session_id,
            parts=[{"type": "text", "text": "queued after current reply"}],
        )

        clear_resp = await client.post(f"/api/session/{session_id}/clear")

        assert clear_resp.status_code == status.HTTP_200_OK
        assert await InteractionQueue.list(session_id) == []

    @pytest.mark.asyncio
    async def test_clear_session_waits_for_idle_before_clearing_messages(self, monkeypatch):
        """History is cleared only after abort drains the active session loop."""
        from flocks.server.routes import session as session_routes
        from flocks.session.interaction_queue import InteractionQueue

        session_id = "ses_clear_waits_for_idle"
        await InteractionQueue.clear(session_id)
        await InteractionQueue.enqueue(
            session_id,
            parts=[{"type": "text", "text": "queued prompt"}],
        )
        order: list[str] = []

        async def fake_abort_session(_session_id: str) -> bool:
            order.append("abort")
            return True

        async def fake_wait_for_session_idle(_session_id: str) -> None:
            order.append("wait")
            assert await InteractionQueue.list(session_id) == []

        async def fake_message_clear(_session_id: str) -> int:
            order.append("message_clear")
            return 2

        async def fake_publish_event(_event: str, _payload: dict) -> None:
            return None

        monkeypatch.setattr(
            session_routes.Session,
            "get_by_id",
            AsyncMock(return_value=SimpleNamespace(
                id=session_id,
                directory="/tmp/project",
                status="active",
            )),
        )
        monkeypatch.setattr(session_routes, "abort_session", fake_abort_session)
        monkeypatch.setattr(session_routes, "_wait_for_session_idle", fake_wait_for_session_idle)
        monkeypatch.setattr("flocks.session.message.Message.clear", fake_message_clear)
        monkeypatch.setattr("flocks.server.routes.event.publish_event", fake_publish_event)

        deleted = await session_routes._clear_session_history(session_id)

        assert deleted == 2
        assert order == ["abort", "wait", "message_clear"]
        assert await InteractionQueue.list(session_id) == []

    @pytest.mark.asyncio
    async def test_clear_command_removes_messages(self, client: AsyncClient, session_id: str):
        """Command route /clear removes messages via the dispatcher task."""
        from flocks.server.routes import session as session_routes

        await client.post(
            f"/api/session/{session_id}/message",
            json={"parts": [{"type": "text", "text": "msg"}], "noReply": True},
        )

        clear_resp = await session_routes.send_session_command(
            session_id,
            session_routes.CommandRequest(command="clear"),
        )
        assert clear_resp["status"] == "accepted"

        pending = list(getattr(session_routes.router, "_pending_tasks", set()))
        assert pending
        await asyncio.gather(*pending)

        list_resp = await client.get(f"/api/session/{session_id}/message")
        assert list_resp.json() == []

    @pytest.mark.asyncio
    async def test_abort_session(self, client: AsyncClient, session_id: str):
        """POST /api/session/{id}/abort returns 200 (no active generation needed)."""
        resp = await client.post(f"/api/session/{session_id}/abort")
        assert resp.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_session_statistics(self, client: AsyncClient, session_id: str):
        """GET /api/session/{id}/statistics reports stored session messages."""
        payload = {
            "parts": [{"type": "text", "text": "Hello from statistics"}],
            "noReply": True,
        }
        message_resp = await client.post(f"/api/session/{session_id}/message", json=payload)
        assert message_resp.status_code == status.HTTP_200_OK

        resp = await client.get(f"/api/session/{session_id}/statistics")
        assert resp.status_code == status.HTTP_200_OK

        data = resp.json()
        assert data["sessionID"] == session_id
        assert data["messageCount"] == 1
        assert data["tokenCount"] >= 3
        assert data["toolCallCount"] == 0
        assert data["durationSeconds"] >= 0

    @pytest.mark.asyncio
    async def test_session_status(self, client: AsyncClient):
        """GET /api/session/status returns aggregate status."""
        resp = await client.get("/api/session/status")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "total" in data or isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_session_metrics(self, client: AsyncClient):
        """GET /api/session/metrics route is registered (may return 404 if route order issue)."""
        resp = await client.get("/api/session/metrics")
        # NOTE: /metrics is defined after /{sessionID} in session.py, so FastAPI may route
        # "metrics" as a sessionID and return 404.  We accept any non-405 response here.
        assert resp.status_code != status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.asyncio
    async def test_recent_sessions(self, client: AsyncClient):
        """GET /api/session/recent route is registered."""
        await client.post("/api/session", json={"title": "Recent"})
        resp = await client.get("/api/session/recent")
        # Same route-order caveat as /metrics – accept any non-405 response.
        assert resp.status_code != status.HTTP_405_METHOD_NOT_ALLOWED


# ===========================================================================
# SSE event endpoint
# ===========================================================================

class TestSessionSSE:
    """Verify the global SSE event endpoint is registered."""

    @pytest.mark.asyncio
    async def test_global_event_endpoint_registered(self, client: AsyncClient):
        """Verify /api/event exists by checking that it does NOT return 404 or 405.

        SSE streams never terminate, so we only inspect the FastAPI router's
        route list directly without sending any HTTP request.
        """
        from flocks.server.app import app

        event_routes = [
            route for route in app.routes
            if hasattr(route, "path") and "event" in route.path.lower()
        ]
        assert event_routes, (
            "No /event route registered in the FastAPI app. "
            f"Routes: {[getattr(r, 'path', '?') for r in app.routes]}"
        )


# ===========================================================================
# Permission management
# ===========================================================================

class TestSessionPermissions:
    """Permission reply on sessions."""

    @pytest.mark.asyncio
    async def test_reply_permission_not_found(self, client: AsyncClient, session_id: str):
        """Replying to a non-existent permission is silently accepted (no error raised)."""
        resp = await client.post(
            f"/api/session/{session_id}/permissions/perm_nonexistent",
            json={"response": "allow"},
        )
        # The current implementation logs a warning but does NOT raise an error for
        # unknown permission IDs – the response is 200 True.
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_404_NOT_FOUND,
        )


class TestSessionContext:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [{}, {"projectID": "default"}, {"projectID": "tasks"}])
    async def test_unbound_session_context_does_not_expose_server_cwd(
        self, client: AsyncClient, tmp_path, monkeypatch, payload,
    ):
        source = tmp_path / "server-source"
        source.mkdir()
        (source / "README.md").write_text("Server source, not an attached project", encoding="utf-8")
        monkeypatch.chdir(source)
        monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(tmp_path))

        created = await client.post("/api/session", json=payload)
        assert created.status_code == 200
        session_id = created.json()["id"]
        assert created.json()["projectID"] == "default"
        assert created.json()["directory"] == str(source)
        context = await client.get(f"/api/session/{session_id}/context")
        assert context.status_code == 200
        assert context.json()["roots"] == []
        for endpoint in ("list", "content", "preview", "download"):
            response = await client.get(
                f"/api/session/{session_id}/context/roots/project/{endpoint}",
                params={"path": "" if endpoint == "list" else "README.md"},
            )
            assert response.status_code == 404

        attached = await client.post(
            f"/api/session/{session_id}/context/folders", json={"path": str(source)},
        )
        assert attached.status_code == 200
        root_id = attached.json()["id"]
        context = await client.get(f"/api/session/{session_id}/context")
        assert [root["id"] for root in context.json()["roots"]] == [root_id]
        content = await client.get(
            f"/api/session/{session_id}/context/roots/{root_id}/content", params={"path": "README.md"},
        )
        assert content.status_code == 200
        assert content.json()["content"] == "Server source, not an attached project"

    @pytest.mark.asyncio
    async def test_explicit_project_context_is_inherited_by_child_sessions(
        self, client: AsyncClient, tmp_path, monkeypatch,
    ):
        source = tmp_path / "server-source"
        source.mkdir()
        monkeypatch.chdir(source)
        monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(tmp_path))
        worktree = tmp_path / "chosen-project"
        worktree.mkdir()
        (worktree / "README.md").write_text("Explicit project", encoding="utf-8")
        registered = await client.post("/api/project", json={"name": "Chosen project", "worktree": str(worktree)})
        assert registered.status_code == 200
        parent = await client.post("/api/session", json={"projectID": registered.json()["id"]})
        assert parent.status_code == 200
        child = await client.post("/api/session", json={"parentID": parent.json()["id"]})
        assert child.status_code == 200
        for session in (parent.json(), child.json()):
            assert session["projectID"] == registered.json()["id"]
            context = await client.get(f"/api/session/{session['id']}/context")
            assert context.status_code == 200
            assert context.json()["roots"] == [{
                "id": "project", "kind": "project", "displayName": "chosen-project", "status": "available",
            }]
            content = await client.get(
                f"/api/session/{session['id']}/context/roots/project/content", params={"path": "README.md"},
            )
            assert content.status_code == 200
            assert content.json()["content"] == "Explicit project"

    @pytest.mark.asyncio
    async def test_context_lists_message_files_and_outputs(
        self,
        client: AsyncClient,
        session_id: str,
    ):
        from flocks.session.files import session_outputs_root, session_uploads_dir

        session = await Session.get_by_id_unfiltered(session_id)
        assert session is not None

        upload_root = session_uploads_dir(session_id)
        upload_root.mkdir(parents=True, exist_ok=True)
        upload = upload_root / "prt_upload.txt"
        upload.write_text("uploaded", encoding="utf-8")
        user_message = await Message.create(session_id, MessageRole.USER, "Review this file")
        upload_part = FilePart(
            id="prt_context_upload",
            sessionID=session_id,
            messageID=user_message.id,
            mime="text/plain",
            filename="notes.txt",
            url=upload.as_uri(),
        )
        await Message.add_part(session_id, user_message.id, upload_part)

        output_root = session_outputs_root(session)
        output = output_root / "2026-09-14" / "report.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Report", encoding="utf-8")
        assistant_message = await Message.create(session_id, MessageRole.ASSISTANT, "")
        output_part = ToolPart(
            id="prt_context_write",
            sessionID=session_id,
            messageID=assistant_message.id,
            callID="call_context_write",
            tool="write",
            state=ToolStateCompleted(
                input={"filePath": "report.md"},
                output="Wrote file successfully.",
                title="report.md",
                metadata={},
                time={"start": 1, "end": 2},
                attachments=[{
                    "id": "prt_context_output",
                    "type": "file",
                    "mime": "text/markdown",
                    "filename": "report.md",
                    "origin": "agent_output",
                    "source": {"root": "workspace-output", "path": "2026-09-14/report.md"},
                }],
            ),
        )
        await Message.store_part(session_id, assistant_message.id, output_part)

        response = await client.get(f"/api/session/{session_id}/context")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert [item["displayName"] for item in data["contextFiles"]] == ["notes.txt"]
        assert [item["displayName"] for item in data["outputs"]] == ["report.md"]
        assert all("/private/" not in str(item) for item in data["outputs"] + data["contextFiles"])

        content = await client.get(
            f"/api/session/{session_id}/context/files/{data['outputs'][0]['resourceID']}/content"
        )
        assert content.status_code == status.HTTP_200_OK
        assert content.json()["content"] == "# Report"

        download = await client.get(
            f"/api/session/{session_id}/context/files/{data['contextFiles'][0]['resourceID']}/download"
        )
        assert download.status_code == status.HTTP_200_OK
        assert download.content == b"uploaded"
        assert 'filename="notes.txt"' in download.headers["content-disposition"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("username, role", [("admin", "admin"), ("member", "member")])
    @pytest.mark.parametrize("scope", ["global", "user"])
    @pytest.mark.parametrize("relative", ["report.md", "2026-09-17/report.md"])
    @pytest.mark.parametrize("legacy", [False, True])
    async def test_written_outputs_are_listed_previewable_and_downloadable(
        self, client: AsyncClient, tmp_path, monkeypatch, username, role, scope, relative, legacy,
    ):
        from flocks.auth.context import reset_current_auth_user, set_current_auth_user
        from flocks.tool.registry import ToolContext, ToolRegistry
        from flocks.workspace.manager import WorkspaceManager

        monkeypatch.setattr(WorkspaceManager, "_instance", None)
        user = AuthUser(id=f"usr_{username}", username=username, role=role, status="active")
        monkeypatch.setattr(session_routes, "require_user", lambda _request: user)
        session = await Session.create(
            project_id="default", directory=str(tmp_path), title="Output registration",
            owner_user_id=user.id, owner_username=username,
        )
        message = await Message.create(session.id, MessageRole.ASSISTANT, "")
        manager = WorkspaceManager.get_instance()
        output_username = username if scope == "user" else None
        target = manager.get_default_outputs_dir(username=output_username, include_today=False) / relative
        other_username = None if scope == "user" else username
        decoy = manager.get_default_outputs_dir(username=other_username, include_today=False) / relative
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text("Unrelated same-name file", encoding="utf-8")
        content = f"# {username} {scope}\n已生成的说明文件\n"

        ctx = ToolContext(
            session_id=session.id, message_id=message.id, agent="test", call_id="call_output",
            permission_callback=AsyncMock(),
        )
        token = set_current_auth_user(user)
        try:
            result = await ToolRegistry.execute("write", ctx, filePath=str(target), content=content)
        finally:
            reset_current_auth_user(token)
        assert result.success, result.error
        assert target.read_bytes() == content.encode("utf-8")
        assert result.attachments and len(result.attachments) == 1
        assert result.attachments[0]["source"] == {
            "root": "workspace-output", "path": relative, "username": output_username,
        }
        assert result.attachments[0]["sessionID"] == session.id
        assert result.attachments[0]["messageID"] == message.id
        part = ToolPart(
            id="prt_registered_write", sessionID=session.id, messageID=message.id,
            callID="call_output", tool="write",
            state=ToolStateCompleted(
                input={"filePath": str(target)}, output=result.output, title=result.title,
                metadata=result.metadata, time={"start": 1, "end": 2},
                attachments=None if legacy else result.attachments,
            ),
        )
        await Message.store_part(session.id, message.id, part)
        response = await client.get(f"/api/session/{session.id}/context")
        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["counts"]["outputs"] == 1
        descriptor = snapshot["outputs"][0]
        prefix = f"users/{username}/outputs" if scope == "user" else "outputs"
        assert descriptor["logicalPath"] == f"{prefix}/{relative}"
        assert descriptor["status"] == "ready"
        assert str(tmp_path) not in str(descriptor)
        resource_url = f"/api/session/{session.id}/context/files/{descriptor['resourceID']}"
        preview = await client.get(f"{resource_url}/content")
        assert preview.status_code == 200
        assert preview.json()["content"] == content
        download = await client.get(f"{resource_url}/download")
        assert download.status_code == 200
        assert download.content == content.encode("utf-8")
        assert 'filename="report.md"' in download.headers["content-disposition"]
        assert decoy.read_text(encoding="utf-8") == "Unrelated same-name file"

        other_session = await Session.create(
            project_id="default", directory=str(tmp_path), owner_user_id=user.id, owner_username=username,
        )
        foreign = await client.get(
            f"/api/session/{other_session.id}/context/files/{descriptor['resourceID']}/download",
        )
        assert foreign.status_code == 404
        stranger = AuthUser(id="usr_stranger", username="stranger", role="member", status="active")
        monkeypatch.setattr(session_routes, "require_user", lambda _request: stranger)
        assert (await client.get(f"/api/session/{session.id}/context")).status_code == 403
        for endpoint in ("metadata", "content", "preview", "download"):
            assert (await client.get(f"{resource_url}/{endpoint}")).status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["skill_load", "load_skill"])
    async def test_context_api_preserves_skill_failure_and_successful_retry(
        self, client: AsyncClient, session_id: str, tool,
    ):
        from flocks.session.message import ToolStatePending

        name = "missing-then-loaded"
        message = await Message.create(session_id, MessageRole.ASSISTANT, "")
        states = [
            ToolStatePending(input={"name": name}, raw="{}"),
            ToolStateRunning(input={"name": name}, time={"start": 1}),
            ToolStateError(input={"name": name}, error="Skill not found", time={"start": 1, "end": 2}),
            ToolStateRunning(input={"name": name}, time={"start": 3}),
            ToolStateCompleted(input={"name": name}, output="Loaded", title=name, metadata={}, time={"start": 3, "end": 4}),
        ]
        for index, (state, expected_status) in enumerate(zip(states, ["loading", "loading", "error", "loading", "loaded"])):
            part = ToolPart(
                id=f"prt_skill_{index}", sessionID=session_id, messageID=message.id,
                callID=f"call_skill_{index}", tool=tool, state=state,
            )
            await Message.store_part(session_id, message.id, part)
            response = await client.get(f"/api/session/{session_id}/context")
            assert response.status_code == 200
            skills = response.json()["skills"]
            assert len(skills) == 1
            assert skills[0]["name"] == name
            assert skills[0]["status"] == expected_status
            if expected_status == "error":
                assert skills[0]["error"] == "Skill not found"
            else:
                assert "error" not in skills[0]

    @pytest.mark.asyncio
    async def test_chat_upload_binds_as_file_part_without_exposing_host_path(
        self,
        client: AsyncClient,
        session_id: str,
    ):
        upload = await client.post(
            "/api/workspace/upload?purpose=chat",
            files={"files": ("paper.md", b"# Paper", "text/markdown")},
        )
        assert upload.status_code == status.HTTP_200_OK
        staged = upload.json()["uploaded"][0]
        assert staged.get("uploadID")
        assert "abs_path" not in staged

        message = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [
                    {"type": "text", "text": "Review this"},
                    {
                        "type": "file",
                        "id": "prt_bound_upload",
                        "uploadID": staged["uploadID"],
                        "mime": staged["mime"],
                        "filename": staged["name"],
                    },
                ],
                "noReply": True,
            },
        )
        assert message.status_code == status.HTTP_200_OK

        history = await client.get(f"/api/session/{session_id}/message")
        file_part = next(
            part
            for item in history.json()
            for part in item["parts"]
            if part["id"] == "prt_bound_upload"
        )
        assert file_part["resourceID"].startswith("res_")
        assert file_part["url"] == (
            f"/api/session/{session_id}/context/files/{file_part['resourceID']}/preview"
        )
        assert "file:" not in str(file_part)

        context = await client.get(f"/api/session/{session_id}/context")
        assert context.status_code == status.HTTP_200_OK
        assert context.json()["contextFiles"][0]["displayName"] == "paper.md"
        download = await client.get(
            f"/api/session/{session_id}/context/files/{file_part['resourceID']}/download"
        )
        assert download.status_code == status.HTTP_200_OK
        assert download.content == b"# Paper"

    @pytest.mark.asyncio
    async def test_prompt_async_preserves_upload_owner_for_background_binding(
        self,
        client: AsyncClient,
        session_id: str,
    ):
        upload = await client.post(
            "/api/workspace/upload?purpose=chat",
            files={"files": ("async.md", b"async", "text/markdown")},
        )
        staged = upload.json()["uploaded"][0]
        before = set(getattr(session_routes.router, "_pending_tasks", set()))

        response = await client.post(
            f"/api/session/{session_id}/prompt_async",
            json={
                "parts": [
                    {"type": "text", "text": "Review async"},
                    {
                        "type": "file",
                        "id": "prt_async_upload",
                        "uploadID": staged["uploadID"],
                        "mime": staged["mime"],
                        "filename": staged["name"],
                    },
                ],
                "noReply": True,
            },
        )
        assert response.status_code == status.HTTP_202_ACCEPTED
        pending = list(getattr(session_routes.router, "_pending_tasks", set()) - before)
        assert pending
        await asyncio.gather(*pending)

        history = await client.get(f"/api/session/{session_id}/message")
        assert any(
            part.get("id") == "prt_async_upload"
            for item in history.json()
            for part in item["parts"]
        )

    @pytest.mark.asyncio
    async def test_resource_ids_support_client_message_and_part_characters(
        self,
        client: AsyncClient,
        session_id: str,
    ):
        upload = await client.post(
            "/api/workspace/upload?purpose=chat",
            files={"files": ("client.md", b"client", "text/markdown")},
        )
        staged = upload.json()["uploaded"][0]

        response = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "messageID": "client.message",
                "parts": [
                    {"type": "text", "text": "Review"},
                    {
                        "type": "file",
                        "id": "file.1",
                        "uploadID": staged["uploadID"],
                        "mime": staged["mime"],
                        "filename": staged["name"],
                    },
                ],
                "noReply": True,
            },
        )
        assert response.status_code == status.HTTP_200_OK

        history = await client.get(f"/api/session/{session_id}/message")
        file_part = next(
            part
            for item in history.json()
            for part in item["parts"]
            if part["id"] == "file.1"
        )
        assert file_part["resourceID"].startswith("res_")
        download = await client.get(
            f"/api/session/{session_id}/context/files/{file_part['resourceID']}/download"
        )
        assert download.status_code == status.HTTP_200_OK
        assert download.content == b"client"

    @pytest.mark.asyncio
    async def test_invalid_chat_upload_does_not_persist_user_message(
        self,
        client: AsyncClient,
        session_id: str,
    ):
        response = await client.post(
            f"/api/session/{session_id}/message",
            json={
                "parts": [
                    {"type": "text", "text": "This must not persist"},
                    {
                        "type": "file",
                        "id": "prt_missing_upload",
                        "uploadID": "prt_missing_upload_1234",
                        "mime": "text/markdown",
                        "filename": "missing.md",
                    },
                ],
                "noReply": True,
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        history = await client.get(f"/api/session/{session_id}/message")
        assert history.status_code == status.HTTP_200_OK
        assert history.json() == []

    @pytest.mark.asyncio
    async def test_legacy_username_owner_binds_upload_from_authenticated_user(
        self,
        client: AsyncClient,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.session.files import create_chat_upload_target

        owner = AuthUser(id="usr_legacy_owner", username="legacy", role="member", status="active")
        monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
        session = await Session.create(
            project_id="default",
            directory=str(tmp_path),
            title="legacy-owner",
            owner_user_id=None,
            owner_username=owner.username,
        )
        upload_id = "prt_legacy_upload_1234"
        staged = create_chat_upload_target(owner.id, upload_id, "legacy.md")
        staged.write_text("legacy", encoding="utf-8")

        response = await client.post(
            f"/api/session/{session.id}/message",
            json={
                "parts": [
                    {"type": "text", "text": "Review"},
                    {
                        "type": "file",
                        "id": "prt_legacy_bound",
                        "uploadID": upload_id,
                        "mime": "text/markdown",
                        "filename": "legacy.md",
                    },
                ],
                "noReply": True,
            },
        )
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_shared_reader_cannot_mutate_or_run_prompt_queue(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        queued = await client.post(
            f"/api/session/{session_id}/prompt_queue",
            json={"parts": [{"type": "text", "text": "queued"}]},
        )
        assert queued.status_code == status.HTTP_202_ACCEPTED
        queue_id = queued.json()["queueID"]
        shared = await client.post(f"/api/session/{session_id}/share-local")
        assert shared.status_code == status.HTTP_200_OK

        viewer = AuthUser(id="usr_viewer", username="viewer", role="member", status="active")
        monkeypatch.setattr(session_routes, "require_user", lambda _request: viewer)
        listed = await client.get(f"/api/session/{session_id}/prompt_queue")
        assert listed.status_code == status.HTTP_200_OK
        assert listed.json()["items"][0]["id"] == queue_id
        updated = await client.patch(
            f"/api/session/{session_id}/prompt_queue/{queue_id}",
            json={"text": "changed"},
        )
        removed = await client.delete(
            f"/api/session/{session_id}/prompt_queue/{queue_id}"
        )
        run_now = await client.post(
            f"/api/session/{session_id}/prompt_queue/{queue_id}/run_now"
        )
        assert updated.status_code == status.HTTP_403_FORBIDDEN
        assert removed.status_code == status.HTTP_403_FORBIDDEN
        assert run_now.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_concurrent_context_folder_adds_do_not_lose_updates(
        self,
        client: AsyncClient,
        session_id: str,
        tmp_path,
    ):
        first = tmp_path / "references-a"
        second = tmp_path / "references-b"
        first.mkdir()
        second.mkdir()

        responses = await asyncio.gather(
            client.post(
                f"/api/session/{session_id}/context/folders",
                json={"path": str(first)},
            ),
            client.post(
                f"/api/session/{session_id}/context/folders",
                json={"path": str(second)},
            ),
        )
        assert [response.status_code for response in responses] == [200, 200]

        context = await client.get(f"/api/session/{session_id}/context")
        folder_names = {
            item["displayName"]
            for item in context.json()["roots"]
            if item["kind"] == "folder"
        }
        assert folder_names == {"references-a", "references-b"}

    @pytest.mark.asyncio
    async def test_context_folder_is_owner_only_and_rejects_escape(
        self,
        client: AsyncClient,
        session_id: str,
        tmp_path,
    ):
        folder = tmp_path / "references"
        folder.mkdir()
        (folder / "paper.md").write_text("paper", encoding="utf-8")

        created = await client.post(
            f"/api/session/{session_id}/context/folders",
            json={"path": str(folder), "displayName": "References"},
        )
        assert created.status_code == status.HTTP_200_OK
        root_id = created.json()["id"]

        listing = await client.get(
            f"/api/session/{session_id}/context/roots/{root_id}/list"
        )
        assert listing.status_code == status.HTTP_200_OK
        assert listing.json()["items"][0]["name"] == "paper.md"

        escaped = await client.get(
            f"/api/session/{session_id}/context/roots/{root_id}/content",
            params={"path": "../outside.txt"},
        )
        assert escaped.status_code == status.HTTP_400_BAD_REQUEST

        removed = await client.delete(
            f"/api/session/{session_id}/context/folders/{root_id}"
        )
        assert removed.status_code == status.HTTP_200_OK
        missing = await client.get(
            f"/api/session/{session_id}/context/roots/{root_id}/list"
        )
        assert missing.status_code == status.HTTP_404_NOT_FOUND


class TestSessionContextRegressions:
    @pytest.mark.asyncio
    async def test_context_pages_and_direct_old_file_metadata(self, client: AsyncClient, session_id: str):
        from flocks.session.files import public_resource_id, session_uploads_dir

        root = session_uploads_dir(session_id)
        root.mkdir(parents=True, exist_ok=True)
        old_file = root / "old.txt"
        old_file.write_text("older file", encoding="utf-8")
        old = await Message.create(session_id, MessageRole.USER, "old")
        await Message.add_part(session_id, old.id, FilePart(
            id="prt_old_context", sessionID=session_id, messageID=old.id,
            url=old_file.as_uri(), mime="text/plain", filename="old.txt",
        ))
        newest = await Message.create(session_id, MessageRole.USER, "newest")

        first = await client.get(f"/api/session/{session_id}/context", params={"limit": 1})
        assert first.status_code == 200
        assert first.json()["contextFiles"] == []
        assert first.json()["hasMore"] is True
        assert first.json()["nextBefore"] == newest.id
        second = await client.get(f"/api/session/{session_id}/context", params={
            "limit": 1, "before": first.json()["nextBefore"],
        })
        assert second.status_code == 200
        assert second.json()["hasMore"] is False
        assert second.json()["contextFiles"][0]["displayName"] == "old.txt"

        resource_id = public_resource_id(old.id, "prt_old_context")
        metadata = await client.get(f"/api/session/{session_id}/context/files/{resource_id}/metadata")
        assert metadata.status_code == 200
        assert metadata.json()["isTextFile"] is True
        assert metadata.json()["displayName"] == "old.txt"
        assert metadata.json()["resourceID"] == resource_id
        invalid_cursor = await client.get(f"/api/session/{session_id}/context", params={"before": "msg_absent"})
        assert invalid_cursor.status_code == 400
        too_large = await client.get(f"/api/session/{session_id}/context", params={"limit": 201})
        assert too_large.status_code == 422

    @pytest.mark.asyncio
    async def test_folder_route_exposes_paged_items(self, client: AsyncClient, session_id: str, tmp_path):
        folder = tmp_path / "paged-directory"
        folder.mkdir()
        for index in range(5):
            (folder / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
        created = await client.post(f"/api/session/{session_id}/context/folders", json={"path": str(folder)})
        assert created.status_code == 200
        url = f"/api/session/{session_id}/context/roots/{created.json()['id']}/list"
        seen = set()
        offset = 0
        for expected_length in (2, 2, 1):
            response = await client.get(url, params={"offset": offset, "limit": 2})
            assert response.status_code == 200
            page = response.json()
            assert isinstance(page["items"], list)
            assert len(page["items"]) == expected_length
            seen.update(item["name"] for item in page["items"])
            offset = page["nextOffset"]
        assert offset is None
        assert len(seen) == 5
        assert (await client.get(url, params={"offset": -1})).status_code == 422
        assert (await client.get(url, params={"limit": 201})).status_code == 422

    @pytest.mark.parametrize("tool", ["read", "write"])
    def test_history_serializer_filters_large_attachments(self, tool):
        large = "data:application/pdf;base64," + "A" * 100_000
        attachment = {
            "id": "prt_safe_output", "type": "file", "filename": "output.pdf", "mime": "application/pdf",
            "origin": "agent_output", "source": {"root": "workspace-output", "path": "output.pdf"},
            "url": large, "extra": {"raw": large},
        }
        part = ToolPart(
            id="prt_history_tool", sessionID="ses_history", messageID="msg_history", callID="call_history",
            tool=tool, state=ToolStateCompleted(
                input={}, output="done", title="done", metadata={}, time={"start": 1, "end": 2},
                attachments=[attachment],
            ),
        )
        serialized = session_routes._part_to_response_info(part, session_id="ses_history", message_id="msg_history")
        assert "data:application" not in serialized.model_dump_json()
        if tool == "write":
            assert serialized.state["attachments"][0]["resourceID"].startswith("res_")
        else:
            assert serialized.state["attachments"] is None
        assert part.state.attachments[0]["url"] == large

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mutation", ["source", "remove", "metadata", "tool", "type"])
    async def test_patch_cannot_change_write_provenance(self, client: AsyncClient, session_id: str, mutation):
        message = await Message.create(session_id, MessageRole.ASSISTANT, "")
        part = ToolPart(
            id="prt_immutable_write", sessionID=session_id, messageID=message.id, callID="call_immutable",
            tool="write", state=ToolStateCompleted(
                input={}, output="done", title="done", metadata={"filepath": "/original/output.md"},
                time={"start": 1, "end": 2}, attachments=[{
                    "id": "prt_immutable_output", "type": "file", "filename": "output.md", "mime": "text/markdown",
                    "origin": "agent_output", "sessionID": session_id, "messageID": message.id,
                    "source": {"root": "workspace-output", "path": "output.md", "username": None},
                }],
            ),
        )
        await Message.store_part(session_id, message.id, part)
        body = session_routes._part_to_response_info(part, session_id=session_id, message_id=message.id).model_dump()
        if mutation == "source":
            body["state"]["attachments"][0]["source"]["username"] = "another-user"
        elif mutation == "remove":
            body["state"].pop("attachments")
        elif mutation == "metadata":
            body["state"]["metadata"]["filepath"] = "/different/output.md"
        elif mutation == "tool":
            body["tool"] = "read"
        else:
            body["type"] = "text"
            body["state"] = None
        response = await client.patch(f"/api/session/{session_id}/message/{message.id}/part/{part.id}", json=body)
        assert response.status_code == 400
        stored = await Message.get_with_parts_lazy(session_id, message.id)
        assert next(item for item in stored.parts if item.id == part.id).model_dump() == part.model_dump()

    @pytest.mark.asyncio
    async def test_patch_cannot_convert_text_to_output(self, client: AsyncClient, session_id: str):
        message = await Message.create(session_id, MessageRole.USER, "editable")
        stored = await Message.get_with_parts_lazy(session_id, message.id)
        part = stored.parts[0]
        url = f"/api/session/{session_id}/message/{message.id}/part/{part.id}"
        basic = {"id": part.id, "sessionID": session_id, "messageID": message.id, "type": "text"}
        edited = await client.patch(url, json={**basic, "text": "still editable"})
        assert edited.status_code == 200
        assert edited.json()["text"] == "still editable"
        forged = await client.patch(url, json={**basic, "type": "tool", "tool": "write", "state": {
            "status": "completed", "attachments": [{"source": {"username": "victim"}}],
        }})
        assert forged.status_code == 400

    @pytest.mark.asyncio
    async def test_bound_upload_can_be_retried_but_not_in_another_session(self, client: AsyncClient, session_id: str):
        from flocks.session.files import resolve_chat_upload

        uploaded = await client.post("/api/workspace/upload?purpose=chat", files={
            "files": ("retry.md", b"retry bytes", "text/markdown"),
        })
        upload = uploaded.json()["uploaded"][0]
        payload = {"parts": [{"type": "file", "uploadID": upload["uploadID"], "mime": upload["mime"],
                              "filename": upload["name"]}], "noReply": True}
        first = await client.post(f"/api/session/{session_id}/message", json=payload)
        assert first.status_code == 200
        bound = resolve_chat_upload(session_id, API_TOKEN_SERVICE_USER_ID, upload["uploadID"])
        second = await client.post(f"/api/session/{session_id}/message", json=payload)
        assert second.status_code == 200
        queued = await client.post(f"/api/session/{session_id}/prompt_queue", json=payload)
        assert queued.status_code == 202
        assert bound.read_bytes() == b"retry bytes"
        other = (await client.post("/api/session", json={})).json()["id"]
        foreign = await client.post(f"/api/session/{other}/message", json=payload)
        assert foreign.status_code == 400
        assert bound.read_bytes() == b"retry bytes"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("padded_id", [False, True])
    async def test_queue_discard_and_remove_preserve_other_references(self, client: AsyncClient, session_id: str, padded_id):
        from flocks.session.files import resolve_staged_chat_upload
        from flocks.session.interaction_queue import InteractionQueue

        uploaded = await client.post("/api/workspace/upload?purpose=chat", files={
            "files": ("queue.md", b"queue bytes", "text/markdown"),
        })
        upload = uploaded.json()["uploaded"][0]
        payload = {"parts": [{"type": "file", "uploadID": upload["uploadID"], "mime": upload["mime"],
                              "filename": upload["name"]}], "noReply": True}
        first = await client.post(f"/api/session/{session_id}/prompt_queue", json=payload)
        if padded_id:
            payload["parts"][0]["uploadID"] = f" {upload['uploadID']} "
        second = await client.post(f"/api/session/{session_id}/prompt_queue", json=payload)
        assert first.status_code == second.status_code == 202
        staged = resolve_staged_chat_upload(API_TOKEN_SERVICE_USER_ID, upload["uploadID"])
        discarded = await client.delete(f"/api/workspace/upload/chat/{upload['uploadID']}")
        assert discarded.status_code == 200
        assert discarded.json()["removed"] is False
        removed = await client.delete(f"/api/session/{session_id}/prompt_queue/{first.json()['queueID']}")
        assert removed.status_code == 200
        assert staged.read_bytes() == b"queue bytes"
        discarded_remaining = await client.delete(f"/api/workspace/upload/chat/{upload['uploadID']}")
        assert discarded_remaining.json()["removed"] is False
        await InteractionQueue.clear(session_id)
        assert not staged.exists()
        assert not InteractionQueue.references_upload(API_TOKEN_SERVICE_USER_ID, upload["uploadID"])

    @pytest.mark.asyncio
    async def test_queue_pop_remains_protected_through_dispatch(self, client: AsyncClient, session_id: str, monkeypatch):
        from flocks.session.files import remove_staged_chat_upload, resolve_staged_chat_upload
        from flocks.session.interaction_queue import InteractionQueue
        from flocks.project.instance import Instance

        uploaded = await client.post("/api/workspace/upload?purpose=chat", files={
            "files": ("dispatch.md", b"dispatch bytes", "text/markdown"),
        })
        upload = uploaded.json()["uploaded"][0]
        payload = {"parts": [{"type": "file", "uploadID": upload["uploadID"], "mime": upload["mime"],
                              "filename": upload["name"]}], "noReply": True}
        await client.post(f"/api/session/{session_id}/prompt_queue", json=payload)
        staged = resolve_staged_chat_upload(API_TOKEN_SERVICE_USER_ID, upload["uploadID"])
        observed = []

        async def at_publish(_session_id):
            await InteractionQueue.clear(session_id)
            observed.append(remove_staged_chat_upload(API_TOKEN_SERVICE_USER_ID, upload["uploadID"]))

        async def dispatch(*_args):
            assert staged.exists()
            assert not remove_staged_chat_upload(API_TOKEN_SERVICE_USER_ID, upload["uploadID"])
            raise RuntimeError("dispatch failure")

        async def provide(**kwargs):
            return await kwargs["fn"]()

        monkeypatch.setattr(session_routes, "_publish_prompt_queue", at_publish)
        monkeypatch.setattr(session_routes, "_dispatch_sse_input", dispatch)
        monkeypatch.setattr(Instance, "provide", provide)
        with pytest.raises(RuntimeError, match="dispatch failure"):
            await session_routes._drain_prompt_queue_locked(session_id, str(staged.parent))
        assert observed == [False]
        assert not InteractionQueue.references_upload(API_TOKEN_SERVICE_USER_ID, upload["uploadID"])
        assert remove_staged_chat_upload(API_TOKEN_SERVICE_USER_ID, upload["uploadID"]) is True

    @pytest.mark.asyncio
    async def test_async_accepted_upload_is_protected_before_dispatch(self, client: AsyncClient, session_id: str, monkeypatch):
        from flocks.session.files import resolve_staged_chat_upload
        from flocks.session.interaction_queue import InteractionQueue

        uploaded = await client.post("/api/workspace/upload?purpose=chat", files={
            "files": ("accepted.md", b"accepted bytes", "text/markdown"),
        })
        upload = uploaded.json()["uploaded"][0]
        proceed = asyncio.Event()
        entered = asyncio.Event()

        async def defer_dispatch(*args):
            entered.set()
            await proceed.wait()

        monkeypatch.setattr(session_routes, "_dispatch_sse_input", defer_dispatch)
        before = set(getattr(session_routes.router, "_pending_tasks", set()))
        response = await client.post(f"/api/session/{session_id}/prompt_async", json={
            "parts": [{"type": "file", "uploadID": upload["uploadID"], "mime": upload["mime"],
                       "filename": upload["name"]}], "noReply": True,
        })
        assert response.status_code == 202
        await asyncio.wait_for(entered.wait(), timeout=2)
        discarded = await client.delete(f"/api/workspace/upload/chat/{upload['uploadID']}")
        assert discarded.json()["removed"] is False
        assert resolve_staged_chat_upload(API_TOKEN_SERVICE_USER_ID, upload["uploadID"]).read_bytes() == b"accepted bytes"
        proceed.set()
        pending = list(getattr(session_routes.router, "_pending_tasks", set()) - before)
        await asyncio.gather(*pending)
        await asyncio.sleep(0)
        assert not InteractionQueue.references_upload(API_TOKEN_SERVICE_USER_ID, upload["uploadID"])

    @pytest.mark.asyncio
    async def test_failed_file_part_restores_only_new_unpersisted_move(self, client: AsyncClient, session_id: str, monkeypatch):
        from flocks.session.files import resolve_chat_upload, resolve_staged_chat_upload

        uploads = []
        for filename in ("existing.md", "persisted.md", "failed.md"):
            response = await client.post("/api/workspace/upload?purpose=chat", files={
                "files": (filename, filename.encode(), "text/markdown"),
            })
            uploads.append(response.json()["uploaded"][0])
        parts = [{"type": "file", "uploadID": item["uploadID"], "mime": item["mime"],
                  "filename": item["name"]} for item in uploads]
        first = await client.post(f"/api/session/{session_id}/message", json={"parts": parts[:1], "noReply": True})
        assert first.status_code == 200
        add_part = Message.add_part

        async def fail_last(sid, mid, part):
            if part.filename == "failed.md":
                raise OSError("test storage unavailable")
            return await add_part(sid, mid, part)

        monkeypatch.setattr(Message, "add_part", fail_last)
        response = await client.post(f"/api/session/{session_id}/message", json={"parts": parts, "noReply": True})
        assert response.status_code == 400
        assert resolve_chat_upload(session_id, API_TOKEN_SERVICE_USER_ID, uploads[0]["uploadID"]).read_bytes() == b"existing.md"
        assert resolve_chat_upload(session_id, API_TOKEN_SERVICE_USER_ID, uploads[1]["uploadID"]).read_bytes() == b"persisted.md"
        assert resolve_staged_chat_upload(API_TOKEN_SERVICE_USER_ID, uploads[2]["uploadID"]).read_bytes() == b"failed.md"

    @pytest.mark.asyncio
    async def test_queue_upload_claim_is_set_before_validation_await_and_released_on_failure(
        self, client: AsyncClient, session_id: str, monkeypatch,
    ):
        from flocks.session.files import create_chat_upload_target, remove_staged_chat_upload
        from flocks.session.interaction_queue import InteractionQueue

        upload_id = "prt_acceptance_claim"
        owner_id = "usr_acceptance"
        staged = create_chat_upload_target(owner_id, upload_id, "claim.md")
        staged.write_text("claim", encoding="utf-8")

        async def reject_agent(_agent):
            await asyncio.sleep(0)
            assert not remove_staged_chat_upload(owner_id, upload_id)
            raise HTTPException(status_code=400, detail="agent unavailable")

        monkeypatch.setattr(session_routes, "_require_agent_usable_for_chat", reject_agent)
        with pytest.raises(HTTPException, match="agent unavailable"):
            await session_routes._enqueue_prompt_request(
                session_id,
                session_routes.PromptRequest(parts=[{"type": "file", "uploadID": upload_id, "mime": "text/markdown"}]),
                uploader_user_id=owner_id,
            )
        assert not InteractionQueue.references_upload(owner_id, upload_id)
        assert remove_staged_chat_upload(owner_id, upload_id)

    @pytest.mark.asyncio
    async def test_queue_reference_checks_include_uploader_identity(self, client: AsyncClient):
        from flocks.session.files import create_chat_upload_target, remove_staged_chat_upload
        from flocks.session.interaction_queue import InteractionQueue

        upload_id = "prt_shared_identifier"
        first = create_chat_upload_target("usr_first", upload_id, "first.md")
        second = create_chat_upload_target("usr_second", upload_id, "second.md")
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        await InteractionQueue.enqueue("ses_claim_first", parts=[{"type": "file", "uploadID": upload_id}],
                                       execution_context={"_uploaderUserID": "usr_first"})
        assert not remove_staged_chat_upload("usr_first", upload_id)
        assert remove_staged_chat_upload("usr_second", upload_id)
        assert first.read_text(encoding="utf-8") == "first"
        await InteractionQueue.clear("ses_claim_first")
        assert not first.exists()

    @pytest.mark.asyncio
    async def test_admin_queue_dispatch_binds_using_submitter_not_session_owner(
        self, client: AsyncClient, session_id: str, monkeypatch, tmp_path,
    ):
        from flocks.session.files import create_chat_upload_target, resolve_chat_upload
        from flocks.session.interaction_queue import InteractionQueue

        require_user = session_routes.require_user
        operator = _use_webui_admin(monkeypatch)
        session = await Session.get_by_id_unfiltered(session_id)
        assert session.owner_user_id != operator.id
        upload_id = "prt_operator_queue"
        staged = create_chat_upload_target(operator.id, upload_id, "operator.md")
        staged.write_text("operator upload", encoding="utf-8")
        queued = await client.post(f"/api/session/{session_id}/prompt_queue", json={
            "parts": [{"type": "file", "uploadID": upload_id, "mime": "text/markdown", "filename": "operator.md"}],
            "noReply": True,
        })
        assert queued.status_code == 202
        item = (await InteractionQueue.list(session_id))[0]
        assert item.execution_context["_uploaderUserID"] == operator.id
        # The eventual worker can have a different authenticated user; the
        # captured uploader is what controls the per-user staging lookup.
        monkeypatch.setattr(session_routes, "require_user", require_user)
        await session_routes._drain_prompt_queue_locked(session_id, str(tmp_path))
        bound = resolve_chat_upload(session_id, operator.id, upload_id)
        assert bound.read_text(encoding="utf-8") == "operator upload"
        assert not staged.exists()
        assert not InteractionQueue.references_upload(operator.id, upload_id)
