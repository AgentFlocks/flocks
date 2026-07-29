"""Permission data types and neutral confirmation transport."""

from flocks.permission.helpers import Ruleset, from_config, merge
from flocks.permission.interactive import legacy_tool_permission_prompt_required
from flocks.permission.manager import Permission, PermissionManager
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
    "auto_approve_enabled",
    "legacy_tool_permission_prompt_required",
]
