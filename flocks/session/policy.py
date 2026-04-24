"""
Session access policy.

Single source of truth for session read/write permission checks in the
single-admin local-account mode. All read filtering (listing, fetch) and
write actions (delete) must consult this module instead of duplicating
logic inline.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from flocks.auth.context import AuthUser
    from flocks.session.session import SessionInfo


class SessionPolicy:
    """Centralized ownership / permission rules for sessions."""

    @staticmethod
    def _resolve_user(user: Optional["AuthUser"]) -> Optional["AuthUser"]:
        if user is not None:
            return user
        try:
            from flocks.auth.context import get_current_auth_user

            return get_current_auth_user()
        except Exception:
            return None

    @staticmethod
    def is_owner(session: "SessionInfo", user: Optional["AuthUser"]) -> bool:
        if user is None:
            return False
        if session.owner_user_id and session.owner_user_id == user.id:
            return True
        if session.owner_username and session.owner_username == user.username:
            return True
        return False

    @staticmethod
    def is_admin(user: Optional["AuthUser"]) -> bool:
        return bool(user and user.role == "admin")

    @classmethod
    def can_read(cls, session: "SessionInfo", user: Optional["AuthUser"] = None) -> bool:
        """
        Whether the session should be visible in listings / fetch.

        - No auth context (CLI/internal runtime): keep legacy permissive behaviour.
        - Admin: sees everything.
        - Otherwise: owner only.
        """
        resolved = cls._resolve_user(user)
        if resolved is None:
            return True
        if cls.is_admin(resolved):
            return True
        return cls.is_owner(session, resolved)

    @classmethod
    def can_delete(cls, session: "SessionInfo", user: Optional["AuthUser"]) -> bool:
        resolved = cls._resolve_user(user)
        if resolved is None:
            return False
        return cls.is_admin(resolved) or cls.is_owner(session, resolved)
