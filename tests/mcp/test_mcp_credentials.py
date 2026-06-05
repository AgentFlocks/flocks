"""Tests for MCP server credential storage via SecretManager."""

import pytest
from flocks.security import get_secret_manager


TEST_SECRET_KEY = "mcp.test-server.api_key"
TEST_VALUE = "sk-test-key-12345"


@pytest.fixture(autouse=True)
def cleanup_test_secret():
    sm = get_secret_manager()
    sm.delete(TEST_SECRET_KEY)
    yield
    sm.delete(TEST_SECRET_KEY)


def test_store_and_retrieve_mcp_credentials():
    """MCP credentials can be stored and retrieved via SecretManager."""
    sm = get_secret_manager()
    sm.set(TEST_SECRET_KEY, TEST_VALUE)
    value = sm.get(TEST_SECRET_KEY)
    assert value == TEST_VALUE


def test_mcp_credential_not_present_by_default():
    """MCP credential lookup returns None when not configured."""
    sm = get_secret_manager()
    value = sm.get("mcp.nonexistent-server.api_key")
    assert value is None


def test_mcp_credential_has():
    """has() correctly reports presence of MCP credentials."""
    sm = get_secret_manager()
    assert sm.has(TEST_SECRET_KEY) is False
    sm.set(TEST_SECRET_KEY, TEST_VALUE)
    assert sm.has(TEST_SECRET_KEY) is True


def test_mcp_credential_delete():
    """Deleting MCP credentials removes them from storage."""
    sm = get_secret_manager()
    sm.set(TEST_SECRET_KEY, TEST_VALUE)
    result = sm.delete(TEST_SECRET_KEY)
    assert result is True
    assert sm.get(TEST_SECRET_KEY) is None
