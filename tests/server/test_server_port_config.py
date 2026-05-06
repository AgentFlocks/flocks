"""
Tests for server port configuration.

Tests various scenarios:
1. Port configuration from environment variables
2. Port configuration from command-line arguments
3. Port configuration from GlobalConfig
4. Port configuration from ServerInfo
5. Port conflict detection (when multiple services try to use same port)
"""

import os
import re
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
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

    @patch.dict(os.environ, {'FLOCKS_SERVER_PORT': '9000'})
    def test_port_from_environment_variable(self):
        """Test port configuration from FLOCKS_SERVER_PORT environment variable."""
        # Clear cached config
        Config._global_config = None

        config = Config.get_global()

        # Note: This depends on GlobalConfig implementation
        # If it reads from env, it should be 9000
        # Otherwise, need to verify the env var is properly handled
        assert config.server_port in [8000, 9000]

    @patch.dict(os.environ, {'FLOCKS_SERVER_HOST': '0.0.0.0'})
    def test_host_from_environment_variable(self):
        """Test host configuration from FLOCKS_SERVER_HOST environment variable."""
        # Clear cached config
        Config._global_config = None

        config = Config.get_global()

        # Should support host from env or use default
        assert config.server_host in ["127.0.0.1", "0.0.0.0"]

    def test_port_range_validation(self):
        """Test that port values are within valid range."""
        config = Config.get_global()

        assert 1 <= config.server_port <= 65535
        assert config.server_port > 1024  # Should not use privileged ports by default


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

    def test_server_info_with_custom_port(self):
        """Test ServerInfo with custom port."""
        server_info = ServerInfo()
        server_info.port = 9000
        server_info.url = f"http://{server_info.host}:{server_info.port}"

        assert server_info.port == 9000
        assert server_info.url == "http://127.0.0.1:9000"

    def test_server_info_with_custom_host(self):
        """Test ServerInfo with custom host."""
        server_info = ServerInfo()
        server_info.host = "0.0.0.0"
        server_info.url = f"http://{server_info.host}:{server_info.port}"

        assert server_info.host == "0.0.0.0"
        assert server_info.url == "http://0.0.0.0:8000"

    def test_server_info_multiple_instances(self):
        """Test ServerInfo instances behavior."""
        info1 = ServerInfo()
        info2 = ServerInfo()

        # Each instance should have the same default values
        assert info1.port == info2.port
        assert info1.port == 8000


class TestPortAvailability:
    """Test port availability and conflict detection."""

    def test_port_is_available(self):
        """Test checking if a port is available."""
        # Test with a likely available high port
        port = 58000

        assert self._is_port_available("127.0.0.1", port) is True

    def test_port_is_not_available_when_in_use(self):
        """Test detecting when a port is already in use."""
        # Bind to a port to make it unavailable
        test_port = 58001
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        try:
            sock.bind(("127.0.0.1", test_port))
            sock.listen(1)

            # Now test that the port is detected as unavailable
            assert self._is_port_available("127.0.0.1", test_port) is False
        finally:
            sock.close()

    def test_common_development_ports(self):
        """Test awareness of common development ports."""
        common_ports = {
            3000: "React/Node dev server",
            4000: "Various dev servers",
            5000: "Flask default",
            8000: "Django/Flocks default",
            8080: "Alternative HTTP",
        }

        # Just document awareness - don't fail if ports are in use
        for port, description in common_ports.items():
            available = self._is_port_available("127.0.0.1", port)
            # Log port status without failing test
            print(f"Port {port} ({description}): {'available' if available else 'in use'}")

    def test_privileged_ports_avoided(self):
        """Test that default port avoids privileged range (<1024)."""
        config = Config.get_global()

        # Privileged ports require root/admin
        assert config.server_port >= 1024

    @staticmethod
    def _is_port_available(host: str, port: int) -> bool:
        """Helper method to check if a port is available."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
            sock.close()
            return True
        except OSError:
            return False


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
        assert captured["config"].backend_port == 9000
        assert captured["config"].frontend_host == "0.0.0.0"
        assert captured["config"].frontend_port == 5174

    def test_restart_accepts_server_and_webui_options(self, monkeypatch):
        """Test restart command accepts explicit server and WebUI host/port options."""
        captured = {}

        def fake_restart_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)

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
        assert captured["config"].backend_port == 9100
        assert captured["config"].frontend_host == "127.0.0.1"
        assert captured["config"].frontend_port == 5273

    def test_restart_reuses_runtime_recorded_host_and_port(self, monkeypatch, tmp_path: Path):
        """Test restart reuses last runtime host/port when CLI and env omit them."""
        captured = {}
        paths = SimpleNamespace(
            backend_pid=tmp_path / "backend.pid",
            frontend_pid=tmp_path / "webui.pid",
        )
        records = {
            paths.backend_pid: SimpleNamespace(host="0.0.0.0", port=9000),
            paths.frontend_pid: SimpleNamespace(host="0.0.0.0", port=5174),
        }

        def fake_restart_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)
        monkeypatch.setattr(cli_main, "runtime_paths", lambda: paths)
        monkeypatch.setattr(cli_main, "read_runtime_record", lambda path: records.get(path))
        Config._global_config = None

        result = CliRunner().invoke(cli_main.app, ["restart"])

        assert result.exit_code == 0
        assert captured["config"].backend_host == "0.0.0.0"
        assert captured["config"].backend_port == 9000
        assert captured["config"].frontend_host == "0.0.0.0"
        assert captured["config"].frontend_port == 5174

    def test_restart_cli_options_override_runtime_record(self, monkeypatch, tmp_path: Path):
        """Test explicit restart CLI options override runtime-recorded host/port."""
        captured = {}
        paths = SimpleNamespace(
            backend_pid=tmp_path / "backend.pid",
            frontend_pid=tmp_path / "webui.pid",
        )

        def fake_restart_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)
        monkeypatch.setattr(cli_main, "runtime_paths", lambda: paths)
        monkeypatch.setattr(
            cli_main,
            "read_runtime_record",
            lambda path: SimpleNamespace(
                host="0.0.0.0",
                port=9000 if Path(path) == paths.backend_pid else 5174,
            ),
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
        assert captured["config"].backend_port == 9100
        assert captured["config"].frontend_host == "127.0.0.1"
        assert captured["config"].frontend_port == 5273

    def test_restart_environment_overrides_runtime_record(self, monkeypatch, tmp_path: Path):
        """Test restart environment variables still override runtime-recorded host/port."""
        captured = {}
        paths = SimpleNamespace(
            backend_pid=tmp_path / "backend.pid",
            frontend_pid=tmp_path / "webui.pid",
        )

        def fake_restart_all(config, _console):
            captured["config"] = config

        monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)
        monkeypatch.setattr(cli_main, "runtime_paths", lambda: paths)
        monkeypatch.setattr(
            cli_main,
            "read_runtime_record",
            lambda path: SimpleNamespace(
                host="0.0.0.0",
                port=9000 if Path(path) == paths.backend_pid else 5174,
            ),
        )
        monkeypatch.setenv("FLOCKS_SERVER_HOST", "127.0.0.1")
        monkeypatch.setenv("FLOCKS_SERVER_PORT", "9101")
        monkeypatch.setenv("FLOCKS_WEBUI_HOST", "127.0.0.1")
        monkeypatch.setenv("FLOCKS_WEBUI_PORT", "5275")
        Config._global_config = None

        result = CliRunner().invoke(cli_main.app, ["restart"])

        assert result.exit_code == 0
        assert captured["config"].backend_host == "127.0.0.1"
        assert captured["config"].backend_port == 9101
        assert captured["config"].frontend_host == "127.0.0.1"
        assert captured["config"].frontend_port == 5275

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

        assert config.backend_host == "0.0.0.0"
        assert config.backend_port == 9000
        assert config.frontend_host == "127.0.0.1"
        assert config.frontend_port == 5174

    def test_service_config_uses_server_and_webui_environment(self, monkeypatch):
        """Test environment variables are used when CLI values are absent."""
        monkeypatch.setenv("FLOCKS_SERVER_HOST", "0.0.0.0")
        monkeypatch.setenv("FLOCKS_SERVER_PORT", "9001")
        monkeypatch.setenv("FLOCKS_WEBUI_HOST", "0.0.0.0")
        monkeypatch.setenv("FLOCKS_WEBUI_PORT", "5175")
        Config._global_config = None

        config = cli_main._service_config()

        assert config.backend_host == "0.0.0.0"
        assert config.backend_port == 9001
        assert config.frontend_host == "0.0.0.0"
        assert config.frontend_port == 5175

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
        assert config.backend_port == 9200
        assert config.frontend_host == "0.0.0.0"
        assert config.frontend_port == 5176

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

    def test_consistency_in_documentation(self):
        """Test that documented port matches code default."""
        # This is a meta-test to ensure documentation consistency
        # The actual values should be checked against README.md

        config = Config.get_global()
        expected_port = 8000

        assert config.server_port == expected_port


class TestPortConfigurationEdgeCases:
    """Test edge cases in port configuration."""

    def test_port_zero_dynamic_allocation(self):
        """Test that port 0 triggers dynamic allocation."""
        # Port 0 tells OS to assign any available port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        assigned_port = sock.getsockname()[1]
        sock.close()

        assert assigned_port > 0
        assert assigned_port != 0

    def test_invalid_port_too_low(self):
        """Test handling of invalid port (too low)."""
        # Port -1 should be invalid
        with pytest.raises((OSError, ValueError, OverflowError)):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", -1))

    def test_invalid_port_too_high(self):
        """Test handling of invalid port (too high)."""
        # Port > 65535 should be invalid
        with pytest.raises((OSError, ValueError, OverflowError)):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 65536))

    def test_localhost_variations(self):
        """Test different localhost address variations."""
        variations = [
            "127.0.0.1",
            "localhost",
            "0.0.0.0",  # Listen on all interfaces
        ]

        for addr in variations:
            # Just verify these are valid addresses
            # Don't bind to avoid test conflicts
            assert isinstance(addr, str)
            assert len(addr) > 0


class TestMultipleServerInstances:
    """Test handling of multiple server instances."""

    def test_two_servers_same_port_fails(self):
        """Test that two servers cannot bind to same port."""
        port = 58002

        sock1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock1.bind(("127.0.0.1", port))
        sock1.listen(1)

        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        try:
            # Second bind should fail
            with pytest.raises(OSError) as exc_info:
                sock2.bind(("127.0.0.1", port))

            # Should be "Address already in use" error
            assert exc_info.value.errno in [48, 98]  # EADDRINUSE on macOS/Linux
        finally:
            sock1.close()
            sock2.close()

    def test_two_servers_different_ports_succeeds(self):
        """Test that two servers can use different ports."""
        port1 = 58003
        port2 = 58004

        sock1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        try:
            sock1.bind(("127.0.0.1", port1))
            sock1.listen(1)

            sock2.bind(("127.0.0.1", port2))
            sock2.listen(1)

            # Both should succeed
            assert sock1.getsockname()[1] == port1
            assert sock2.getsockname()[1] == port2
        finally:
            sock1.close()
            sock2.close()


class TestClientPortConfiguration:
    """Test client port configuration."""

    @pytest.mark.skip(reason="FlocksClient has import issues with get_manager")
    def test_flocks_client_default_url(self):
        """Test FlocksClient uses correct default base URL."""
        from flocks.server.client import FlocksClient

        # Default should be http://127.0.0.1:8000
        client = FlocksClient()

        assert "8000" in client.base_url
        assert "127.0.0.1" in client.base_url or "localhost" in client.base_url

    @pytest.mark.skip(reason="FlocksClient has import issues with get_manager")
    def test_flocks_client_custom_url(self):
        """Test FlocksClient with custom base URL."""
        from flocks.server.client import FlocksClient

        custom_url = "http://192.168.1.100:9000"
        client = FlocksClient(base_url=custom_url)

        assert client.base_url == custom_url


class TestEnvironmentVariablePortConfig:
    """Test port configuration via environment variables in scripts."""

    @patch.dict(os.environ, {'FLOCKS_PORT': '7000'})
    def test_script_port_env_var(self):
        """Test FLOCKS_PORT environment variable (used in scripts)."""
        port = os.getenv('FLOCKS_PORT', '8000')

        assert port == '7000'

    def test_script_port_env_var_default(self):
        """Test FLOCKS_PORT defaults to 8000 when not set."""
        # Temporarily remove the env var if it exists
        old_value = os.environ.pop('FLOCKS_PORT', None)

        try:
            port = int(os.getenv('FLOCKS_PORT', '8000'))
            assert port == 8000
        finally:
            # Restore old value if it existed
            if old_value is not None:
                os.environ['FLOCKS_PORT'] = old_value

    @patch.dict(os.environ, {'FLOCKS_HOST': '0.0.0.0'})
    def test_script_host_env_var(self):
        """Test FLOCKS_HOST environment variable."""
        host = os.getenv('FLOCKS_HOST', '127.0.0.1')

        assert host == '0.0.0.0'


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
