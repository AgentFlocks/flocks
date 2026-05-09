"""
MCP Server Catalog

Manages a catalog of known open-source MCP servers.
Provides discovery, search, and configuration generation for MCP servers.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field

from flocks.config.config import Config
from flocks.mcp.installer import rewrite_local_command_for_managed_python
from flocks.utils.log import Log

log = Log.create(service="mcp.catalog")

def _resolve_catalog_file() -> Path:
    """Return the catalog file to load.

    Loads the catalog from the unified user config directory.
    Raises FileNotFoundError if not found.
    """
    candidate = Config.get_mcp_catalog_file()
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"mcp_list.json not found. Make sure {candidate} exists."
    )

API_CATEGORIES = frozenset({
    "threat_intelligence",
    "vulnerability",
    "siem",
    "incident_response",
    "network_security",
    "osint",
    "cloud_security",
    "identity_security",
    "secops_platform",
    "communication",
    "monitoring",
    "search",
    "ai_model",
    "firewall",
})


class EnvVarSpec(BaseModel):
    """Environment variable specification for an MCP server."""

    required: bool = False
    description: str = ""
    default: Optional[str] = None
    secret: bool = False


class ParamSpec(BaseModel):
    """Specification for a positional parameter placeholder in local_command."""

    description: str = ""
    default: Optional[str] = None


class InstallSpec(BaseModel):
    """Installation specification for an MCP server."""

    model_config = {"extra": "allow"}

    pip: Optional[str] = None
    npx: Optional[str] = None
    uvx: Optional[str] = None
    command: Optional[List[str]] = None
    local_command: Optional[List[str]] = None
    params: Optional[Dict[str, ParamSpec]] = None
    note: Optional[str] = None


class RemoteConfigSpec(BaseModel):
    """Remote MCP configuration template stored in the catalog."""

    model_config = {"extra": "allow"}

    url: str = ""
    transport: Literal["auto", "sse", "http"] = "auto"
    headers: Optional[Dict[str, str]] = None
    auth: Optional[Dict[str, Any]] = None
    oauth: Optional[Any] = None
    timeout: Optional[int] = None


class CatalogEntry(BaseModel):
    """A single MCP server entry in the catalog."""

    model_config = {"extra": "allow"}

    id: str
    name: str
    description: str
    description_cn: Optional[str] = None
    category: str
    github: str
    language: str
    license: str = "MIT"
    stars: int = 0
    transport: str = "local"
    install: InstallSpec = Field(default_factory=InstallSpec)
    remote: Optional[RemoteConfigSpec] = None
    env_vars: Dict[str, EnvVarSpec] = Field(default_factory=dict)
    system_deps: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    official: bool = False

    @property
    def tool_type(self) -> str:
        """Classify as 'api' (wraps external services) or 'mcp' (local tools)."""
        return "api" if self.category in API_CATEGORIES else "mcp"

    @property
    def github_url(self) -> str:
        return f"https://github.com/{self.github}"

    @property
    def requires_auth(self) -> bool:
        return any(v.secret for v in self.env_vars.values())

    @property
    def required_env_vars(self) -> Dict[str, EnvVarSpec]:
        return {k: v for k, v in self.env_vars.items() if v.required}

    def to_mcp_config(
        self,
        env_overrides: Optional[Dict[str, str]] = None,
        args: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Generate MCP server configuration for ~/.flocks/config/flocks.json.

        Args:
            env_overrides: Override environment variable values.
            args: Positional parameter overrides, used to replace {param:xxx}
                placeholders in local_command (e.g. allowed path for filesystem).

        Returns:
            Configuration dict ready to be inserted into the mcp section.
        """
        env = env_overrides or {}

        if self.transport == "local":
            raw_cmd = self.install.local_command
            if not raw_cmd:
                return {}

            # Replace {param:xxx} placeholders in local_command.
            # Priority: caller-supplied args > params.default > keep placeholder as-is
            cmd: List[str] = []
            for token in raw_cmd:
                if token.startswith("{param:") and token.endswith("}"):
                    key = token[7:-1]
                    if args and key in args:
                        cmd.append(args[key])
                    elif self.install.params and key in self.install.params:
                        default = self.install.params[key].default
                        cmd.append(default if default is not None else token)
                    else:
                        cmd.append(token)
                else:
                    cmd.append(token)

            cmd = rewrite_local_command_for_managed_python(cmd, self.install.pip)

            config: Dict[str, Any] = {
                "type": "local",
                "command": cmd,
                "enabled": False,
            }

            environment: Dict[str, str] = {}
            for var_name, spec in self.env_vars.items():
                if var_name in env:
                    environment[var_name] = env[var_name]
                elif spec.secret:
                    environment[var_name] = f"{{secret:{var_name.lower()}}}"
                elif spec.default:
                    environment[var_name] = spec.default
                else:
                    environment[var_name] = f"{{env:{var_name}}}"

            if environment:
                config["environment"] = environment

            return config

        elif self.transport == "remote":
            config: Dict[str, Any] = {
                "type": "remote",
                "url": env.get("url", self.remote.url if self.remote else ""),
                "enabled": False,
            }
            if self.remote:
                remote_template = self.remote.model_dump(exclude_none=True)
                if "transport" in remote_template:
                    config["transport"] = remote_template["transport"]
                if remote_template.get("headers"):
                    config["headers"] = remote_template["headers"]
                if remote_template.get("auth"):
                    config["auth"] = remote_template["auth"]
                if "oauth" in remote_template:
                    config["oauth"] = remote_template["oauth"]
                if remote_template.get("timeout") is not None:
                    config["timeout"] = remote_template["timeout"]
            return config

        return {}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize including computed fields."""
        d = self.model_dump()
        d["tool_type"] = self.tool_type
        d["requires_auth"] = self.requires_auth
        return d


class CategoryInfo(BaseModel):
    """Category metadata."""

    label: str
    description: str


class McpCatalog:
    """MCP Server Catalog manager.

    Loads the catalog data and provides search, filter, and config generation.
    Auto-reloads when the underlying mcp_list.json file is modified.
    """

    _instance: Optional["McpCatalog"] = None
    _entries: List[CatalogEntry]
    _categories: Dict[str, CategoryInfo]
    _version: str
    _catalog_path: Optional[Path]
    _catalog_mtime: float

    def __init__(self) -> None:
        self._entries = []
        self._categories = {}
        self._version = "0.0.0"
        self._catalog_path = None
        self._catalog_mtime = 0.0
        self._load()

    @classmethod
    def get(cls) -> "McpCatalog":
        """Get singleton instance, reloading if the catalog file has changed."""
        if cls._instance is None:
            cls._instance = cls()
        else:
            cls._instance._reload_if_changed()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    def _reload_if_changed(self) -> None:
        """Reload catalog from disk if the file has been modified since last load."""
        if self._catalog_path is None:
            return
        try:
            mtime = self._catalog_path.stat().st_mtime
            if mtime != self._catalog_mtime:
                log.info("catalog.reloading", {"path": str(self._catalog_path)})
                self._entries = []
                self._categories = {}
                self._version = "0.0.0"
                self._load()
        except OSError:
            pass

    def _load(self) -> None:
        """Load catalog data from JSON file."""
        try:
            catalog_path = _resolve_catalog_file()
            self._catalog_path = catalog_path
            self._catalog_mtime = catalog_path.stat().st_mtime
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
            self._version = raw.get("version", "0.0.0")
            log.info("catalog.file", {"path": str(catalog_path)})

            for cat_id, cat_data in raw.get("categories", {}).items():
                self._categories[cat_id] = CategoryInfo(**cat_data)

            for entry_data in raw.get("servers", []):
                env_vars_raw = entry_data.get("env_vars", {})
                parsed_env: Dict[str, EnvVarSpec] = {}
                for k, v in env_vars_raw.items():
                    if isinstance(v, dict):
                        parsed_env[k] = EnvVarSpec(**v)
                    else:
                        parsed_env[k] = EnvVarSpec()
                entry_data["env_vars"] = parsed_env

                install_raw = entry_data.get("install", {})
                if isinstance(install_raw, dict):
                    # Normalise params values to ParamSpec instances
                    params_raw = install_raw.get("params")
                    if isinstance(params_raw, dict):
                        install_raw = dict(install_raw)
                        install_raw["params"] = {
                            k: ParamSpec(**v) if isinstance(v, dict) else ParamSpec()
                            for k, v in params_raw.items()
                        }
                    entry_data["install"] = InstallSpec(**install_raw)

                self._entries.append(CatalogEntry(**entry_data))

            log.info(
                "catalog.loaded",
                {"version": self._version, "servers": len(self._entries), "categories": len(self._categories)},
            )
        except Exception as e:
            log.error("catalog.load_failed", {"error": str(e)})

    @property
    def version(self) -> str:
        return self._version

    @property
    def entries(self) -> List[CatalogEntry]:
        return list(self._entries)

    @property
    def categories(self) -> Dict[str, CategoryInfo]:
        return dict(self._categories)

    def get_entry(self, server_id: str) -> Optional[CatalogEntry]:
        """Get a catalog entry by ID."""
        for entry in self._entries:
            if entry.id == server_id:
                return entry
        return None

    def search(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
        language: Optional[str] = None,
        tags: Optional[List[str]] = None,
        official_only: bool = False,
    ) -> List[CatalogEntry]:
        """Search catalog entries with filters.

        Args:
            query: Free-text search across name, bilingual descriptions, and tags.
            category: Filter by category ID.
            language: Filter by programming language.
            tags: Filter by tags (any match).
            official_only: Only return official MCP servers.

        Returns:
            Matching catalog entries.
        """
        results = list(self._entries)

        if official_only:
            results = [e for e in results if e.official]

        if category:
            results = [e for e in results if e.category == category]

        if language:
            lang_lower = language.lower()
            results = [e for e in results if e.language.lower() == lang_lower]

        if tags:
            tag_set = {t.lower() for t in tags}
            results = [e for e in results if tag_set & {t.lower() for t in e.tags}]

        if query:
            q = query.lower()
            scored: List[tuple[int, CatalogEntry]] = []
            for entry in results:
                score = 0
                if q in entry.name.lower():
                    score += 10
                if q in entry.id.lower():
                    score += 8
                if q in entry.description.lower():
                    score += 5
                if entry.description_cn and q in entry.description_cn.lower():
                    score += 5
                if any(q in t.lower() for t in entry.tags):
                    score += 3
                if score > 0:
                    scored.append((score, entry))
            scored.sort(key=lambda x: (-x[0], -x[1].stars))
            results = [e for _, e in scored]

        return results

    def list_by_category(self) -> Dict[str, List[CatalogEntry]]:
        """Group all entries by category."""
        grouped: Dict[str, List[CatalogEntry]] = {}
        for entry in self._entries:
            grouped.setdefault(entry.category, []).append(entry)
        return grouped

    def generate_config(
        self,
        server_ids: Optional[List[str]] = None,
        env_overrides: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Generate MCP configuration for selected servers.

        Args:
            server_ids: List of server IDs to include (None = all).
            env_overrides: Per-server environment overrides keyed by server_id.

        Returns:
            Dict suitable for the ``mcp`` key in flocks.json.
        """
        overrides = env_overrides or {}
        mcp_config: Dict[str, Any] = {}

        entries = self._entries if server_ids is None else [e for e in self._entries if e.id in server_ids]

        for entry in entries:
            config = entry.to_mcp_config(overrides.get(entry.id))
            if config:
                mcp_config[entry.id] = config

        return mcp_config

    def generate_full_config_template(self) -> Dict[str, Any]:
        """Generate a complete config template with all servers (disabled by default).

        Useful for users who want to see all available servers and selectively enable them.

        Returns:
            Complete flocks.json-compatible configuration.
        """
        return {"mcp": self.generate_config()}

    def get_stats(self) -> Dict[str, Any]:
        """Get catalog statistics."""
        by_category: Dict[str, int] = {}
        by_language: Dict[str, int] = {}
        official_count = 0
        requires_auth_count = 0

        for entry in self._entries:
            by_category[entry.category] = by_category.get(entry.category, 0) + 1
            by_language[entry.language] = by_language.get(entry.language, 0) + 1
            if entry.official:
                official_count += 1
            if entry.requires_auth:
                requires_auth_count += 1

        return {
            "version": self._version,
            "total_servers": len(self._entries),
            "total_categories": len(self._categories),
            "official_servers": official_count,
            "requires_auth": requires_auth_count,
            "by_category": by_category,
            "by_language": by_language,
        }


__all__ = [
    "McpCatalog",
    "CatalogEntry",
    "CategoryInfo",
    "EnvVarSpec",
    "InstallSpec",
    "ParamSpec",
]
