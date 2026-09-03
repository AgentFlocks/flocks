"""Idempotent plugin registration entrypoint."""

from __future__ import annotations

import os
from typing import Any

from flocks_code_security.agents import register_agents
from flocks_code_security.projection import register_projection
from flocks_code_security.public_tool import register_public_tool
from flocks_code_security.runtime import get_runtime
from flocks_code_security.service import get_audit_service
from flocks_code_security.tools import register_tools


_service_initialized = False


def register(_loader: Any = None) -> None:
    global _service_initialized
    get_runtime()
    register_tools()
    register_public_tool()
    register_projection()
    register_agents()
    if not _service_initialized:
        # Parallel batch workers share one database; a worker must not recover
        # scans owned by sibling processes during plugin initialization.
        skip_recovery = os.environ.get("FLOCKS_CODE_SECURITY_SKIP_ORPHAN_RECOVERY", "").lower()
        if skip_recovery not in {"1", "true", "yes"}:
            get_audit_service().recover_orphaned_scans()
        _service_initialized = True
