"""API-service plugin schema utilities.

Lives under ``flocks.tool`` because credential schemas and provider metadata
are part of the tool/plugin model — they describe *how* the agent integrates
with an external API, independent of any HTTP transport. The ``server.routes``
layer should depend on this module, not the other way around.
"""
from .api_service_schema import (
    APIServiceCredentialField,
    _build_api_service_credential_schema,
    _default_api_service_field_label,
    _extract_secret_id,
    _get_api_service_default_secret_id,
    _get_api_service_schema_field,
    _get_api_service_secret_candidates,
    _get_api_service_secret_field_names,
    _get_compound_secret_metadata,
    _load_api_service_metadata_data,
    _load_provider_yaml_metadata,
    _normalize_api_service_credential_field,
    _should_persist_secondary_secret,
)

__all__ = [
    "APIServiceCredentialField",
    "_build_api_service_credential_schema",
    "_default_api_service_field_label",
    "_extract_secret_id",
    "_get_api_service_default_secret_id",
    "_get_api_service_schema_field",
    "_get_api_service_secret_candidates",
    "_get_api_service_secret_field_names",
    "_get_compound_secret_metadata",
    "_load_api_service_metadata_data",
    "_load_provider_yaml_metadata",
    "_normalize_api_service_credential_field",
    "_should_persist_secondary_secret",
]
