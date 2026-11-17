"""G3b webfetch/websearch 加固回归测试（TOL-0636/0641-0644/0663）。"""
from __future__ import annotations

import asyncio
import os

import pytest

from flocks.tool.web.webfetch import _ssrf_error, html_to_markdown, is_loopback_host


def test_html_to_markdown_tag_boundaries():
    """<b> 不得吞掉 <body>/<br>，<i> 不得吞掉 <img>，<p> 不得吞掉 <pre>。"""
    html = "<html><body><h1>T</h1><p>a <b>b</b> &amp; c</p><br><img src=x><pre>code</pre><a href='/u'>L</a></body></html>"
    out = html_to_markdown(html)
    assert "**b**" in out and "# T" in out and "& c" in out and "[L](/u)" in out
    assert "**<h1>" not in out and not out.startswith("**")


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/", "http://192.168.1.1/", "http://172.16.0.1/", "http://0.0.0.0/",
    "http://[fe80::1]/", "http://[fd00::1]/", "http://[::ffff:10.0.0.1]/",
])
def test_ssrf_guard_blocks_internal(monkeypatch, url):
    monkeypatch.delenv("FLOCKS_WEBFETCH_ALLOW_INTERNAL", raising=False)
    err = _ssrf_error(url)
    assert err and "Blocked request to internal" in err


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/api/health",
    "http://127.8.9.10/",
    "http://localhost:8088/page.html",
    "http://LOCALHOST./",
    "http://[::1]:8080/",
    "http://[::ffff:127.0.0.1]/",
])
def test_ssrf_guard_allows_loopback(monkeypatch, url):
    """本机回环（localhost、127.0.0.0/8、::1）不拦：本地服务和代理都在这上面。"""
    monkeypatch.delenv("FLOCKS_WEBFETCH_ALLOW_INTERNAL", raising=False)
    assert _ssrf_error(url) is None


def test_ssrf_guard_allowlist_and_dns_failure(monkeypatch):
    monkeypatch.setenv("FLOCKS_WEBFETCH_ALLOW_INTERNAL", "10.0.0.5")
    assert _ssrf_error("http://10.0.0.5:8088/page.html") is None
    monkeypatch.delenv("FLOCKS_WEBFETCH_ALLOW_INTERNAL", raising=False)
    assert "Blocked request to internal" in (_ssrf_error("http://10.0.0.5:8088/page.html") or "")
    # 解析失败不当作 SSRF 拦截，交给请求本身报错
    assert _ssrf_error("http://no.such.host.invalid/") is None


@pytest.mark.parametrize(("host", "expected"), [
    ("localhost", True), ("LOCALHOST.", True), ("api.localhost", True),
    ("127.0.0.1", True), ("127.1.2.3", True), ("::1", True), ("[::1]", True),
    ("::1%lo0", True), ("::1%25lo0", True), ("::ffff:127.0.0.1", True),
    ("localhost.example.com", False), ("evil-localhost", False), ("10.0.0.1", False),
    # a % after an IPv4 address makes a name, not an address
    ("127.0.0.1%25.attacker.example", False), ("127.0.0.1%eth0", False),
    ("0.0.0.0", False), ("::", False), ("example.com", False), ("", False), (None, False),
])
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected


@pytest.mark.asyncio
async def test_webfetch_reads_a_page_served_on_loopback(monkeypatch):
    """真起一个只听 127.0.0.1 的服务，webfetch 用 127.0.0.1 和 localhost 都能抓到。"""
    import http.server
    import threading

    from flocks.tool.registry import ToolContext
    from flocks.tool.web.webfetch import webfetch_tool

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = "<html><body><h1>local ok</h1></body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    monkeypatch.delenv("FLOCKS_WEBFETCH_ALLOW_INTERNAL", raising=False)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        ctx = ToolContext(session_id="s", message_id="m")
        for host in ("127.0.0.1", "localhost"):
            result = await webfetch_tool(ctx, url=f"http://{host}:{port}/page.html")
            assert result.success, result.error
            assert "local ok" in result.output
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_websearch_timeout_message(monkeypatch):
    import aiohttp
    import flocks.tool.web.websearch as ws
    from flocks.tool.registry import ToolContext

    class _Sess:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            raise TimeoutError()  # asyncio/aiohttp 超时异常无 message

    monkeypatch.setattr(aiohttp, "ClientSession", _Sess)
    res = await ws.websearch_tool(ToolContext(session_id="s", message_id="m"), query="x")
    assert res.success is False and res.error == "Search request timed out"
