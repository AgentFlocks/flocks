from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.server.routes.strix_chat import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/integrations/strix/chat")
    return TestClient(app)


def test_start_chat_proxies_validated_payload() -> None:
    sidecar = AsyncMock()
    sidecar.start_chat.return_value = {
        "id": "chat-abc",
        "status": "starting",
        "events": [],
    }
    with patch("flocks.server.routes.strix_chat._client", return_value=sidecar):
        response = _client().post(
            "/api/integrations/strix/chat",
            json={
                "message": "Map the attack surface",
                "targets": ["/workspace/app"],
                "scan_mode": "standard",
            },
        )
    assert response.status_code == 200
    assert response.json()["id"] == "chat-abc"
    sidecar.start_chat.assert_awaited_once_with(
        {
            "message": "Map the attack surface",
            "targets": ["/workspace/app"],
            "scan_mode": "standard",
        },
    )


def test_start_chat_rejects_invalid_scan_mode() -> None:
    response = _client().post(
        "/api/integrations/strix/chat",
        json={"message": "hello", "scan_mode": "unbounded"},
    )
    assert response.status_code == 422


def test_list_chats_proxies_retained_sessions() -> None:
    sidecar = AsyncMock()
    sidecar.list_chats.return_value = {
        "chats": [
            {
                "id": "chat-abc",
                "title": "Map the attack surface",
                "status": "waiting",
            },
        ],
    }
    with patch("flocks.server.routes.strix_chat._client", return_value=sidecar):
        response = _client().get("/api/integrations/strix/chat")
    assert response.status_code == 200
    assert response.json()["chats"][0]["id"] == "chat-abc"
    sidecar.list_chats.assert_awaited_once_with()


def test_get_chat_forwards_incremental_cursor() -> None:
    sidecar = AsyncMock()
    sidecar.get_chat.return_value = {
        "id": "chat-abc",
        "status": "waiting",
        "events": [],
    }
    with patch("flocks.server.routes.strix_chat._client", return_value=sidecar):
        response = _client().get("/api/integrations/strix/chat/chat-abc?after=41")
    assert response.status_code == 200
    sidecar.get_chat.assert_awaited_once_with("chat-abc", after=41)


def test_stop_chat_retains_session() -> None:
    sidecar = AsyncMock()
    sidecar.stop_chat.return_value = {
        "id": "chat-abc",
        "status": "stopped",
        "events": [],
    }
    with patch("flocks.server.routes.strix_chat._client", return_value=sidecar):
        response = _client().post("/api/integrations/strix/chat/chat-abc/stop")
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    sidecar.stop_chat.assert_awaited_once_with("chat-abc")
