"""/api/config/network/proxy — read, update (secret handling, validation, immediate effect) and test."""

from __future__ import annotations

import os

import pytest
from fastapi import status
from httpx import AsyncClient

from flocks.net import proxy as proxy_mod
from tests.net.proxy_fixture import EchoServer, RecordingProxy


@pytest.fixture
def proxy_config(tmp_path, monkeypatch):
    """Isolated flocks.json + .secret.json and a clean process environment."""
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    Config._global_config = None
    Config._cached_config = None
    ConfigWriter._write_raw({"theme": "dark"})
    for name in proxy_mod.PROXY_ENV_NAMES + ("FLOCKS_PROXY_MODE", "FLOCKS_PROXY_URL", "FLOCKS_PROXY_BYPASS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(proxy_mod, "_os_proxy_settings", lambda: (None, None, []))  # no Clash / OS proxy in tests

    class SecretStore:
        def __init__(self):
            self.values: dict[str, str] = {}

        def set(self, key, value):
            self.values[key] = value

        def get(self, key):
            return self.values.get(key)

        def delete(self, key):
            return self.values.pop(key, None) is not None

    store = SecretStore()
    import flocks.security as security_module

    monkeypatch.setattr(security_module, "get_secret_manager", lambda: store)  # resolve_secret_refs reads through this
    proxy_mod.invalidate_proxy_settings()
    yield ConfigWriter, store
    proxy_mod.invalidate_proxy_settings()


async def test_get_defaults_to_system_mode(client: AsyncClient, proxy_config):
    resp = await client.get("/api/config/network/proxy")
    assert resp.status_code == status.HTTP_200_OK, resp.text
    body = resp.json()
    assert body["mode"] == "system" and body["source"] == "default"
    assert body["bypassPrivate"] is True and body["bypass"] == []
    assert body["effective"]["active"] is False
    assert body["hasPassword"] is False


async def test_update_writes_config_secret_and_environment(client: AsyncClient, proxy_config):
    ConfigWriter, store = proxy_config
    resp = await client.patch(
        "/api/config/network/proxy",
        json={
            "mode": "manual",
            "https": "socks5h://127.0.0.1:7890",
            "http": "127.0.0.1:7890",
            "username": "corp",
            "password": 's3c"re\\t',
            "bypass": ["*.corp.example", "10.0.0.0/8", " gitlab.internal:8443 "],
            "bypassPrivate": True,
        },
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    body = resp.json()
    assert body["mode"] == "manual" and body["source"] == "config"
    assert body["https"] == "socks5h://127.0.0.1:7890" and body["http"] == "http://127.0.0.1:7890"
    assert body["hasPassword"] is True and "s3c" not in resp.text
    assert body["bypass"] == ["*.corp.example", "10.0.0.0/8", "gitlab.internal:8443"]
    assert body["effective"]["active"] is True

    raw = ConfigWriter._read_raw()
    assert raw["theme"] == "dark"  # other sections untouched
    section = raw["network"]["proxy"]
    assert section["password"] == "{secret:network_proxy_password}"
    assert store.values["network_proxy_password"] == 's3c"re\\t'
    assert "s3c" not in (ConfigWriter._get_config_path()).read_text(encoding="utf-8")

    # applied to the process immediately: trust_env libraries and children see it;
    # quotes / backslashes in the password survive (no JSON escaping on the way out)
    assert os.environ["HTTPS_PROXY"] == "socks5h://corp:s3c%22re%5Ct@127.0.0.1:7890"
    assert "10.0.0.0/8" in os.environ["NO_PROXY"] and ".corp.example" in os.environ["NO_PROXY"]
    settings = proxy_mod.get_proxy_settings()
    assert settings.password == 's3c"re\\t'
    assert settings.proxy_url_for("https://api.openai.com/") == "socks5h://corp:s3c%22re%5Ct@127.0.0.1:7890"
    assert settings.proxy_url_for("https://git.corp.example/") is None

    # clearing the password removes the secret and the reference
    resp = await client.patch("/api/config/network/proxy", json={"password": ""})
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["hasPassword"] is False
    assert "password" not in ConfigWriter._read_raw()["network"]["proxy"]
    assert "network_proxy_password" not in store.values

    # switching off scrubs the environment
    resp = await client.patch("/api/config/network/proxy", json={"mode": "off"})
    assert resp.status_code == status.HTTP_200_OK
    assert "HTTPS_PROXY" not in os.environ and os.environ["NO_PROXY"] == "*"
    assert resp.json()["effective"]["active"] is False


async def test_update_rejects_bad_url_and_bad_whitelist(client: AsyncClient, proxy_config):
    ConfigWriter, store = proxy_config
    resp = await client.patch("/api/config/network/proxy", json={"mode": "manual", "https": "ftp://nope", "password": "never-stored"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "scheme" in resp.json()["message"]
    assert "network" not in ConfigWriter._read_raw()
    assert "network_proxy_password" not in store.values  # rejected requests touch neither file

    resp = await client.patch("/api/config/network/proxy", json={"bypass": ["ok.example", "not a rule!", "10.0.0.0/99"]})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "not a rule!" in resp.json()["message"] and "10.0.0.0/99" in resp.json()["message"]

    resp = await client.patch("/api/config/network/proxy", json={"mode": "sideways"})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    resp = await client.patch("/api/config/network/proxy", json={"unknown": 1})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_update_keeps_inline_credentials_when_client_echoes_the_redacted_url(client: AsyncClient, proxy_config):
    ConfigWriter, _ = proxy_config
    ConfigWriter._write_raw({"network": {"proxy": {"mode": "manual", "https": "http://alice:pw@proxy.corp:3128"}}})
    proxy_mod.invalidate_proxy_settings()
    resp = await client.get("/api/config/network/proxy")
    assert resp.json()["https"] == "http://alice:***@proxy.corp:3128"

    # a panel that sends every field back must not overwrite the password with "***"
    resp = await client.patch("/api/config/network/proxy", json={"https": "http://alice:***@proxy.corp:3128", "bypass": ["*.corp.example"]})
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert ConfigWriter._read_raw()["network"]["proxy"]["https"] == "http://alice:pw@proxy.corp:3128"
    assert ConfigWriter._read_raw()["network"]["proxy"]["bypass"] == ["*.corp.example"]
    # while a genuinely new value still lands
    resp = await client.patch("/api/config/network/proxy", json={"https": "http://bob:new@proxy.corp:3128"})
    assert ConfigWriter._read_raw()["network"]["proxy"]["https"] == "http://bob:new@proxy.corp:3128"

    # a hand-written value without port / scheme is handed out normalised: echoing that back is "unchanged" too
    ConfigWriter._write_raw({"network": {"proxy": {"mode": "manual", "http": "alice:pw@proxy.corp", "https": "http://alice:pw@proxy.corp"}}})
    proxy_mod.invalidate_proxy_settings()
    body = (await client.get("/api/config/network/proxy")).json()
    assert body["http"] == "http://alice:***@proxy.corp:80" and body["https"] == "http://alice:***@proxy.corp:80"
    resp = await client.patch("/api/config/network/proxy", json={"http": body["http"], "https": body["https"]})
    assert resp.status_code == status.HTTP_200_OK, resp.text
    stored = ConfigWriter._read_raw()["network"]["proxy"]
    assert stored["http"] == "alice:pw@proxy.corp" and stored["https"] == "http://alice:pw@proxy.corp"


async def test_update_requires_admin(proxy_config):
    from httpx import ASGITransport

    from flocks.server.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"User-Agent": "curl/8.0"}) as anon:
        resp = await anon.patch("/api/config/network/proxy", json={"mode": "off"})
        assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        resp = await anon.get("/api/config/network/proxy")
        assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


async def test_connectivity_test_reports_route_and_status(client: AsyncClient, proxy_config):
    proxy = await RecordingProxy().start()
    target = await EchoServer().start()
    try:
        resp = await client.patch(
            "/api/config/network/proxy",
            json={"mode": "manual", "all": proxy.http_url, "bypassPrivate": False, "bypass": ["<-loopback>", f"127.0.0.1:{target.port + 1}"]},
        )
        assert resp.status_code == status.HTTP_200_OK, resp.text

        resp = await client.post("/api/config/network/proxy/test", json={"url": f"{target.url}/probe", "timeout": 5})
        assert resp.status_code == status.HTTP_200_OK, resp.text
        body = resp.json()
        assert body["ok"] is True and body["status"] == 200 and body["via"] == proxy.http_url
        assert body["elapsedMs"] is not None
        assert proxy.targets == [("http", "127.0.0.1", target.port)]

        resp = await client.post("/api/config/network/proxy/test", json={"url": "ftp://x"})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

        resp = await client.post("/api/config/network/proxy/test", json={"url": "http://127.0.0.1:1/closed", "timeout": 2})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["ok"] is False and resp.json()["error"]
    finally:
        await proxy.stop()
        await target.stop()
