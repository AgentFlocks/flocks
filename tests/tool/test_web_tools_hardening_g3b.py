"""G3b webfetch/websearch 加固回归测试（TOL-0636/0641-0644/0663）。"""
from __future__ import annotations

import asyncio
import os

import pytest

from flocks.tool.web.webfetch import _ssrf_error, html_to_markdown


def test_html_to_markdown_tag_boundaries():
    """<b> 不得吞掉 <body>/<br>，<i> 不得吞掉 <img>，<p> 不得吞掉 <pre>。"""
    html = "<html><body><h1>T</h1><p>a <b>b</b> &amp; c</p><br><img src=x><pre>code</pre><a href='/u'>L</a></body></html>"
    out = html_to_markdown(html)
    assert "**b**" in out and "# T" in out and "& c" in out and "[L](/u)" in out
    assert "**<h1>" not in out and not out.startswith("**")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/api/health",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/", "http://192.168.1.1/", "http://172.16.0.1/", "http://0.0.0.0/",
])
def test_ssrf_guard_blocks_internal(monkeypatch, url):
    monkeypatch.delenv("FLOCKS_WEBFETCH_ALLOW_INTERNAL", raising=False)
    err = _ssrf_error(url)
    assert err and "Blocked request to internal" in err


def test_ssrf_guard_allowlist_and_dns_failure(monkeypatch):
    monkeypatch.setenv("FLOCKS_WEBFETCH_ALLOW_INTERNAL", "127.0.0.1")
    assert _ssrf_error("http://127.0.0.1:8088/page.html") is None
    monkeypatch.delenv("FLOCKS_WEBFETCH_ALLOW_INTERNAL", raising=False)
    # 解析失败不当作 SSRF 拦截，交给请求本身报错
    assert _ssrf_error("http://no.such.host.invalid/") is None


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
