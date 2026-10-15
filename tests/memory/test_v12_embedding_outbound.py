"""Text sent for embedding passes the same outbound stage as a prompt, and the
default provider choice stays on this host or a private network."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.memory import embedding


@pytest.fixture
def hook_pipeline(monkeypatch: pytest.MonkeyPatch):
    HookPipeline.reset()
    monkeypatch.setattr(
        HookPipeline,
        "ensure_initialized",
        AsyncMock(side_effect=lambda *args, **kwargs: tuple(HookPipeline._hooks)),
    )
    yield HookPipeline
    HookPipeline.reset()


@pytest.mark.parametrize(
    "url, local",
    [
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:8000", True),
        ("http://10.2.3.4/v1", True),
        ("https://192.168.1.20/api", True),
        ("http://[::1]:9000", True),
        ("https://api.openai.com/v1", False),
        ("https://8.8.8.8/v1", False),
        (None, False),
    ],
)
def test_local_endpoints(url, local):
    assert embedding.is_local_endpoint(url) is local


class _Outbound(HookBase):
    def __init__(self, *, deny: bool = False) -> None:
        self.seen: list[dict] = []
        self.deny = deny

    async def llm_before(self, ctx):
        self.seen.append(dict(ctx.input))
        if self.deny:
            raise RuntimeError("not to that destination")
        ctx.output["request"] = {
            "messages": [
                {**message, "content": message["content"].replace("ACME-SECRET", "[ORG_1]")}
                for message in ctx.input["request"]["messages"]
            ],
        }


async def test_embedding_text_passes_the_outbound_stage(hook_pipeline, monkeypatch):
    sent: list[list[str]] = []

    async def _embed_batch(texts, provider_id=None, model=None, **kwargs):
        sent.append(list(texts))
        return [[0.1] for _ in texts]

    from flocks.provider.provider import Provider

    monkeypatch.setattr(Provider, "embed_batch", _embed_batch)
    monkeypatch.setattr(Provider, "get", lambda pid: SimpleNamespace(_config=SimpleNamespace(base_url="https://api.openai.com/v1")))
    outbound = _Outbound()
    hook_pipeline.register("outbound", outbound, order=1)

    vectors = await embedding.embed_many(["about ACME-SECRET", "plain"], provider_id="openai", model="m")

    assert vectors == [[0.1], [0.1]]
    assert sent == [["about [ORG_1]", "plain"]]
    meta = outbound.seen[0]
    assert meta["purpose"] == "embedding" and meta["embeddingLocal"] is False
    assert meta["callerID"] and meta["model_call_id"].startswith("embedding:")


async def test_a_refused_embedding_is_not_sent(hook_pipeline, monkeypatch):
    from flocks.provider.provider import Provider

    sent: list[str] = []
    monkeypatch.setattr(Provider, "embed", AsyncMock(side_effect=lambda **kwargs: sent.append(kwargs["text"])))
    monkeypatch.setattr(Provider, "get", lambda pid: None)
    hook_pipeline.register("outbound", _Outbound(deny=True), order=1, fail_policy="fail_closed")

    with pytest.raises(Exception):
        await embedding.embed_one("hello", provider_id="openai", model="m")
    assert sent == []


def test_the_default_provider_choice_is_local(monkeypatch):
    from flocks.memory.config import MemoryEmbeddingConfig
    from flocks.memory.manager import MemoryManager
    from flocks.provider.provider import Provider

    assert MemoryEmbeddingConfig().provider == "local"
    providers = {
        "openai": SimpleNamespace(_config=SimpleNamespace(base_url="https://api.openai.com/v1")),
        "ollama": SimpleNamespace(_config=SimpleNamespace(base_url="http://127.0.0.1:11434/v1")),
    }
    monkeypatch.setattr(Provider, "list_providers", classmethod(lambda cls: list(providers)))
    monkeypatch.setattr(Provider, "get", classmethod(lambda cls, pid: providers.get(pid)))

    manager = MemoryManager.__new__(MemoryManager)
    manager._embedding_enabled = True
    manager._provider_can_embed = lambda pid: pid in providers
    manager._requested_provider = "local"
    assert manager._resolve_embedding_provider() == "ollama"
    manager._requested_provider = "auto"
    assert manager._resolve_embedding_provider() == "openai"
