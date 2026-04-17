"""
Tests for ConfigWriter — flocks.json provider CRUD.

Covers:
- ConfigWriter.add_provider / get_provider_raw / remove_provider
- ConfigWriter.update_provider_field
- ConfigWriter.add_model / remove_model
- ConfigWriter.build_provider_config
- ConfigWriter.list_provider_ids
- Atomic write safety
"""

import json

import pytest
from flocks.config.config import Config


@pytest.fixture
def temp_project(tmp_path, monkeypatch):
    """Create an isolated user config directory with a preloaded flocks.json."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    Config._global_config = None
    Config._cached_config = None
    config_file = config_dir / "flocks.json"
    config_file.write_text(json.dumps({
        "provider": {
            "anthropic": {
                "npm": "@ai-sdk/anthropic",
                "options": {
                    "apiKey": "{secret:anthropic_llm_key}",
                    "baseURL": "https://api.anthropic.com"
                },
                "models": {
                    "claude-sonnet-4-5": {"name": "Claude Sonnet 4.5"}
                }
            }
        },
        "mcp": {"test": {"type": "local"}},
    }, indent=2))
    return config_dir


class TestConfigWriter:

    def test_read_raw(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        data = ConfigWriter._read_raw()
        assert "provider" in data
        assert "anthropic" in data["provider"]
        assert "mcp" in data  # Other sections preserved

    def test_list_provider_ids(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ids = ConfigWriter.list_provider_ids()
        assert ids == ["anthropic"]

    def test_get_provider_raw(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        raw = ConfigWriter.get_provider_raw("anthropic")
        assert raw is not None
        assert raw["npm"] == "@ai-sdk/anthropic"
        assert raw["options"]["baseURL"] == "https://api.anthropic.com"

    def test_get_provider_raw_nonexistent(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.get_provider_raw("nonexistent") is None

    def test_add_provider(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        ConfigWriter.add_provider("openai", {
            "npm": "@ai-sdk/openai",
            "options": {"apiKey": "{secret:openai_llm_key}", "baseURL": "https://api.openai.com/v1"},
            "models": {"gpt-4": {"name": "GPT-4"}},
        })

        ids = ConfigWriter.list_provider_ids()
        assert "openai" in ids
        assert "anthropic" in ids  # Still there

        raw = ConfigWriter.get_provider_raw("openai")
        assert raw["npm"] == "@ai-sdk/openai"
        assert raw["models"]["gpt-4"]["name"] == "GPT-4"

    def test_add_provider_preserves_other_sections(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        ConfigWriter.add_provider("google", {
            "npm": "@ai-sdk/google",
            "options": {},
            "models": {},
        })

        data = ConfigWriter._read_raw()
        assert "mcp" in data
        assert data["mcp"]["test"]["type"] == "local"

    def test_remove_provider(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        result = ConfigWriter.remove_provider("anthropic")
        assert result is True
        assert ConfigWriter.get_provider_raw("anthropic") is None
        assert ConfigWriter.list_provider_ids() == []

    def test_remove_provider_nonexistent(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.remove_provider("nonexistent") is False

    def test_remove_provider_preserves_other_sections(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        ConfigWriter.remove_provider("anthropic")
        data = ConfigWriter._read_raw()
        assert "mcp" in data

    def test_update_provider_field(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        result = ConfigWriter.update_provider_field(
            "anthropic", "options.baseURL", "https://new-url.com"
        )
        assert result is True

        raw = ConfigWriter.get_provider_raw("anthropic")
        assert raw["options"]["baseURL"] == "https://new-url.com"
        # apiKey should be preserved
        assert raw["options"]["apiKey"] == "{secret:anthropic_llm_key}"

    def test_update_provider_field_nested_create(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        # Create a new nested path
        result = ConfigWriter.update_provider_field(
            "anthropic", "extra.nested.key", "value"
        )
        assert result is True

        raw = ConfigWriter.get_provider_raw("anthropic")
        assert raw["extra"]["nested"]["key"] == "value"

    def test_update_provider_field_nonexistent_provider(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        result = ConfigWriter.update_provider_field(
            "nonexistent", "options.baseURL", "test"
        )
        assert result is False

    def test_add_model(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        result = ConfigWriter.add_model(
            "anthropic", "claude-opus-4", {"name": "Claude Opus 4"}
        )
        assert result is True

        raw = ConfigWriter.get_provider_raw("anthropic")
        assert "claude-opus-4" in raw["models"]
        assert raw["models"]["claude-opus-4"]["name"] == "Claude Opus 4"
        # Original model still there
        assert "claude-sonnet-4-5" in raw["models"]

    def test_add_model_nonexistent_provider(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.add_model("nonexistent", "model", {}) is False

    def test_remove_model(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        result = ConfigWriter.remove_model("anthropic", "claude-sonnet-4-5")
        assert result is True

        raw = ConfigWriter.get_provider_raw("anthropic")
        assert "claude-sonnet-4-5" not in raw["models"]

    def test_remove_model_nonexistent(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.remove_model("anthropic", "nonexistent") is False

    def test_build_provider_config(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        config = ConfigWriter.build_provider_config(
            "deepseek",
            npm="@ai-sdk/openai-compatible",
            base_url="https://api.deepseek.com/v1",
            models={"deepseek-chat": {"name": "DeepSeek Chat"}},
        )

        assert config["npm"] == "@ai-sdk/openai-compatible"
        assert config["options"]["apiKey"] == "{secret:deepseek_llm_key}"
        assert config["options"]["baseURL"] == "https://api.deepseek.com/v1"
        assert config["models"]["deepseek-chat"]["name"] == "DeepSeek Chat"

    def test_build_provider_config_no_url(self, temp_project):
        from flocks.config.config_writer import ConfigWriter

        config = ConfigWriter.build_provider_config("test")
        assert "baseURL" not in config["options"]
        assert config["options"]["apiKey"] == "{secret:test_llm_key}"

    def test_atomic_write_no_corruption(self, temp_project):
        """Verify the file is valid JSON after multiple operations."""
        from flocks.config.config_writer import ConfigWriter

        for i in range(10):
            ConfigWriter.add_provider(f"provider-{i}", {
                "npm": "@ai-sdk/openai-compatible",
                "options": {},
                "models": {},
            })

        # Read raw and verify
        data = ConfigWriter._read_raw()
        assert len(data["provider"]) == 11  # 10 new + 1 original

        for i in range(5):
            ConfigWriter.remove_provider(f"provider-{i}")

        data = ConfigWriter._read_raw()
        assert len(data["provider"]) == 6

    def test_empty_config_file(self, tmp_path, monkeypatch):
        """ConfigWriter handles missing or empty config gracefully."""
        config_dir = tmp_path / "home" / ".flocks" / "config"
        config_dir.mkdir(parents=True)
        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
        Config._global_config = None
        Config._cached_config = None
        (config_dir / "flocks.json").write_text("{}")

        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.list_provider_ids() == []

        ConfigWriter.add_provider("test", {"npm": "x", "options": {}, "models": {}})
        assert ConfigWriter.list_provider_ids() == ["test"]

    def test_no_config_file(self, tmp_path, monkeypatch):
        """ConfigWriter creates flocks.json if it doesn't exist."""
        config_dir = tmp_path / "home" / ".flocks" / "config"
        config_dir.mkdir(parents=True)
        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
        Config._global_config = None
        Config._cached_config = None

        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.add_provider("new", {"npm": "x", "options": {}, "models": {}})

        config_file = config_dir / "flocks.json"
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert "new" in data["provider"]


class TestConfigWriterModelSettings:
    """Test model_settings section CRUD."""

    def test_get_model_setting_empty(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.get_model_setting("openai", "gpt-4o") is None

    def test_set_and_get_model_setting(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_model_setting("openai", "gpt-4o", {
            "enabled": False,
            "default_parameters": {"temperature": 0.5},
        })

        setting = ConfigWriter.get_model_setting("openai", "gpt-4o")
        assert setting is not None
        assert setting["enabled"] is False
        assert setting["default_parameters"]["temperature"] == 0.5

    def test_update_model_setting_merges(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_model_setting("openai", "gpt-4o", {
            "enabled": True,
            "default_parameters": {"temperature": 0.5},
        })
        ConfigWriter.set_model_setting("openai", "gpt-4o", {
            "enabled": False,
        })

        setting = ConfigWriter.get_model_setting("openai", "gpt-4o")
        assert setting["enabled"] is False
        # Previous value preserved via merge
        assert setting["default_parameters"]["temperature"] == 0.5

    def test_remove_model_setting(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_model_setting("openai", "gpt-4o", {"enabled": True})
        assert ConfigWriter.remove_model_setting("openai", "gpt-4o") is True
        assert ConfigWriter.get_model_setting("openai", "gpt-4o") is None

    def test_remove_nonexistent_model_setting(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.remove_model_setting("openai", "gpt-4o") is False

    def test_get_all_model_settings(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_model_setting("openai", "gpt-4o", {"enabled": True})
        ConfigWriter.set_model_setting("anthropic", "claude-sonnet", {"enabled": False})

        all_settings = ConfigWriter.get_all_model_settings()
        assert "openai/gpt-4o" in all_settings
        assert "anthropic/claude-sonnet" in all_settings
        assert len(all_settings) == 2

    def test_model_settings_preserve_other_sections(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_model_setting("openai", "gpt-4o", {"enabled": True})

        data = ConfigWriter._read_raw()
        assert "provider" in data
        assert "anthropic" in data["provider"]
        assert "mcp" in data


class TestConfigWriterToolSettings:
    """Test tool_settings section CRUD (user-level overlay for plugin tools)."""

    def test_list_empty(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.list_tool_settings() == {}

    def test_get_missing_returns_none(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.get_tool_setting("onesec_threat") is None

    def test_set_and_get(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_tool_setting("onesec_threat", {"enabled": False})
        entry = ConfigWriter.get_tool_setting("onesec_threat")
        assert entry == {"enabled": False}
        assert ConfigWriter.list_tool_settings() == {"onesec_threat": {"enabled": False}}

    def test_set_merges_existing_keys(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_tool_setting("onesec_threat", {"enabled": False, "note": "x"})
        ConfigWriter.set_tool_setting("onesec_threat", {"enabled": True})
        entry = ConfigWriter.get_tool_setting("onesec_threat")
        assert entry == {"enabled": True, "note": "x"}

    def test_delete_existing(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_tool_setting("onesec_threat", {"enabled": False})
        assert ConfigWriter.delete_tool_setting("onesec_threat") is True
        assert ConfigWriter.get_tool_setting("onesec_threat") is None
        assert ConfigWriter.list_tool_settings() == {}

    def test_delete_last_entry_pops_section(self, temp_project):
        """Removing the last entry should drop ``tool_settings`` entirely."""
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_tool_setting("a", {"enabled": False})
        ConfigWriter.set_tool_setting("b", {"enabled": True})

        ConfigWriter.delete_tool_setting("a")
        assert "tool_settings" in ConfigWriter._read_raw()

        ConfigWriter.delete_tool_setting("b")
        assert "tool_settings" not in ConfigWriter._read_raw()

    def test_delete_missing_returns_false(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.delete_tool_setting("not_set") is False

    def test_set_empty_name_raises(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        with pytest.raises(ValueError):
            ConfigWriter.set_tool_setting("", {"enabled": True})

    def test_preserves_other_sections(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_tool_setting("onesec_threat", {"enabled": False})
        data = ConfigWriter._read_raw()
        assert "provider" in data
        assert "anthropic" in data["provider"]
        assert data["tool_settings"]["onesec_threat"]["enabled"] is False

    def test_corrupt_settings_section_treated_as_empty(self, temp_project):
        """If tool_settings is somehow not a dict, the API should not crash."""
        import json as _json
        from flocks.config.config import Config
        cfg_path = Config.get_config_file()
        data = _json.loads(cfg_path.read_text())
        data["tool_settings"] = "garbage"
        cfg_path.write_text(_json.dumps(data))

        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.list_tool_settings() == {}
        assert ConfigWriter.get_tool_setting("anything") is None


class TestConfigWriterDefaultModels:
    """Test default_models section CRUD."""

    def test_get_default_model_empty(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.get_default_model("llm") is None

    def test_set_and_get_default_model(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_default_model("llm", "anthropic", "claude-sonnet-4-20250514")

        default = ConfigWriter.get_default_model("llm")
        assert default is not None
        assert default["provider_id"] == "anthropic"
        assert default["model_id"] == "claude-sonnet-4-20250514"

    def test_overwrite_default_model(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_default_model("llm", "anthropic", "claude-sonnet")
        ConfigWriter.set_default_model("llm", "openai", "gpt-4o")

        default = ConfigWriter.get_default_model("llm")
        assert default["provider_id"] == "openai"
        assert default["model_id"] == "gpt-4o"

    def test_delete_default_model(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_default_model("llm", "openai", "gpt-4o")
        assert ConfigWriter.delete_default_model("llm") is True
        assert ConfigWriter.get_default_model("llm") is None

    def test_delete_nonexistent_default_model(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        assert ConfigWriter.delete_default_model("llm") is False

    def test_get_all_default_models(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_default_model("llm", "anthropic", "claude-sonnet")
        ConfigWriter.set_default_model("text-embedding", "openai", "text-embedding-3-small")

        all_defaults = ConfigWriter.get_all_default_models()
        assert "llm" in all_defaults
        assert "text-embedding" in all_defaults
        assert len(all_defaults) == 2

    def test_default_models_preserve_other_sections(self, temp_project):
        from flocks.config.config_writer import ConfigWriter
        ConfigWriter.set_default_model("llm", "anthropic", "claude")

        data = ConfigWriter._read_raw()
        assert "provider" in data
        assert "mcp" in data
