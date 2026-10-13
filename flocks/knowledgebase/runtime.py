"""Optional direct knowledgebase client; startup never contacts the engine."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from flocks.config.config import Config
from flocks.security.secrets import get_secret_manager

from .client import Connection, KnowledgebaseClient, validate_connection_options
from .errors import KnowledgebaseError
from .flocksrag import FlocksragAdapter


@dataclass
class _Generation:
    client: KnowledgebaseClient
    leases: int = 0
    retired: bool = False
    closing: bool = False


_current: _Generation | None = None
_stopping = False
_epoch = 0
_closing_tasks: set[asyncio.Task] = set()


def get_client() -> KnowledgebaseClient | None:
    """Snapshot for status only. Operations must hold a lease."""
    return _current.client if _current is not None else None


async def close_client(client: KnowledgebaseClient) -> None:
    """Cleanup must not turn an already committed save into a failure."""
    try:
        await client.close()
    except Exception:
        # Do not expose credentials or upstream exception text.
        logging.getLogger(__name__).warning("Knowledgebase client cleanup failed.")


def _close_if_unused(generation: _Generation) -> asyncio.Task | None:
    if not generation.retired or generation.leases or generation.closing:
        return None
    generation.closing = True
    return _schedule_close(generation.client)


def _schedule_close(client: KnowledgebaseClient) -> asyncio.Task:
    task = asyncio.create_task(close_client(client))
    _closing_tasks.add(task)
    task.add_done_callback(_closing_tasks.discard)
    return task


async def discard_client(client: KnowledgebaseClient) -> None:
    await asyncio.shield(_schedule_close(client))


@asynccontextmanager
async def lease_client():
    """Pin one generation through all awaits, including permission and file I/O."""
    generation = _current
    if generation is not None:
        generation.leases += 1
    try:
        yield generation.client if generation is not None else None
    finally:
        if generation is not None:
            generation.leases -= 1
            task = _close_if_unused(generation)
            if task is not None:
                # The retained task keeps running even if cleanup is cancelled again.
                await asyncio.shield(task)


def publication_epoch() -> int:
    ensure_publishable(_epoch)
    return _epoch


def ensure_publishable(epoch: int) -> None:
    if _stopping or epoch != _epoch:
        raise KnowledgebaseError(503, "knowledgebase_stopping", "Knowledgebase is shutting down.")


def publish(client: KnowledgebaseClient, epoch: int) -> asyncio.Task | None:
    """No await: callers can commit disk state and publish under the same lock."""
    global _current
    ensure_publishable(epoch)
    previous = _current
    _current = _Generation(client)
    if previous is not None:
        previous.retired = True
        return _close_if_unused(previous)
    return None


async def start_from_config() -> dict[str, Any]:
    """Resolve the deployment connection once, without probing the remote engine."""
    global _stopping
    if _current is not None:
        return {"status": "already_started"}
    _stopping = False
    epoch = _epoch
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
    if _stopping or epoch != _epoch or _current is not None:
        await discard_client(client)
        return {"status": "disabled"}
    publish(client, epoch)
    return {"status": "configured"}


async def stop() -> None:
    global _current, _stopping, _epoch
    _stopping = True
    _epoch += 1
    previous, _current = _current, None
    if previous is not None:
        previous.retired = True
        _close_if_unused(previous)
    # Active leases retire on release; never close a client beneath an operation.
    if _closing_tasks:
        await asyncio.shield(asyncio.gather(*tuple(_closing_tasks)))
