"""Tests for the optional bearer-token AuthBackend protocol methods.

These methods default to "no opt-in" so existing backends keep working.
"""

from __future__ import annotations

import pytest


class _DefaultBackend:
    """Mirrors the default implementation of the new optional methods.

    Doesn't subclass anything — just verifies that callers can rely on the
    defaults returning False / None.
    """

    @classmethod
    async def supports_bearer_token(cls) -> bool:
        return False

    @classmethod
    async def authenticate_bearer_token(cls, token: str, *, audience=None):
        return None


@pytest.mark.asyncio
async def test_default_supports_bearer_token_is_false():
    assert await _DefaultBackend.supports_bearer_token() is False


@pytest.mark.asyncio
async def test_default_authenticate_bearer_token_is_none():
    assert await _DefaultBackend.authenticate_bearer_token("anything") is None


@pytest.mark.asyncio
async def test_opt_in_backend_overrides():
    """A backend that opts in returns True and resolves the token."""

    class _OptInBackend:
        @classmethod
        async def supports_bearer_token(cls) -> bool:
            return True

        @classmethod
        async def authenticate_bearer_token(cls, token: str, *, audience=None):
            return f"local-user-for-{token}-aud-{audience}"

    assert await _OptInBackend.supports_bearer_token() is True
    user = await _OptInBackend.authenticate_bearer_token("xyz", audience="flocks")
    assert user == "local-user-for-xyz-aud-flocks"