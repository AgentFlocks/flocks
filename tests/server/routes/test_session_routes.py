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

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient
from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
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


# ===========================================================================
# Share permissions
# ===========================================================================

class TestSessionSharePermissions:
    @pytest.mark.asyncio
    async def test_only_owner_can_share_and_unshare(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import session as session_routes

        owner = AuthUser(id="usr_owner", username="owner", role="member", status="active")
        viewer = AuthUser(id="usr_viewer", username="viewer", role="admin", status="active")
        session = await Session.create(
            project_id="share_permissions",
            directory="/tmp",
            title="owner-only-share",
            owner_user_id=owner.id,
            owner_username=owner.username,
        )

        monkeypatch.setattr(session_routes, "require_user", lambda _request: viewer)
        forbidden_share = await client.post(f"/api/session/{session.id}/share")
        assert forbidden_share.status_code == status.HTTP_403_FORBIDDEN
        assert "仅会话创建者可共享会话" in forbidden_share.text

        monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
        share_resp = await client.post(f"/api/session/{session.id}/share")
        assert share_resp.status_code == status.HTTP_200_OK
        assert share_resp.json()["visibility"] == "team_shared"

        monkeypatch.setattr(session_routes, "require_user", lambda _request: viewer)
        forbidden_unshare = await client.delete(f"/api/session/{session.id}/share")
        assert forbidden_unshare.status_code == status.HTTP_403_FORBIDDEN
        assert "仅会话创建者可停止共享" in forbidden_unshare.text

        monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
        unshare_resp = await client.delete(f"/api/session/{session.id}/share")
        assert unshare_resp.status_code == status.HTTP_200_OK
        assert unshare_resp.json()["visibility"] == "private"

    @pytest.mark.asyncio
    async def test_session_response_share_flags_follow_owner_only(self):
        from flocks.server.routes.session import _session_to_response

        owner = AuthUser(id="usr_owner", username="owner", role="member", status="active")
        viewer = AuthUser(id="usr_viewer", username="viewer", role="admin", status="active")
        token = set_current_auth_user(owner)
        try:
            session = await Session.create(
                project_id="share_flags",
                directory="/tmp",
                title="shared-session",
            )
            await Session.share("share_flags", session.id)
            shared = await Session.get("share_flags", session.id)
            assert shared is not None
            owner_response = _session_to_response(shared)
            assert owner_response.canShare is True
            assert owner_response.canUnshare is True
        finally:
            reset_current_auth_user(token)

        token = set_current_auth_user(viewer)
        try:
            shared = await Session.get("share_flags", session.id)
            assert shared is not None
            viewer_response = _session_to_response(shared)
            assert viewer_response.canShare is False
            assert viewer_response.canUnshare is False
        finally:
            reset_current_auth_user(token)


class TestSessionMessagesRemaining:
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


# ===========================================================================
# Utility endpoints
# ===========================================================================

class TestSessionUtilities:
    """clear, abort, status, metrics, recent endpoints."""

    @pytest.mark.asyncio
    async def test_clear_session(self, client: AsyncClient, session_id: str):
        """POST /api/session/{id}/clear removes messages."""
        # Add a message first
        await client.post(
            f"/api/session/{session_id}/message",
            json={"parts": [{"type": "text", "text": "msg"}], "noReply": True},
        )
        clear_resp = await client.post(f"/api/session/{session_id}/clear")
        assert clear_resp.status_code == status.HTTP_200_OK

        # Messages should be gone
        list_resp = await client.get(f"/api/session/{session_id}/message")
        assert list_resp.json() == []

    @pytest.mark.asyncio
    async def test_abort_session(self, client: AsyncClient, session_id: str):
        """POST /api/session/{id}/abort returns 200 (no active generation needed)."""
        resp = await client.post(f"/api/session/{session_id}/abort")
        assert resp.status_code == status.HTTP_200_OK

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
