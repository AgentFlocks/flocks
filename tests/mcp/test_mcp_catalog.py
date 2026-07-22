"""Tests for MCP Server Catalog."""

import json
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.mcp.catalog import (
    _resolve_catalog_file,
    CatalogEntry,
    CategoryInfo,
    EnvVarSpec,
    InstallSpec,
    McpCatalog,
    RemoteConfigSpec,
)
from flocks.mcp.installer import managed_python_bin_dir, managed_python_executable


@pytest.fixture(autouse=True)
def _reset_catalog(tmp_path, monkeypatch):
    """Reset catalog singleton and isolate the user config directory."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    Config._global_config = None
    Config._cached_config = None
    repo_example = Path(__file__).resolve().parents[2] / ".flocks" / "mcp_list.json.example"
    (config_dir / "mcp_list.json").write_text(repo_example.read_text(encoding="utf-8"), encoding="utf-8")
    McpCatalog.reset()
    yield
    McpCatalog.reset()
    Config._global_config = None
    Config._cached_config = None


class TestCatalogDataIntegrity:
    """Validate the mcp_list.json / catalog_data.json file itself."""

    def test_catalog_file_exists(self):
        catalog_file = _resolve_catalog_file()
        assert catalog_file.exists(), f"Catalog file not found: {catalog_file}"

    def test_catalog_file_valid_json(self):
        raw = json.loads(_resolve_catalog_file().read_text(encoding="utf-8"))
        assert "version" in raw
        assert "categories" in raw
        assert "servers" in raw

    def test_all_servers_have_required_fields(self):
        raw = json.loads(_resolve_catalog_file().read_text(encoding="utf-8"))
        required_fields = {"id", "name", "description", "category", "github", "language"}
        for server in raw["servers"]:
            missing = required_fields - set(server.keys())
            assert not missing, f"Server '{server.get('id', '?')}' missing fields: {missing}"

    def test_all_server_categories_exist(self):
        raw = json.loads(_resolve_catalog_file().read_text(encoding="utf-8"))
        valid_categories = set(raw["categories"].keys())
        for server in raw["servers"]:
            assert server["category"] in valid_categories, (
                f"Server '{server['id']}' has unknown category '{server['category']}'"
            )

    def test_no_duplicate_server_ids(self):
        raw = json.loads(_resolve_catalog_file().read_text(encoding="utf-8"))
        ids = [s["id"] for s in raw["servers"]]
        duplicates = [sid for sid in ids if ids.count(sid) > 1]
        assert not duplicates, f"Duplicate server IDs found: {set(duplicates)}"


class TestCatalogEntry:
    """Test CatalogEntry model."""

    def _make_entry(self, **overrides) -> CatalogEntry:
        defaults = {
            "id": "test_server",
            "name": "Test Server",
            "description": "A test MCP server",
            "category": "threat_intelligence",
            "github": "test/test-mcp",
            "language": "python",
            "license": "MIT",
            "stars": 100,
            "transport": "local",
            "install": InstallSpec(local_command=["python", "-m", "test_mcp"]),
            "env_vars": {
                "API_KEY": EnvVarSpec(required=True, description="API key", secret=True),
                "BASE_URL": EnvVarSpec(required=False, default="https://api.test.com"),
            },
            "tags": ["test", "example"],
        }
        defaults.update(overrides)
        return CatalogEntry(**defaults)

    def test_github_url(self):
        entry = self._make_entry()
        assert entry.github_url == "https://github.com/test/test-mcp"

    def test_requires_auth(self):
        entry = self._make_entry()
        assert entry.requires_auth is True

    def test_no_auth_required(self):
        entry = self._make_entry(env_vars={})
        assert entry.requires_auth is False

    def test_required_env_vars(self):
        entry = self._make_entry()
        required = entry.required_env_vars
        assert "API_KEY" in required
        assert "BASE_URL" not in required

    def test_to_mcp_config_local(self):
        entry = self._make_entry()
        config = entry.to_mcp_config()
        assert config["type"] == "local"
        assert config["command"] == [managed_python_executable(), "-m", "test_mcp"]
        assert config["enabled"] is False
        assert "API_KEY" in config["environment"]
        assert config["environment"]["BASE_URL"] == "https://api.test.com"

    def test_to_mcp_config_with_overrides(self):
        entry = self._make_entry()
        config = entry.to_mcp_config({"API_KEY": "my-secret-key"})
        assert config["environment"]["API_KEY"] == "my-secret-key"

    def test_to_mcp_config_remote(self):
        entry = self._make_entry(
            transport="remote",
            remote=RemoteConfigSpec(
                url="https://example.com/mcp?apikey={secret:api_key}",
                transport="sse",
                auth={
                    "type": "apikey",
                    "location": "header",
                    "param_name": "Authorization",
                    "value": "Bearer {secret:api_key}",
                },
                oauth=False,
            ),
        )
        config = entry.to_mcp_config({"url": "https://example.com/mcp"})
        assert config["type"] == "remote"
        assert config["url"] == "https://example.com/mcp"
        assert config["transport"] == "sse"
        assert config["auth"]["param_name"] == "Authorization"
        assert config["oauth"] is False

    def test_to_mcp_config_local_console_script_uses_managed_venv(self):
        entry = self._make_entry(
            install=InstallSpec(pip="test-mcp", local_command=["test-mcp"]),
        )
        config = entry.to_mcp_config()
        assert config["command"] == [str(managed_python_bin_dir() / "test-mcp")]

    def test_secret_env_vars_use_secret_ref(self):
        entry = self._make_entry()
        config = entry.to_mcp_config()
        assert config["environment"]["API_KEY"] == "{secret:api_key}"


class TestMcpCatalog:
    """Test McpCatalog manager."""

    def test_singleton(self):
        a = McpCatalog.get()
        b = McpCatalog.get()
        assert a is b

    def test_load_entries(self):
        catalog = McpCatalog.get()
        assert len(catalog.entries) > 0

    def test_load_categories(self):
        catalog = McpCatalog.get()
        assert len(catalog.categories) > 0

    def test_version(self):
        catalog = McpCatalog.get()
        assert catalog.version == "1.0.0"

    def test_get_entry_by_id(self):
        catalog = McpCatalog.get()
        entry = catalog.get_entry("npm_audit")
        assert entry is not None
        assert entry.name == "NPM Security Audit"

    def test_get_entry_not_found(self):
        catalog = McpCatalog.get()
        entry = catalog.get_entry("nonexistent_server")
        assert entry is None

    def test_search_by_query(self):
        catalog = McpCatalog.get()
        results = catalog.search(query="audit")
        assert len(results) >= 1
        assert any(e.id == "npm_audit" for e in results)

    def test_search_by_category(self):
        catalog = McpCatalog.get()
        results = catalog.search(category="threat_intelligence")
        assert len(results) >= 1
        assert all(e.category == "threat_intelligence" for e in results)

    def test_search_by_language(self):
        catalog = McpCatalog.get()
        results = catalog.search(language="python")
        assert len(results) >= 1
        assert all(e.language == "python" for e in results)

    def test_search_by_tags(self):
        catalog = McpCatalog.get()
        results = catalog.search(tags=["audit"])
        assert len(results) >= 1
        for entry in results:
            assert any("audit" in t.lower() for t in entry.tags)

    def test_search_official_only(self):
        catalog = McpCatalog.get()
        results = catalog.search(official_only=True)
        assert all(e.official for e in results)

    def test_search_combined_filters(self):
        catalog = McpCatalog.get()
        results = catalog.search(category="code_security", language="typescript")
        for entry in results:
            assert entry.category == "code_security"
            assert entry.language == "typescript"

    def test_list_by_category(self):
        catalog = McpCatalog.get()
        grouped = catalog.list_by_category()
        assert isinstance(grouped, dict)
        assert len(grouped) > 0
        for cat, entries in grouped.items():
            assert len(entries) > 0
            assert all(e.category == cat for e in entries)

    def test_generate_config_specific_servers(self):
        catalog = McpCatalog.get()
        config = catalog.generate_config(server_ids=["npm_audit", "tenzir"])
        assert "npm_audit" in config
        assert "tenzir" in config
        assert config["npm_audit"]["type"] == "local"
        assert config["npm_audit"]["enabled"] is False
        assert config["tenzir"]["command"][0] == str(managed_python_bin_dir() / "tenzir-mcp")

    def test_generate_config_all_servers(self):
        catalog = McpCatalog.get()
        config = catalog.generate_config()
        assert len(config) > 0

    def test_generate_full_config_template(self):
        catalog = McpCatalog.get()
        template = catalog.generate_full_config_template()
        assert "mcp" in template
        assert len(template["mcp"]) > 0

    def test_get_stats(self):
        catalog = McpCatalog.get()
        stats = catalog.get_stats()
        assert stats["total_servers"] > 0
        assert stats["total_categories"] > 0
        assert "by_category" in stats
        assert "by_language" in stats


class TestCatalogSecurityServers:
    """Verify key security servers are present in catalog."""

    @pytest.fixture()
    def catalog(self):
        return McpCatalog.get()

    @pytest.mark.parametrize(
        "server_id",
        [
            "panther",
            "secops",
            "tenzir",
            "cybersec_watchdog",
            "npm_audit",
            "gridinsoft",
            "chimera_csl",
            "aim_guard",
        ],
    )
    def test_security_server_exists(self, catalog, server_id):
        entry = catalog.get_entry(server_id)
        assert entry is not None, f"Security server '{server_id}' not found in catalog"
        assert entry.description, f"Server '{server_id}' has empty description"
        assert entry.github, f"Server '{server_id}' has no github repo"

    def test_official_servers_flag_matches_search(self, catalog):
        official_entries = catalog.search(official_only=True)
        assert all(entry.official is True for entry in official_entries)
