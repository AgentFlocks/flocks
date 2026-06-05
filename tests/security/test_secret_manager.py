#!/usr/bin/env python3
"""
Test script for SecretManager (flat KV)

Usage:
    .venv/bin/python scripts/test_secret_manager.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flocks.security import get_secret_manager
from flocks.security.secrets import SecretManager


def test_basic_operations():
    """Test basic CRUD operations."""
    print("Testing SecretManager (flat KV)...")

    secrets = get_secret_manager()

    # Test 1: Set secret
    print("\n1. Setting secret...")
    secrets.set("test_api_key", "sk-1234567890abcdef")
    print("   OK: Secret set")

    # Test 2: Get secret
    print("\n2. Getting secret...")
    value = secrets.get("test_api_key")
    assert value == "sk-1234567890abcdef", "Secret value mismatch"
    print(f"   OK: Retrieved: {value}")

    # Test 3: Mask secret
    print("\n3. Masking secret...")
    masked = SecretManager.mask(value)
    print(f"   OK: Masked: {masked}")
    assert "***" in masked, "Masking failed"
    assert masked.startswith("sk-"), "Prefix not preserved"

    # Test 4: Check existence
    print("\n4. Checking existence...")
    assert secrets.has("test_api_key"), "Secret should exist"
    assert not secrets.has("nonexistent"), "Should not exist"
    print("   OK: Existence check passed")

    # Test 5: List secrets
    print("\n5. Listing secrets...")
    ids = secrets.list()
    print(f"   OK: Secret IDs: {ids}")
    assert "test_api_key" in ids, "Secret not in list"

    # Test 6: Delete secret
    print("\n6. Deleting secret...")
    deleted = secrets.delete("test_api_key")
    assert deleted, "Delete failed"
    assert secrets.get("test_api_key") is None, "Should be deleted"
    print("   OK: Secret deleted")

    print("\nAll tests passed!")
    print(f"\nSecret file location: {secrets.secret_file}")


def test_file_permissions():
    """Test file permissions."""
    import stat

    print("\nTesting file permissions...")

    secrets = get_secret_manager()
    user_perms = secrets.secret_file.stat().st_mode & 0o777
    print(f"   File permissions: {oct(user_perms)}")
    assert user_perms == 0o600, f"Expected 0o600, got {oct(user_perms)}"
    print("   OK: Permissions are secure (600)")


def main():
    try:
        test_basic_operations()
        test_file_permissions()
        print("\n" + "=" * 50)
        print("All tests completed successfully!")
        print("=" * 50)
    except AssertionError as e:
        print(f"\nTest failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
