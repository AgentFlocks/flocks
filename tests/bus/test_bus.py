"""
Tests for Bus event system

Validates event definition, publishing, and subscription.
"""

import pytest
import asyncio
from collections import defaultdict
from pydantic import BaseModel
from flocks.bus.bus_event import BusEvent
from flocks.bus.bus import Bus


class SampleEventProps(BaseModel):
    """Sample event properties for testing"""
    message: str
    count: int


@pytest.fixture(autouse=True)
def cleanup(monkeypatch):
    """Isolate test events while retaining subscribers installed at import."""
    monkeypatch.setattr(BusEvent, "_registry", {})
    monkeypatch.setattr(Bus, "_subscriptions", defaultdict(list))
    monkeypatch.setattr(Bus, "_global_callback", None)


def test_define_event():
    """Test event definition"""
    TestEvent = BusEvent.define("test.event", SampleEventProps)
    
    assert TestEvent.type == "test.event"
    assert TestEvent.properties_schema == SampleEventProps
    
    # Should be in registry
    assert "test.event" in BusEvent.list_types()


def test_validate_event_properties():
    """Test event property validation"""
    TestEvent = BusEvent.define("test.validate", SampleEventProps)
    
    # Valid properties
    validated = TestEvent.validate({"message": "hello", "count": 5})
    assert validated.message == "hello"
    assert validated.count == 5
    
    # Invalid properties should raise error
    with pytest.raises(Exception):
        TestEvent.validate({"message": "hello"})  # missing count


@pytest.mark.asyncio
async def test_publish_and_subscribe():
    """Test basic publish/subscribe"""
    TestEvent = BusEvent.define("test.pubsub", SampleEventProps)
    
    received = []
    
    def handler(event):
        received.append(event)
    
    # Subscribe
    unsubscribe = Bus.subscribe(TestEvent, handler)
    
    # Publish
    await Bus.publish(TestEvent, {"message": "test", "count": 1})
    
    # Should receive event
    assert len(received) == 1
    assert received[0]["type"] == "test.pubsub"
    assert received[0]["properties"]["message"] == "test"
    assert received[0]["properties"]["count"] == 1
    
    # Unsubscribe
    unsubscribe()
    
    # Publish again
    await Bus.publish(TestEvent, {"message": "test2", "count": 2})
    
    # Should not receive (unsubscribed)
    assert len(received) == 1


@pytest.mark.asyncio
async def test_multiple_subscribers():
    """Test multiple subscribers"""
    TestEvent = BusEvent.define("test.multiple", SampleEventProps)
    
    received1 = []
    received2 = []
    
    Bus.subscribe(TestEvent, lambda e: received1.append(e))
    Bus.subscribe(TestEvent, lambda e: received2.append(e))
    
    await Bus.publish(TestEvent, {"message": "broadcast", "count": 3})
    
    # Both should receive
    assert len(received1) == 1
    assert len(received2) == 1
    assert received1[0]["properties"]["message"] == "broadcast"
    assert received2[0]["properties"]["message"] == "broadcast"


@pytest.mark.asyncio
async def test_subscribe_all():
    """Test wildcard subscription"""
    Event1 = BusEvent.define("test.event1", SampleEventProps)
    Event2 = BusEvent.define("test.event2", SampleEventProps)
    
    received = []
    
    Bus.subscribe_all(lambda e: received.append(e))
    
    await Bus.publish(Event1, {"message": "e1", "count": 1})
    await Bus.publish(Event2, {"message": "e2", "count": 2})
    
    # Should receive both events
    assert len(received) == 2
    assert received[0]["type"] == "test.event1"
    assert received[1]["type"] == "test.event2"


@pytest.mark.asyncio
async def test_once():
    """Test once subscription"""
    TestEvent = BusEvent.define("test.once", SampleEventProps)
    
    received = []
    
    def handler(event):
        received.append(event)
        return "done"  # Signal to unsubscribe
    
    Bus.once(TestEvent, handler)
    
    # First publish
    await Bus.publish(TestEvent, {"message": "first", "count": 1})
    assert len(received) == 1
    
    # Second publish - should not receive
    await Bus.publish(TestEvent, {"message": "second", "count": 2})
    assert len(received) == 1  # Still only 1


@pytest.mark.asyncio
async def test_async_handler():
    """Test async event handlers"""
    TestEvent = BusEvent.define("test.async", SampleEventProps)
    
    received = []
    
    async def async_handler(event):
        await asyncio.sleep(0.01)  # Simulate async work
        received.append(event)
    
    Bus.subscribe(TestEvent, async_handler)
    
    await Bus.publish(TestEvent, {"message": "async", "count": 10})
    
    # Should receive after async work completes
    assert len(received) == 1


@pytest.mark.asyncio
async def test_subscription_count():
    """Test subscription counting"""
    TestEvent = BusEvent.define("test.count", SampleEventProps)
    
    assert Bus.get_subscription_count() == 0
    assert Bus.get_subscription_count("test.count") == 0
    
    unsub1 = Bus.subscribe(TestEvent, lambda e: None)
    assert Bus.get_subscription_count("test.count") == 1
    
    unsub2 = Bus.subscribe(TestEvent, lambda e: None)
    assert Bus.get_subscription_count("test.count") == 2
    
    unsub1()
    assert Bus.get_subscription_count("test.count") == 1
    
    unsub2()
    assert Bus.get_subscription_count("test.count") == 0


def test_get_definition():
    """Test getting event definition"""
    TestEvent = BusEvent.define("test.getdef", SampleEventProps)
    
    retrieved = BusEvent.get_definition("test.getdef")
    assert retrieved.type == "test.getdef"
    assert retrieved.properties_schema == SampleEventProps
    
    # Non-existent event should raise error
    with pytest.raises(KeyError):
        BusEvent.get_definition("nonexistent")
