"""Tests for Provider credentials management via SecretManager."""

import pytest
from flocks.security import get_secret_manager


@pytest.fixture(autouse=True)
def cleanup_test_secret():
    """Remove test secret before and after each test."""
    sm = get_secret_manager()
    test_key = "provider.test-openai.api_key"
    sm.delete(test_key)
    yield
    sm.delete(test_key)


def test_set_and_get_secret():
    """set/get roundtrip works correctly."""
    sm = get_secret_manager()
    sm.set("provider.test-openai.api_key", "sk-test-1234567890")
    value = sm.get("provider.test-openai.api_key")
    assert value == "sk-test-1234567890"


def test_has_secret():
    """has() returns True after set and False before set."""
    sm = get_secret_manager()
    assert sm.has("provider.test-openai.api_key") is False
    sm.set("provider.test-openai.api_key", "sk-value")
    assert sm.has("provider.test-openai.api_key") is True


def test_delete_secret():
    """delete() removes the secret."""
    sm = get_secret_manager()
    sm.set("provider.test-openai.api_key", "sk-value")
    result = sm.delete("provider.test-openai.api_key")
    assert result is True
    assert sm.get("provider.test-openai.api_key") is None


def test_mask_secret():
    """mask() hides most of the secret value."""
    sm = get_secret_manager()
    original = "sk-test-1234567890abcdef"
    masked = sm.mask(original)
    assert masked != original
    assert "***" in masked or "*" in masked


def test_get_nonexistent_secret():
    """get() returns None for unknown keys."""
    sm = get_secret_manager()
    value = sm.get("provider.nonexistent-provider.api_key")
    assert value is None


def test_list_secrets():
    """list() includes recently added secrets."""
    sm = get_secret_manager()
    sm.set("provider.test-openai.api_key", "sk-value")
    keys = sm.list()
    assert isinstance(keys, list)
    assert "provider.test-openai.api_key" in keys
