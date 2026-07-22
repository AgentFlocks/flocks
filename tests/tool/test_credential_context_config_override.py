"""Unit tests for get_config_override dual-key matching.

Regression suite for the bug where handlers whose ``SERVICE_ID`` constant uses
the full versioned storage_key (e.g. ``"sangfor_af_v8_0_48"``) instead of the
bare service_id (e.g. ``"sangfor_af"``) would always receive ``None`` from
``get_config_override``, causing a silent fallback to the global default config
(wrong IP / wrong credentials).

The tests set ContextVars directly — no DB, no ToolRegistry, no network.
"""
from __future__ import annotations

import pytest

from flocks.tool.credential_context import (
    _config_override,
    _config_override_service,
    _config_override_storage_key,
    activate_device_credentials,
    get_config_override,
)

_SAMPLE_CONFIG = {"base_url": "https://10.201.255.17", "enabled": True}


@pytest.fixture(autouse=True)
def _reset_context_vars():
    """Reset all three ContextVars before AND after every test.

    ContextVars persist for the lifetime of an execution context (the test
    thread), so without explicit cleanup a test that sets a var can leak state
    into the next test — even if the next test calls _set_context(), it might
    leave the var in an unexpected state if it only sets some of the vars.
    """
    _config_override.set(None)
    _config_override_service.set(None)
    _config_override_storage_key.set(None)
    yield
    _config_override.set(None)
    _config_override_service.set(None)
    _config_override_storage_key.set(None)


def _set_context(
    *,
    config: dict,
    service_id: str | None,
    storage_key: str | None,
):
    """Set the three ContextVars that activate_device_credentials populates."""
    _config_override.set(config)
    _config_override_service.set(service_id)
    _config_override_storage_key.set(storage_key)


# ---------------------------------------------------------------------------
# Core matching scenarios
# ---------------------------------------------------------------------------

def test_matches_bare_service_id():
    """Handler uses bare service_id — original behaviour must still work."""
    _set_context(
        config=_SAMPLE_CONFIG,
        service_id="sangfor_af",
        storage_key="sangfor_af_v8_0_48",
    )
    result = get_config_override("sangfor_af")
    assert result is _SAMPLE_CONFIG


def test_matches_versioned_storage_key():
    """Handler uses versioned SERVICE_ID — the new behaviour introduced by this fix."""
    _set_context(
        config=_SAMPLE_CONFIG,
        service_id="sangfor_af",
        storage_key="sangfor_af_v8_0_48",
    )
    result = get_config_override("sangfor_af_v8_0_48")
    assert result is _SAMPLE_CONFIG


def test_no_match_returns_none():
    """A completely unrelated service_id must never receive another device's config."""
    _set_context(
        config=_SAMPLE_CONFIG,
        service_id="sangfor_af",
        storage_key="sangfor_af_v8_0_48",
    )
    assert get_config_override("tdp_api") is None
    assert get_config_override("sangfor_af_v8_0_85") is None  # different version


def test_no_override_active_returns_none():
    """When no device credential context is active, always return None."""
    _config_override.set(None)
    _config_override_service.set(None)
    _config_override_storage_key.set(None)

    assert get_config_override("sangfor_af") is None
    assert get_config_override("sangfor_af_v8_0_48") is None


# ---------------------------------------------------------------------------
# Guard: storage_key=None should not accidentally match an empty string lookup
# ---------------------------------------------------------------------------

def test_none_storage_key_does_not_match_empty_string():
    """storage_key=None must not match service_id='' (falsy equality trap)."""
    _set_context(
        config=_SAMPLE_CONFIG,
        service_id="sangfor_af",
        storage_key=None,
    )
    assert get_config_override("") is None


def test_none_service_id_does_not_match_empty_string():
    """service_id=None must not match service_id='' (falsy equality trap)."""
    _set_context(
        config=_SAMPLE_CONFIG,
        service_id=None,
        storage_key="sangfor_af_v8_0_48",
    )
    assert get_config_override("") is None


# ---------------------------------------------------------------------------
# Identical service_id and storage_key (edge case)
# ---------------------------------------------------------------------------

def test_identical_service_and_storage_key():
    """When both keys are the same (no version suffix), matching still works."""
    _set_context(
        config=_SAMPLE_CONFIG,
        service_id="tdp_api",
        storage_key="tdp_api",
    )
    assert get_config_override("tdp_api") is _SAMPLE_CONFIG
    assert get_config_override("other") is None


@pytest.mark.asyncio
async def test_activate_preserves_legacy_fields_not_in_current_schema(monkeypatch):
    """Old device rows can keep using fields removed from a newer schema."""

    async def _fake_credentials(_device_id: str):
        return {
            "storage_key": "ngtip_api_v5_1_5",
            "service_id": "ngtip_api",
            "verify_ssl": False,
            "fields": {
                "apikey": "legacy-key",
                "query_apikey": "query-key",
            },
        }

    monkeypatch.setattr(
        "flocks.tool.device.store.get_device_credentials",
        _fake_credentials,
    )
    monkeypatch.setattr(
        "flocks.tool.credential_context._load_credential_fields",
        lambda _storage_key: [
            {
                "key": "query_apikey",
                "storage": "secret",
                "secret_id": "ngtip_query_apikey",
                "config_key": "queryApiKey",
            }
        ],
    )

    async with activate_device_credentials("dev-a") as active:
        assert active is True
        config = get_config_override("ngtip_api_v5_1_5")

    assert config is not None
    assert config["queryApiKey"] == "{secret:ngtip_query_apikey}"
    assert config["apikey"] == "legacy-key"
