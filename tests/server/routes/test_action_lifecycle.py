from __future__ import annotations

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.server.routes.action_lifecycle import action_operation_payload


def test_action_operation_payload_includes_audit_actor_from_auth_context() -> None:
    def endpoint() -> None:
        return None

    token = set_current_auth_user(
        AuthUser(id="user-123", username="alice", role="admin", status="active")
    )
    try:
        payload = action_operation_payload("agent", endpoint, (), {})
    finally:
        reset_current_auth_user(token)

    assert payload["tool_context_extra"]["execution_context"]["audit_actor"] == {
        "id": "user-123",
        "name": "alice",
    }
