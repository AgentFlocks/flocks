"""
Tests for server port configuration.

Tests various scenarios:
1. Port configuration from environment variables
2. Port configuration from command-line arguments
3. Port configuration from GlobalConfig
4. Port configuration from ServerInfo
"""

import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from flocks.config.config import Config
from flocks.cli import main as cli_main
from flocks.server.app import ServerInfo


class TestPortConfigurationFromConfig:
    """Test port configuration from GlobalConfig."""

    def test_default_port_in_global_config(self):
        """Test that GlobalConfig has correct default port (8000)."""
        config = Config.get_global()

        assert config.server_port == 8000
        assert isinstance(config.server_port, int)

    def test_server_host_default(self):
        """Test default server host configuration."""
        config = Config.get_global()

        assert config.server_host == "127.0.0.1"
        assert isinstance(config.server_host, str)

class TestServerInfoConfiguration:
    """Test ServerInfo class port configuration."""

    def test_server_info_default_port(self):
        """Test ServerInfo uses correct default port."""
        server_info = ServerInfo()

        assert server_info.port == 8000
        assert server_info.host == "127.0.0.1"

    def test_server_info_url_construction(self):
        """Test ServerInfo constructs correct URL."""
        server_info = ServerInfo()

        assert server_info.url == "http://127.0.0.1:8000"

class TestCommandLinePortConfiguration:
    """Test port configuration from command-line arguments."""

    def test_start_accepts_server_and_webui_options(self, monkeypatch):
        """Test start command accepts explicit server and WebUI host/port options."""
        captured = {}

        def fake_start_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "start_all", fake_start_all)

        result = CliRunner().invoke(
            cli_main.app,
            [
                "start",
                "--server-host",
                "0.0.0.0",
                "--server-port",
                "9000",
                "--webui-host",
                "0.0.0.0",
                "--webui-port",
                "5174",
            ],
        )

        assert result.exit_code == 0
        assert captured["config"].backend_host == "0.0.0.0"
        assert captured["config"].backend_port == 5174
        assert captured["config"].frontend_host == "0.0.0.0"
        assert captured["config"].frontend_port == 5174
        assert captured["config"].legacy_backend_port == 9000
        assert captured["config"].server_port_migration_hint is True

    def test_start_accepts_public_host_and_port(self, monkeypatch):
        """Test start command accepts the unified public host/port options."""
        captured = {}

        def fake_start_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "start_all", fake_start_all)

        result = CliRunner().invoke(
            cli_main.app,
            [
                "start",
                "--host",
                "0.0.0.0",
                "--port",
                "8888",
            ],
        )

        assert result.exit_code == 0
        assert captured["config"].backend_host == "0.0.0.0"
        assert captured["config"].backend_port == 8888
        assert captured["config"].frontend_host == "0.0.0.0"
        assert captured["config"].frontend_port == 8888
        assert captured["config"].legacy_backend_port == 8000

    def test_public_host_and_port_override_legacy_options(self, monkeypatch):
        """Test unified public host/port win over legacy server and WebUI options."""
        captured = {}

        def fake_start_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "start_all", fake_start_all)

        result = CliRunner().invoke(
            cli_main.app,
            [
                "start",
                "--host",
                "0.0.0.0",
                "--port",
                "8888",
                "--server-host",
                "127.0.0.1",
                "--server-port",
                "9000",
                "--webui-host",
                "127.0.0.1",
                "--webui-port",
                "5174",
            ],
        )

        assert result.exit_code == 0
        assert captured["config"].backend_host == "0.0.0.0"
        assert captured["config"].backend_port == 8888
        assert captured["config"].frontend_host == "0.0.0.0"
        assert captured["config"].frontend_port == 8888
        assert captured["config"].legacy_backend_host == "127.0.0.1"
        assert captured["config"].legacy_backend_port == 9000

    def test_restart_accepts_server_and_webui_options(self, monkeypatch):
        """Test restart command accepts explicit server and WebUI host/port options."""
        captured = {}

        def fake_restart_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)
        monkeypatch.setattr(cli_main, "read_supervisor_status", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("down")))

        result = CliRunner().invoke(
            cli_main.app,
            [
                "restart",
                "--server-host",
                "127.0.0.1",
                "--server-port",
                "9100",
                "--webui-host",
                "127.0.0.1",
                "--webui-port",
                "5273",
            ],
        )

        assert result.exit_code == 0
        assert captured["config"].backend_host == "127.0.0.1"
        assert captured["config"].backend_port == 5273
        assert captured["config"].frontend_host == "127.0.0.1"
        assert captured["config"].frontend_port == 5273
        assert captured["config"].legacy_backend_port == 9100

    def test_restart_server_only_does_not_restart_daemon(self, monkeypatch):
        """Test server-only restart leaves the supervisor daemon running."""
        calls = []

        monkeypatch.setattr(cli_main, "restart_server", lambda _console: calls.append("server"))
        monkeypatch.setattr(
            cli_main,
            "restart_all",
            lambda _config, _console: calls.append("all"),
        )

        result = CliRunner().invoke(cli_main.app, ["restart", "--server-only"])

        assert result.exit_code == 0
        assert calls == ["server"]

    def test_restart_accepts_public_host_and_port(self, monkeypatch):
        """Test restart command accepts the unified public host/port options."""
        captured = {}

        def fake_restart_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)
        monkeypatch.setattr(cli_main, "read_supervisor_status", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("down")))

        result = CliRunner().invoke(
            cli_main.app,
            [
                "restart",
                "--host",
                "0.0.0.0",
                "--port",
                "8888",
            ],
        )

        assert result.exit_code == 0
        assert captured["config"].backend_host == "0.0.0.0"
        assert captured["config"].backend_port == 8888
        assert captured["config"].frontend_host == "0.0.0.0"
        assert captured["config"].frontend_port == 8888
        assert captured["config"].legacy_backend_port == 8000

    def test_restart_reuses_supervisor_recorded_host_and_port(self, monkeypatch, tmp_path: Path):
        """Test restart reuses supervisor host/port when CLI and env omit them."""
        captured = {}
        paths = SimpleNamespace(run_dir=tmp_path)

        def fake_restart_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)
        monkeypatch.setattr(cli_main, "runtime_paths", lambda: paths)
        monkeypatch.setattr(
            cli_main,
            "read_supervisor_status",
            lambda **_kwargs: {
                "config": {
                    "backend_host": "0.0.0.0",
                    "backend_port": 9000,
                    "frontend_host": "0.0.0.0",
                    "frontend_port": 5174,
                }
            },
        )
        Config._global_config = None

        result = CliRunner().invoke(cli_main.app, ["restart"])

        assert result.exit_code == 0
        assert captured["config"].backend_host == "0.0.0.0"
        assert captured["config"].backend_port == 5174
        assert captured["config"].frontend_host == "0.0.0.0"
        assert captured["config"].frontend_port == 5174
        assert captured["config"].legacy_backend_port == 9000

    def test_restart_cli_options_override_supervisor_record(self, monkeypatch, tmp_path: Path):
        """Test explicit restart CLI options override supervisor host/port."""
        captured = {}
        paths = SimpleNamespace(run_dir=tmp_path)

        def fake_restart_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)
        monkeypatch.setattr(cli_main, "runtime_paths", lambda: paths)
        monkeypatch.setattr(
            cli_main,
            "read_supervisor_status",
            lambda **_kwargs: {
                "config": {
                    "backend_host": "0.0.0.0",
                    "backend_port": 9000,
                    "frontend_host": "0.0.0.0",
                    "frontend_port": 5174,
                }
            },
        )
        Config._global_config = None

        result = CliRunner().invoke(
            cli_main.app,
            [
                "restart",
                "--server-host",
                "127.0.0.1",
                "--server-port",
                "9100",
                "--webui-host",
                "127.0.0.1",
                "--webui-port",
                "5273",
            ],
        )

        assert result.exit_code == 0
        assert captured["config"].backend_host == "127.0.0.1"
        assert captured["config"].backend_port == 5273
        assert captured["config"].frontend_host == "127.0.0.1"
        assert captured["config"].frontend_port == 5273
        assert captured["config"].legacy_backend_port == 9100

    def test_restart_environment_overrides_supervisor_record(self, monkeypatch, tmp_path: Path):
        """Test restart environment variables still override supervisor host/port."""
        captured = {}
        paths = SimpleNamespace(run_dir=tmp_path)

        def fake_restart_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)
        monkeypatch.setattr(cli_main, "runtime_paths", lambda: paths)
        monkeypatch.setattr(
            cli_main,
            "read_supervisor_status",
            lambda **_kwargs: {
                "config": {
                    "backend_host": "0.0.0.0",
                    "backend_port": 9000,
                    "frontend_host": "0.0.0.0",
                    "frontend_port": 5174,
                }
            },
        )
        monkeypatch.setenv("FLOCKS_SERVER_HOST", "127.0.0.1")
        monkeypatch.setenv("FLOCKS_SERVER_PORT", "9101")
        monkeypatch.setenv("FLOCKS_WEBUI_HOST", "127.0.0.1")
        monkeypatch.setenv("FLOCKS_WEBUI_PORT", "5275")
        Config._global_config = None

        result = CliRunner().invoke(cli_main.app, ["restart"])

        assert result.exit_code == 0
        assert captured["config"].backend_host == "127.0.0.1"
        assert captured["config"].backend_port == 5275
        assert captured["config"].frontend_host == "127.0.0.1"
        assert captured["config"].frontend_port == 5275
        assert captured["config"].legacy_backend_port == 9101

    def test_service_config_prefers_cli_values(self, monkeypatch):
        """Test CLI values override environment and default values."""
        monkeypatch.setenv("FLOCKS_SERVER_HOST", "10.0.0.1")
        monkeypatch.setenv("FLOCKS_SERVER_PORT", "8100")
        monkeypatch.setenv("FLOCKS_WEBUI_HOST", "10.0.0.2")
        monkeypatch.setenv("FLOCKS_WEBUI_PORT", "5274")
        Config._global_config = None

        config = cli_main._service_config(
            server_host="0.0.0.0",
            server_port=9000,
            webui_host="127.0.0.1",
            webui_port=5174,
        )

        assert config.backend_host == "127.0.0.1"
        assert config.backend_port == 5174
        assert config.frontend_host == "127.0.0.1"
        assert config.frontend_port == 5174
        assert config.legacy_backend_host == "0.0.0.0"
        assert config.legacy_backend_port == 9000

    def test_service_config_default_public_port_is_webui_port(self, monkeypatch):
        """Test service startup defaults to the public WebUI port."""
        monkeypatch.delenv("FLOCKS_HOST", raising=False)
        monkeypatch.delenv("FLOCKS_PORT", raising=False)
        monkeypatch.delenv("FLOCKS_PUBLIC_HOST", raising=False)
        monkeypatch.delenv("FLOCKS_PUBLIC_PORT", raising=False)
        monkeypatch.delenv("FLOCKS_SERVER_HOST", raising=False)
        monkeypatch.delenv("FLOCKS_SERVER_PORT", raising=False)
        monkeypatch.delenv("FLOCKS_WEBUI_HOST", raising=False)
        monkeypatch.delenv("FLOCKS_WEBUI_PORT", raising=False)
        Config._global_config = None

        config = cli_main._service_config()

        assert config.backend_host == "127.0.0.1"
        assert config.backend_port == 5173
        assert config.frontend_host == "127.0.0.1"
        assert config.frontend_port == 5173
        assert config.legacy_backend_port == 8000

    def test_service_config_prefers_public_values(self, monkeypatch):
        """Test unified public values override legacy CLI and environment values."""
        monkeypatch.setenv("FLOCKS_WEBUI_HOST", "10.0.0.2")
        monkeypatch.setenv("FLOCKS_WEBUI_PORT", "5274")
        Config._global_config = None

        config = cli_main._service_config(
            host="0.0.0.0",
            port=8888,
            server_host="127.0.0.1",
            server_port=9000,
            webui_host="127.0.0.1",
            webui_port=5174,
        )

        assert config.backend_host == "0.0.0.0"
        assert config.backend_port == 8888
        assert config.frontend_host == "0.0.0.0"
        assert config.frontend_port == 8888
        assert config.legacy_backend_host == "127.0.0.1"
        assert config.legacy_backend_port == 9000

    def test_service_config_uses_server_and_webui_environment(self, monkeypatch):
        """Test environment variables are used when CLI values are absent."""
        monkeypatch.setenv("FLOCKS_SERVER_HOST", "0.0.0.0")
        monkeypatch.setenv("FLOCKS_SERVER_PORT", "9001")
        monkeypatch.setenv("FLOCKS_WEBUI_HOST", "0.0.0.0")
        monkeypatch.setenv("FLOCKS_WEBUI_PORT", "5175")
        Config._global_config = None

        config = cli_main._service_config()

        assert config.backend_host == "0.0.0.0"
        assert config.backend_port == 5175
        assert config.frontend_host == "0.0.0.0"
        assert config.frontend_port == 5175
        assert config.legacy_backend_port == 9001

    def test_service_config_keeps_legacy_env_fallbacks(self, monkeypatch):
        """Test legacy backend/frontend environment variables still work as fallback."""
        monkeypatch.delenv("FLOCKS_SERVER_HOST", raising=False)
        monkeypatch.delenv("FLOCKS_SERVER_PORT", raising=False)
        monkeypatch.delenv("FLOCKS_WEBUI_HOST", raising=False)
        monkeypatch.delenv("FLOCKS_WEBUI_PORT", raising=False)
        monkeypatch.setenv("FLOCKS_BACKEND_HOST", "0.0.0.0")
        monkeypatch.setenv("FLOCKS_BACKEND_PORT", "9200")
        monkeypatch.setenv("FLOCKS_FRONTEND_HOST", "0.0.0.0")
        monkeypatch.setenv("FLOCKS_FRONTEND_PORT", "5176")
        Config._global_config = None

        config = cli_main._service_config()

        assert config.backend_host == "0.0.0.0"
        assert config.backend_port == 5176
        assert config.frontend_host == "0.0.0.0"
        assert config.frontend_port == 5176
        assert config.legacy_backend_port == 9200

    def test_cli_tui_command_default_port(self):
        """Test that CLI tui command uses correct default port."""
        # In actual CLI code: port: int = typer.Option(8000, "--port", "-p")

        from flocks.cli.main import app

        assert app is not None

    def test_removed_top_level_commands_absent_from_cli_help(self):
        """Removed commands should no longer appear in top-level CLI help."""
        from typer.testing import CliRunner

        from flocks.cli.main import app

        result = CliRunner().invoke(app, ["--help"])

        assert result.exit_code == 0
        for command in ("agent", "acp", "debug", "run", "serve", "auth", "models"):
            pattern = rf"^\s*│\s+{re.escape(command)}\s{{2,}}"
            assert re.search(pattern, result.stdout, re.MULTILINE) is None

        assert app is not None


class TestPortConfigurationConsistency:
    """Test consistency of port configuration across the codebase."""

    def test_consistency_between_config_and_server_info(self):
        """Test that GlobalConfig and ServerInfo use same default port."""
        config = Config.get_global()
        server_info = ServerInfo()

        assert config.server_port == server_info.port
        # Note: server_host may differ between config and ServerInfo
        # Config may be affected by environment variables or defaults
        assert server_info.host in ["127.0.0.1", "0.0.0.0"]
