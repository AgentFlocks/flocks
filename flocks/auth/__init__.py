"""
Local account authentication package.
"""

from flocks.auth.backend import AuthBackend
from flocks.auth.context import AuthUser
from flocks.auth.local import LocalAuthBackend
from flocks.auth.service import AuthService


def register_backend(backend: type[AuthBackend]) -> None:
    AuthService.register_backend(backend)


def get_backend():
    return AuthService.get_backend()

__all__ = [
    "AuthBackend",
    "AuthUser",
    "AuthService",
    "LocalAuthBackend",
    "register_backend",
    "get_backend",
]
