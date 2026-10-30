"""An exchanged console login cannot be replayed through finish (local reject).

Kept from the console upgrade guards (2026-09-14) when the upgrade routes left
the open core: the single exchange is the console login's own guard.
"""

from __future__ import annotations

import httpx
import pytest

from flocks.console.login import ConsoleLoginService
from flocks.storage.storage import Storage

pytestmark = pytest.mark.asyncio


async def test_finish_console_login_rejects_replay(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.mock")
    await Storage.init()
    calls = {"exchange": 0}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"console_session_token": "tok", "passport_uid": "u1", "user_display": "Alice", "expires_at": None}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, **kwargs):
            calls["exchange"] += 1
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    await Storage.set("console:login:cl-1", {"state": "S1", "status": "pending_remote"}, "json")

    first = await ConsoleLoginService.finish_console_login(console_login_id="cl-1", state="S1")
    assert first["console_session_token"] == "tok" and first["user_display"] == "Alice"
    assert calls["exchange"] == 1
    assert (await Storage.get("console:login:cl-1"))["status"] == "exchanged"

    with pytest.raises(ValueError, match="已使用"):
        await ConsoleLoginService.finish_console_login(console_login_id="cl-1", state="S1")
    # No second exchange was sent to the console.
    assert calls["exchange"] == 1
