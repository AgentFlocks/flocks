"""Workshop auth plugin (PoC, v1.1 route B+).

Registers a JWT-based AuthBackend so the Workshop facade can call Flocks
with ``Cookie: flocks_session=<per-team JWT>`` and get a real
``AuthUser(tenant_ids=[...])`` — real tenant isolation via
``flocks/contracts/access``.

Production mount (env-gated, zero core changes):
    Set ``FLOCKS_AUTH=workshop_jwt`` and Flocks will call
    ``register_workshop_auth()`` on startup. PoC reproducer and tests
    call it directly.

    Add to ``flocks/server/__init__.py`` startup path::

        if os.getenv("FLOCKS_AUTH") == "workshop_jwt":
            from flocks.workshop_auth import register_workshop_auth
            register_workshop_auth()
"""

import os

from flocks.workshop_auth.backend import TeamJWTAuthBackend
from flocks.workshop_auth.adapter import register_workshop_adapters

__all__ = ["TeamJWTAuthBackend", "register_workshop_auth", "maybe_register_on_env"]


def register_workshop_auth() -> None:
    """Register the auth backend AND the request-context adapter.

    Idempotent: re-registration is safe — Flocks' register_backend
    overwrites the previous backend and register_auth_context_adapter
    is dict-assignment.
    """
    from flocks.auth.service import AuthService

    AuthService.register_backend(TeamJWTAuthBackend)
    register_workshop_adapters()


def maybe_register_on_env(env_var: str = "FLOCKS_AUTH", expected: str = "workshop_jwt") -> bool:
    """Conditionally register based on env. Returns True if registered.

    Designed to be called from the Flocks startup hook. Keeps zero changes
    to the core server when the env is not set.
    """
    if os.getenv(env_var) == expected:
        register_workshop_auth()
        return True
    return False
