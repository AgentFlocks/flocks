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

    @staticmethod
    def is_local_shared(session: "SessionInfo") -> bool:
        metadata = getattr(session, "metadata", None)
        if not isinstance(metadata, dict):
            return False
        return bool(metadata.get("shared_local"))

    @staticmethod
    def _has_no_owner(session: "SessionInfo") -> bool:
        return not session.owner_user_id and not session.owner_username

    @classmethod
    def is_shared(cls, session: "SessionInfo") -> bool:
        """Whether the session is accessible to all local users.

        True when explicitly marked ``shared_local`` or when the session has no
        owner (e.g. channel-originated sessions created without an HTTP auth
        context). Used both for permission checks and the UI "shared" badge.
        """
        return cls.is_local_shared(session) or cls._has_no_owner(session)

    @staticmethod
    def _shared_read_user_ids(session: "SessionInfo") -> set[str]:
        metadata = getattr(session, "metadata", None)
        if not isinstance(metadata, dict):
            return set()
        raw = metadata.get("shared_read_access_user_ids", [])
        if not isinstance(raw, list):
            return set()
        return {str(item) for item in raw if item}

    @classmethod
    def is_shared_read_only(cls, session: "SessionInfo", user: Optional["AuthUser"]) -> bool:
        if user is None:
            return False
        if cls.is_owner(session, user):
            return False
        if cls.is_local_shared(session):
            return True
        return user.id in cls._shared_read_user_ids(session)

    @classmethod
    def can_read(cls, session: "SessionInfo", user: Optional["AuthUser"] = None) -> bool:
        """
        Whether the session should be visible in listings / fetch.

        - No auth context (CLI/internal runtime): keep legacy permissive behaviour.
        - Logged-in users: owner, local-shared readers, or any user for ownerless
          sessions (e.g. channel-originated sessions created without an auth context).
        """
        resolved = cls._resolve_user(user)
        if resolved is None:
            return True
        if cls.is_owner(session, resolved):
            return True
        if cls._has_no_owner(session):
            return True
        return cls.is_shared_read_only(session, resolved)

    @classmethod
    def can_write(cls, session: "SessionInfo", user: Optional["AuthUser"] = None) -> bool:
        """
        Session write permission.

        Owner can always write. Any authenticated user may write ownerless
        sessions (e.g. channel-originated sessions that have no HTTP auth
        context at creation time).
        """
        resolved = cls._resolve_user(user)
        if resolved is None:
            return False
        if cls.is_owner(session, resolved):
            return True
        if cls._has_no_owner(session):
            return True
        return False

    @classmethod
    def can_delete(cls, session: "SessionInfo", user: Optional["AuthUser"]) -> bool:
        resolved = cls._resolve_user(user)
        if resolved is None:
            return False
        if cls.is_owner(session, resolved):
            return True
        if cls._has_no_owner(session):
            return True
        return False
