"""Regression: updating a device with the masked preview must not destroy the secret.

The list/GET endpoints only ever expose a masked preview of a secret field
(e.g. ``sk-***1234``). If that preview is submitted back unchanged on an update,
``persist_fields`` must recognise it as "not edited" and keep the stored secret,
rather than overwriting the real secret with its own mask.
"""

from __future__ import annotations

import pytest

from flocks.security.secrets import SecretManager
from flocks.tool.device import secrets as device_secrets


class _FakeSecretManager:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, sid: str):
        return self.store.get(sid)

    def set(self, sid: str, value: str) -> None:
        self.store[sid] = value

    def delete(self, sid: str) -> bool:
        return self.store.pop(sid, None) is not None


@pytest.fixture
def fake_secrets(monkeypatch):
    mgr = _FakeSecretManager()
    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: mgr)
    # Treat api_key as a secret field regardless of plugin metadata.
    monkeypatch.setattr(device_secrets, "_secret_keys_for", lambda storage_key: frozenset({"api_key"}))
    return mgr


def test_masked_value_preserves_secret(fake_secrets):
    device_id = "dev-1"
    # First write: real secret stored, placeholder returned.
    db = device_secrets.persist_fields(device_id, "k", {"base_url": "https://x", "api_key": "sk-real-1234"})
    sid = f"device_{device_id}_api_key"
    assert db["api_key"] == f"{{secret:{sid}}}"
    assert fake_secrets.get(sid) == "sk-real-1234"

    masked = SecretManager.mask("sk-real-1234")
    assert masked == "sk-***1234"

    # Update submitting the masked preview back → secret must be untouched.
    db2 = device_secrets.persist_fields(device_id, "k", {"api_key": masked}, prior_db_fields=db)
    assert db2["api_key"] == f"{{secret:{sid}}}"
    assert fake_secrets.get(sid) == "sk-real-1234", "masked preview overwrote the real secret"


def test_real_new_value_still_updates(fake_secrets):
    device_id = "dev-2"
    db = device_secrets.persist_fields(device_id, "k", {"api_key": "sk-old-0001"})
    sid = f"device_{device_id}_api_key"
    db2 = device_secrets.persist_fields(device_id, "k", {"api_key": "sk-new-9999"}, prior_db_fields=db)
    assert db2["api_key"] == f"{{secret:{sid}}}"
    assert fake_secrets.get(sid) == "sk-new-9999"


def test_empty_value_clears_secret(fake_secrets):
    device_id = "dev-3"
    db = device_secrets.persist_fields(device_id, "k", {"api_key": "sk-x-0001"})
    sid = f"device_{device_id}_api_key"
    db2 = device_secrets.persist_fields(device_id, "k", {"api_key": ""}, prior_db_fields=db)
    assert "api_key" not in db2
    assert fake_secrets.get(sid) is None
