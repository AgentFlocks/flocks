"""Credential schema & metadata helpers for API-service plugins.

These functions are pure data-layer utilities — no FastAPI / HTTP coupling —
shared between the provider routes (``flocks.server.routes.provider``) and the
device integration domain (``flocks.tool.device``).

The leading-underscore names are preserved here for git-history continuity with
the original definitions in ``flocks.server.routes.provider``; callers that
treat them as the project's public schema API should ignore the visual hint.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel

from flocks.config.api_versioning import discover_api_service_descriptors
from flocks.config.config_writer import ConfigWriter
from flocks.tool.tool_loader import extract_provider_version
from flocks.utils.log import Log

log = Log.create(service="tool.api_service.schema")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class APIServiceCredentialField(BaseModel):
    """Credential field definition exposed to the WebUI."""

    key: str
    label: str
    description: Optional[str] = None
    storage: str = "config"
    sensitive: bool = False
    required: bool = False
    input_type: str = "text"
    config_key: str
    secret_id: Optional[str] = None
    default_value: Optional[str] = None
    internal: bool = False


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _default_api_service_field_label(field_key: str) -> str:
    labels = {
        "api_key": "API Key",
        "base_url": "Base URL",
        "secret": "Secret",
        "username": "Username",
        "password": "Password",
    }
    return labels.get(field_key, field_key.replace("_", " ").title())


def _extract_secret_id(secret_ref: Any) -> Optional[str]:
    if isinstance(secret_ref, str) and secret_ref.startswith("{secret:") and secret_ref.endswith("}"):
        return secret_ref[len("{secret:"):-1]
    return None


def _get_compound_secret_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    compound_secret = metadata.get("compound_secret")
    if isinstance(compound_secret, dict):
        return compound_secret
    return {}


def _should_persist_secondary_secret(metadata: Optional[Dict[str, Any]]) -> bool:
    compound_secret = _get_compound_secret_metadata(metadata)
    if compound_secret.get("persist_secondary_secret") is False:
        return False
    return True


# ---------------------------------------------------------------------------
# Metadata loading
# ---------------------------------------------------------------------------

#: ``flocks/tool/security/metadata`` — kept here for legacy plugin JSON files.
#: Resolved as the sibling ``security/metadata`` directory under ``flocks/tool/``.
_LEGACY_METADATA_DIR = Path(__file__).resolve().parent.parent / "security" / "metadata"


def _load_provider_yaml_metadata(provider_id: str) -> Optional[Dict[str, Any]]:
    """Load ``_provider.yaml`` metadata for an API plugin.

    ``provider_id`` may be either the storage_key (the post-versioning
    canonical id, e.g. ``tdp_api_v3_3_10``) or the bare unversioned
    ``service_id``. Discovery is delegated to
    :func:`discover_api_service_descriptors`, so plugin directories whose name
    does not match ``provider_id`` still resolve correctly.
    """
    try:
        descriptor = next(
            (
                d
                for d in discover_api_service_descriptors()
                if provider_id in (d.storage_key, d.service_id)
            ),
            None,
        )
        if descriptor is None:
            descriptor = next(
                (
                    d
                    for d in discover_api_service_descriptors(refresh=True)
                    if provider_id in (d.storage_key, d.service_id)
                ),
                None,
            )
        if descriptor is None:
            return None

        api_dir = descriptor.provider_yaml.parent
        prov = yaml.safe_load(descriptor.provider_yaml.read_text(encoding="utf-8"))
        if not isinstance(prov, dict):
            return None

        tool_apis: List[Dict[str, Any]] = []
        for item in sorted(api_dir.iterdir()):
            if item.suffix not in (".yaml", ".yml") or item.name.startswith("_"):
                continue
            try:
                tool_data = yaml.safe_load(item.read_text(encoding="utf-8"))
            except Exception as e:
                log.debug("schema.yaml_metadata.tool_read_failed", {
                    "provider_id": provider_id, "file": item.name, "error": str(e),
                })
                continue
            if isinstance(tool_data, dict) and tool_data.get("name"):
                tool_apis.append({
                    "name": tool_data["name"],
                    "description": tool_data.get("description", ""),
                })

        return {
            "name": prov.get("name", provider_id),
            "service_id": prov.get("service_id", provider_id),
            "version": extract_provider_version(prov),
            "description": prov.get("description"),
            "description_cn": prov.get("description_cn"),
            "docs_url": prov.get("docs_url"),
            "auth": prov.get("auth"),
            "credential_fields": prov.get("credential_fields"),
            "defaults": prov.get("defaults", {}),
            "apis": tool_apis or None,
            "integration_type": prov.get("integration_type"),
            "vendor": prov.get("vendor"),
        }
    except Exception as e:
        log.debug("schema.yaml_metadata.load_failed", {"provider_id": provider_id, "error": str(e)})
        return None


def _load_api_service_metadata_data(provider_id: str) -> Optional[Dict[str, Any]]:
    """Load raw API service metadata from config, metadata JSON, or YAML provider."""
    merged: Dict[str, Any] = {}

    config_data = ConfigWriter.get_api_service_raw(provider_id)
    if isinstance(config_data, dict):
        merged.update(config_data)

    meta_file = _LEGACY_METADATA_DIR / f"{provider_id}.json"
    if meta_file.is_file():
        with open(meta_file, "r", encoding="utf-8") as f:
            metadata_data = json.load(f)
        if isinstance(metadata_data, dict):
            merged = {**metadata_data, **merged}

    yaml_data = _load_provider_yaml_metadata(provider_id)
    if isinstance(yaml_data, dict):
        merged = {**yaml_data, **merged}

    return merged or None


# ---------------------------------------------------------------------------
# Credential field normalization & schema building
# ---------------------------------------------------------------------------

def _normalize_api_service_credential_field(
    provider_id: str,
    raw_field: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[APIServiceCredentialField]:
    if not isinstance(raw_field, dict):
        return None

    key = raw_field.get("key")
    if not isinstance(key, str) or not key.strip():
        return None
    key = key.strip()

    storage = raw_field.get("storage")
    if storage not in {"config", "secret"}:
        storage = "secret" if key in {"api_key", "secret", "password", "token", "client_secret"} else "config"

    config_key = raw_field.get("config_key")
    if not isinstance(config_key, str) or not config_key.strip():
        config_key = "apiKey" if key == "api_key" else key

    label = raw_field.get("label")
    if not isinstance(label, str) or not label.strip():
        label = _default_api_service_field_label(key)

    input_type = raw_field.get("input_type")
    if input_type not in {"text", "password", "url"}:
        if storage == "secret":
            input_type = "password"
        elif key.endswith("url"):
            input_type = "url"
        else:
            input_type = "text"

    sensitive = raw_field.get("sensitive")
    if sensitive is None:
        sensitive = storage == "secret"
    else:
        sensitive = bool(sensitive)

    secret_id = raw_field.get("secret_id")
    if storage == "secret":
        if not isinstance(secret_id, str) or not secret_id.strip():
            secret_id = _get_api_service_default_secret_id(provider_id, field_name=key)
        else:
            secret_id = secret_id.strip()
    else:
        secret_id = None

    default_value = raw_field.get("default_value")
    if default_value is None:
        default_value = raw_field.get("default")
    if default_value is None and key == "base_url":
        defaults = (metadata or {}).get("defaults", {})
        if isinstance(defaults, dict):
            default_value = defaults.get("base_url")
        if default_value is None:
            default_value = (metadata or {}).get("base_url")
    if default_value is not None and not isinstance(default_value, str):
        default_value = str(default_value)

    description = raw_field.get("description")
    if description is not None and not isinstance(description, str):
        description = str(description)

    internal = bool(raw_field.get("internal") or raw_field.get("hidden"))

    return APIServiceCredentialField(
        key=key,
        label=label,
        description=description,
        storage=storage,
        sensitive=sensitive,
        required=bool(raw_field.get("required", False)),
        input_type=input_type,
        config_key=config_key,
        secret_id=secret_id,
        default_value=default_value,
        internal=internal,
    )


def _build_api_service_credential_schema(
    provider_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[APIServiceCredentialField]:
    metadata = metadata or _load_api_service_metadata_data(provider_id) or {}
    raw_fields = metadata.get("credential_fields")
    normalized_fields: List[APIServiceCredentialField] = []

    if isinstance(raw_fields, list):
        for raw_field in raw_fields:
            field = _normalize_api_service_credential_field(provider_id, raw_field, metadata)
            if field:
                normalized_fields.append(field)
    else:
        auth = metadata.get("authentication") or metadata.get("auth")
        if isinstance(auth, dict):
            api_secret_id = auth.get("secret_key") or auth.get("api_key_secret") or auth.get("secret")
            if api_secret_id:
                normalized_fields.append(
                    APIServiceCredentialField(
                        key="api_key",
                        label="API Key",
                        storage="secret",
                        sensitive=True,
                        input_type="password",
                        config_key="apiKey",
                        secret_id=str(api_secret_id).strip(),
                    )
                )
            secondary_secret_id = auth.get("secret_secret")
            if secondary_secret_id and _should_persist_secondary_secret(metadata):
                normalized_fields.append(
                    APIServiceCredentialField(
                        key="secret",
                        label="Secret",
                        storage="secret",
                        sensitive=True,
                        input_type="password",
                        config_key="secret",
                        secret_id=str(secondary_secret_id).strip(),
                    )
                )

        defaults = metadata.get("defaults", {})
        base_url = None
        if isinstance(defaults, dict):
            base_url = defaults.get("base_url")
        if base_url or metadata.get("base_url"):
            normalized_fields.append(
                APIServiceCredentialField(
                    key="base_url",
                    label="Base URL",
                    storage="config",
                    sensitive=False,
                    input_type="url",
                    config_key="base_url",
                    default_value=str(base_url or metadata.get("base_url")),
                )
            )

    deduped: List[APIServiceCredentialField] = []
    seen_keys: set[str] = set()
    for field in normalized_fields:
        if field.key in seen_keys:
            continue
        deduped.append(field)
        seen_keys.add(field.key)
    return deduped


def _get_api_service_schema_field(
    provider_id: str,
    field_name: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[APIServiceCredentialField]:
    for field in _build_api_service_credential_schema(provider_id, metadata):
        if field.key == field_name:
            return field
    return None


def _get_api_service_secret_field_names(
    provider_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[str]:
    return [
        field.key
        for field in _build_api_service_credential_schema(provider_id, metadata)
        if field.storage == "secret"
    ]


# ---------------------------------------------------------------------------
# Secret id resolution
# ---------------------------------------------------------------------------

def _get_api_service_default_secret_id(provider_id: str, field_name: str = "api_key") -> str:
    """Return the canonical secret id for an API service credential field."""
    metadata = _load_api_service_metadata_data(provider_id) or {}
    raw_fields = metadata.get("credential_fields")
    if isinstance(raw_fields, list):
        for raw_field in raw_fields:
            if not isinstance(raw_field, dict):
                continue
            if raw_field.get("key") != field_name:
                continue
            storage = raw_field.get("storage")
            if storage == "config":
                break
            secret_id = raw_field.get("secret_id")
            if isinstance(secret_id, str):
                secret_id = secret_id.strip()
                if secret_id:
                    return secret_id

    auth = metadata.get("authentication") or metadata.get("auth")
    if isinstance(auth, dict):
        if field_name == "api_key":
            secret_id = auth.get("secret_key") or auth.get("api_key_secret") or auth.get("secret")
        else:
            secret_id = auth.get("secret_secret") or auth.get(f"{field_name}_secret")
        if isinstance(secret_id, str):
            secret_id = secret_id.strip()
            if secret_id:
                return secret_id
    if field_name == "api_key":
        return f"{provider_id}_api_key"
    return f"{provider_id}_{field_name}"


def _get_api_service_secret_candidates(
    provider_id: str,
    raw_service: Optional[Dict[str, Any]] = None,
    field_name: str = "api_key",
) -> List[str]:
    """Return candidate secret ids for an API service, ordered by preference."""
    candidates: List[str] = []
    if raw_service is None:
        raw_service = ConfigWriter.get_api_service_raw(provider_id) or {}

    ref_key = "apiKey" if field_name == "api_key" else field_name
    secret_ref = raw_service.get(ref_key, "")
    extracted = _extract_secret_id(secret_ref)
    if extracted:
        candidates.append(extracted)

    candidates.append(_get_api_service_default_secret_id(provider_id, field_name=field_name))
    candidates.append(f"{provider_id}_{field_name}")

    deduped: List[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped
