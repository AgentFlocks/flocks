"""
Security utilities for Flocks

Provides secret management and credential handling.

Secret resolution:
- In ~/.flocks/config/flocks.json, use {secret:SECRET_ID} to reference .secret.json values
- Use {env:VAR_NAME} to reference environment variables (handled by Config)
- Secrets are stored in ~/.flocks/config/.secret.json as flat {secret_id: secret_value} KV pairs
"""

import os
import re
from typing import Any, Optional

from .secrets import SecretManager, get_secret_manager

__all__ = [
    "SecretManager",
    "get_secret_manager",
    "resolve_secret_value",
    "resolve_secret_refs",
    "resolve_value",
]


_FOFA_COMPOUND_SECRET_PATTERN = re.compile(
    r"^\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\s*:\s*(\S.+?)\s*$"
)


def _resolve_fofa_derived_secret(secret_id: str, secrets: SecretManager) -> Optional[str]:
    if secret_id not in {"fofa_email", "fofa_api_key"}:
        return None

    combined = secrets.get("fofa_key")
    if not isinstance(combined, str):
        return None

    match = _FOFA_COMPOUND_SECRET_PATTERN.match(combined)
    if not match:
        return None

    email = match.group(1).strip()
    api_key = match.group(2).strip()
    if not email or not api_key:
        return None

    if secret_id == "fofa_email":
        return email
    return api_key


def resolve_secret_value(secret_id: str, secrets: Optional[SecretManager] = None) -> Optional[str]:
    """Resolve a secret by id, including read-only legacy fallbacks."""
    if secrets is None:
        secrets = get_secret_manager()

    value = secrets.get(secret_id)
    if value is not None:
        return value

    if secret_id.endswith("_llm_key"):
        provider_id = secret_id.removesuffix("_llm_key")
        legacy_value = secrets.get(f"{provider_id}_api_key")
        if legacy_value is not None:
            return legacy_value

    return _resolve_fofa_derived_secret(secret_id, secrets)


def resolve_secret_refs(text: str, secrets: Optional[SecretManager] = None) -> str:
    """
    Replace {secret:SECRET_ID} references in text with values from .secret.json.

    Works alongside Config.replace_env_vars() which handles {env:VAR}.

    Args:
        text: Text with {secret:xxx} placeholders
        secrets: SecretManager instance (uses singleton if not provided)

    Returns:
        Text with secrets substituted
    """
    if secrets is None:
        secrets = get_secret_manager()

    def replacer(match: re.Match) -> str:
        secret_id = match.group(1)
        value = resolve_secret_value(secret_id, secrets)
        if value is not None:
            return value
        # Return empty string if secret not found (same as {env:VAR} behavior)
        return ""

    return re.sub(r'\{secret:([^}]+)\}', replacer, text)


def resolve_value(value: Any, secrets: Optional[SecretManager] = None) -> Any:
    """
    Resolve a config value that may contain secret or env references.

    Handles:
    - String with {secret:xxx}: resolves from .secret.json
    - String with {env:xxx}: resolves from environment
    - Plain string/number/bool: returns as-is
    - Dict: recursively resolves values
    - List: recursively resolves items

    Args:
        value: Config value to resolve
        secrets: SecretManager instance (uses singleton if not provided)

    Returns:
        Resolved value
    """
    if secrets is None:
        secrets = get_secret_manager()

    if isinstance(value, str):
        # Resolve {secret:xxx} references
        if "{secret:" in value:
            value = resolve_secret_refs(value, secrets)
        # Resolve {env:xxx} references
        if "{env:" in value:
            value = re.sub(
                r'\{env:([^}]+)\}',
                lambda m: os.getenv(m.group(1), ""),
                value,
            )
        return value

    if isinstance(value, dict):
        return {k: resolve_value(v, secrets) for k, v in value.items()}

    if isinstance(value, list):
        return [resolve_value(v, secrets) for v in value]

    return value
