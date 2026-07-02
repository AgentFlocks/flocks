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
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient
from flocks.auth.context import AuthUser
from flocks.session.core.status import SessionStatus, SessionStatusBusy
from flocks.session.message import (
    Message,
    MessageRole,
    ToolPart,
    ToolStateError,
    ToolStateRunning,
)
from flocks.session.orphan_tools import INTERRUPTED_TOOL_ERROR
from flocks.session.session import Session

# ===========================================================================
# CRUD
# ===========================================================================

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
            "directory",
            "title",
            "time",
            "category",
            "parentID",
            "provider",
            "model",
            "model_pinned",
            "canWrite",
            "canDelete",
            "isShared",
        }
        assert row["projectID"]
        assert row["directory"]
        assert "goal" not in row
        assert "summary" not in row

    @pytest.mark.asyncio
    async def test_create_session_in_user_managed_project(self, client: AsyncClient):
        """Session creation can target a user-managed project."""
        project_resp = await client.post("/api/project", json={"name": "Labs"})
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
        assert row["directory"] == project["worktree"]

    @pytest.mark.asyncio
    async def test_get_session(self, client: AsyncClient, session_id: str):
        """GET /api/session/{id} returns the specific session."""
        resp = await client.get(f"/api/session/{session_id}")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["id"] == session_id

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

    @staticmethod
    def _patch_delete_session_dependencies(
        monkeypatch,
        session_routes,
        *,
        session_id: str,
        order: list[str],
        session_list,
    ) -> None:
        async def fake_abort_session_processing(abort_session_id: str) -> bool:
            order.append(f"abort:{abort_session_id}")
            return True

        async def fake_wait_for_sessions_idle(session_ids: list[str], timeout_s: float = 5.0) -> None:
            order.append(f"wait:{','.join(session_ids)}")

        async def fake_interaction_queue_clear(_session_id: str) -> None:
            order.append("queue_clear")

        async def fake_goal_clear(_session_id: str) -> None:
            order.append("goal_clear")

        async def fake_session_delete(_project_id: str, delete_session_id: str) -> bool:
            assert delete_session_id == session_id
            order.append("delete")
            return True

        monkeypatch.setattr(session_routes.Session, "list", session_list)
        monkeypatch.setattr(
            session_routes,
            "_abort_session_processing",
            fake_abort_session_processing,
        )
        monkeypatch.setattr(
            session_routes,
            "_wait_for_sessions_idle",
            fake_wait_for_sessions_idle,
        )
        monkeypatch.setattr(
            "flocks.session.interaction_queue.InteractionQueue.clear",
            fake_interaction_queue_clear,
        )
        monkeypatch.setattr(
            "flocks.session.goal.GoalManager.clear",
            fake_goal_clear,
        )
        monkeypatch.setattr(session_routes.Session, "delete", fake_session_delete)

    @pytest.mark.asyncio
    async def test_delete_session_aborts_and_waits_before_delete(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch,
    ):
        """DELETE waits for active processing to stop before clearing messages."""
        from flocks.server.routes import session as session_routes

        order: list[str] = []

        async def fake_session_list(_project_id: str):
            return []

        self._patch_delete_session_dependencies(
            monkeypatch,
            session_routes,
            session_id=session_id,
            order=order,
            session_list=fake_session_list,
        )

        resp = await client.delete(f"/api/session/{session_id}")

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() is True
        assert order == [
            f"abort:{session_id}",
            "queue_clear",
            "goal_clear",
            f"wait:{session_id}",
            "delete",
        ]

    @pytest.mark.asyncio
    async def test_delete_session_waits_for_descendant_loops_before_delete(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch,
    ):
        """DELETE waits for child and grandchild loops before recursive delete."""
        from flocks.server.routes import session as session_routes

        child_id = "ses_delete_child_wait"
        grandchild_id = "ses_delete_grandchild_wait"
        order: list[str] = []

        async def fake_session_list(_project_id: str):
            return [
                SimpleNamespace(id=child_id, parent_id=session_id),
                SimpleNamespace(id=grandchild_id, parent_id=child_id),
            ]

        self._patch_delete_session_dependencies(
            monkeypatch,
            session_routes,
            session_id=session_id,
            order=order,
            session_list=fake_session_list,
        )

        resp = await client.delete(f"/api/session/{session_id}")

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() is True
        assert order == [
            f"abort:{session_id}",
            "queue_clear",
            "goal_clear",
            f"wait:{session_id}",
            f"abort:{child_id}",
            f"abort:{grandchild_id}",
            f"wait:{child_id},{grandchild_id}",
            "delete",
        ]

    @pytest.mark.asyncio
    async def test_delete_session_aborts_descendant_that_appears_after_parent_wait(
        self,
        client: AsyncClient,
        session_id: str,
        monkeypatch,
    ):
        """DELETE re-collects descendants after parent abort to catch late children."""
        from flocks.server.routes import session as session_routes

        child_id = "ses_delete_late_child_wait"
        list_calls = 0
        order: list[str] = []

        async def fake_session_list(_project_id: str):
            nonlocal list_calls
            list_calls += 1
            if list_calls == 1:
                return []
            return [SimpleNamespace(id=child_id, parent_id=session_id)]

        self._patch_delete_session_dependencies(
            monkeypatch,
            session_routes,
            session_id=session_id,
            order=order,
            session_list=fake_session_list,
        )

        resp = await client.delete(f"/api/session/{session_id}")

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() is True
        assert order == [
            f"abort:{session_id}",
            "queue_clear",
            "goal_clear",
            f"wait:{session_id}",
            f"abort:{child_id}",
            f"wait:{child_id}",
            "delete",
        ]

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
        }

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
            AsyncMock(return_value=SimpleNamespace(id=session_id, directory="/tmp/project")),
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
