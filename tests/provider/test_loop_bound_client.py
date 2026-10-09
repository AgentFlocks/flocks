"""``BaseProvider._client`` is one SDK client per event loop.

Regression for workflow ``delegate_task`` subagents (workflow loop) and
interactive sessions (server loop) sharing a single ``AsyncOpenAI`` client:
httpx/anyio connections are bound to the loop that created them, so the second
loop failed with ``RuntimeError: ... is bound to a different event loop``
(surfaced as ``APIConnectionError: Connection error.``) or lost wake-ups.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from flocks.provider.provider import BaseProvider, ChatMessage, ProviderConfig
from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider
from flocks.workflow import _async_runtime


class _Provider(BaseProvider):
    def __init__(self) -> None:
        super().__init__(provider_id="slot-test", name="Slot Test")
        self._client = None
        self.created = 0

    def _get_client(self):
        if self._client is None:
            self.created += 1
            self._client = object()
        return self._client


def _run_on_fresh_loop(fn):
    """Run *fn* (sync, may call asyncio.run-style helpers) on a new loop in a worker thread."""
    result = {}

    def _target():
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(fn())
        finally:
            loop.close()

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    return result["value"]


async def test_same_loop_reuses_one_client():
    provider = _Provider()
    first = provider._get_client()
    assert provider._get_client() is first
    assert provider.created == 1


async def test_each_loop_gets_its_own_client():
    provider = _Provider()
    main_client = provider._get_client()

    async def _other():
        return provider._get_client()

    other_client = _run_on_fresh_loop(_other)
    assert other_client is not main_client
    assert provider.created == 2
    # The main loop still sees its own client, untouched by the other loop.
    assert provider._get_client() is main_client


async def test_assigning_none_drops_every_loop_client():
    provider = _Provider()
    provider._get_client()

    async def _other():
        provider._get_client()

    _run_on_fresh_loop(_other)
    assert provider.created == 2

    provider._client = None
    assert provider._client is None
    provider._get_client()
    assert provider.created == 3


async def test_configure_change_resets_clients_on_all_loops():
    provider = _Provider()
    first = provider._get_client()

    provider.configure(ProviderConfig(provider_id="slot-test", api_key="k1"))
    assert provider._client is None
    replaced = provider._get_client()
    assert replaced is not first
    assert provider.created == 2

    # Unchanged config keeps the client.
    provider.configure(ProviderConfig(provider_id="slot-test", api_key="k1"))
    assert provider._get_client() is replaced
    assert provider.created == 2


def test_client_injected_without_a_loop_is_adopted_by_first_loop():
    """Tests (and sync setup code) assign fakes outside any loop."""
    provider = _Provider()
    fake = object()
    provider._client = fake  # no running loop here
    assert provider._client is fake

    async def _use():
        return provider._get_client()

    assert asyncio.run(_use()) is fake
    assert provider.created == 0
    # Adopted: it now belongs to a loop, so the sync view no longer sees it...
    assert provider._client is None
    # ...but a *different* loop gets a fresh client rather than the adopted one.
    assert _run_on_fresh_loop(_use) is not fake


async def test_copied_provider_does_not_share_client_state():
    provider = _Provider()
    original = provider._get_client()
    clone = copy.copy(provider)
    clone._client = None
    assert clone._client is None
    assert provider._client is original
    clone._client = object()
    assert provider._client is original


# --------------------------------------------------------------------------
# End-to-end: one OpenAI-compatible provider instance, two event loops, real
# sockets. Before the per-loop slot the second loop's request failed at once.
# --------------------------------------------------------------------------


def _start_fake_openai_server():
    """Minimal keep-alive HTTP/1.1 server answering chat completions."""
    ready = threading.Event()
    state = {"port": None, "requests": 0}

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = 0
                for line in head.decode("latin-1").split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":", 1)[1].strip())
                if length:
                    await reader.readexactly(length)
                state["requests"] += 1
                body = json.dumps({
                    "id": "cmpl", "object": "chat.completion", "created": 0, "model": "m",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: keep-alive\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def _serve():
        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        state["port"] = server.sockets[0].getsockname()[1]
        ready.set()
        async with server:
            await asyncio.Event().wait()

    thread = threading.Thread(target=lambda: asyncio.run(_serve()), daemon=True)
    thread.start()
    ready.wait(5)
    return state


@pytest.fixture
def fake_openai_server():
    return _start_fake_openai_server()


async def test_same_provider_instance_serves_two_event_loops(fake_openai_server):
    provider = OpenAICompatibleProvider()
    provider.configure(
        ProviderConfig(
            provider_id="openai-compatible",
            api_key="test",
            base_url=f"http://127.0.0.1:{fake_openai_server['port']}/v1",
            custom_settings={"trust_env": False},
        )
    )
    messages = [ChatMessage(role="user", content="hi")]

    async def _chat():
        return (await provider.chat(model_id="m", messages=messages)).content

    def _via_workflow_loop():
        # Same path as tools_adapter / workflow llm.py: a worker thread submits
        # the provider call to the dedicated workflow loop.
        return _async_runtime.run_sync(_chat())

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert await _chat() == "ok"                                   # server loop warms a keep-alive connection
        assert await asyncio.get_running_loop().run_in_executor(pool, _via_workflow_loop) == "ok"
        assert await _chat() == "ok"
        assert await asyncio.get_running_loop().run_in_executor(pool, _via_workflow_loop) == "ok"

    workflow_loop, _ = _async_runtime._get_loop_for_testing()
    assert workflow_loop is not None
    main_client = provider._client
    assert main_client is not None
    assert _async_runtime.run_sync(_client_of(provider)) is not main_client
    assert fake_openai_server["requests"] == 4


async def _client_of(provider):
    return provider._client
