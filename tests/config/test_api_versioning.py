"""Tests for ``flocks.config.api_versioning``.

Covers:
- ``derive_storage_key`` shape and edge cases
- Plugin descriptor discovery + cache invalidation
- ``legacy_service_id_for`` resolution (registry + heuristic)
- ``shadowed_legacy_ids`` behaviour
- ``migrate_api_services`` copy-only semantics, idempotence, backup
- ``ConfigWriter.get_api_service_raw`` legacy fallback
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from flocks.config import api_versioning as versioning
from flocks.config.api_versioning import (
    derive_storage_key,
    discover_api_service_descriptors,
    legacy_service_id_for,
    migrate_api_services,
    shadowed_legacy_ids,
    versioned_storage_key_for,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_provider_yaml(plugin_dir: Path, *, service_id: str, version: str | None) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {"name": service_id, "service_id": service_id}
    if version is not None:
        payload["version"] = version
    (plugin_dir / "_provider.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
    )


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Run each test inside an empty project + private user-config dir."""
    from flocks.config.config import Config

    project_root = tmp_path / "project"
    user_config = tmp_path / "home" / ".flocks" / "config"
    user_config.mkdir(parents=True)
    project_root.mkdir(parents=True)

    monkeypatch.chdir(project_root)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(user_config))
    Config._global_config = None
    Config._cached_config = None

    versioning._reset_descriptor_cache()
    yield project_root, user_config
    versioning._reset_descriptor_cache()


@pytest.fixture
def api_root(isolated_env):
    project_root, _ = isolated_env
    root = project_root / ".flocks" / "plugins" / "tools" / "api"
    root.mkdir(parents=True)
    return root


def _write_flocks_json(user_config: Path, payload: dict) -> Path:
    path = user_config / "flocks.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# derive_storage_key
# ---------------------------------------------------------------------------

class TestDeriveStorageKey:
    def test_no_version_returns_service_id(self):
        assert derive_storage_key("foo", None) == "foo"
        assert derive_storage_key("foo", "") == "foo"

    def test_simple_version(self):
        assert derive_storage_key("tdp_api", "3.3.10") == "tdp_api_v3_3_10"

    def test_version_with_spaces_and_punctuation(self):
        assert (
            derive_storage_key("onesig_api", "2.5.3 D20260321")
            == "onesig_api_v2_5_3_D20260321"
        )

    def test_version_with_only_separators_collapses_to_service_id(self):
        # Pure punctuation can't form a meaningful suffix; fall back gracefully.
        assert derive_storage_key("foo", "...") == "foo"

    def test_empty_service_id_rejected(self):
        with pytest.raises(ValueError):
            derive_storage_key("", "1.0")


# ---------------------------------------------------------------------------
# discover_api_service_descriptors
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_picks_up_versioned_and_unversioned(self, api_root):
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_provider_yaml(api_root / "qingteng", service_id="qingteng", version=None)

        descriptors = discover_api_service_descriptors(refresh=True)
        by_key = {d.storage_key: d for d in descriptors}

        assert by_key["tdp_api_v3_3_10"].service_id == "tdp_api"
        assert by_key["tdp_api_v3_3_10"].version == "3.3.10"
        assert by_key["qingteng"].service_id == "qingteng"
        assert by_key["qingteng"].version is None

    def test_directory_name_does_not_constrain_storage_key(self, api_root):
        # Storage key is derived from yaml content, NOT from directory name.
        _write_provider_yaml(api_root / "weirdly_named", service_id="tdp_api", version="3.3.10")
        descriptors = discover_api_service_descriptors(refresh=True)
        assert any(d.storage_key == "tdp_api_v3_3_10" for d in descriptors)

    def test_skips_dirs_without_provider_yaml(self, api_root):
        (api_root / "stray").mkdir()
        (api_root / "stray" / "junk.yaml").write_text("name: junk\n")
        assert discover_api_service_descriptors(refresh=True) == []

    def test_refresh_picks_up_new_plugin(self, api_root):
        _write_provider_yaml(api_root / "qingteng", service_id="qingteng", version=None)
        first = discover_api_service_descriptors(refresh=True)
        assert {d.storage_key for d in first} == {"qingteng"}

        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        cached = discover_api_service_descriptors()  # cached, doesn't see new plugin
        assert {d.storage_key for d in cached} == {"qingteng"}

        refreshed = discover_api_service_descriptors(refresh=True)
        assert {d.storage_key for d in refreshed} == {"qingteng", "tdp_api_v3_3_10"}


# ---------------------------------------------------------------------------
# legacy_service_id_for / shadowed_legacy_ids
# ---------------------------------------------------------------------------

class TestLegacyResolution:
    def test_resolves_via_registry(self, api_root):
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        versioning._reset_descriptor_cache()
        assert legacy_service_id_for("tdp_api_v3_3_10") == "tdp_api"

    def test_unversioned_known_key_returns_none(self, api_root):
        _write_provider_yaml(api_root / "qingteng", service_id="qingteng", version=None)
        versioning._reset_descriptor_cache()
        assert legacy_service_id_for("qingteng") is None

    def test_heuristic_strip_for_unknown_key(self, api_root):
        # Empty registry, fallback to suffix-strip heuristic.
        versioning._reset_descriptor_cache()
        assert legacy_service_id_for("foo_v1_2_3") == "foo"

    def test_no_version_suffix_returns_none(self, api_root):
        versioning._reset_descriptor_cache()
        assert legacy_service_id_for("plain_name") is None

    def test_shadowed_when_versioned_key_present(self, api_root):
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        versioning._reset_descriptor_cache()
        assert shadowed_legacy_ids({"tdp_api", "tdp_api_v3_3_10"}) == {"tdp_api"}

    def test_not_shadowed_when_no_versioned_counterpart(self, api_root):
        _write_provider_yaml(api_root / "qingteng", service_id="qingteng", version=None)
        versioning._reset_descriptor_cache()
        assert shadowed_legacy_ids({"qingteng"}) == set()

    def test_not_shadowed_when_versioned_key_absent(self, api_root):
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        versioning._reset_descriptor_cache()
        # Versioned key not present in flocks.json -> legacy block stays visible.
        assert shadowed_legacy_ids({"tdp_api"}) == set()

    def test_shadowed_legacy_ids_batch(self, api_root):
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_provider_yaml(api_root / "skyeye_v4_0_14_0_SP2",
                             service_id="skyeye_api", version="4.0.14.0.SP2")
        _write_provider_yaml(api_root / "ngtip_v5_1_5", service_id="ngtip_api", version="5.1.5")
        _write_provider_yaml(api_root / "qingteng", service_id="qingteng", version=None)
        versioning._reset_descriptor_cache()

        # ``tdp_api_v3_3_10`` and ``skyeye_api_v4_0_14_0_SP2`` are present
        # → their legacy ids ``tdp_api`` / ``skyeye_api`` are shadowed,
        # whether or not the legacy keys themselves appear in ``present``.
        # ``ngtip_api_v5_1_5`` is absent → ``ngtip_api`` not shadowed.
        # ``qingteng`` is unversioned → no shadow possible.
        present = {"tdp_api", "tdp_api_v3_3_10", "skyeye_api_v4_0_14_0_SP2",
                   "ngtip_api", "qingteng"}
        assert shadowed_legacy_ids(present) == {"tdp_api", "skyeye_api"}

        # Set-difference is the typical caller idiom — surviving ids are
        # exactly the ones a UI should display.
        assert present - shadowed_legacy_ids(present) == {
            "tdp_api_v3_3_10", "skyeye_api_v4_0_14_0_SP2", "ngtip_api", "qingteng",
        }


# ---------------------------------------------------------------------------
# versioned_storage_key_for
# ---------------------------------------------------------------------------

class TestVersionedStorageKeyResolution:
    def test_returns_unique_versioned_key(self, api_root):
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        versioning._reset_descriptor_cache()
        assert versioned_storage_key_for("tdp_api") == "tdp_api_v3_3_10"

    def test_returns_none_when_only_unversioned(self, api_root):
        _write_provider_yaml(api_root / "qingteng", service_id="qingteng", version=None)
        versioning._reset_descriptor_cache()
        assert versioned_storage_key_for("qingteng") is None

    def test_returns_none_when_unknown_service_id(self, api_root):
        versioning._reset_descriptor_cache()
        assert versioned_storage_key_for("ghost_service") is None

    def test_refuses_to_guess_when_multiple_versions(self, api_root):
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_provider_yaml(api_root / "tdp_v4_0_0", service_id="tdp_api", version="4.0.0")
        versioning._reset_descriptor_cache()
        # Ambiguous → caller must choose explicitly.
        assert versioned_storage_key_for("tdp_api") is None


# ---------------------------------------------------------------------------
# migrate_api_services
# ---------------------------------------------------------------------------

class TestMigration:
    def test_copies_legacy_to_storage_key(self, isolated_env, api_root):
        _, user_config = isolated_env
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        config_path = _write_flocks_json(user_config, {
            "api_services": {
                "tdp_api": {
                    "enabled": True,
                    "apiKey": "{secret:tdp_key}",
                    "base_url": "https://tdp.example",
                },
            },
        })

        actions = migrate_api_services()

        assert actions == {"tdp_api_v3_3_10": "copied"}
        data = json.loads(config_path.read_text(encoding="utf-8"))
        services = data["api_services"]
        # Both keys present after copy-only migration.
        assert services["tdp_api"]["base_url"] == "https://tdp.example"
        assert services["tdp_api_v3_3_10"]["base_url"] == "https://tdp.example"
        assert services["tdp_api_v3_3_10"]["apiKey"] == "{secret:tdp_key}"

    def test_idempotent_when_storage_key_exists(self, isolated_env, api_root):
        _, user_config = isolated_env
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_flocks_json(user_config, {
            "api_services": {
                "tdp_api":          {"base_url": "old"},
                "tdp_api_v3_3_10":  {"base_url": "new"},
            },
        })

        actions = migrate_api_services()

        assert actions == {"tdp_api_v3_3_10": "existed"}
        services = json.loads((user_config / "flocks.json").read_text())["api_services"]
        # Versioned key untouched even when legacy differs.
        assert services["tdp_api_v3_3_10"]["base_url"] == "new"
        assert services["tdp_api"]["base_url"] == "old"

    def test_no_legacy_no_op(self, isolated_env, api_root):
        _, user_config = isolated_env
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_flocks_json(user_config, {"api_services": {}})

        actions = migrate_api_services()
        assert actions == {"tdp_api_v3_3_10": "no-source"}

    def test_skips_unversioned_descriptors(self, isolated_env, api_root):
        _, user_config = isolated_env
        _write_provider_yaml(api_root / "qingteng", service_id="qingteng", version=None)
        _write_flocks_json(user_config, {
            "api_services": {"qingteng": {"enabled": True}},
        })

        # No versioned descriptors → nothing to migrate at all.
        assert migrate_api_services() == {}

    def test_creates_backup_only_when_writing(self, isolated_env, api_root):
        _, user_config = isolated_env
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_flocks_json(user_config, {
            "api_services": {"tdp_api": {"enabled": True}},
        })

        migrate_api_services()
        backups = list(user_config.glob("flocks.json.bak.*"))
        assert len(backups) == 1, f"expected one backup, got {backups}"

        # Second run is a no-op; no extra backup is created.
        migrate_api_services()
        backups_after = list(user_config.glob("flocks.json.bak.*"))
        assert len(backups_after) == 1

    def test_no_backup_when_disabled(self, isolated_env, api_root):
        _, user_config = isolated_env
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_flocks_json(user_config, {
            "api_services": {"tdp_api": {"enabled": True}},
        })

        migrate_api_services(backup=False)
        assert list(user_config.glob("flocks.json.bak.*")) == []

    def test_preserves_other_sections(self, isolated_env, api_root):
        _, user_config = isolated_env
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_flocks_json(user_config, {
            "provider": {"anthropic": {"npm": "@ai-sdk/anthropic"}},
            "api_services": {"tdp_api": {"enabled": True}},
            "mcp": {"foo": {"type": "local"}},
        })

        migrate_api_services()
        data = json.loads((user_config / "flocks.json").read_text())
        assert data["provider"]["anthropic"]["npm"] == "@ai-sdk/anthropic"
        assert data["mcp"]["foo"]["type"] == "local"

    def test_missing_config_file_is_safe(self, isolated_env, api_root):
        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        # No flocks.json on disk → silently no-op.
        assert migrate_api_services() == {}


# ---------------------------------------------------------------------------
# ConfigWriter fallback (integration with the new module)
# ---------------------------------------------------------------------------

class TestConfigWriterFallback:
    def test_direct_hit(self, isolated_env, api_root):
        _, user_config = isolated_env
        from flocks.config.config_writer import ConfigWriter

        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_flocks_json(user_config, {
            "api_services": {"tdp_api_v3_3_10": {"base_url": "fresh"}},
        })

        assert ConfigWriter.get_api_service_raw("tdp_api_v3_3_10") == {"base_url": "fresh"}

    def test_falls_back_to_legacy(self, isolated_env, api_root):
        _, user_config = isolated_env
        from flocks.config.config_writer import ConfigWriter

        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_flocks_json(user_config, {
            "api_services": {"tdp_api": {"base_url": "legacy"}},
        })
        versioning._reset_descriptor_cache()

        assert ConfigWriter.get_api_service_raw("tdp_api_v3_3_10") == {"base_url": "legacy"}

    def test_returns_none_when_neither_present(self, isolated_env, api_root):
        _, user_config = isolated_env
        from flocks.config.config_writer import ConfigWriter

        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_flocks_json(user_config, {"api_services": {}})

        assert ConfigWriter.get_api_service_raw("tdp_api_v3_3_10") is None

    def test_no_fallback_for_unversioned_lookup(self, isolated_env, api_root):
        _, user_config = isolated_env
        from flocks.config.config_writer import ConfigWriter

        _write_provider_yaml(api_root / "qingteng", service_id="qingteng", version=None)
        _write_flocks_json(user_config, {"api_services": {}})

        assert ConfigWriter.get_api_service_raw("qingteng") is None

    def test_legacy_lookup_prefers_versioned_shadow(self, isolated_env, api_root):
        """Handlers that hard-code an unversioned ``SERVICE_ID`` should
        transparently see post-migration credentials written under the
        versioned storage key, even when the legacy block still exists.
        """
        _, user_config = isolated_env
        from flocks.config.config_writer import ConfigWriter

        _write_provider_yaml(api_root / "qt_v3_4_1_66", service_id="qingteng", version="3.4.1.66")
        _write_flocks_json(user_config, {
            "api_services": {
                "qingteng":             {"base_url": "stale-legacy"},
                "qingteng_v3_4_1_66":   {"base_url": "fresh-versioned"},
            },
        })
        versioning._reset_descriptor_cache()

        assert ConfigWriter.get_api_service_raw("qingteng") == {"base_url": "fresh-versioned"}

    def test_legacy_lookup_returns_legacy_when_no_versioned_shadow_yet(
        self, isolated_env, api_root,
    ):
        _, user_config = isolated_env
        from flocks.config.config_writer import ConfigWriter

        _write_provider_yaml(api_root / "qt_v3_4_1_66", service_id="qingteng", version="3.4.1.66")
        _write_flocks_json(user_config, {
            "api_services": {"qingteng": {"base_url": "legacy-only"}},
        })
        versioning._reset_descriptor_cache()

        # Migration has not run yet; legacy block is still the only source.
        assert ConfigWriter.get_api_service_raw("qingteng") == {"base_url": "legacy-only"}

    def test_set_api_service_warns_when_writing_shadowed_legacy(
        self, isolated_env, api_root, capsys,
    ):
        """Writing to a legacy id while a versioned shadow exists should
        emit a warning so the read/write asymmetry is loud, not silent.
        """
        _, user_config = isolated_env
        from flocks.config.config_writer import ConfigWriter

        _write_provider_yaml(api_root / "qt_v3_4_1_66", service_id="qingteng", version="3.4.1.66")
        _write_flocks_json(user_config, {
            "api_services": {
                "qingteng":            {"base_url": "stale"},
                "qingteng_v3_4_1_66":  {"base_url": "fresh"},
            },
        })
        versioning._reset_descriptor_cache()

        ConfigWriter.set_api_service("qingteng", {"base_url": "definitely-stale"})

        # Flocks logs structured records to stderr; assert against that.
        captured = capsys.readouterr()
        assert "api_service.write.shadowed_legacy" in captured.err, (
            f"expected a shadow warning in stderr; got: {captured.err}"
        )

    def test_set_api_service_no_warning_for_storage_key(
        self, isolated_env, api_root, capsys,
    ):
        _, user_config = isolated_env
        from flocks.config.config_writer import ConfigWriter

        _write_provider_yaml(api_root / "qt_v3_4_1_66", service_id="qingteng", version="3.4.1.66")
        _write_flocks_json(user_config, {"api_services": {}})
        versioning._reset_descriptor_cache()

        ConfigWriter.set_api_service("qingteng_v3_4_1_66", {"base_url": "ok"})

        captured = capsys.readouterr()
        assert "api_service.write.shadowed_legacy" not in captured.err

    def test_get_api_service_raw_handles_null_api_services(self, isolated_env, api_root):
        """``"api_services": null`` in flocks.json must not crash the reader."""
        _, user_config = isolated_env
        from flocks.config.config_writer import ConfigWriter

        _write_provider_yaml(api_root / "tdp_v3_3_10", service_id="tdp_api", version="3.3.10")
        _write_flocks_json(user_config, {"api_services": None})
        versioning._reset_descriptor_cache()

        assert ConfigWriter.get_api_service_raw("tdp_api_v3_3_10") is None
        assert ConfigWriter.get_api_service_raw("anything") is None


# ---------------------------------------------------------------------------
# Regression: provider-route metadata loader must accept storage_keys
# ---------------------------------------------------------------------------

class TestProviderYamlMetadataResolution:
    """``_load_provider_yaml_metadata`` is called with whatever ``provider_id``
    the WebUI passes — which is now the storage_key after the tool loader
    promotion. The directory layout may use a SHORTENED name (e.g. plugin
    dir ``tdp_v3_3_10`` for service_id ``tdp_api``), so the resolver must
    derive the storage_key from each candidate ``_provider.yaml`` and not
    rely on directory naming.
    """

    def test_resolves_when_dir_name_does_not_match_storage_key(
        self, isolated_env, api_root,
    ):
        from flocks.server.routes.provider import _load_provider_yaml_metadata

        # Note: dir name ``tdp_v3_3_10`` ≠ storage_key ``tdp_api_v3_3_10``.
        plugin_dir = api_root / "tdp_v3_3_10"
        _write_provider_yaml(plugin_dir, service_id="tdp_api", version="3.3.10")

        metadata = _load_provider_yaml_metadata("tdp_api_v3_3_10")
        assert metadata is not None
        assert metadata["service_id"] == "tdp_api"
        assert metadata["version"] == "3.3.10"

    def test_resolves_legacy_service_id_lookup(self, isolated_env, api_root):
        from flocks.server.routes.provider import _load_provider_yaml_metadata

        plugin_dir = api_root / "tdp_v3_3_10"
        _write_provider_yaml(plugin_dir, service_id="tdp_api", version="3.3.10")

        # Legacy callers still passing the bare service_id keep working.
        metadata = _load_provider_yaml_metadata("tdp_api")
        assert metadata is not None
        assert metadata["service_id"] == "tdp_api"

    def test_returns_none_for_unknown_id(self, isolated_env, api_root):
        from flocks.server.routes.provider import _load_provider_yaml_metadata

        _write_provider_yaml(api_root / "tdp_v3_3_10",
                             service_id="tdp_api", version="3.3.10")

        assert _load_provider_yaml_metadata("ghost_v9_9_9") is None
