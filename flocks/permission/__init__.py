"""
Permission management module.

Exports canonical permission interfaces and removes legacy inline duplicate
implementations to avoid drift.
"""

from flocks.permission.helpers import Ruleset, from_config, merge
from flocks.permission.manager import PermissionManager, Permission
from flocks.permission.next import DeniedError, PermissionNext, PermissionRequestInfo
from flocks.permission.rule import (
    PermissionLevel,
    PermissionRequest,
    PermissionResult,
    PermissionRule,
    PermissionScope,
)

__all__ = [
    "Permission",
    "PermissionManager",
    "PermissionLevel",
    "PermissionScope",
    "PermissionRule",
    "PermissionRequest",
    "PermissionResult",
    "Ruleset",
    "from_config",
    "merge",
    "PermissionRequestInfo",
    "DeniedError",
    "PermissionNext",
]
