from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from flocks.server.routes import pty as pty_routes


class _FakeWebSocket:
    def __init__(self) -> None:
        self.close_code = None
        self.close_reason = None
        self.accepted = False

    async def close(self, code: int, reason: str = "") -> None:
        self.close_code = code
        self.close_reason = reason

    async def accept(self) -> None:
        self.accepted = True


@pytest.mark.asyncio
async def test_pty_websocket_authenticates_before_session_lookup(monkeypatch: pytest.MonkeyPatch):
    websocket = _FakeWebSocket()

    async def _reject(_websocket):
        raise HTTPException(status_code=401, detail="missing auth")

    get_session = Mock()
    monkeypatch.setattr(pty_routes, "apply_auth_for_request", _reject)
    monkeypatch.setattr(pty_routes.Pty, "get", get_session)

    await pty_routes.connect_session(websocket, "pty_missing")

    assert websocket.close_code == 4401
    assert websocket.close_reason == "missing auth"
    assert websocket.accepted is False
    get_session.assert_not_called()
