"""
Embedding requests, as they leave the process.

Text sent for embedding leaves the process just like a prompt does, so it
passes the same ``llm.call.before`` stage first (purpose ``embedding``): an
installed extension can check where it is going, refuse it, or replace what
it must not send.  ``embeddingLocal`` in the metadata says whether the
provider is a local / private-network endpoint, so an extension can apply its
outbound rules only where they matter.  With no handler registered nothing
changes.

The default provider choice is local too (``local`` in the memory config):
only providers whose endpoint is on this host or a private network qualify,
and an external provider is used only when configured by name or with
``auto``.
"""

from __future__ import annotations

import ipaddress
from typing import Any, List, Optional
from urllib.parse import urlsplit

from flocks.utils.log import Log

log = Log.create(service="memory.embedding")


def is_local_endpoint(base_url: Optional[str]) -> bool:
    """Whether *base_url* points at this host or a private network."""
    if not base_url:
        return False
    try:
        host = (urlsplit(base_url).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def provider_base_url(provider: Any) -> Optional[str]:
    config = getattr(provider, "_config", None)
    url = getattr(config, "base_url", None) or getattr(provider, "_base_url", None)
    return str(url) if url else None


def is_local_provider(provider_id: Optional[str]) -> bool:
    if not provider_id:
        return False
    from flocks.provider.provider import Provider

    provider = Provider.get(provider_id)
    return provider is not None and is_local_endpoint(provider_base_url(provider))


async def outbound_texts(
    texts: List[str],
    *,
    provider_id: Optional[str],
    model: Optional[str],
    session_id: Optional[str] = None,
    agent: str = "memory.embedding",
) -> List[str]:
    """The texts as they may be sent, after the ``llm.call.before`` stage.

    *agent* names what is embedding (``memory.embedding``,
    ``knowledgebase.embedding``), so a handler can tell the sources apart.
    """
    if not texts:
        return []
    from flocks.provider.provider import ChatMessage
    from flocks.session.llm_hook_utils import model_call_hook_metadata, run_llm_before_for_call

    metadata = await model_call_hook_metadata(
        session_id=session_id or "memory",
        agent=agent,
        purpose="embedding",
        provider_id=provider_id,
        model_id=model,
    )
    metadata["embeddingLocal"] = is_local_provider(provider_id)
    messages = [ChatMessage(role="user", content=text) for text in texts]
    messages, _options, _replacements = await run_llm_before_for_call(metadata, messages, {})
    if len(messages) != len(texts):
        raise RuntimeError("an llm.call.before handler changed the number of texts to embed")
    return [message.content if isinstance(message.content, str) else str(message.content or "") for message in messages]


async def embed_one(
    text: str,
    *,
    provider_id: str,
    model: Optional[str],
    session_id: Optional[str] = None,
    agent: str = "memory.embedding",
) -> List[float]:
    from flocks.provider.provider import Provider

    (sendable,) = await outbound_texts(
        [text], provider_id=provider_id, model=model, session_id=session_id, agent=agent,
    )
    return await Provider.embed(text=sendable, provider_id=provider_id, model=model)


async def embed_many(
    texts: List[str],
    *,
    provider_id: str,
    model: Optional[str],
    session_id: Optional[str] = None,
    agent: str = "memory.embedding",
) -> List[List[float]]:
    from flocks.provider.provider import Provider

    sendable = await outbound_texts(
        texts, provider_id=provider_id, model=model, session_id=session_id, agent=agent,
    )
    return await Provider.embed_batch(texts=sendable, provider_id=provider_id, model=model)


__all__ = [
    "embed_many",
    "embed_one",
    "is_local_endpoint",
    "is_local_provider",
    "outbound_texts",
    "provider_base_url",
]
