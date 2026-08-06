"""
Tests for config file initialization from examples.
"""

import json

import pytest


def test_ensure_config_files_creates_from_examples(tmp_path, monkeypatch):
    """Test that ensure_config_files creates config files from examples."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    example_dir = tmp_path / "examples"
    example_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))

    # Create example files
    config_example = example_dir / "flocks.json.example"
    config_example.write_text('{"test": "config"}')

    mcp_example = example_dir / "mcp_list.json.example"
    mcp_example.write_text('{"test": "mcp"}')
    
    secret_example = example_dir / ".secret.json.example"
    secret_example.write_text('{"test": "secret"}')
    
    # Files should not exist yet
    config_file = config_dir / "flocks.json"
    mcp_file = config_dir / "mcp_list.json"
    secret_file = config_dir / ".secret.json"
    assert not config_file.exists()
    assert not mcp_file.exists()
    assert not secret_file.exists()
    
    # Run initialization
    from flocks.config.config import Config
    from flocks.config import config_writer
    Config._global_config = None
    Config._cached_config = None
    monkeypatch.setattr(config_writer, "_get_example_config_dir", lambda: example_dir)
    ensure_config_files = config_writer.ensure_config_files
    ensure_config_files()
    
    # Files should now exist
    assert config_file.exists()
    assert mcp_file.exists()
    assert secret_file.exists()
    
    # Existing example content is preserved and Memory defaults are persisted.
    config_data = json.loads(config_file.read_text(encoding="utf-8"))
    assert config_data["test"] == "config"
    assert set(config_data["memory"]) == {"dream", "search"}
    embedding_config = config_data["memory"]["search"]["embedding"]
    assert set(embedding_config) == {"enabled", "model", "provider"}
    assert embedding_config["provider"] == "auto"
    assert embedding_config["enabled"] is False
    assert config_data["memory"]["dream"]["enabled"] is True
    assert set(config_data["memory"]["dream"]) == {
        "enabled",
        "interval_hours",
        "recent_daily_days",
    }
    assert mcp_file.read_text(encoding="utf-8") == '{"test": "mcp"}'
    assert secret_file.read_text(encoding="utf-8") == '{"test": "secret"}'


def test_ensure_config_files_skips_if_exists(tmp_path, monkeypatch):
    """Test that ensure_config_files doesn't overwrite existing files."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    example_dir = tmp_path / "examples"
    config_dir.mkdir(parents=True)
    example_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))

    # Create example files
    config_example = example_dir / "flocks.json.example"
    config_example.write_text('{"test": "example"}')

    mcp_example = example_dir / "mcp_list.json.example"
    mcp_example.write_text('{"test": "mcp-example"}')
    
    # Create existing config file
    config_file = config_dir / "flocks.json"
    config_file.write_text('{"test": "existing"}')

    mcp_file = config_dir / "mcp_list.json"
    mcp_file.write_text('{"test": "mcp-existing"}')
    
    # Run initialization
    from flocks.config.config import Config
    from flocks.config import config_writer
    Config._global_config = None
    Config._cached_config = None
    monkeypatch.setattr(config_writer, "_get_example_config_dir", lambda: example_dir)
    ensure_config_files = config_writer.ensure_config_files
    ensure_config_files()
    
    # Existing fields are preserved while the missing Memory config is added.
    config_data = json.loads(config_file.read_text(encoding="utf-8"))
    assert config_data["test"] == "existing"
    assert set(config_data["memory"]) == {"dream", "search"}
    assert mcp_file.read_text() == '{"test": "mcp-existing"}'


def test_ensure_config_files_preserves_explicitly_disabled_dream(
    tmp_path,
    monkeypatch,
):
    """An existing Memory setting remains user-controlled."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    example_dir = tmp_path / "examples"
    config_dir.mkdir(parents=True)
    example_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))

    config_file = config_dir / "flocks.json"
    config_file.write_text(
        '{"test": "existing", "memory": {"dream": {"enabled": false}}}',
        encoding="utf-8",
    )

    from flocks.config.config import Config
    from flocks.config import config_writer

    Config._global_config = None
    Config._cached_config = None
    monkeypatch.setattr(config_writer, "_get_example_config_dir", lambda: example_dir)
    config_writer.ensure_config_files()

    config_data = json.loads(config_file.read_text(encoding="utf-8"))
    assert config_data == {
        "test": "existing",
        "memory": {"dream": {"enabled": False}},
    }


def test_ensure_memory_config_is_written_to_flocks_json(
    tmp_path,
    monkeypatch,
):
    """The generated Memory section belongs to the primary flocks.json."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    flocks_json = config_dir / "flocks.json"
    flocks_json.write_text("{}", encoding="utf-8")
    flocks_jsonc = config_dir / "flocks.jsonc"
    flocks_jsonc.write_text('{"test": "jsonc"}', encoding="utf-8")

    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    Config._global_config = None
    Config._cached_config = None
    assert ConfigWriter.ensure_memory_config() is True
    memory_config = json.loads(flocks_json.read_text(encoding="utf-8"))["memory"]
    assert set(memory_config) == {"dream", "search"}
    assert flocks_jsonc.read_text(encoding="utf-8") == '{"test": "jsonc"}'


def test_ensure_config_files_handles_missing_examples(tmp_path, monkeypatch):
    """Test that ensure_config_files handles missing example files gracefully."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    example_dir = tmp_path / "examples"
    example_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    
    # Run initialization (should not raise error)
    from flocks.config.config import Config
    from flocks.config import config_writer
    Config._global_config = None
    Config._cached_config = None
    monkeypatch.setattr(config_writer, "_get_example_config_dir", lambda: example_dir)
    ensure_config_files = config_writer.ensure_config_files
    ensure_config_files()
    
    # Files should be initialized from fallback templates.
    config_file = config_dir / "flocks.json"
    mcp_file = config_dir / "mcp_list.json"
    secret_file = config_dir / ".secret.json"
    assert config_file.exists()
    assert mcp_file.exists()
    assert secret_file.exists()


def test_ensure_config_files_creates_flocks_dir(tmp_path, monkeypatch):
    """Test that ensure_config_files creates ~/.flocks/config if needed."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    example_dir = tmp_path / "examples"
    example_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    assert not config_dir.exists()
    
    # Run initialization
    from flocks.config.config import Config
    from flocks.config import config_writer
    Config._global_config = None
    Config._cached_config = None
    monkeypatch.setattr(config_writer, "_get_example_config_dir", lambda: example_dir)
    ensure_config_files = config_writer.ensure_config_files
    ensure_config_files()
    
    assert config_dir.exists()
    assert config_dir.is_dir()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
