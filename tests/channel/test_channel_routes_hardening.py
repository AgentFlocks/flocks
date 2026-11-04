"""Regression tests for channel route/plugin edge cases.

Covers: Feishu schema-2.0 token verification, Telegram webhook authentication
evidence, empty-message 422, webhook on stream-only channels, unknown channel
on record-inbound/bind, iLink baseUrl validation and WhatsApp path scoping.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.channel.builtin.feishu.config import verify_verification_token
from flocks.channel.builtin.telegram.channel import TelegramChannel
from flocks.channel.registry import default_registry
from flocks.server.routes import channel as channel_routes


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(channel_routes.router, prefix="/api/channel")
    default_registry.init()
    return TestClient(app, raise_server_exceptions=False)


def test_verification_token_accepts_schema_2_header_token():
    assert verify_verification_token({"token": "vt1", "challenge": "x"}, "vt1")
    assert verify_verification_token({"schema": "2.0", "header": {"token": "vt1"}}, "vt1")
    assert not verify_verification_token({"schema": "2.0", "header": {"token": "other"}}, "vt1")
    assert not verify_verification_token({"header": "not-a-dict"}, "vt1")


@pytest.mark.asyncio
async def test_telegram_webhook_evidence_reflects_secret_check():
    channel = TelegramChannel()
    channel._config = {"botToken": "1:x", "mode": "webhook", "webhookSecret": "s1"}
    # Not started: never authenticated.
    assert (await channel.webhook_authentication_evidence(b"{}", {"x-telegram-bot-api-secret-token": "s1"}))["plugin_authenticated"] is False
    channel._on_message = AsyncMock()
    assert (await channel.webhook_authentication_evidence(b"{}", {"x-telegram-bot-api-secret-token": "wrong"}))["plugin_authenticated"] is False
    evidence = await channel.webhook_authentication_evidence(b"{}", {"X-Telegram-Bot-Api-Secret-Token": "s1"})
    assert evidence["plugin_authenticated"] is True and evidence["provider"] == "telegram"


def test_send_rejects_blank_text_without_media(client):
    r = client.post("/api/channel/send", json={"channel_id": "feishu", "to": "u", "text": "   "})
    assert r.status_code == 422
    r = client.post("/api/channel/session-send", json={"session_id": "ses_x", "text": ""})
    assert r.status_code == 422


def test_webhook_on_stream_only_channel_is_404(client):
    r = client.post("/api/channel/dingtalk/webhook", content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 404
    assert "does not support webhook mode" in r.text


def test_record_inbound_and_bind_reject_unknown_channel(client):
    assert client.post("/api/channel/nope/record-inbound").status_code == 404
    r = client.post("/api/channel/nope/bind", json={"session_id": "ses_x", "chat_id": "c"})
    assert r.status_code == 404


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:5173", "https://localhost", "https://10.0.0.1", "ftp://x"])
def test_weixin_qr_rejects_non_public_base_url(client, base_url):
    r = client.post("/api/channel/weixin/qr-login/start", json={"baseUrl": base_url})
    assert r.status_code == 422, r.text
    r = client.get("/api/channel/weixin/qr-login/status", params={"qrcode": "abc", "baseUrl": base_url})
    assert r.status_code == 422, r.text


def test_whatsapp_paths_must_stay_inside_flocks_root(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    r = client.get("/api/channel/whatsapp/session-status", params={"sessionPath": "/etc"})
    assert r.status_code == 400 and "inside the Flocks data directory" in r.text
    r = client.post("/api/channel/whatsapp/pair/start", json={"sessionPath": "/tmp/x", "bridgeDir": "/tmp/evil"})
    assert r.status_code == 400
    inside = tmp_path / "root" / "workspace" / "channels" / "whatsapp" / "session"
    inside.mkdir(parents=True)
    (inside / "creds.json").write_text("{}")
    r = client.get("/api/channel/whatsapp/session-status", params={"sessionPath": str(inside)})
    assert r.status_code == 200 and r.json()["paired"] is True


@pytest.mark.asyncio
async def test_restart_failure_is_logged_not_dropped(client, monkeypatch):
    import asyncio

    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(channel_routes.log, "error", lambda ev, data=None: logged.append((ev, data or {})))

    async def boom(channel_id):
        raise ValueError("Invalid channel config: Missing required config: appId and appSecret")

    monkeypatch.setattr(channel_routes.default_manager, "restart_channel", boom)
    r = client.post("/api/channel/feishu/restart")
    assert r.status_code == 200
    # let the background task run inside the TestClient's loop
    for _ in range(20):
        if logged:
            break
        await asyncio.sleep(0.05)
    assert logged and logged[0][0] == "channel.restart_failed" and "Missing required config" in logged[0][1]["error"]


def test_session_send_refuses_other_users_session(monkeypatch):
    """A logged-in user cannot relay into a session they cannot read (403)."""
    from types import SimpleNamespace

    from starlette.middleware.base import BaseHTTPMiddleware

    from flocks.session import session as session_module
    from flocks.session.policy import SessionPolicy

    member = SimpleNamespace(id="usr_member", username="member", role="member")
    other_session = SimpleNamespace(id="ses_other", owner_user_id="usr_admin", owner_username="admin", status="active")

    async def fake_get(session_id):
        return other_session if session_id == "ses_other" else None

    monkeypatch.setattr(session_module.Session, "get_by_id_unfiltered", staticmethod(fake_get))
    monkeypatch.setattr(SessionPolicy, "can_read", classmethod(lambda cls, session, user=None, **kw: False))

    app = FastAPI()

    class InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.auth_user = member
            return await call_next(request)

    app.add_middleware(InjectUser)
    app.include_router(channel_routes.router, prefix="/api/channel")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/channel/session-send", json={"session_id": "ses_other", "text": "x"})
    assert r.status_code == 403, r.text
