"""
Authentication context helpers for request-scoped user identity.
"""

from __future__ import annotations

import contextvars
from typing import Optional

from pydantic import BaseModel, Field


API_TOKEN_SERVICE_USER_ID = "api-token-service"


class AuthUser(BaseModel):
    """Current authenticated local user."""

    id: str
    username: str
    role: str = Field(..., description="admin or member")
    status: str = Field("active", description="active or disabled")
    must_reset_password: bool = False
    tenant_ids: tuple[str, ...] = Field(default_factory=tuple)
    asset_groups: tuple[str, ...] = Field(default_factory=tuple)


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


_current_auth_session: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_auth_session",
    default=None,
)


def set_current_auth_session(session_id: Optional[str]) -> contextvars.Token:
    """Set the login session the current request was authenticated with."""

    return _current_auth_session.set(session_id)


def reset_current_auth_session(token: contextvars.Token) -> None:
    _current_auth_session.reset(token)


def get_current_auth_session() -> Optional[str]:
    """The browser login session behind the current request, if it came with one.

    None for API-token requests and work outside a request.  An account
    backend that keeps its accounts elsewhere passes it on, so the place that
    keeps them can tell who asks for a change rather than take this process's
    word for it.
    """

    return _current_auth_session.get()
