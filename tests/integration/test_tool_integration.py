"""
Tool integration tests.

Tests tool registration.
Tests that relied on the removed runtime/ module (ToolCoordinator, strategy)
have been removed as part of the runtime/ cleanup.
"""

import pytest

from flocks.tool.registry import ToolRegistry


class TestToolRegistration:
    """Test tool registration."""

    def test_threatbook_tools_registered(self):
        """Verify ThreatBook tools are registered."""
        ToolRegistry.init()
        tools = [t.name for t in ToolRegistry.list_tools()]
        threatbook_tools = [name for name in tools if name.startswith("threatbook")]

        if not threatbook_tools:
            pytest.skip("ThreatBook tools are not available in this environment")

        assert any(name.endswith("ip_query") for name in threatbook_tools)
        assert any(name.endswith("domain_query") for name in threatbook_tools)
