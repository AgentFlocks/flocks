"""
Model management service (aggregation layer)

Aggregates model definitions from providers with user settings (enable/disable,
default parameters, default models) stored in flocks.json via ConfigWriter.

All static configuration is stored in flocks.json.
SQLite is only used for dynamic data (usage records).
"""

from typing import Any, Dict, List, Optional

from flocks.config.config_writer import ConfigWriter
from flocks.provider.provider import Provider
from flocks.provider.types import (
    DefaultModelConfig,
    ModelDefinition,
    ModelSetting,
    ModelType,
)
from flocks.utils.log import Log

log = Log.create(service="model_manager")


class ModelManager:
    """
    High-level model management service.

    Combines provider model definitions with user-level settings
    (enable/disable, default parameters, default model per type).

    All settings are persisted in flocks.json — no SQLite dependency.
    """

    # ==================== Model Listing ====================

    def list_models(
        self,
        provider_id: Optional[str] = None,
        model_type: Optional[ModelType] = None,
        enabled_only: bool = False,
    ) -> List[ModelDefinition]:
        """List all model definitions, optionally filtered.

        Merges provider-defined models with user settings from flocks.json.
        """
        Provider._ensure_initialized()

        definitions: List[ModelDefinition] = []

        if provider_id:
            provider = Provider.get(provider_id)
            if provider:
                definitions = provider.get_model_definitions()
        else:
            for pid in Provider.list_providers():
                p = Provider.get(pid)
                if p:
                    definitions.extend(p.get_model_definitions())

        # Filter by model_type
        if model_type:
            definitions = [d for d in definitions if d.model_type == model_type]

        # Apply user settings (enabled/disabled)
        if enabled_only:
            settings_map = ConfigWriter.get_all_model_settings()
            filtered = []
            for d in definitions:
                key = f"{d.provider_id}/{d.id}"
                setting = settings_map.get(key)
                if setting and not setting.get("enabled", True):
                    continue
                filtered.append(d)
            definitions = filtered

        return definitions

    def get_model(
        self, provider_id: str, model_id: str
    ) -> Optional[ModelDefinition]:
        """Get a single model definition."""
        provider = Provider.get(provider_id)
        if not provider:
            return None

        for d in provider.get_model_definitions():
            if d.id == model_id:
                return d
        return None

    # ==================== Model Settings ====================

    def get_setting(
        self, provider_id: str, model_id: str
    ) -> Optional[ModelSetting]:
        """Get user settings for a model from flocks.json."""
        raw = ConfigWriter.get_model_setting(provider_id, model_id)
        if not raw:
            return None

        return ModelSetting(
            provider_id=provider_id,
            model_id=model_id,
            enabled=raw.get("enabled", True),
            credential_id=raw.get("credential_id"),
            default_parameters=raw.get("default_parameters", {}),
        )

    def update_setting(
        self,
        provider_id: str,
        model_id: str,
        enabled: Optional[bool] = None,
        credential_id: Optional[str] = None,
        default_parameters: Optional[Dict[str, Any]] = None,
    ) -> ModelSetting:
        """Create or update model settings in flocks.json."""
        update: Dict[str, Any] = {}
        if enabled is not None:
            update["enabled"] = enabled
        if credential_id is not None:
            update["credential_id"] = credential_id
        if default_parameters is not None:
            update["default_parameters"] = default_parameters

        if update:
            ConfigWriter.set_model_setting(provider_id, model_id, update)

        log.info("model_setting.updated", {
            "provider_id": provider_id,
            "model_id": model_id,
        })

        # Return the current state
        return self.get_setting(provider_id, model_id) or ModelSetting(
            provider_id=provider_id,
            model_id=model_id,
        )

    def get_effective_default_parameters(
        self,
        provider_id: str,
        model_id: str,
    ) -> Dict[str, Any]:
        """Get inherited default parameters for a provider model."""
        return ConfigWriter.get_effective_model_default_parameters(
            provider_id,
            model_id,
        )

    # ==================== Default Models ====================

    def get_default_model(
        self, model_type: ModelType
    ) -> Optional[DefaultModelConfig]:
        """Get default model for a given type from flocks.json."""
        raw = ConfigWriter.get_default_model(model_type.value)
        if not raw:
            return None

        return DefaultModelConfig(
            model_type=model_type,
            provider_id=raw["provider_id"],
            model_id=raw["model_id"],
        )

    def set_default_model(
        self,
        model_type: ModelType,
        provider_id: str,
        model_id: str,
    ) -> DefaultModelConfig:
        """Set default model for a given type in flocks.json."""
        ConfigWriter.set_default_model(model_type.value, provider_id, model_id)

        log.info("default_model.set", {
            "model_type": model_type.value,
            "provider_id": provider_id,
            "model_id": model_id,
        })

        return DefaultModelConfig(
            model_type=model_type,
            provider_id=provider_id,
            model_id=model_id,
        )

    def get_all_defaults(self) -> List[DefaultModelConfig]:
        """Get all configured default models from flocks.json."""
        raw_defaults = ConfigWriter.get_all_default_models()
        results = []
        for model_type_str, cfg in raw_defaults.items():
            try:
                results.append(DefaultModelConfig(
                    model_type=ModelType(model_type_str),
                    provider_id=cfg["provider_id"],
                    model_id=cfg["model_id"],
                ))
            except (ValueError, KeyError):
                log.warning("default_model.invalid_entry", {
                    "model_type": model_type_str,
                })
                continue
        return results

    def delete_default_model(self, model_type: ModelType) -> bool:
        """Delete default model for a given type from flocks.json."""
        return ConfigWriter.delete_default_model(model_type.value)


# Singleton
_manager: Optional[ModelManager] = None


def get_model_manager() -> ModelManager:
    """Get singleton ModelManager instance."""
    global _manager
    if _manager is None:
        _manager = ModelManager()
    return _manager
