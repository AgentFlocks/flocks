"""
Authentication context helpers for request-scoped user identity.
"""

from __future__ import annotations

import contextvars
from typing import Optional

from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    """Current authenticated local user."""

    id: str
    username: str
    role: str = Field(..., description="admin or member")
    status: str = Field("active", description="active or disabled")
    must_reset_password: bool = False


_current_auth_user: contextvars.ContextVar[Optional[AuthUser]] = contextvars.ContextVar(
    "current_auth_user",
    default=None,
)


def set_current_auth_user(user: Optional[AuthUser]) -> contextvars.Token:
    """Set current request user in context."""

    return _current_auth_user.set(user)


def reset_current_auth_user(token: contextvars.Token) -> None:
    """Reset request user context."""

    _current_auth_user.reset(token)


def get_current_auth_user() -> Optional[AuthUser]:
    """Get current request user from context."""

    return _current_auth_user.get()
