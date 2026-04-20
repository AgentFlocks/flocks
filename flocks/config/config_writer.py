"""
Atomic CRUD operations for the provider section of flocks.json.

All writes follow: read raw JSON → modify in-memory → atomic write (tmp + rename) → clear Config cache.

This module is the single entry point for programmatic writes to flocks.json provider config.
Secrets (API keys) are NOT written here — use SecretManager for those.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from flocks.config.config import Config
from flocks.utils.log import Log

log = Log.create(service="config.writer")


_FALLBACK_CONFIG_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "flocks.json": {},
    ".secret.json": {},
    "mcp_list.json": {
        "version": "1.0.0",
        "categories": {},
        "servers": [],
    },
}


def _get_example_config_dir() -> Path:
    """Return the bundled example directory used for first-run initialization."""
    return Path(__file__).resolve().parents[2] / ".flocks"


def ensure_config_files() -> None:
    """
    Initialize configuration files from examples if they don't exist.
    
    This function is called during server startup to ensure that
    flocks.json, .secret.json, and mcp_list.json exist. If they don't,
    they are copied from their .example counterparts.
    """
    config_dir = Config.get_config_path()
    config_dir.mkdir(parents=True, exist_ok=True)
    example_dir = _get_example_config_dir()

    for target_name, example_name in [
        ("flocks.json", "flocks.json.example"),
        (".secret.json", ".secret.json.example"),
        ("mcp_list.json", "mcp_list.json.example"),
    ]:
        target_file = config_dir / target_name
        example_file = example_dir / example_name

        if target_file.exists():
            continue

        try:
            if example_file.exists():
                shutil.copy2(example_file, target_file)
            else:
                target_file.write_text(
                    json.dumps(_FALLBACK_CONFIG_TEMPLATES[target_name], indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            log.info("config.initialized", {
                "file": target_name,
                "source": str(example_file) if example_file.exists() else "fallback-template",
            })
        except Exception as e:
            log.error("config.init_failed", {
                "file": target_name,
                "error": str(e),
            })


class ConfigWriter:
    """Atomic read-modify-write operations on the provider section of flocks.json."""

    @classmethod
    def _get_config_path(cls) -> Path:
        """Return the unified user flocks.json path."""
        config_dir = Config.get_config_path()
        jsonc_path = config_dir / "flocks.jsonc"
        if jsonc_path.exists():
            return jsonc_path
        return Config.get_config_file()

    @classmethod
    def _read_raw(cls) -> Dict[str, Any]:
        """Read flocks.json as raw dict (no secret resolution)."""
        path = cls._get_config_path()
        if not path.exists():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                return {}
            return json.loads(text)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("config_writer.read_failed", {"path": str(path), "error": str(exc)})
            return {}

    @classmethod
    def _write_raw(cls, data: Dict[str, Any]) -> None:
        """Atomic write: write to tmp file then rename, then clear Config cache."""
        path = cls._get_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write via temp file in same directory
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".flocks_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp_path, str(path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # Clear Config cache so next Config.get() reloads from disk
        try:
            Config.clear_cache()
        except Exception:
            pass

        log.debug("config_writer.written", {"path": str(path)})

    # ------------------------------------------------------------------
    # Provider-level CRUD
    # ------------------------------------------------------------------

    @classmethod
    def get_provider_raw(cls, provider_id: str) -> Optional[Dict[str, Any]]:
        """Read a single provider entry from flocks.json (raw, secrets unresolved).

        Returns:
            The provider config dict, or None if not found.
        """
        data = cls._read_raw()
        providers = data.get("provider", {})
        return providers.get(provider_id)

    @classmethod
    def list_provider_ids(cls) -> list[str]:
        """Return all provider IDs present in flocks.json."""
        data = cls._read_raw()
        providers = data.get("provider", {})
        return list(providers.keys())
    
    @classmethod
    def get_all_providers(cls) -> Dict[str, Any]:
        """Get all provider configurations from flocks.json (raw, secrets unresolved).
        
        Returns:
            Dictionary of provider_id -> provider_config
        """
        data = cls._read_raw()
        return data.get("provider", {})

    @classmethod
    def add_provider(cls, provider_id: str, provider_config: Dict[str, Any]) -> None:
        """Add or replace a provider entry in flocks.json.

        Args:
            provider_id: e.g. "anthropic", "custom-my-llm"
            provider_config: Full provider config dict with npm, options, models, etc.
        """
        data = cls._read_raw()
        if "provider" not in data:
            data["provider"] = {}
        data["provider"][provider_id] = provider_config
        cls._write_raw(data)
        log.info("config_writer.provider_added", {"provider_id": provider_id})

    @classmethod
    def remove_provider(cls, provider_id: str) -> bool:
        """Remove a provider entry from flocks.json.

        Returns:
            True if the provider existed and was removed.
        """
        data = cls._read_raw()
        providers = data.get("provider", {})
        if provider_id not in providers:
            return False
        del providers[provider_id]
        data["provider"] = providers
        cls._write_raw(data)
        log.info("config_writer.provider_removed", {"provider_id": provider_id})
        return True

    @classmethod
    def update_provider_field(
        cls,
        provider_id: str,
        field_path: str,
        value: Any,
    ) -> bool:
        """Update a nested field inside a provider entry.

        Args:
            provider_id: Provider ID
            field_path: Dot-separated path, e.g. "options.baseURL"
            value: New value

        Returns:
            True if updated successfully.
        """
        data = cls._read_raw()
        providers = data.get("provider", {})
        pconfig = providers.get(provider_id)
        if pconfig is None:
            return False

        keys = field_path.split(".")
        target = pconfig
        for key in keys[:-1]:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value

        data["provider"][provider_id] = pconfig
        cls._write_raw(data)
        log.info("config_writer.field_updated", {
            "provider_id": provider_id,
            "field": field_path,
        })
        return True

    # ------------------------------------------------------------------
    # Model-level operations inside a provider
    # ------------------------------------------------------------------

    @classmethod
    def add_model(
        cls,
        provider_id: str,
        model_id: str,
        model_config: Dict[str, Any],
    ) -> bool:
        """Add or update a model entry inside a provider's models dict.

        Returns:
            True if provider exists and model was added.
        """
        data = cls._read_raw()
        pconfig = data.get("provider", {}).get(provider_id)
        if pconfig is None:
            return False

        if "models" not in pconfig:
            pconfig["models"] = {}
        pconfig["models"][model_id] = model_config
        data["provider"][provider_id] = pconfig
        cls._write_raw(data)
        log.info("config_writer.model_added", {
            "provider_id": provider_id,
            "model_id": model_id,
        })
        return True

    @classmethod
    def remove_model(cls, provider_id: str, model_id: str) -> bool:
        """Remove a model entry from a provider's models dict.

        Returns:
            True if the model existed and was removed.
        """
        data = cls._read_raw()
        pconfig = data.get("provider", {}).get(provider_id)
        if pconfig is None:
            return False

        models = pconfig.get("models", {})
        if model_id not in models:
            return False

        del models[model_id]
        pconfig["models"] = models
        data["provider"][provider_id] = pconfig
        cls._write_raw(data)
        log.info("config_writer.model_removed", {
            "provider_id": provider_id,
            "model_id": model_id,
        })
        return True

    # ------------------------------------------------------------------
    # Model settings (model_settings section)
    # ------------------------------------------------------------------

    @classmethod
    def get_model_setting(cls, provider_id: str, model_id: str) -> Optional[Dict[str, Any]]:
        """Get setting for a specific model from flocks.json model_settings section."""
        data = cls._read_raw()
        settings = data.get("model_settings", {})
        key = f"{provider_id}/{model_id}"
        return settings.get(key)

    @classmethod
    def set_model_setting(
        cls,
        provider_id: str,
        model_id: str,
        setting: Dict[str, Any],
    ) -> None:
        """Set or update model setting in flocks.json model_settings section."""
        data = cls._read_raw()
        if "model_settings" not in data:
            data["model_settings"] = {}
        key = f"{provider_id}/{model_id}"
        existing = data["model_settings"].get(key, {})
        existing.update(setting)
        data["model_settings"][key] = existing
        cls._write_raw(data)
        log.info("config_writer.model_setting_updated", {
            "provider_id": provider_id,
            "model_id": model_id,
        })

    @classmethod
    def remove_model_setting(cls, provider_id: str, model_id: str) -> bool:
        """Remove a model setting from flocks.json."""
        data = cls._read_raw()
        settings = data.get("model_settings", {})
        key = f"{provider_id}/{model_id}"
        if key not in settings:
            return False
        del settings[key]
        data["model_settings"] = settings
        cls._write_raw(data)
        return True

    @classmethod
    def get_all_model_settings(cls) -> Dict[str, Dict[str, Any]]:
        """Get all model settings. Returns dict keyed by 'provider_id/model_id'."""
        data = cls._read_raw()
        return data.get("model_settings", {})

    # ------------------------------------------------------------------
    # Default models (default_models section)
    # ------------------------------------------------------------------

    @classmethod
    def get_default_model(cls, model_type: str) -> Optional[Dict[str, Any]]:
        """Get default model config for a given model type."""
        data = cls._read_raw()
        defaults = data.get("default_models", {})
        return defaults.get(model_type)

    @classmethod
    def set_default_model(
        cls,
        model_type: str,
        provider_id: str,
        model_id: str,
    ) -> None:
        """Set default model for a given model type."""
        data = cls._read_raw()
        if "default_models" not in data:
            data["default_models"] = {}
        data["default_models"][model_type] = {
            "provider_id": provider_id,
            "model_id": model_id,
        }
        cls._write_raw(data)
        log.info("config_writer.default_model_set", {
            "model_type": model_type,
            "provider_id": provider_id,
            "model_id": model_id,
        })

    @classmethod
    def delete_default_model(cls, model_type: str) -> bool:
        """Delete default model for a given model type."""
        data = cls._read_raw()
        defaults = data.get("default_models", {})
        if model_type not in defaults:
            return False
        del defaults[model_type]
        data["default_models"] = defaults
        cls._write_raw(data)
        return True

    @classmethod
    def get_all_default_models(cls) -> Dict[str, Dict[str, Any]]:
        """Get all default model configs."""
        data = cls._read_raw()
        return data.get("default_models", {})

    # ------------------------------------------------------------------
    # MCP server CRUD (mcp section)
    # ------------------------------------------------------------------

    @classmethod
    def get_mcp_server(cls, name: str) -> Optional[Dict[str, Any]]:
        """Get a single MCP server config from flocks.json (raw, secrets unresolved)."""
        data = cls._read_raw()
        return data.get("mcp", {}).get(name)

    @classmethod
    def list_mcp_servers(cls) -> Dict[str, Any]:
        """Return all MCP server configs from flocks.json (raw, secrets unresolved)."""
        data = cls._read_raw()
        return data.get("mcp", {})

    @classmethod
    def add_mcp_server(cls, name: str, server_config: Dict[str, Any]) -> None:
        """Add or replace an MCP server entry in flocks.json.

        Args:
            name: MCP server name (key in mcp section)
            server_config: Full server config dict (McpLocalConfig or McpRemoteConfig)
        """
        data = cls._read_raw()
        if "mcp" not in data:
            data["mcp"] = {}
        data["mcp"][name] = server_config
        cls._write_raw(data)
        log.info("config_writer.mcp_server_added", {"name": name})

    @classmethod
    def remove_mcp_server(cls, name: str) -> bool:
        """Remove an MCP server entry from flocks.json.

        Returns:
            True if the server existed and was removed.
        """
        data = cls._read_raw()
        mcp = data.get("mcp", {})
        if name not in mcp:
            return False
        del mcp[name]
        data["mcp"] = mcp
        cls._write_raw(data)
        log.info("config_writer.mcp_server_removed", {"name": name})
        return True

    @classmethod
    def update_mcp_server_field(cls, name: str, field: str, value: Any) -> bool:
        """Update a single field in an MCP server's config.

        Returns:
            True if the server existed and the field was updated.
        """
        data = cls._read_raw()
        mcp = data.get("mcp", {})
        if name not in mcp:
            return False
        mcp[name][field] = value
        data["mcp"] = mcp
        cls._write_raw(data)
        log.info("config_writer.mcp_server_field_updated", {"name": name, "field": field})
        return True

    # ------------------------------------------------------------------
    # API Services CRUD  (api_services section)
    # ------------------------------------------------------------------

    @classmethod
    def get_api_service_raw(cls, service_id: str) -> Optional[Dict[str, Any]]:
        """Read a single api_services entry (raw, secrets unresolved).

        Returns:
            The service config dict, or None if not found.
        """
        data = cls._read_raw()
        return data.get("api_services", {}).get(service_id)

    @classmethod
    def list_api_services_raw(cls) -> Dict[str, Any]:
        """Return all raw api_services entries from flocks.json."""
        data = cls._read_raw()
        return data.get("api_services", {})

    @classmethod
    def set_api_service(cls, service_id: str, service_config: Dict[str, Any]) -> None:
        """Create or replace an api_services entry in flocks.json.

        Uses the same ``{secret:xxx}`` reference format as LLM providers::

            ConfigWriter.set_api_service(
                "threatbook_api",
                {"apiKey": "{secret:threatbook_api_key}"},
            )

        Args:
            service_id: e.g. "threatbook_api", "virustotal"
            service_config: Config dict; use ``"apiKey": "{secret:<id>}"`` to
                            reference a secret stored in .secret.json.
        """
        data = cls._read_raw()
        if "api_services" not in data:
            data["api_services"] = {}
        data["api_services"][service_id] = service_config
        cls._write_raw(data)
        log.info("config_writer.api_service_set", {"service_id": service_id})

    @classmethod
    def remove_api_service(cls, service_id: str) -> bool:
        """Remove an api_services entry from flocks.json.

        Returns:
            True if the entry existed and was removed.
        """
        data = cls._read_raw()
        services = data.get("api_services", {})
        if service_id not in services:
            return False
        del services[service_id]
        data["api_services"] = services
        cls._write_raw(data)
        log.info("config_writer.api_service_removed", {"service_id": service_id})
        return True

    # ------------------------------------------------------------------
    # Tool settings  (tool_settings section)
    # ------------------------------------------------------------------
    #
    # User-level overlay for per-tool settings (currently: ``enabled``).
    # The section mirrors ``model_settings`` for naming consistency —
    # both are flat maps keyed by the entity's unique id.
    #
    # Why this exists:  YAML plugin tool files under
    # ``<project>/.flocks/plugins/tools/`` are tracked by git and may be
    # overwritten on upgrade.  Writing a user toggle (e.g. enable/disable)
    # back into the YAML pollutes git diffs and breaks upgrades.  We keep
    # the YAML as "factory defaults" and store the user's choice in
    # ``flocks.json`` instead.
    #
    # The same overlay applies uniformly to user-level YAML files under
    # ``~/.flocks/plugins/tools/`` so that UI behaviour is consistent
    # regardless of where the YAML lives.

    @classmethod
    def list_tool_settings(cls) -> Dict[str, Dict[str, Any]]:
        """Return all raw tool_settings entries from flocks.json."""
        data = cls._read_raw()
        settings = data.get("tool_settings", {})
        return settings if isinstance(settings, dict) else {}

    @classmethod
    def get_tool_setting(cls, tool_name: str) -> Optional[Dict[str, Any]]:
        """Read a single tool_settings entry, or None if not set."""
        settings = cls.list_tool_settings()
        entry = settings.get(tool_name)
        return entry if isinstance(entry, dict) else None

    @classmethod
    def set_tool_setting(cls, tool_name: str, setting: Dict[str, Any]) -> None:
        """Merge ``setting`` into the tool_settings[tool_name] entry.

        Existing keys not present in ``setting`` are preserved so callers
        can update a single field (e.g. ``{"enabled": False}``) without
        wiping other overlay fields that may be added later.
        """
        if not tool_name:
            raise ValueError("tool_name must be a non-empty string")
        data = cls._read_raw()
        settings = data.get("tool_settings")
        if not isinstance(settings, dict):
            settings = {}
        existing = settings.get(tool_name)
        if not isinstance(existing, dict):
            existing = {}
        merged = {**existing, **(setting or {})}
        settings[tool_name] = merged
        data["tool_settings"] = settings
        cls._write_raw(data)
        log.info("config_writer.tool_setting_set", {
            "tool": tool_name,
            "fields": sorted(merged.keys()),
        })

    @classmethod
    def delete_tool_setting(cls, tool_name: str) -> bool:
        """Remove the tool_settings[tool_name] entry.

        Pops the whole ``tool_settings`` key when the last entry is
        removed so flocks.json doesn't accumulate empty container objects
        as users toggle their last customised tool back to default.

        Returns True if an entry existed and was removed.
        """
        data = cls._read_raw()
        settings = data.get("tool_settings")
        if not isinstance(settings, dict) or tool_name not in settings:
            return False
        del settings[tool_name]
        if settings:
            data["tool_settings"] = settings
        else:
            data.pop("tool_settings", None)
        cls._write_raw(data)
        log.info("config_writer.tool_setting_removed", {"tool": tool_name})
        return True

    # ------------------------------------------------------------------
    # Default model cleanup helpers
    # ------------------------------------------------------------------

    @classmethod
    def clear_default_models_for_provider(cls, provider_id: str) -> List[str]:
        """Remove all default model entries that reference a given provider.

        Called automatically when a provider is deleted so no stale default
        model references remain in flocks.json.

        Args:
            provider_id: The provider being removed.

        Returns:
            List of model_type keys that were cleared (e.g. ["llm"]).
        """
        all_defaults = cls.get_all_default_models()
        cleared: List[str] = []
        for model_type, cfg in all_defaults.items():
            if cfg.get("provider_id") == provider_id:
                cls.delete_default_model(model_type)
                cleared.append(model_type)
                log.info("config_writer.default_model_cleared", {
                    "model_type": model_type,
                    "reason": "provider_deleted",
                    "provider_id": provider_id,
                })
        return cleared

    @classmethod
    def clear_default_models_for_model(cls, provider_id: str, model_id: str) -> List[str]:
        """Remove all default model entries that reference a specific model.

        Called automatically when a model definition is deleted.

        Args:
            provider_id: Provider that owns the model.
            model_id: The model being removed.

        Returns:
            List of model_type keys that were cleared (e.g. ["llm"]).
        """
        all_defaults = cls.get_all_default_models()
        cleared: List[str] = []
        for model_type, cfg in all_defaults.items():
            if cfg.get("provider_id") == provider_id and cfg.get("model_id") == model_id:
                cls.delete_default_model(model_type)
                cleared.append(model_type)
                log.info("config_writer.default_model_cleared", {
                    "model_type": model_type,
                    "reason": "model_deleted",
                    "provider_id": provider_id,
                    "model_id": model_id,
                })
        return cleared

    # ------------------------------------------------------------------
    # Helper: build a standard provider config dict
    # ------------------------------------------------------------------

    @classmethod
    def build_provider_config(
        cls,
        provider_id: str,
        *,
        npm: str = "@ai-sdk/openai-compatible",
        base_url: Optional[str] = None,
        models: Optional[Dict[str, Dict[str, Any]]] = None,
        extra_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a standard provider config dict for flocks.json.

        The apiKey field always uses {secret:<provider_id>_llm_key} reference.

        Args:
            provider_id: Provider identifier
            npm: NPM package name (for TUI compatibility)
            base_url: Base URL for the API
            models: Dict of model_id -> model config
            extra_options: Additional options to merge into provider options

        Returns:
            Provider config dict ready for add_provider()
        """
        options: Dict[str, Any] = {
            "apiKey": f"{{secret:{provider_id}_llm_key}}",
        }
        if base_url:
            options["baseURL"] = base_url
        if extra_options:
            options.update(extra_options)

        config: Dict[str, Any] = {
            "npm": npm,
            "options": options,
        }
        if models:
            config["models"] = models
        else:
            config["models"] = {}

        return config
