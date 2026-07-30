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
    # Future built-in hooks are registered here.
    log.info("hooks.builtin.registered")
