"""flocks.console.connectivity: whether this install reaches Console now.

Case list (编号 | 场景):
  CN1 no Console address: not configured, not online, nothing asked
  CN2 Console answering /health is online, an older one answering 404 too; a 5xx in its place, no
      answer or a timeout is not
  CN3 an answer is kept for CACHE_SECONDS; refresh asks again; another address asks again
"""

from __future__ import annotations

import httpx
import pytest
from flocks.console import connectivity

pytestmark = pytest.mark.asyncio

_REAL_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):
    connectivity.forget()
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example")
    yield
    connectivity.forget()


def console(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """A Console answering through handler; the requests it got."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    class Client(_REAL_CLIENT):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(record)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(connectivity.httpx, "AsyncClient", Client)
    return seen


async def test_cn1_no_console_address(monkeypatch: pytest.MonkeyPatch):
    seen = console(monkeypatch, lambda r: httpx.Response(200))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "")
    answer = await connectivity.console_connectivity()
    assert (answer["configured"], answer["online"], answer["console_host"]) == (False, False, "")
    assert seen == []


@pytest.mark.parametrize("respond, online", [
    (lambda r: httpx.Response(200, json={"status": "ok"}), True),
    (lambda r: httpx.Response(404), True),
    (lambda r: httpx.Response(502, text="Bad Gateway"), False),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")), False),
    (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), False),
])
async def test_cn2_what_counts_as_online(monkeypatch: pytest.MonkeyPatch, respond, online: bool):
    seen = console(monkeypatch, respond)
    answer = await connectivity.console_connectivity()
    assert answer["configured"] is True and answer["online"] is online
    assert answer["console_host"] == "console.example" and answer["checked_at"]
    assert [str(r.url) for r in seen] == ["https://console.example/health"]


async def test_cn3_an_answer_is_kept_for_a_while(monkeypatch: pytest.MonkeyPatch):
    seen = console(monkeypatch, lambda r: httpx.Response(200))
    assert (await connectivity.console_connectivity())["online"] is True
    assert (await connectivity.console_connectivity())["online"] is True
    assert len(seen) == 1
    # Asked again on purpose, or for another address.
    await connectivity.console_connectivity(refresh=True)
    assert len(seen) == 2
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "console-b.example")
    answer = await connectivity.console_connectivity()
    assert len(seen) == 3 and answer["console_host"] == "console-b.example"
    assert str(seen[-1].url) == "https://console-b.example/health"
    # Kept no longer than CACHE_SECONDS.
    base, asked_at, kept = connectivity._kept
    connectivity._kept = (base, asked_at - connectivity.CACHE_SECONDS - 1, kept)
    await connectivity.console_connectivity()
    assert len(seen) == 4
