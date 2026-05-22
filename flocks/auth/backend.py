"""
Auth backend extension protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Protocol, Tuple, Literal

from flocks.auth.context import AuthUser

if TYPE_CHECKING:
    from flocks.auth.service import LocalUser


class AuthBackend(Protocol):
    """Contract for pluggable auth backends."""

    @classmethod
    async def init(cls) -> None: ...

    @classmethod
    async def has_users(cls) -> bool: ...

    @classmethod
    async def get_bootstrap_status(cls) -> Dict[str, bool]: ...

    @classmethod
    async def bootstrap_admin(cls, username: str, password: str) -> "LocalUser": ...

    @classmethod
    async def get_user_by_id(cls, user_id: str) -> Optional["LocalUser"]: ...

    @classmethod
    async def get_user_by_username(cls, username: str) -> Optional[Tuple["LocalUser", str, Optional[str]]]: ...

    @classmethod
    async def list_users(cls) -> List["LocalUser"]: ...

    @classmethod
    async def create_user(
        cls,
        *,
        username: str,
        password: str,
        role: Literal["admin", "member"],
    ) -> "LocalUser": ...

    @classmethod
    async def update_user_role(
        cls,
        *,
        target_user_id: str,
        new_role: Literal["admin", "member"],
    ) -> "LocalUser": ...

    @classmethod
    async def delete_user(cls, *, target_user_id: str) -> None: ...

    @classmethod
    async def get_user_by_session_id(cls, session_id: str) -> Optional["LocalUser"]: ...

    @classmethod
    async def revoke_session(cls, session_id: str) -> None: ...

    @classmethod
    async def login(cls, username: str, password: str, *, persist: bool = True) -> Tuple["LocalUser", str]: ...

    @classmethod
    async def change_password(
        cls,
        user: AuthUser,
        *,
        current_password: str,
        new_password: str,
    ) -> None: ...

    @classmethod
    async def set_password(
        cls,
        *,
        target_user_id: str,
        new_password: str,
        must_reset_password: bool,
        temp_password_expires_at: Optional[str] = None,
    ) -> None: ...

    @classmethod
    async def generate_admin_temp_password(cls, *, username: str = "admin") -> str: ...

    @classmethod
    async def reassign_orphan_sessions(
        cls,
        admin_user_id: str,
        *,
        dry_run: bool = False,
    ) -> Dict[str, int]: ...

    @classmethod
    async def migrate_legacy_sessions_to_admin(cls, admin_user_id: str) -> None: ...

