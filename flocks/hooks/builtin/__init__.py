"""
Builtin hooks initialization

Registers all built-in hooks that come with Flocks.
"""

from flocks.utils.log import Log

log = Log.create(service="hooks.builtin")


def register_builtin_hooks() -> None:
    """
    Register all built-in hooks

    Should be called once during application startup.
    """
    log.info("hooks.builtin.registering")
    from flocks.rea import attachment_guard

    # The REA analysis prompt without a file asks for the file (PRD 6.2).
    attachment_guard.register()
    log.info("hooks.builtin.registered")
