"""Tests for ``storage_key_to_service_id`` and ``row_to_device``.

Regression: when the plugin author bakes the version into the
``service_id`` itself (so ``_provider.yaml`` declares
``service_id: onesig_v2_5_3_D20250710_api``), the resulting
``storage_key`` carries *two* ``_v…`` segments. The naive greedy regex
``re.sub(r"_v[\\w.]+$", "")`` strips both back to ``onesig`` — which
then fails to resolve any ``_provider.yaml`` metadata, so the
"add device" form renders blank instead of showing the credential
fields.

These tests lock in the descriptor-aware resolution + the read-time
recompute in :func:`row_to_device` that self-heals already-saved rows.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from flocks.tool.device.store import row_to_device, storage_key_to_service_id


@pytest.fixture(autouse=True)
def reset_descriptor_cache(monkeypatch, tmp_path):
    """Point the descriptor scanner at an isolated tools dir for each test."""
    from flocks.config import api_versioning as versioning

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    versioning._reset_descriptor_cache()
    yield
    versioning._reset_descriptor_cache()


def _drop_plugin(home: Path, *, plugin_id: str, service_id: str, version: str) -> None:
    plugin_dir = home / ".flocks" / "plugins" / "tools" / "device" / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "_provider.yaml").write_text(
        yaml.safe_dump(
            {
                "name": plugin_id,
                "service_id": service_id,
                "version": version,
                "integration_type": "device",
                "credential_fields": [{"key": "base_url", "label": "Base URL"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


class TestStorageKeyToServiceId:
    def test_strips_trailing_version_suffix(self):
        # No descriptor needed — pure regex fallback path.
        assert storage_key_to_service_id("sangfor_af_v8_0_106") == "sangfor_af"
        assert storage_key_to_service_id("tdp_api_v3_3_10") == "tdp_api"
        assert storage_key_to_service_id("ngsoc_api_v4_15_1") == "ngsoc_api"

    def test_returns_unversioned_input_unchanged(self):
        assert storage_key_to_service_id("foo") == "foo"
        assert storage_key_to_service_id("") == ""

    def test_descriptor_lookup_prefers_provider_service_id(self, tmp_path):
        """When the plugin's ``service_id`` already contains its own
        ``_v…`` token, descriptor lookup must return the full declared
        ``service_id`` instead of greedily peeling both segments.

        Without the descriptor-aware path, the naive regex collapses
        ``onesig_v2_5_3_D20250710_api_v2_5_3_D20250710`` to ``onesig``
        and the device-add form ends up with no credential fields.
        """
        _drop_plugin(
            Path.home(),
            plugin_id="onesig_v2_5_3_D20250710",
            service_id="onesig_v2_5_3_D20250710_api",
            version="2.5.3 D20250710",
        )
        from flocks.config.api_versioning import (
            discover_api_service_descriptors,
        )

        discover_api_service_descriptors(refresh=True)

        result = storage_key_to_service_id(
            "onesig_v2_5_3_D20250710_api_v2_5_3_D20250710"
        )
        assert result == "onesig_v2_5_3_D20250710_api", (
            "descriptor lookup must restore the plugin's declared "
            "service_id even when it contains its own _v… token"
        )

    def test_fallback_when_no_descriptor(self, tmp_path):
        """Without a descriptor we fall back to the anchored regex.

        The fallback intentionally stays best-effort — its job is to
        avoid crashing for stale config rows whose backing plugin has
        already been uninstalled.
        """
        # No plugin dropped → empty descriptor cache.
        assert (
            storage_key_to_service_id("dangling_plugin_v1_0_0")
            == "dangling_plugin"
        )


class TestRowToDeviceSelfHeals:
    def test_recomputes_service_id_from_storage_key(self, tmp_path):
        """A row created before the fix may have a wrong ``service_id``
        stored in the DB (e.g. ``onesig`` instead of
        ``onesig_v2_5_3_D20250710_api``). ``row_to_device`` should heal
        it on read by recomputing from the row's ``storage_key`` —
        otherwise the device-edit form keeps showing blank credentials.
        """
        _drop_plugin(
            Path.home(),
            plugin_id="onesig_v2_5_3_D20250710",
            service_id="onesig_v2_5_3_D20250710_api",
            version="2.5.3 D20250710",
        )
        from flocks.config.api_versioning import (
            discover_api_service_descriptors,
        )

        discover_api_service_descriptors(refresh=True)

        row = MagicMock()
        row.__getitem__.side_effect = {
            "id": "dev-1",
            "group_id": "default-room",
            "name": "OneSIG-older",
            "storage_key": "onesig_v2_5_3_D20250710_api_v2_5_3_D20250710",
            # Intentionally wrong — pretend the row was saved before the fix.
            "service_id": "onesig",
            "enabled": 1,
            "verify_ssl": 0,
            "fields": "{}",
            "status": "unknown",
            "message": None,
            "latency_ms": None,
            "checked_at": None,
            "created_at": 0,
            "updated_at": 0,
        }.__getitem__

        device = row_to_device(row)
        assert device.service_id == "onesig_v2_5_3_D20250710_api"
        assert device.storage_key == "onesig_v2_5_3_D20250710_api_v2_5_3_D20250710"
