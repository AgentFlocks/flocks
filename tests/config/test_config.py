"""
Tests for configuration module
"""

import pytest
import json
from pathlib import Path

from flocks.config.config import Config, GlobalConfig, ConfigInfo


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch):
    """Isolate config state under a temporary ~/.flocks/config directory."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("FLOCKS_ROOT", raising=False)
    monkeypatch.delenv("FLOCKS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    Config._global_config = None
    Config._cached_config = None
    yield home_dir / ".flocks" / "config"
    Config._global_config = None
    Config._cached_config = None


def test_global_config():
    """Test global configuration"""
    config = Config.get_global()
    
    assert isinstance(config, GlobalConfig)
    assert config.config_dir.exists()
    assert config.server_port == 8000


@pytest.mark.asyncio
async def test_config_loading():
    """Test configuration loading"""
    # Get merged config (global + project + env)
    config = await Config.get()
    
    assert isinstance(config, ConfigInfo)
    # Should have defaults
    assert config.agent is not None
    assert config.plugin is not None
    assert config.keybinds is not None


@pytest.mark.asyncio
async def test_config_file_loading(tmp_path):
    """Test loading configuration from file"""
    config_file = tmp_path / "test_config.json"
    config_data = {
        "$schema": "https://opencode.ai/config.json",
        "model": "gpt-4",
        "theme": "dark",
        "agent": {
            "test": {
                "name": "Test Agent",
                "temperature": 0.8
            }
        }
    }
    config_file.write_text(json.dumps(config_data, indent=2))
    
    # Load
    config = await Config.load_file(config_file)
    
    assert config.model == "gpt-4"
    assert config.theme == "dark"
    assert "test" in config.agent
    assert config.agent["test"].temperature == 0.8


@pytest.mark.asyncio
async def test_config_update(tmp_path, monkeypatch):
    """Test configuration update"""
    # Create a config
    config = ConfigInfo.model_validate({
        "model": "gpt-4",
        "theme": "dark",
    })
    
    # Update
    await Config.update(config)
    
    # Verify file was created in the user config directory
    config_file = Config.get_config_file()
    assert config_file.exists()
    
    # Load and verify
    loaded = await Config.load_file(config_file)
    assert loaded.model == "gpt-4"
    assert loaded.theme == "dark"


@pytest.mark.asyncio
async def test_config_merge(tmp_path, monkeypatch):
    """Test configuration merging"""
    # Create initial config
    config1 = ConfigInfo.model_validate({
        "model": "gpt-3.5",
        "theme": "light",
    })
    await Config.update(config1)
    
    # Update with new config (should merge)
    config2 = ConfigInfo.model_validate({
        "model": "gpt-4",  # Override
        "username": "testuser",  # Add
    })
    await Config.update(config2)
    
    # Load and verify from the user config directory
    config_file = Config.get_config_file()
    loaded = await Config.load_file(config_file)
    
    assert loaded.model == "gpt-4"  # Overridden
    assert loaded.theme == "light"  # Preserved
    assert loaded.username == "testuser"  # Added


@pytest.mark.asyncio
async def test_xdg_config_dir(tmp_path, monkeypatch):
    """The config dir should stay under ~/.flocks/config."""
    xdg_config = tmp_path / "config"
    xdg_config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    
    # Clear cached config
    Config._global_config = None
    Config._cached_config = None
    
    # Get global config - XDG should not override the unified ~/.flocks/config path.
    global_cfg = Config.get_global()
    expected_dir = Path(tmp_path / "home" / ".flocks" / "config")
    assert global_cfg.config_dir == expected_dir

    # Create config file in the unified config directory
    config_dir = expected_dir
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "flocks.json"
    config_data = {
        "model": "gpt-4-xdg",
        "theme": "xdg-theme"
    }
    config_file.write_text(json.dumps(config_data, indent=2))
    
    # Load global config
    loaded = await Config.load_global_config()
    assert loaded.model == "gpt-4-xdg"
    assert loaded.theme == "xdg-theme"


@pytest.mark.asyncio
async def test_backward_compatibility_legacy_dir(tmp_path, monkeypatch):
    """Default config location should be ~/.flocks/config."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "flocks.json"
    config_data = {
        "model": "home-model",
        "theme": "home-theme"
    }
    config_file.write_text(json.dumps(config_data, indent=2))
    
    # Clear cached config
    Config._global_config = None
    Config._cached_config = None
    
    global_cfg = Config.get_global()
    assert global_cfg.config_dir == config_dir

    loaded = await Config.load_global_config()
    assert loaded.model == "home-model"
    assert loaded.theme == "home-theme"


@pytest.mark.asyncio
async def test_project_config_is_discovered_from_nested_cwd(tmp_path, monkeypatch):
    """Config.get should ignore project .flocks and only use the user config."""
    project_root = tmp_path / "project"
    config_dir = project_root / ".flocks"
    nested_dir = project_root / "nested" / "deeper"
    config_dir.mkdir(parents=True)
    nested_dir.mkdir(parents=True)

    config_file = config_dir / "flocks.json"
    config_file.write_text(
        json.dumps({"model": "nested-provider/nested-model", "theme": "nested-theme"}, indent=2)
    )

    user_config_dir = tmp_path / "home" / ".flocks" / "config"
    user_config_dir.mkdir(parents=True)
    (user_config_dir / "flocks.json").write_text(
        json.dumps({"model": "home-provider/home-model", "theme": "home-theme"}, indent=2)
    )
    monkeypatch.chdir(nested_dir)

    Config._global_config = None
    Config._cached_config = None

    loaded = await Config.get()

    assert loaded.model == "home-provider/home-model"
    assert loaded.theme == "home-theme"


def test_deprecated_workflow_runtime_is_ignored():
    """Deprecated workflow.runtime should not raise validation errors."""
    config = ConfigInfo.model_validate(
        {
            "workflow": {
                "runtime": {
                    "default": "sandbox",
                }
            }
        }
    )
    assert isinstance(config, ConfigInfo)


def test_sandbox_mode_config_parsing():
    """ConfigInfo should parse sandbox mode as unified runtime switch."""
    config = ConfigInfo.model_validate({"sandbox": {"mode": "on"}})
    assert config.sandbox is not None
    assert config.sandbox.get("mode") == "on"


@pytest.mark.asyncio
async def test_load_text_handles_secret_with_backslash(tmp_path, monkeypatch):
    """Secret values containing ``\\`` must be JSON-escaped on substitution.

    Regression test for the "Invalid \\escape" failure observed on Anolis OS
    after configuring an API service whose key contained a backslash.
    The substituted value used to land verbatim inside the JSON string,
    producing an unparsable file (``json.JSONDecodeError: Invalid \\escape``).
    """
    secret_value = r"abc\def\u1234\"qq"  # backslashes + quote + \u sequence

    monkeypatch.setattr(
        "flocks.security.resolve_secret_value",
        lambda secret_id, secrets=None: secret_value if secret_id == "broken_key" else None,
    )
    monkeypatch.setattr(
        "flocks.security.get_secret_manager",
        lambda: object(),
    )

    config_file = tmp_path / "flocks.json"
    config_file.write_text(
        json.dumps({"provider": {"x": {"options": {"apiKey": "{secret:broken_key}"}}}}, indent=2),
        encoding="utf-8",
    )

    loaded = await Config.load_file(config_file)

    provider = (loaded.provider or {}).get("x")
    assert provider is not None
    assert provider.options is not None
    assert provider.options.api_key == secret_value


@pytest.mark.asyncio
async def test_load_text_handles_env_with_backslash(tmp_path, monkeypatch):
    """Environment values containing ``\\`` must also be JSON-escaped."""
    monkeypatch.setenv("FLOCKS_TEST_BACKSLASH", r"C:\Users\me\token\nVAL")

    config_file = tmp_path / "flocks.json"
    config_file.write_text(
        json.dumps({"provider": {"x": {"options": {"apiKey": "{env:FLOCKS_TEST_BACKSLASH}"}}}}, indent=2),
        encoding="utf-8",
    )

    loaded = await Config.load_file(config_file)

    provider = (loaded.provider or {}).get("x")
    assert provider is not None
    assert provider.options is not None
    assert provider.options.api_key == r"C:\Users\me\token\nVAL"
