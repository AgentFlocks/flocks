"""Optional knowledgebase client. Importing this module does not connect anywhere."""

from __future__ import annotations

import os
from typing import Any

from .client import Connection, KnowledgebaseClient

_client: KnowledgebaseClient | None = None


def get_client() -> KnowledgebaseClient | None:
    return _client


def _connection_from_environment(environment: dict[str, str]) -> Connection | None:
    base_url = environment.get("FLOCKS_KNOWLEDGEBASE_URL", "")
    token = environment.get("FLOCKS_KNOWLEDGEBASE_API_TOKEN", "")
    if not base_url and not token:
        return None
    if not base_url or not token:
        raise ValueError("Knowledgebase connection is incomplete")
    return Connection(base_url=base_url, api_token=token)


async def start_from_environment(*, environment: dict[str, str] | None = None) -> dict[str, Any]:
    """Record an explicit connection. This does not call RAGFlow or the knowledge service."""
    global _client
    if _client is not None:
        return {"status": "already_started"}
    try:
        connection = _connection_from_environment(dict(os.environ) if environment is None else environment)
    except ValueError:
        return {"status": "disabled", "reason": "incomplete"}
    if connection is None:
        return {"status": "disabled"}
    _client = KnowledgebaseClient(connection)
    return {"status": "configured"}


async def stop() -> None:
    global _client
    client = _client
    _client = None
    if client is not None:
        await client.close()
