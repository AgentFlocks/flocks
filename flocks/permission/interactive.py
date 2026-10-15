"""Interactive permission policy for legacy OSS tool prompts."""

import os


def auto_approve_enabled() -> bool:
    """Return whether TUI/CLI non-interactive approval mode is active.

    Not in a managed install: there a supervisor owns the runtime, and the
    confirmations its policy asks for are answered by a person, not by a
    switch in the environment.
    """
    if os.environ.get("FLOCKS_AUTO_APPROVE", "").strip().lower() != "true":
        return False
    from flocks.updater.deploy import declared_managed

    return not declared_managed()


def legacy_tool_permission_prompt_required() -> bool:
    """Return whether ``ctx.ask`` should block on ``PermissionNext``.

    OSS tool permissions (write/read/edit/external_directory) are not
    interactively gated. Optional execution-policy extensions own command
    confirmation.
    """
    return False


__all__ = ["auto_approve_enabled", "legacy_tool_permission_prompt_required"]
