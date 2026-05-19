"""
Simple secret management using flat KV JSON file

Stores all secrets in ~/.flocks/config/.secret.json as a flat {secret_id: secret_value} dict.
For MVP/development use only. For production, consider encryption.

Naming conventions for secret IDs:
    - LLM provider keys:  "{provider_id}_llm_key"  e.g. "anthropic_llm_key"
    - API service keys:   "{service_id}_api_key"   e.g. "virustotal_api_key"
    - MCP server keys:    "{server_name}_mcp_key"  e.g. "threatbook_mcp_key"

Usage:
    secrets = SecretManager()
    secrets.set("anthropic_llm_key", "sk-xxx")
    api_key = secrets.get("anthropic_llm_key")
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional

from flocks.config.config import Config
from flocks.utils.log import Log

log = Log.create(service="security.secrets")


class SecretManager:
    """
    Flat KV secret manager using plain JSON file

    Stores secrets in ~/.flocks/config/.secret.json with 600 permissions.
    Format: {"secret_id": "secret_value", ...}

    Naming conventions:
        LLM provider keys:  "{provider_id}_llm_key"  e.g. "anthropic_llm_key"
        API service keys:   "{service_id}_api_key"   e.g. "virustotal_api_key"
        MCP server keys:    "{server_name}_mcp_key"  e.g. "threatbook_mcp_key"

    Usage:
        secrets = SecretManager()
        secrets.set("anthropic_llm_key", "sk-xxx")
        api_key = secrets.get("anthropic_llm_key")
        secrets.delete("anthropic_llm_key")
    """

    def __init__(self, secret_file: Optional[Path] = None):
        """
        Initialize secret manager.

        Args:
            secret_file: Path to secret file. Defaults to ~/.flocks/config/.secret.json
                         (or the directory specified via FLOCKS_CONFIG_DIR).
        """
        if secret_file is None:
            secret_file = Config.get_secret_file()

        self.secret_file = secret_file
        self._ensure_secure()

    def _ensure_secure(self) -> None:
        """Ensure secret file exists with secure permissions."""
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.secret_file.exists():
            self.secret_file.write_text("{}")
            log.info("secrets.created", {"path": str(self.secret_file)})

        # Set secure permissions (600: owner read/write only)
        try:
            current_mode = self.secret_file.stat().st_mode & 0o777
            if current_mode != 0o600:
                self.secret_file.chmod(0o600)
                log.info("secrets.permissions_fixed", {
                    "path": str(self.secret_file),
                    "old_mode": oct(current_mode),
                    "new_mode": "0o600",
                })
        except Exception as e:
            log.warning("secrets.chmod_failed", {"error": str(e)})

    def _load(self) -> Dict[str, str]:
        """Load secrets from file."""
        try:
            if self.secret_file.exists():
                content = self.secret_file.read_text()
                if not content.strip():
                    return {}
                return json.loads(content)
            return {}
        except json.JSONDecodeError as e:
            log.error("secrets.load_failed", {
                "path": str(self.secret_file),
                "error": str(e),
            })
            return {}
        except Exception as e:
            log.error("secrets.load_error", {"error": str(e)})
            return {}

    def _save(self, data: Dict[str, str]) -> None:
        """Save secrets to file."""
        try:
            self.secret_file.write_text(json.dumps(data, indent=2))
            # Ensure permissions are still secure after write
            self.secret_file.chmod(0o600)
            log.debug("secrets.saved", {"path": str(self.secret_file)})
        except Exception as e:
            log.error("secrets.save_failed", {"error": str(e)})
            raise

    def get(self, secret_id: str) -> Optional[str]:
        """
        Get secret value by ID.

        Args:
            secret_id: Secret identifier (e.g., "anthropic_llm_key")

        Returns:
            Secret value or None if not found
        """
        # Per-device credential context takes priority over global secrets.
        try:
            from flocks.tool.credential_context import get_secret_override
            override = get_secret_override(secret_id)
            if override is not None:
                return override
        except Exception:
            pass

        data = self._load()
        value = data.get(secret_id)
        if value:
            log.debug("secrets.get", {"secret_id": secret_id})
        return value

    def set(self, secret_id: str, value: str) -> None:
        """
        Set secret value.

        Args:
            secret_id: Secret identifier (e.g., "anthropic_llm_key")
            value: Secret value
        """
        data = self._load()
        data[secret_id] = value
        self._save(data)
        log.info("secrets.set", {"secret_id": secret_id})

    def delete(self, secret_id: str) -> bool:
        """
        Delete a secret.

        Args:
            secret_id: Secret identifier

        Returns:
            True if deleted, False if not found
        """
        data = self._load()
        if secret_id in data:
            del data[secret_id]
            self._save(data)
            log.info("secrets.deleted", {"secret_id": secret_id})
            return True
        return False

    def list(self) -> list[str]:
        """
        List all secret IDs.

        Returns:
            List of secret IDs
        """
        data = self._load()
        return list(data.keys())

    def has(self, secret_id: str) -> bool:
        """
        Check if secret exists.

        Args:
            secret_id: Secret identifier

        Returns:
            True if secret exists
        """
        data = self._load()
        return secret_id in data

    @staticmethod
    def mask(value: str, show_chars: int = 4) -> str:
        """
        Mask secret for display (e.g., sk-***abc123).

        Args:
            value: Secret value to mask
            show_chars: Number of chars to show at end

        Returns:
            Masked string
        """
        if not value:
            return ""

        if len(value) <= show_chars + 3:
            return "***"

        prefix = value[:3] if value.startswith(("sk-", "pk-")) else value[:2]
        suffix = value[-show_chars:]
        return f"{prefix}***{suffix}"


# Singleton instance
_secret_manager: Optional[SecretManager] = None


def get_secret_manager() -> SecretManager:
    """Get singleton secret manager instance."""
    global _secret_manager
    if _secret_manager is None:
        _secret_manager = SecretManager()
    return _secret_manager
