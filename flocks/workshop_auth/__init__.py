"""Workshop auth plugin (PoC, v1.1 route B+).

Registers a JWT-based AuthBackend so the Workshop facade can call Flocks
with ``Cookie: flocks_session=<per-team JWT>`` and get a real
``AuthUser(tenant_ids=[...])`` — real tenant isolation via
``flocks/contracts/access``.

Zero core changes: only this package is added. Enable at startup with::

    from flocks.workshop_auth import register_workshop_auth
    register_workshop_auth()
"""

from flocks.workshop_auth.backend import TeamJWTAuthBackend

__all__ = ["TeamJWTAuthBackend", "register_workshop_auth"]


def register_workshop_auth() -> None:
    from flocks.auth.service import AuthService

    AuthService.register_backend(TeamJWTAuthBackend)
