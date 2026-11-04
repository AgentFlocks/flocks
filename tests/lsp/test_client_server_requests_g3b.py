"""G3b 回归：LSP 客户端必须回应服务端发来的请求（workspace/configuration 等）。

缺陷：``LSPClient._handle_message`` 把所有带 method 的消息当通知，带 id 的服务端请求
（pyright 在 initialize 后发的 workspace/configuration、client/registerCapability）从不回复，
pyright 在收到回复前不处理 textDocument/hover，于是 hover 永远超时。

用一个只讲 stdio JSON-RPC 的假服务端复现：它收到 hover 后，只有在三个服务端请求都得到
回复时才应答；否则 hover 挂起。真 pyright 的端到端在 test_real_pyright 里（未安装则 skip）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from flocks.lsp.client import LSPClient, LSPServerHandle

FAKE_SERVER = textwrap.dedent(
    r'''
    import json, sys

    def read():
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            ch = sys.stdin.buffer.read(1)
            if not ch:
                return None
            header += ch
        length = int([l for l in header.decode().split("\r\n") if l.lower().startswith("content-length")][0].split(":")[1])
        return json.loads(sys.stdin.buffer.read(length))

    def send(msg):
        data = json.dumps(msg).encode()
        sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
        sys.stdout.buffer.flush()

    answered = {}
    pending_hover = None
    while True:
        msg = read()
        if msg is None:
            break
        if msg.get("method") == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {"hoverProvider": True}}})
            # 像 pyright 一样，初始化后立刻向客户端发请求
            send({"jsonrpc": "2.0", "id": "cfg-1", "method": "workspace/configuration",
                  "params": {"items": [{"section": "python"}, {"section": "python.analysis"}]}})
            send({"jsonrpc": "2.0", "id": "reg-1", "method": "client/registerCapability",
                  "params": {"registrations": [{"id": "r1", "method": "workspace/didChangeWatchedFiles"}]}})
            send({"jsonrpc": "2.0", "id": "unknown-1", "method": "server/unknownThing", "params": {}})
            continue
        if "id" in msg and "method" not in msg:
            answered[msg["id"]] = msg
            if pending_hover is not None and all(k in answered for k in ("cfg-1", "reg-1", "unknown-1")):
                send({"jsonrpc": "2.0", "id": pending_hover, "result": {"contents": {"kind": "plaintext", "value": "answered"},
                      "configuration": answered["cfg-1"].get("result"), "unknown": answered.get("unknown-1")}})
                pending_hover = None
            continue
        if msg.get("method") == "textDocument/hover":
            if all(k in answered for k in ("cfg-1", "reg-1", "unknown-1")):
                send({"jsonrpc": "2.0", "id": msg["id"], "result": {"contents": {"kind": "plaintext", "value": "answered"},
                      "configuration": answered["cfg-1"].get("result"), "unknown": answered.get("unknown-1")}})
            else:
                pending_hover = msg["id"]   # 卡住直到客户端回复
            continue
        if msg.get("method") == "shutdown":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
        if msg.get("method") == "exit":
            break
    '''
)


@pytest.fixture
def fake_server(tmp_path):
    script = tmp_path / "fake_lsp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


async def test_client_answers_server_requests_so_hover_completes(fake_server, tmp_path):
    client = await LSPClient.create("fake", LSPServerHandle(process=fake_server), str(tmp_path))
    assert client is not None
    try:
        result = await asyncio.wait_for(
            LSPClient._request(client, "textDocument/hover", {"textDocument": {"uri": "file:///x.py"}, "position": {"line": 0, "character": 0}}, timeout=5),
            timeout=6,
        )
    finally:
        await LSPClient.shutdown(client)
    assert result["contents"]["value"] == "answered"
    # workspace/configuration 按 items 数返回一项一个 null
    assert result["configuration"] == [None, None]
    # 未知的服务端请求得到 JSON-RPC 错误而不是石沉大海
    assert result["unknown"]["error"]["code"] == -32601


@pytest.mark.skipif(shutil.which("pyright-langserver") is None, reason="pyright-langserver not installed")
async def test_real_pyright_hover_answers(tmp_path):
    """真 pyright：hover 在 initialize 后不再永远超时。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyrightconfig.json").write_text("{}", encoding="utf-8")
    src = root / "m.py"
    src.write_text("def greet(name: str) -> str:\n    return 'hi ' + name\n\nvalue = greet('x')\n", encoding="utf-8")
    proc = subprocess.Popen(
        [shutil.which("pyright-langserver"), "--stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(root),
        env={**os.environ, "PYRIGHT_PYTHON_IGNORE_WARNINGS": "1"},
    )
    try:
        client = await LSPClient.create("pyright", LSPServerHandle(process=proc), str(root))
        assert client is not None
        try:
            await LSPClient.open_file(client, str(src))
            hover = None
            for _ in range(5):
                hover = await asyncio.wait_for(
                    LSPClient._request(client, "textDocument/hover", {
                        "textDocument": {"uri": f"file://{src}"}, "position": {"line": 3, "character": 9},
                    }, timeout=20),
                    timeout=25,
                )
                if hover:
                    break
                await asyncio.sleep(1)
        finally:
            await LSPClient.shutdown(client)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert hover and "greet" in json.dumps(hover)
