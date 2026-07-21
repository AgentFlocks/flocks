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
    def is_shared(
        cls,
        session: "SessionInfo",
        shared_project_ids: Optional[set[str]] = None,
    ) -> bool:
        """Whether the session is explicitly shared to all local users.

        Ownerless sessions are a legacy / unauthenticated compatibility state,
        not a sharing state. The UI badge should only reflect explicit sharing.
        """
        return cls.is_local_shared(session) or cls.is_project_shared(
            session,
            shared_project_ids,
        )

    @staticmethod
    def is_project_shared(
        session: "SessionInfo",
        shared_project_ids: Optional[set[str]] = None,
    ) -> bool:
        """Whether the session belongs to a locally shared project."""

        if shared_project_ids is not None:
            return session.project_id in shared_project_ids
        try:
            from flocks.project.project import Project

            return Project.is_local_shared(session.owner_user_id, session.project_id)
        except Exception:
            return False

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
    def is_shared_read_only(
        cls,
        session: "SessionInfo",
        user: Optional["AuthUser"],
        shared_project_ids: Optional[set[str]] = None,
    ) -> bool:
        if user is None:
            return False
        if cls.is_owner(session, user):
            return False
        if cls.is_local_shared(session) or cls.is_project_shared(
            session,
            shared_project_ids,
        ):
            return True
        return user.id in cls._shared_read_user_ids(session)

    @classmethod
    def can_read(
        cls,
        session: "SessionInfo",
        user: Optional["AuthUser"] = None,
        *,
        shared_project_ids: Optional[set[str]] = None,
    ) -> bool:
        """
        Whether the session should be visible in listings / fetch.

        - No auth context (CLI/internal runtime): keep legacy permissive behaviour.
        - Logged-in users: owner, local-shared readers, shared readers, or admins
          managing ownerless legacy/channel sessions.
        """
        resolved = cls._resolve_user(user)
        if resolved is None:
            return True
        if cls.is_owner(session, resolved):
            return True
        if cls._has_no_owner(session) and cls.is_admin(resolved):
            return True
        return cls.is_shared_read_only(session, resolved, shared_project_ids)

    @classmethod
    def can_write(cls, session: "SessionInfo", user: Optional["AuthUser"] = None) -> bool:
        """
        Session write permission.

        Owner can always write. Admins may repair/manage ownerless sessions
        accumulated before local ownership was available.
        """
        resolved = cls._resolve_user(user)
        if resolved is None:
            return False
        if cls.is_owner(session, resolved):
            return True
        if cls._has_no_owner(session) and cls.is_admin(resolved):
            return True
        return False

    @classmethod
    def can_delete(cls, session: "SessionInfo", user: Optional["AuthUser"]) -> bool:
        resolved = cls._resolve_user(user)
        if resolved is None:
            return False
        if cls.is_owner(session, resolved):
            return True
        if cls._has_no_owner(session) and cls.is_admin(resolved):
            return True
        return False
