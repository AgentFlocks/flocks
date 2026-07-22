"""Interactive permission policy for legacy OSS tool prompts."""

import os


def auto_approve_enabled() -> bool:
    """Return whether TUI/CLI non-interactive approval mode is active."""
    return os.environ.get("FLOCKS_AUTO_APPROVE", "").strip().lower() == "true"


def legacy_tool_permission_prompt_required() -> bool:
    """Return whether ``ctx.ask`` should block on ``PermissionNext``.

    OSS tool permissions (write/read/edit/external_directory) are not
    interactively gated.  Pro command confirmation uses ``PolicyGateHook``.
    """
    return False


__all__ = ["auto_approve_enabled", "legacy_tool_permission_prompt_required"]
