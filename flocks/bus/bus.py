"""
Event Bus

Publish-subscribe event system for inter-module communication.
Based on Flocks' ported src/bus/index.ts
"""

from typing import Dict, List, Callable, Any, Optional, TypeVar, Generic
from collections import defaultdict
import asyncio
from flocks.utils.log import Log
from flocks.bus.bus_event import EventDefinition, BusEvent


log = Log.create(service="bus")


# Type alias for event callback
EventCallback = Callable[[Dict[str, Any]], Any]


class EventPayload(Generic[TypeVar("T")]):
    """Event payload with type and properties"""
    
    def __init__(self, event_type: str, properties: Dict[str, Any]):
        self.type = event_type
        self.properties = properties
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "type": self.type,
            "properties": self.properties,
        }


class Bus:
    """
    Event Bus for publish-subscribe communication
    
    Mirrors original Flocks Bus namespace from bus/index.ts
    
    Example:
        >>> # Define an event
        >>> class SessionCreatedProps(BaseModel):
        ...     session_id: str
        ... 
        >>> SessionCreated = BusEvent.define("session.created", SessionCreatedProps)
        >>> 
        >>> # Subscribe to event
        >>> def on_session_created(event):
        ...     print(f"Session created: {event['properties']['session_id']}")
        >>> 
        >>> Bus.subscribe(SessionCreated, on_session_created)
        >>> 
        >>> # Publish event
        >>> await Bus.publish(SessionCreated, {"session_id": "abc123"})
    """
    
    # Subscriptions: event_type -> list of callbacks
    _subscriptions: Dict[str, List[EventCallback]] = defaultdict(list)
    
    # Global event callback (for debugging/monitoring)
    _global_callback: Optional[EventCallback] = None
    
    @classmethod
    async def publish(
        cls,
        definition: EventDefinition,
        properties: Dict[str, Any],
    ) -> None:
        """
        Publish an event to all subscribers
        
        Matches TypeScript Bus.publish()
        
        Args:
            definition: Event definition from BusEvent.define()
            properties: Event properties (validated against schema)
            
        Example:
            >>> await Bus.publish(SessionCreated, {"session_id": "abc123"})
        """
        # Validate properties
        try:
            validated = definition.validate(properties)
            properties = validated.model_dump(by_alias=True)
        except Exception as e:
            log.error("bus.publish.validation_error", {
                "type": definition.type,
                "error": str(e),
            })
            raise
        
        # Create payload
        payload = EventPayload(definition.type, properties)

        log.info("bus.publishing", {"type": definition.type})
        
        # Collect callbacks for this event type and wildcard ("*")
        callbacks = []
        
        # Specific event type subscribers
        if definition.type in cls._subscriptions:
            callbacks.extend(cls._subscriptions[definition.type])
        
        # Wildcard subscribers
        if "*" in cls._subscriptions:
            callbacks.extend(cls._subscriptions["*"])
        
        # Global callback
        if cls._global_callback:
            callbacks.append(cls._global_callback)
        
        # Execute all callbacks
        tasks = []
        for callback in callbacks:
            try:
                result = callback(payload.to_dict())
                # Handle both sync and async callbacks
                if asyncio.iscoroutine(result):
                    tasks.append(result)
            except Exception as e:
                log.error("bus.callback.error", {
                    "type": definition.type,
                    "error": str(e),
                })
        
        # Wait for async callbacks
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    @classmethod
    def subscribe(
        cls,
        definition: EventDefinition,
        callback: EventCallback,
    ) -> Callable[[], None]:
        """
        Subscribe to an event
        
        Matches TypeScript Bus.subscribe()
        
        Args:
            definition: Event definition
            callback: Callback function to call when event is published
            
        Returns:
            Unsubscribe function
            
        Example:
            >>> def handler(event):
            ...     print(event)
            >>> 
            >>> unsubscribe = Bus.subscribe(SessionCreated, handler)
            >>> # Later...
            >>> unsubscribe()
        """
        log.debug("bus.subscribing", {"type": definition.type})
        
        cls._subscriptions[definition.type].append(callback)
        
        # Return unsubscribe function
        def unsubscribe():
            log.info("bus.unsubscribing", {"type": definition.type})
            if definition.type in cls._subscriptions:
                try:
                    cls._subscriptions[definition.type].remove(callback)
                    # Clean up empty list
                    if not cls._subscriptions[definition.type]:
                        del cls._subscriptions[definition.type]
                except ValueError:
                    pass
        
        return unsubscribe
    
    @classmethod
    def once(
        cls,
        definition: EventDefinition,
        callback: Callable[[Dict[str, Any]], Optional[str]],
    ) -> None:
        """
        Subscribe to an event once (auto-unsubscribe after first call)
        
        Matches TypeScript Bus.once()
        
        Args:
            definition: Event definition
            callback: Callback that returns "done" to unsubscribe
        """
        unsubscribe_fn = None
        
        def wrapper(event: Dict[str, Any]):
            result = callback(event)
            if result == "done" and unsubscribe_fn:
                unsubscribe_fn()
        
        unsubscribe_fn = cls.subscribe(definition, wrapper)
    
    @classmethod
    def subscribe_all(cls, callback: EventCallback) -> Callable[[], None]:
        """
        Subscribe to all events (wildcard subscription)
        
        Matches TypeScript Bus.subscribeAll()
        
        Args:
            callback: Callback for all events
            
        Returns:
            Unsubscribe function
        """
        log.debug("bus.subscribing", {"type": "*"})
        
        cls._subscriptions["*"].append(callback)
        
        def unsubscribe():
            log.info("bus.unsubscribing", {"type": "*"})
            if "*" in cls._subscriptions:
                try:
                    cls._subscriptions["*"].remove(callback)
                    if not cls._subscriptions["*"]:
                        del cls._subscriptions["*"]
                except ValueError:
                    pass
        
        return unsubscribe
    
    @classmethod
    def set_global_callback(cls, callback: Optional[EventCallback]) -> None:
        """
        Set a global callback for all events (monitoring/debugging)
        
        Args:
            callback: Global callback or None to clear
        """
        cls._global_callback = callback
        log.info("bus.global_callback.set", {"enabled": callback is not None})
    
    @classmethod
    def clear_subscriptions(cls) -> None:
        """Clear all subscriptions (for testing)"""
        cls._subscriptions.clear()
        cls._global_callback = None
        log.info("bus.subscriptions.cleared")
    
    @classmethod
    def get_subscription_count(cls, event_type: Optional[str] = None) -> int:
        """
        Get number of subscribers
        
        Args:
            event_type: Specific event type, or None for all
            
        Returns:
            Number of subscribers
        """
        if event_type:
            return len(cls._subscriptions.get(event_type, []))
        return sum(len(callbacks) for callbacks in cls._subscriptions.values())
