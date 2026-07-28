"""
Builtin hooks initialization

Registers all built-in hooks that come with Flocks.
"""

from flocks.hooks.builtin.session_evolution import (
    register_session_evolution_hook,
)
from flocks.utils.log import Log

log = Log.create(service="hooks.builtin")


def register_builtin_hooks() -> None:
    """
    Register all built-in hooks
    
    Should be called once during application startup.
    """
    log.info("hooks.builtin.registering")
    
    try:
        register_session_evolution_hook()
        
        # Future: Register additional built-in hooks here
        # register_command_logger_hook()
        # register_error_reporter_hook()
        
        log.info("hooks.builtin.registered")
        
    except Exception as e:
        log.error("hooks.builtin.register_failed", {
            "error": str(e),
        })
        raise
