"""flocks.net: routing decision, whitelist, environment hand-off, httpx / aiohttp adapters."""

from __future__ import annotations

import os

import httpx
import pytest

from flocks.net import proxy as proxy_mod
from flocks.net.proxy import (
    DEFAULT_BYPASS_RULES,
    LOOPBACK_OPT_OUT as LOOPBACK,
    ProxySettings,
    apply_process_environment,
    child_env,
    chromium_proxy_args,
    load_proxy_settings,
    parse_bypass_rule,
    requests_proxies,
    validate_bypass_rules,
)
from tests.net.proxy_fixture import EchoServer, RecordingProxy

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def manual(**overrides):
    base = dict(mode="manual", https="http://proxy.test:3128", http="http://proxy.test:3128")
    base.update(overrides)
    return ProxySettings.build(**base)


# --------------------------------------------------------------------------- whitelist


@pytest.mark.parametrize(
    "rule,host,port,expected",
    [
        ("*", "anything.example", 443, True),
        ("<local>", "wiki", None, True),
        ("<local>", "wiki.corp", None, False),
        ("localhost", "LOCALHOST", 80, True),
        ("*.corp.example", "a.b.corp.example", 443, True),
        ("*.corp.example", "corp.example", 443, True),
        ("*.corp.example", "notcorp.example", 443, False),
        (".corp.example", "x.corp.example", 443, True),
        ("gitlab.internal:8443", "gitlab.internal", 8443, True),
        ("gitlab.internal:8443", "gitlab.internal", 443, False),
        ("10.0.0.0/8", "10.20.30.40", 443, True),
        ("10.0.0.0/8", "11.0.0.1", 443, False),
        ("fd00::/8", "fd00::1", 443, True),
        ("[::1]", "::1", 443, True),
        ("192.168.1.10", "192.168.1.10", 22, True),
    ],
)
def test_bypass_rules(rule, host, port, expected):
    parsed = parse_bypass_rule(rule)
    assert parsed is not None
    assert parsed.matches(host.lower(), port) is expected


@pytest.fixture(autouse=True)
def _no_os_proxy(monkeypatch):
    """The developer machine may have an OS-level proxy (Clash); tests must not depend on it."""
    monkeypatch.setattr(proxy_mod, "_os_proxy_settings", lambda: (None, None, []))


def test_system_mode_falls_back_to_os_proxy_settings(monkeypatch):
    for name in proxy_mod.PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        proxy_mod, "_os_proxy_settings", lambda: ("http://127.0.0.1:7890", "http://127.0.0.1:7890", ["*.corp.example", "<local>"])
    )
    settings = ProxySettings.build(mode="system").resolved()
    assert settings.mode == "manual" and settings.source == "os"
    assert settings.proxy_url_for("https://api.openai.com/") == "http://127.0.0.1:7890"
    assert settings.proxy_url_for("https://git.corp.example/") is None
    assert settings.proxy_url_for("http://wiki/") is None  # <local>
    assert ProxySettings.build(mode="system").public_dict()["effective"]["source"] == "os"

    # exported variables win over the OS settings
    monkeypatch.setenv("HTTPS_PROXY", "socks5h://127.0.0.1:1080")
    assert ProxySettings.build(mode="system").resolved().proxy_url_for("https://api.openai.com/") == "socks5h://127.0.0.1:1080"
    assert ProxySettings.build(mode="system").resolved().source == "env"


def test_private_defaults_go_direct():
    settings = manual()
    for url in (
        "http://localhost:8000/x",
        "http://127.0.0.1:5173/",
        "https://10.2.3.4/",
        "https://192.168.1.10/",
        "https://172.20.0.5/",
        "https://[fd12::1]/",
        "http://printer.local/",
        "https://vault.internal/",
        "http://169.254.169.254/latest/meta-data",
    ):
        assert settings.proxy_url_for(url) is None, url
    assert settings.proxy_url_for("https://api.openai.com/v1") == "http://proxy.test:3128"


def test_private_defaults_can_be_disabled_but_loopback_stays_direct():
    settings = manual(bypass_private=False)
    assert settings.proxy_url_for("https://10.2.3.4/") == "http://proxy.test:3128"
    assert settings.proxy_url_for("http://printer.local/") == "http://proxy.test:3128"
    for url in ("http://localhost/", "http://127.0.0.1:5173/", "http://[::1]:9222/", "http://app.localhost/"):
        assert settings.proxy_url_for(url) is None, url
    assert "127.0.0.1" in settings.no_proxy_value() and "10.0.0.0/8" not in settings.no_proxy_value()

    # Chrome's "<-loopback>" removes the implicit loopback rules (tests, exotic tunnels)
    opted_out = manual(bypass_private=False, bypass=["<-loopback>"])
    assert opted_out.proxy_url_for("http://127.0.0.1:5173/") == "http://proxy.test:3128"
    assert opted_out.proxy_url_for("http://localhost/") == "http://proxy.test:3128"
    assert "<-loopback>" not in opted_out.no_proxy_value() and "127.0.0.1" not in opted_out.no_proxy_value()
    assert validate_bypass_rules(["<-loopback>", "10.0.0.0/8"]) == []
    assert chromium_proxy_args(opted_out)[1] == "--proxy-bypass-list=<-loopback>"


def test_user_whitelist_wins_over_proxy():
    settings = manual(bypass=["*.corp.example", "203.0.113.0/24", "public.example:9443"])
    assert settings.proxy_url_for("https://git.corp.example/") is None
    assert settings.proxy_url_for("https://203.0.113.9/") is None
    assert settings.proxy_url_for("https://public.example:9443/") is None
    assert settings.proxy_url_for("https://public.example/") == "http://proxy.test:3128"


def test_proxy_url_normalisation():
    settings = ProxySettings.build(mode="manual", http="proxy.corp", https="https://proxy.corp", all="socks5h://sock")
    assert settings.http == "http://proxy.corp:80" and settings.https == "https://proxy.corp:443"
    assert settings.all == "socks5h://sock:1080"
    for bad in ("socks4://h:1", "socks4a://h:1", "ftp://h", "http://", "http://h:notaport"):
        with pytest.raises(ValueError):
            ProxySettings.build(mode="manual", https=bad)
    assert validate_bypass_rules(["169.254/16", "10/8"]) == []  # macOS shorthand
    assert parse_bypass_rule("169.254/16").matches("169.254.169.254", None)


def test_scheme_selection_and_fallback():
    settings = ProxySettings.build(mode="manual", http="http://h:1", https="socks5h://s:2")
    assert settings.proxy_url_for("http://example.com/") == "http://h:1"
    assert settings.proxy_url_for("https://example.com/") == "socks5h://s:2"
    assert settings.proxy_url_for("wss://example.com/") == "socks5h://s:2"
    only_all = ProxySettings.build(mode="manual", all="socks5://a:3")
    assert only_all.proxy_url_for("https://example.com/") == "socks5://a:3"
    assert only_all.proxy_url_for("http://example.com/") == "socks5://a:3"


def test_credentials_are_embedded_and_redacted():
    settings = manual(username="user", password="p@ss:word")
    assert settings.proxy_url_for("https://example.com/") == "http://user:p%40ss%3Aword@proxy.test:3128"
    public = settings.public_dict()
    assert public["hasPassword"] is True
    assert "p%40ss" not in str(public)
    assert "***" not in public["https"]  # stored url had no inline credentials
    env = settings.env_map()
    assert env["HTTPS_PROXY"].startswith("http://user:")


def test_shorthand_and_invalid_urls():
    assert ProxySettings.build(mode="manual", http="127.0.0.1:7890").http == "http://127.0.0.1:7890"
    with pytest.raises(ValueError):
        ProxySettings.build(mode="manual", https="ftp://x")
    with pytest.raises(ValueError):
        ProxySettings.build(mode="nope")


def test_validate_bypass_rules_flags_garbage():
    assert validate_bypass_rules(["*.ok.example", "10.0.0.0/8", "host:1", "", "bad rule!", "10.0.0.0/99"]) == [
        "bad rule!",
        "10.0.0.0/99",
    ]


# --------------------------------------------------------------------------- loading


def test_load_from_config_and_env_overrides(monkeypatch):
    raw = {"network": {"proxy": {"mode": "manual", "https": "socks5h://127.0.0.1:7890", "noProxy": ["*.corp"], "bypassPrivate": False}}}
    settings = load_proxy_settings(raw, env={})
    assert settings.mode == "manual" and settings.https == "socks5h://127.0.0.1:7890"
    assert settings.bypass == ("*.corp",) and settings.bypass_private is False
    assert settings.source == "config"

    overridden = load_proxy_settings(raw, env={"FLOCKS_PROXY_URL": "http://1.2.3.4:8080", "FLOCKS_PROXY_BYPASS": "a.example, b.example"})
    assert overridden.all == "http://1.2.3.4:8080" and overridden.https is None
    assert overridden.bypass == ("a.example", "b.example") and overridden.source == "env"

    off = load_proxy_settings(raw, env={"FLOCKS_PROXY_MODE": "off"})
    assert off.mode == "off" and off.proxy_url_for("https://x.example/") is None

    empty = load_proxy_settings({}, env={})
    assert empty.mode == "system" and empty.source == "default"

    broken = load_proxy_settings({"network": {"proxy": {"mode": "manual", "https": "ftp://x"}}}, env={})
    assert broken.mode == "off"


def test_system_mode_follows_environment(monkeypatch):
    for name in proxy_mod.PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:3128")
    monkeypatch.setenv("NO_PROXY", "internal.corp,.svc.local")
    settings = load_proxy_settings({"network": {"proxy": {"mode": "system", "bypass": ["extra.example"]}}}, env={})
    assert settings.proxy_url_for("https://api.github.com/") == "http://corp-proxy:3128"
    assert settings.proxy_url_for("https://internal.corp/") is None
    assert settings.proxy_url_for("https://a.svc.local/") is None
    assert settings.proxy_url_for("https://extra.example/") is None
    assert settings.proxy_url_for("http://10.1.1.1/") is None  # private defaults still apply
    monkeypatch.delenv("HTTPS_PROXY")
    assert settings.proxy_url_for("https://api.github.com/") is None


def test_cached_settings_follow_config_file(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "flocks.json"
    config_file.write_text('{"network": {"proxy": {"mode": "off"}}}', encoding="utf-8")
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(proxy_mod, "_config_path", lambda: config_file)
    monkeypatch.setattr(proxy_mod, "_read_raw", lambda: __import__("json").loads(config_file.read_text()))
    proxy_mod.invalidate_proxy_settings()
    assert proxy_mod.get_proxy_settings().mode == "off"
    config_file.write_text('{"network": {"proxy": {"mode": "manual", "https": "http://p:1"}}}', encoding="utf-8")
    os.utime(config_file, (config_file.stat().st_atime, config_file.stat().st_mtime + 5))
    proxy_mod._cached_check = 0.0  # skip the 1s stat throttle
    assert proxy_mod.get_proxy_settings().mode == "manual"
    proxy_mod.invalidate_proxy_settings()


# --------------------------------------------------------------------------- environment hand-off


def test_apply_process_environment_manual_off_system(monkeypatch):
    env: dict = {"HTTP_PROXY": "http://old:1", "no_proxy": "old.example", "PATH": "/bin"}
    applied = apply_process_environment(manual(username="u", password="p"), env=env)
    assert env["HTTPS_PROXY"] == "http://u:p@proxy.test:3128" == env["https_proxy"]
    assert "old.example" not in env["NO_PROXY"] and "10.0.0.0/8" in env["NO_PROXY"] and ".corp.example" not in env["NO_PROXY"]
    assert set(applied) >= {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}

    apply_process_environment(ProxySettings.build(mode="off"), env=env)
    assert "HTTPS_PROXY" not in env and env["NO_PROXY"] == "*" and env["PATH"] == "/bin"

    env2 = {"HTTPS_PROXY": "http://mine:9"}
    assert apply_process_environment(ProxySettings.build(mode="system"), env=env2) == {"HTTPS_PROXY": "http://mine:9"}
    assert env2 == {"HTTPS_PROXY": "http://mine:9"}


def test_child_env_and_requests_and_chromium():
    settings = manual(bypass=["*.corp.example"])
    env = child_env({"A": "1", "HTTP_PROXY": "http://stale:1"}, settings=settings)
    assert env["A"] == "1" and env["HTTPS_PROXY"] == "http://proxy.test:3128"
    assert requests_proxies("https://api.example/", settings) == {"http": "http://proxy.test:3128", "https": "http://proxy.test:3128"}
    assert requests_proxies("https://x.corp.example/", settings) == {"http": None, "https": None}
    args = chromium_proxy_args(settings)
    assert args[0] == "--proxy-server=http://proxy.test:3128"
    assert ".corp.example" in args[1] and "10.0.0.0/8" in args[1] and "<local>" in args[1]
    socks = chromium_proxy_args(ProxySettings.build(mode="manual", all="socks5h://127.0.0.1:7890"))
    assert socks[0] == "--proxy-server=socks5://127.0.0.1:7890"
    assert chromium_proxy_args(ProxySettings.build(mode="off")) == []
    assert DEFAULT_BYPASS_RULES[0] == "localhost"


def test_child_env_can_hide_socks_from_http_only_children():
    settings = ProxySettings.build(mode="manual", https="socks5h://s:1080", http="http://h:3128", bypass=["*.corp.example"])
    env = child_env({"PATH": "/bin"}, settings, socks_capable=False)
    assert env["HTTP_PROXY"] == "http://h:3128" and "HTTPS_PROXY" not in env and "https_proxy" not in env
    assert ".corp.example" in env["NO_PROXY"]
    assert child_env({"PATH": "/bin"}, settings)["HTTPS_PROXY"] == "socks5h://s:1080"


def test_system_mode_restores_the_startup_environment(monkeypatch):
    for name in proxy_mod.PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://corp.proxy:3128")
    monkeypatch.setenv("NO_PROXY", "corp.example")
    monkeypatch.setattr(proxy_mod, "_startup_env", None)

    apply_process_environment(ProxySettings.build(mode="off"))
    assert "HTTPS_PROXY" not in os.environ and os.environ["NO_PROXY"] == "*"
    apply_process_environment(ProxySettings.build(mode="manual", all="socks5h://127.0.0.1:1080"))
    assert os.environ["ALL_PROXY"] == "socks5h://127.0.0.1:1080"

    # back to system: exactly what the operator exported, nothing of the modes in between
    applied = apply_process_environment(ProxySettings.build(mode="system"))
    assert os.environ["HTTPS_PROXY"] == "http://corp.proxy:3128" and os.environ["NO_PROXY"] == "corp.example"
    assert "ALL_PROXY" not in os.environ and "all_proxy" not in os.environ
    assert applied == {"HTTPS_PROXY": "http://corp.proxy:3128", "NO_PROXY": "corp.example"}
    assert ProxySettings.build(mode="system").resolved().proxy_url_for("https://api.openai.com/") == "http://corp.proxy:3128"


def test_startup_snapshot_survives_into_child_processes(monkeypatch):
    """The CLI rewrites os.environ before spawning the server: the child must still learn the operator's variables."""
    import json

    for name in proxy_mod.PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(proxy_mod.STARTUP_ENV_VAR, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:3128")
    monkeypatch.setattr(proxy_mod, "_startup_env", None)

    # parent CLI process: config says off
    apply_process_environment(ProxySettings.build(mode="off"))
    handed_down = json.loads(os.environ[proxy_mod.STARTUP_ENV_VAR])
    assert handed_down == {"HTTPS_PROXY": "http://corp-proxy:3128"}
    assert "HTTPS_PROXY" not in os.environ and os.environ["NO_PROXY"] == "*"

    # child process: inherits the rewritten environment (NO_PROXY=* and the hand-down variable)
    monkeypatch.setattr(proxy_mod, "_startup_env", None)
    apply_process_environment(ProxySettings.build(mode="system"))
    assert os.environ["HTTPS_PROXY"] == "http://corp-proxy:3128" and "NO_PROXY" not in os.environ
    assert ProxySettings.build(mode="system").resolved().proxy_url_for("https://api.openai.com/") == "http://corp-proxy:3128"

    # garbage in the hand-down variable is ignored, not fatal
    monkeypatch.setattr(proxy_mod, "_startup_env", None)
    monkeypatch.setenv(proxy_mod.STARTUP_ENV_VAR, "{not json")
    assert proxy_mod._remember_startup_env() == {"HTTPS_PROXY": "http://corp-proxy:3128"}


def test_off_mode_hides_addresses_from_the_effective_view():
    public = ProxySettings.build(mode="off", https="http://proxy.test:3128").public_dict()
    assert public["effective"]["active"] is False
    assert public["effective"]["http"] is None and public["effective"]["https"] is None
    assert public["https"] == "http://proxy.test:3128"  # the stored value itself stays visible


# --------------------------------------------------------------------------- real sockets


@pytest.fixture
async def stack():
    proxy = await RecordingProxy().start()
    target = await EchoServer().start()
    try:
        yield proxy, target
    finally:
        await proxy.stop()
        await target.stop()


async def test_httpx_routing_transport_uses_proxy_and_whitelist(stack):
    from flocks.net.http import async_client

    proxy, target = stack
    # "<-loopback>" lets 127.0.0.1 through the proxy; then whitelist a port to prove bypass
    settings = ProxySettings.build(mode="manual", http=proxy.http_url, https=proxy.http_url, bypass_private=False, bypass=[LOOPBACK])
    async with async_client(settings=settings, timeout=5) as client:
        response = await client.get(f"{target.url}/via-proxy")
        assert response.status_code == 200 and response.json()["path"] == "/via-proxy"
    assert proxy.targets == [("http", "127.0.0.1", target.port)]

    bypass = ProxySettings.build(
        mode="manual", http=proxy.http_url, https=proxy.http_url, bypass_private=False, bypass=[LOOPBACK, f"127.0.0.1:{target.port}"]
    )
    async with async_client(settings=bypass, timeout=5) as client:
        response = await client.get(f"{target.url}/direct")
        assert response.status_code == 200
    assert len(proxy.targets) == 1  # second request never touched the proxy
    assert target.hits == 2


async def test_httpx_socks_routing(stack):
    from flocks.net.http import async_client

    proxy, target = stack
    settings = ProxySettings.build(mode="manual", all=proxy.socks_url, bypass_private=False, bypass=[LOOPBACK])
    async with async_client(settings=settings, timeout=5) as client:
        response = await client.get(f"{target.url}/socks")
        assert response.status_code == 200
    assert proxy.targets == [("socks5", "127.0.0.1", target.port)]


async def test_httpx_client_ignores_ambient_env_when_direct(stack, monkeypatch):
    from flocks.net.http import async_client

    proxy, target = stack
    monkeypatch.setenv("HTTP_PROXY", proxy.http_url)
    settings = ProxySettings.build(mode="off")
    async with async_client(settings=settings, timeout=5) as client:
        assert (await client.get(f"{target.url}/off")).status_code == 200
    assert proxy.targets == []


def test_sync_client_routes_through_proxy(stack_sync):
    from flocks.net.http import sync_client

    proxy, target = stack_sync
    settings = ProxySettings.build(mode="manual", all=proxy.http_url, bypass_private=False, bypass=[LOOPBACK])
    with sync_client(settings=settings, timeout=5) as client:
        assert client.get(f"{target.url}/sync").status_code == 200
    assert proxy.targets[-1] == ("http", "127.0.0.1", target.port)


@pytest.fixture
def stack_sync():
    import asyncio
    import threading

    loop = asyncio.new_event_loop()
    proxy = RecordingProxy()
    target = EchoServer()

    async def boot():
        await proxy.start()
        await target.start()

    loop.run_until_complete(boot())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield proxy, target
    finally:
        async def shutdown():
            await proxy.stop()
            await target.stop()

        asyncio.run_coroutine_threadsafe(shutdown(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


async def test_aiohttp_session_uses_socks_connector(stack):
    from aiohttp_socks import ProxyConnector

    from flocks.net.aio import make_session, session_kwargs

    proxy, target = stack
    settings = ProxySettings.build(mode="manual", all=proxy.socks_url, bypass_private=False, bypass=[LOOPBACK])
    async with make_session(target_url=target.url, settings=settings) as session:
        async with session.get(f"{target.url}/aio") as response:
            assert response.status == 200
    assert proxy.targets == [("socks5", "127.0.0.1", target.port)] and proxy.socks_users == [None]

    direct = ProxySettings.build(mode="manual", all=proxy.socks_url, bypass_private=False, bypass=[LOOPBACK, "127.0.0.1"])
    assert session_kwargs(target_url=target.url, settings=direct) == {"trust_env": False}
    kwargs = session_kwargs(target_url=target.url, settings=settings)
    assert isinstance(kwargs["connector"], ProxyConnector) and kwargs["trust_env"] is False
    await kwargs["connector"].close()


async def test_aiohttp_socks5h_with_credentials(stack):
    """python-socks does not know the ``socks5h`` spelling: the URL is normalised, credentials are offered."""
    from flocks.net.aio import make_session

    proxy, target = stack
    settings = ProxySettings.build(
        mode="manual", all=proxy.socks5h_url, username="corp user", password="p@ss", bypass_private=False, bypass=[LOOPBACK]
    )
    assert settings.proxy_url_for(target.url) == proxy.socks5h_url.replace("://", "://corp%20user:p%40ss@")
    async with make_session(target_url=target.url, settings=settings) as session:
        async with session.get(f"{target.url}/socks5h") as response:
            assert response.status == 200
    assert proxy.targets == [("socks5", "127.0.0.1", target.port)]
    assert proxy.socks_users == ["corp user"]  # percent-decoded before the handshake


async def test_aiohttp_socks5h_lets_the_proxy_resolve_names(stack):
    """socks5h hands the name to the proxy (ATYP=3); plain socks5 resolves locally and sends the address."""
    from flocks.net.aio import make_session

    proxy, target = stack
    remote = ProxySettings.build(mode="manual", all=proxy.socks5h_url, bypass_private=False, bypass=[LOOPBACK])
    async with make_session(target_url=f"http://localhost:{target.port}", settings=remote) as session:
        async with session.get(f"http://localhost:{target.port}/rdns") as response:
            assert response.status == 200
    assert proxy.targets[-1] == ("socks5", "localhost", target.port)

    local = ProxySettings.build(mode="manual", all=proxy.socks_url, bypass_private=False, bypass=[LOOPBACK])
    async with make_session(target_url=f"http://localhost:{target.port}", settings=local) as session:
        async with session.get(f"http://localhost:{target.port}/local") as response:
            assert response.status == 200
    assert proxy.targets[-1][0] == "socks5" and proxy.targets[-1][1] in ("127.0.0.1", "::1")


async def test_aiohttp_http_proxy_credentials_go_as_proxy_auth(stack):
    """Credentials travel as ``proxy_auth`` (aiohttp would read the URL userinfo too; we keep the URL clean)."""
    from flocks.net.aio import make_session, session_kwargs

    proxy, target = stack
    settings = ProxySettings.build(
        mode="manual", all=proxy.http_url, username="corp", password="s3cret", bypass_private=False, bypass=[LOOPBACK]
    )
    kwargs = session_kwargs(target_url=target.url, settings=settings)
    assert kwargs["proxy"] == proxy.http_url and kwargs["trust_env"] is False
    assert kwargs["proxy_auth"].login == "corp" and kwargs["proxy_auth"].password == "s3cret"
    async with make_session(target_url=target.url, settings=settings) as session:
        async with session.get(f"{target.url}/authed") as response:
            assert response.status == 200
    assert proxy.targets == [("http", "127.0.0.1", target.port)]
    assert proxy.proxy_auth == ["Basic Y29ycDpzM2NyZXQ="]


async def test_aiohttp_session_uses_http_connect_proxy(stack):
    from flocks.net.aio import make_session

    proxy, target = stack
    settings = ProxySettings.build(mode="manual", all=proxy.http_url, bypass_private=False, bypass=[LOOPBACK])
    async with make_session(target_url=target.url, settings=settings) as session:
        async with session.get(f"{target.url}/aio-http") as response:
            assert response.status == 200
    assert proxy.targets and proxy.targets[0][0] in ("connect", "http")


def test_transport_failure_surfaces_as_httpx_error():
    from flocks.net.http import sync_client

    settings = ProxySettings.build(mode="manual", all="http://127.0.0.1:9", bypass_private=False, bypass=[LOOPBACK])
    with sync_client(settings=settings, timeout=2) as client:
        with pytest.raises(httpx.HTTPError):
            client.get("http://127.0.0.1:1/never")
