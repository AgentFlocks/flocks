"""
Tests for hook registry
"""

import pytest
import asyncio
from flocks.hooks.registry import HookRegistry
from flocks.hooks.types import CommandHookEvent
from flocks.hooks.utils import create_command_event


@pytest.fixture
def registry():
    """Create a new registry instance for each test"""
    reg = HookRegistry()
    yield reg
    reg.clear()


@pytest.mark.asyncio
async def test_register_and_trigger(registry):
    """Test basic registration and triggering"""
    called = []
    
    async def handler(event):
        called.append(event.action)
    
    registry.register("command:new", handler)
    
    event = create_command_event("new", "test_session")
    await registry.trigger(event)
    
    assert called == ["new"]


@pytest.mark.asyncio
async def test_multiple_handlers(registry):
    """Test multiple handlers execute in order"""
    results = []
    
    async def handler1(event):
        results.append("handler1")
    
    async def handler2(event):
        results.append("handler2")
    
    registry.register("command", handler1)
    registry.register("command", handler2)
    
    event = create_command_event("test", "test_session")
    await registry.trigger(event)
    
    assert results == ["handler1", "handler2"]


@pytest.mark.asyncio
async def test_error_isolation(registry):
    """Test error isolation - one handler failure doesn't affect others"""
    results = []
    
    async def failing_handler(event):
        raise ValueError("Handler error")
    
    async def success_handler(event):
        results.append("success")
    
    registry.register("command", failing_handler)
    registry.register("command", success_handler)
    
    event = create_command_event("test", "test_session")
    await registry.trigger(event)
    
    # Even though first handler failed, second one still executes
    assert results == ["success"]


@pytest.mark.asyncio
async def test_type_and_specific_handlers(registry):
    """Test both type and specific handlers are called"""
    results = []
    
    async def type_handler(event):
        results.append(f"type:{event.action}")
    
    async def specific_handler(event):
        results.append(f"specific:{event.action}")
    
    # Register for all commands
    registry.register("command", type_handler)
    # Register for specific command
    registry.register("command:new", specific_handler)
    
    event = create_command_event("new", "test_session")
    await registry.trigger(event)
    
    # Both handlers should be called
    assert results == ["type:new", "specific:new"]


@pytest.mark.asyncio
async def test_unregister(registry):
    """Test unregistering handlers"""
    called = []
    
    async def handler(event):
        called.append(event.action)
    
    registry.register("command:test", handler)
    registry.unregister("command:test", handler)
    
    event = create_command_event("test", "test_session")
    await registry.trigger(event)
    
    # Handler should not be called after unregistering
    assert called == []


def test_get_stats(registry):
    """Test getting registry statistics"""
    async def handler1(event):
        pass
    
    async def handler2(event):
        pass
    
    registry.register("command", handler1)
    registry.register("command:new", handler2)
    
    stats = registry.get_stats()
    
    assert stats["total_event_keys"] == 2
    assert stats["total_handlers"] == 2
    assert "command" in stats["event_keys"]
    assert "command:new" in stats["event_keys"]


def test_register_builtin_hooks_is_idempotent():
    from flocks.hooks.builtin import register_builtin_hooks

    HookRegistry.reset_instance()
    try:
        register_builtin_hooks()
        register_builtin_hooks()

        stats = HookRegistry.get_instance().get_stats()
        assert stats["event_keys"]["command:new"]["handler_count"] == 1
    finally:
        HookRegistry.get_instance().clear()
        HookRegistry.reset_instance()


@pytest.mark.asyncio
async def test_sync_handler(registry):
    """Test that sync handlers also work"""
    results = []
    
    def sync_handler(event):
        results.append("sync")
    
    registry.register("command", sync_handler)
    
    event = create_command_event("test", "test_session")
    await registry.trigger(event)
    
    assert results == ["sync"]


@pytest.mark.asyncio
async def test_duplicate_handler_registration_is_idempotent(registry):
    """Registering the same handler twice should not duplicate side effects."""
    results = []

    async def handler(event):
        results.append(event.action)

    registry.register("command:new", handler)
    registry.register("command:new", handler)

    event = create_command_event("new", "test_session")
    await registry.trigger(event)

    assert results == ["new"]
    assert registry.get_stats()["total_handlers"] == 1


@pytest.mark.asyncio
async def test_named_handler_registration_replaces_previous_handler(registry):
    results = []

    async def old_handler(event):
        results.append("old")

    async def new_handler(event):
        results.append("new")

    registry.register("command:new", old_handler, {"name": "session-memory"})
    registry.register("command:new", new_handler, {"name": "session-memory"})

    event = create_command_event("new", "test_session")
    await registry.trigger(event)

    assert results == ["new"]
    assert registry.get_stats()["total_handlers"] == 1


@pytest.mark.asyncio
async def test_handler_timeout_isolated_by_default(registry):
    results = []

    async def slow_handler(event):
        await asyncio.sleep(0.05)
        results.append("slow")

    async def success_handler(event):
        results.append("success")

    registry.register("command", slow_handler, {"name": "slow", "timeout_seconds": 0.01})
    registry.register("command", success_handler)

    event = create_command_event("test", "test_session")
    await registry.trigger(event)

    assert results == ["success"]


@pytest.mark.asyncio
async def test_handler_timeout_can_propagate(registry):
    async def slow_handler(event):
        await asyncio.sleep(0.05)

    registry.register(
        "command",
        slow_handler,
        {"name": "critical-slow", "timeout_seconds": 0.01, "fail_policy": "propagate"},
    )

    event = create_command_event("test", "test_session")
    with pytest.raises(asyncio.TimeoutError):
        await registry.trigger(event)
