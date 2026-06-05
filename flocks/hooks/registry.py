"""
Hook Registry - Central registry for hook handlers

Manages registration, unregistration, and triggering of hooks.
Based on OpenClaw's internal-hooks.ts design.
"""

from typing import Any, Dict, List, Optional
import asyncio
import time

from flocks.extensions import FailPolicy, normalize_fail_policy, normalize_timeout
from flocks.hooks.types import HookEvent, AsyncHookHandler
from flocks.utils.log import Log

log = Log.create(service="hooks.registry")

DEFAULT_EVENT_HOOK_TIMEOUT_SECONDS = 10.0


class HookRegistry:
    """
    Hook registration and execution manager
    
    Manages all hook handlers and provides methods for:
    - Registering handlers for specific events
    - Triggering events and calling handlers
    - Error isolation (one handler failure doesn't affect others)
    """
    
    # Global singleton instance
    _instance: Optional["HookRegistry"] = None
    
    def __init__(self):
        # Store handlers: event_key -> [handlers]
        self._handlers: Dict[str, List[AsyncHookHandler]] = {}
        # Store metadata (for debugging and management)
        self._metadata: Dict[str, Dict] = {}
    
    @classmethod
    def get_instance(cls) -> "HookRegistry":
        """Get global singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (useful for testing)"""
        cls._instance = None
    
    def register(
        self,
        event_key: str,
        handler: AsyncHookHandler,
        metadata: Optional[Dict] = None,
    ) -> None:
        """
        Register a hook handler
        
        Args:
            event_key: Event key, format: "type" or "type:action"
                      e.g., "command" (all commands) or "command:new" (only /new)
            handler: Handler function (sync or async)
            metadata: Optional metadata (name, description, priority, etc.)
        
        Examples:
            >>> registry = HookRegistry.get_instance()
            >>> registry.register("command:new", save_session_to_memory)
            >>> registry.register("command", log_all_commands)
        """
        if event_key not in self._handlers:
            self._handlers[event_key] = []
        handlers = self._handlers[event_key]
        metadata = dict(metadata or {})
        handler_id = self._handler_id(event_key, handler)
        hook_name = metadata.get("name")

        if hook_name:
            for idx, existing in enumerate(list(handlers)):
                existing_id = self._handler_id(event_key, existing)
                existing_meta = self._metadata.get(existing_id, {})
                if existing_meta.get("name") == hook_name:
                    handlers[idx] = handler
                    self._metadata.pop(existing_id, None)
                    self._metadata[handler_id] = metadata
                    self._sort_handlers(event_key)
                    log.info("hooks.registered", {
                        "event_key": event_key,
                        "handler": self._handler_name(handler),
                        "name": hook_name,
                        "replaced": True,
                        "total_handlers": len(handlers),
                    })
                    return

        if handler in handlers:
            if metadata:
                self._metadata[handler_id] = metadata
                self._sort_handlers(event_key)
            log.debug("hooks.register_skipped_duplicate", {
                "event_key": event_key,
                "handler": self._handler_name(handler),
                "name": hook_name,
                "total_handlers": len(handlers),
            })
            return

        handlers.append(handler)

        # Save metadata
        if metadata:
            self._metadata[handler_id] = metadata

        self._sort_handlers(event_key)
        
        log.info("hooks.registered", {
            "event_key": event_key,
            "handler": self._handler_name(handler),
            "name": hook_name,
            "total_handlers": len(self._handlers[event_key]),
        })
    
    def unregister(
        self,
        event_key: str,
        handler: AsyncHookHandler,
    ) -> bool:
        """
        Unregister a hook handler
        
        Args:
            event_key: Event key
            handler: Handler function to remove
            
        Returns:
            True if handler was found and removed
        """
        if event_key not in self._handlers:
            return False
        
        handlers = self._handlers[event_key]
        try:
            handlers.remove(handler)
            
            # Clean up empty list
            if not handlers:
                del self._handlers[event_key]
            
            # Clean up metadata
            self._metadata.pop(self._handler_id(event_key, handler), None)
            
            log.info("hooks.unregistered", {"event_key": event_key})
            return True
        except ValueError:
            return False
    
    def clear(self, event_key: Optional[str] = None) -> None:
        """
        Clear hook handlers
        
        Args:
            event_key: If specified, only clear handlers for this event;
                      otherwise clear all handlers
        """
        if event_key:
            if event_key in self._handlers:
                for handler in self._handlers[event_key]:
                    self._metadata.pop(self._handler_id(event_key, handler), None)
                del self._handlers[event_key]
                log.info("hooks.cleared", {"event_key": event_key})
        else:
            self._handlers.clear()
            self._metadata.clear()
            log.info("hooks.cleared_all")
    
    def get_registered_keys(self) -> List[str]:
        """Get all registered event keys"""
        return list(self._handlers.keys())
    
    async def trigger(self, event: HookEvent) -> None:
        """
        Trigger a hook event
        
        Execution order:
        1. First call general type handlers (e.g., "command")
        2. Then call specific action handlers (e.g., "command:new")
        
        Error handling:
        - Individual handler failures don't affect other handlers
        - Errors are logged but not propagated
        
        Args:
            event: Hook event object
        """
        # Construct event keys
        type_key = event.type
        specific_key = f"{event.type}:{event.action}"
        
        # Get all matching handlers
        type_handlers = self._handlers.get(type_key, [])
        specific_handlers = self._handlers.get(specific_key, [])
        
        all_handlers = type_handlers + specific_handlers
        
        if not all_handlers:
            log.debug("hooks.no_handlers", {
                "type": event.type,
                "action": event.action,
            })
            return
        
        log.debug("hooks.trigger", {
            "type": event.type,
            "action": event.action,
            "handlers_count": len(all_handlers),
        })
        
        # Execute all handlers
        for handler in all_handlers:
            meta = self._metadata_for(type_key, specific_key, handler)
            hook_name = str(meta.get("name") or self._handler_name(handler))
            fail_policy = normalize_fail_policy(
                meta.get("fail_policy"),
                critical=bool(meta.get("critical", False)),
            )
            if "timeout_seconds" in meta:
                timeout_seconds = normalize_timeout(meta.get("timeout_seconds"))
            else:
                timeout_seconds = DEFAULT_EVENT_HOOK_TIMEOUT_SECONDS
            started_at = time.perf_counter()
            try:
                # Check if coroutine function
                if timeout_seconds is not None:
                    await asyncio.wait_for(
                        self._invoke_handler(handler, event),
                        timeout=timeout_seconds,
                    )
                else:
                    await self._invoke_handler(handler, event)
            except asyncio.TimeoutError:
                duration_ms = int((time.perf_counter() - started_at) * 1000)
                log.warning("hooks.handler_timeout", {
                    "type": event.type,
                    "action": event.action,
                    "handler": self._handler_name(handler),
                    "hook_name": hook_name,
                    "duration_ms": duration_ms,
                    "timeout_ms": int((timeout_seconds or 0) * 1000),
                    "fail_policy": fail_policy.value,
                })
                if fail_policy != FailPolicy.ISOLATE:
                    raise
            except Exception as e:
                log.error("hooks.handler_error", {
                    "type": event.type,
                    "action": event.action,
                    "handler": self._handler_name(handler),
                    "hook_name": hook_name,
                    "error": str(e),
                    "fail_policy": fail_policy.value,
                })
                if fail_policy != FailPolicy.ISOLATE:
                    raise
    
    def get_stats(self) -> Dict:
        """Get hook system statistics"""
        stats = {
            "total_event_keys": len(self._handlers),
            "total_handlers": sum(len(handlers) for handlers in self._handlers.values()),
            "event_keys": {},
        }
        
        for event_key, handlers in self._handlers.items():
            stats["event_keys"][event_key] = {
                "handler_count": len(handlers),
                "handlers": [
                    h.__name__ if hasattr(h, '__name__') else str(h)
                    for h in handlers
                ],
            }
        
        return stats

    @staticmethod
    def _handler_id(event_key: str, handler: AsyncHookHandler) -> str:
        return f"{event_key}:{id(handler)}"

    @staticmethod
    def _handler_name(handler: AsyncHookHandler) -> str:
        return handler.__name__ if hasattr(handler, "__name__") else str(handler)

    async def _invoke_handler(self, handler: AsyncHookHandler, event: HookEvent) -> None:
        if asyncio.iscoroutinefunction(handler):
            await handler(event)
        else:
            await asyncio.to_thread(handler, event)

    def _sort_handlers(self, event_key: str) -> None:
        self._handlers[event_key].sort(
            key=lambda handler: self._metadata.get(
                self._handler_id(event_key, handler),
                {},
            ).get("priority", 100)
        )

    def _metadata_for(
        self,
        type_key: str,
        specific_key: str,
        handler: AsyncHookHandler,
    ) -> Dict[str, Any]:
        return (
            self._metadata.get(self._handler_id(type_key, handler))
            or self._metadata.get(self._handler_id(specific_key, handler))
            or {}
        )


# Convenience functions

def register_hook(event_key: str, handler: AsyncHookHandler, metadata: Optional[Dict] = None) -> None:
    """Register a hook handler"""
    registry = HookRegistry.get_instance()
    registry.register(event_key, handler, metadata)


def unregister_hook(event_key: str, handler: AsyncHookHandler) -> bool:
    """Unregister a hook handler"""
    registry = HookRegistry.get_instance()
    return registry.unregister(event_key, handler)


def clear_hooks(event_key: Optional[str] = None) -> None:
    """Clear hook handlers"""
    registry = HookRegistry.get_instance()
    registry.clear(event_key)


async def trigger_hook(event: HookEvent) -> None:
    """Trigger a hook event"""
    registry = HookRegistry.get_instance()
    await registry.trigger(event)


def get_hook_stats() -> Dict:
    """Get hook statistics"""
    registry = HookRegistry.get_instance()
    return registry.get_stats()
