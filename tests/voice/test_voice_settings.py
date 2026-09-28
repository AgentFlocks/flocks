from __future__ import annotations

import concurrent.futures
import json
import os

import pytest

from flocks.voice import settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "_legacy_path", lambda: tmp_path / "legacy" / "voice-config.json")
    monkeypatch.setattr(settings, "_legacy_key", lambda: os.getenv("TEST_VOICE_LEGACY_KEY", ""))
    monkeypatch.delenv("TEST_VOICE_LEGACY_KEY", raising=False)
    monkeypatch.delenv(settings.KEY_ENV, raising=False)


def test_explicit_enable_and_removal():
    assert settings.read_settings().public() == {
        "enabled": False, "configured": False, "available": False, "capture_mode": "voice",
    }
    saved = settings.update_settings({"api_key": "test-credential"})
    assert not saved.enabled
    assert saved.public()["configured"]
    enabled = settings.update_settings({"enabled": True})
    assert enabled.public()["available"]
    assert "test-credential" not in repr(enabled)
    assert "test-credential" not in json.dumps(enabled.public(admin=True))
    cleared = settings.update_settings({"api_key": "  ", "enabled": True})
    assert not cleared.enabled and not cleared.api_key
    assert settings.settings_path().stat().st_mode & 0o777 == 0o600


def test_env_requires_manual_enable_and_overrides_stored(monkeypatch):
    monkeypatch.setenv(settings.KEY_ENV, "environment-test")
    assert not settings.read_settings().enabled
    settings.update_settings({"api_key": "stored-test"})
    enabled = settings.update_settings({"enabled": True})
    assert enabled.api_key == "environment-test"
    assert enabled.credential_source == "environment"
    assert "environment-test" not in settings.settings_path().read_text()
    assert not settings.update_settings({"api_key": ""}).enabled
    assert settings.read_settings().api_key == "environment-test"


@pytest.mark.parametrize("changes", [
    {"enabled": True}, {"enabled": "true"}, {"enabled": None},
    {"api_key": None}, {"api_key": "bad\nheader"}, {"api_key": "x" * 4097},
])
def test_invalid_configuration_does_not_write(changes):
    with pytest.raises(ValueError):
        settings.update_settings(changes)
    assert not settings.settings_path().exists()


def test_atomic_shared_updates_and_fresh_reads():
    settings.update_settings({"api_key": "test"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: settings.update_settings({"enabled": True}), range(16)))
    assert all(item.enabled for item in results)
    assert settings.read_settings().enabled
    settings.update_settings({"enabled": False})
    assert not settings.read_settings().enabled
    assert set(json.loads(settings.settings_path().read_text())) == {
        "enabled", "api_key", "hotwords", "capture_mode", "recognition_scene",
    }


@pytest.mark.parametrize("contents", ["not-json", "[]", '{"enabled":"true","api_key":"test"}', "x" * (settings.MAX_CONFIG_BYTES + 1)])
def test_corrupt_configuration_fails_closed(contents):
    settings.settings_path().write_text(contents)
    assert not settings.read_settings().enabled


def test_legacy_settings_migrate_without_modifying_original_or_resurrecting_key():
    path = settings._legacy_path()
    path.parent.mkdir(parents=True)
    original = '{"enabled":true,"api_key":"legacy-test-key"}'
    path.write_text(original)
    assert settings.read_settings().enabled
    assert settings.read_settings().api_key == "legacy-test-key"
    assert not settings.settings_path().exists()  # reading has no migration side effect
    saved = settings.update_settings({"hotwords": ["Flocks", " 海淀 ", "Flocks"], "capture_mode": "raw"})
    assert saved.hotwords == ("Flocks", "海淀") and saved.capture_mode == "raw"
    assert saved.api_key == "legacy-test-key" and saved.enabled
    assert path.read_text() == original
    assert settings.settings_path().stat().st_mode & 0o777 == 0o600
    settings.update_settings({"api_key": ""})
    assert not settings.read_settings().api_key and not settings.read_settings().enabled
    assert path.read_text() == original


def test_core_and_environment_precedence_never_falls_back_to_removed_legacy_key(monkeypatch):
    legacy = settings._legacy_path()
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"enabled":true,"api_key":"legacy-test-key"}')
    monkeypatch.setenv("TEST_VOICE_LEGACY_KEY", "legacy-env-test")
    assert settings.read_settings().api_key == "legacy-env-test"
    monkeypatch.setenv(settings.KEY_ENV, "core-env-test")
    assert settings.read_settings().api_key == "core-env-test"
    monkeypatch.delenv("TEST_VOICE_LEGACY_KEY")
    monkeypatch.delenv(settings.KEY_ENV)
    settings.settings_path().write_text('{}')
    assert not settings.read_settings().api_key
    settings.settings_path().write_text('invalid-json')
    assert not settings.read_settings().enabled


@pytest.mark.parametrize("changes", [
    {"hotwords": None}, {"hotwords": "word"}, {"hotwords": ["a"] * 101},
    {"hotwords": ["a" * 65]}, {"hotwords": [""]}, {"hotwords": ["a\nb"]},
    {"capture_mode": None}, {"capture_mode": "magic"}, {"unknown": True},
    {"recognition_scene": None}, {"recognition_scene": "magic"},
    {"recognition_scene": []}, {"recognition_scene": {}},
])
def test_invalid_recognition_options_leave_existing_settings_intact(changes):
    settings.update_settings({"api_key": "stored-test", "enabled": True, "hotwords": ["Flocks"]})
    original = settings.settings_path().read_bytes()
    with pytest.raises(ValueError):
        settings.update_settings(changes)
    assert settings.settings_path().read_bytes() == original


@pytest.mark.parametrize("stored_scene", [None, "unknown", [], {}, 1])
def test_legacy_or_invalid_scene_keeps_general_behavior(stored_scene):
    settings.settings_path().write_text(json.dumps({
        "api_key": "test", "enabled": True, "recognition_scene": stored_scene,
    }))
    current = settings.read_settings()
    assert current.enabled and current.recognition_scene == "general"
    assert current.recognition_context == ""


def test_scene_persists_without_replacing_user_hotwords_or_credentials():
    saved = settings.update_settings({
        "api_key": "stored-test", "enabled": True, "hotwords": ["用户自定义产品"],
        "capture_mode": "raw", "recognition_scene": "security_internet",
    })
    assert saved.recognition_context == settings.RECOGNITION_CONTEXTS["security_internet"]
    assert all(word in saved.recognition_context for word in ("Flocks", "NDR", "EDR"))
    updated = settings.update_settings({"enabled": False})
    assert updated.recognition_scene == "security_internet"
    assert updated.hotwords == ("用户自定义产品",) and updated.capture_mode == "raw"
    assert updated.api_key == "stored-test"
    assert updated.public(admin=True)["recognition_scene"] == "security_internet"
    assert "recognition_scene" not in updated.public()
    assert "stored-test" not in json.dumps(updated.public(admin=True))
    general = settings.update_settings({"recognition_scene": "general"})
    assert general.recognition_context == "" and general.hotwords == updated.hotwords
