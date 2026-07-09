from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from flocks.auth.context import AuthUser


class Subject(BaseModel):
    """Unified security identity flowing through request/session pipelines."""

    subject_id: str
    subject_type: str = Field(default="human")
    display_name: str = ""
    role: str = Field(default="member")
    status: str = Field(default="active")
    tenant_ids: tuple[str, ...] = Field(default_factory=tuple)
    department: str = ""
    asset_groups: tuple[str, ...] = Field(default_factory=tuple)
    entry: str = Field(default="unknown")
    auth_source: str = Field(default="unknown")
    permission_mode: str = Field(default="default_interactive")
    verified: bool = Field(default=True)

    @classmethod
    def from_auth_user(
        cls,
        user: AuthUser,
        *,
        entry: str,
        auth_source: str,
        permission_mode: str = "default_interactive",
        subject_type: str = "human",
        verified: bool = True,
    ) -> "Subject":
        return cls(
            subject_id=user.id,
            subject_type=subject_type,
            display_name=user.username,
            role=user.role,
            status=user.status,
            tenant_ids=tuple(user.tenant_ids),
            department=user.department,
            asset_groups=tuple(user.asset_groups),
            entry=entry,
            auth_source=auth_source,
            permission_mode=permission_mode,
            verified=verified,
        )

    def as_auth_user(self) -> "AuthUser":
        from flocks.auth.context import AuthUser

        return AuthUser(
            id=self.subject_id,
            username=self.display_name or self.subject_id,
            role=self.role,
            status=self.status,
            must_reset_password=False,
            tenant_ids=tuple(self.tenant_ids),
            department=self.department,
            asset_groups=tuple(self.asset_groups),
        )


_current_subject: contextvars.ContextVar[Optional[Subject]] = contextvars.ContextVar(
    "current_subject",
    default=None,
)


def set_current_subject(subject: Optional[Subject]) -> contextvars.Token:
    return _current_subject.set(subject)


def reset_current_subject(token: contextvars.Token) -> None:
    _current_subject.reset(token)


def get_current_subject() -> Optional[Subject]:
    return _current_subject.get()
