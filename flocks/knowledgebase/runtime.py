"""Optional direct knowledgebase client; startup never contacts the engine."""

from __future__ import annotations

from typing import Any

from flocks.config.config import Config
from flocks.security.secrets import get_secret_manager

from .client import Connection, KnowledgebaseClient, validate_connection_options
from .flocksrag import FlocksragAdapter

_client: KnowledgebaseClient | None = None


def get_client() -> KnowledgebaseClient | None:
    return _client


async def start_from_config() -> dict[str, Any]:
    """Resolve the deployment connection once, without probing the remote engine."""
    global _client
    if _client is not None:
        return {"status": "already_started"}
    try:
        config = await Config.get()
        services = getattr(config, "api_services", None)
        if services is None:
            return {"status": "disabled"}
        if not isinstance(services, dict):
            raise ValueError("Invalid API service configuration")
        settings = services.get("knowledgebase")
        if settings is None:
            return {"status": "disabled"}
        if not isinstance(settings, dict) or type(settings.get("enabled", False)) is not bool:
            raise ValueError("Invalid knowledgebase configuration")
        if not settings.get("enabled", False):
            return {"status": "disabled"}

        provider = settings.get("provider")
        if provider == "flocksrag" and not FlocksragAdapter.implemented:
            return {"status": "disabled", "reason": "not_implemented"}
        if provider != "ragflow":
            raise ValueError("Unsupported knowledgebase provider")

        base_url = settings.get("base_url")
        credential_id = settings.get("credential_id")
        timeout = settings.get("timeout_seconds", 30.0)
        upload_limit = settings.get("max_upload_bytes", 32 * 1024 * 1024)
        if (
            not isinstance(base_url, str)
            or not isinstance(credential_id, str)
            or not credential_id
            or credential_id != credential_id.strip()
            or "{" in credential_id
            or "}" in credential_id
            or type(timeout) not in {int, float}
        ):
            raise ValueError("Knowledgebase connection is incomplete")
        validate_connection_options(base_url, timeout, upload_limit)

        # Resolve only the selected credential in the service startup context.
        token = get_secret_manager().get(credential_id)
        connection = Connection(base_url, token, timeout, upload_limit)
        client = KnowledgebaseClient(connection)
    except Exception:
        return {"status": "disabled", "reason": "incomplete"}
    _client = client
    return {"status": "configured"}


async def stop() -> None:
    global _client
    client = _client
    _client = None
    if client is not None:
        await client.close()
