"""Server module for HTTP API.

Startup hook (env-gated, zero core change):
    If ``FLOCKS_AUTH=workshop_jwt`` is set, register the Workshop auth
    backend + adapter on import. Keeps zero changes for deployments that
    don't use Workshop.
"""

import os

if os.getenv("FLOCKS_AUTH") == "workshop_jwt":
    try:
        from flocks.workshop_auth import register_workshop_auth

        register_workshop_auth()
    except Exception as _exc:  # noqa: BLE001
        # Never crash server startup due to optional auth plugin.
        # Operators can see the failure via logs/metrics; defaulting to
        # LocalAuthBackend keeps the deployment usable.
        import sys

        print(f"[server] failed to register workshop auth: {_exc!r}", file=sys.stderr)