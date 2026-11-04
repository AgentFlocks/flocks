"""G3b ssh_host_cmd 黑名单补强回归（TOL-0678/0681）+ webfetch/websearch 参数校验（TOL-0650/0652/0667）。"""
from __future__ import annotations

import asyncio

import pytest

from flocks.tool.security.ssh_host_cmd import classify_command


@pytest.mark.parametrize("cmd", [
    "python3 -c 'import os; os.remove(\"/tmp/x\")'",
    "perl -e 'unlink q(/tmp/x)'",
    "ruby -e 'File.delete(\"/x\")'",
    "node -e 'require(\"fs\").rmSync(\"/x\")'",
    "sh -c 'rm -rf /tmp/x'",
    "bash -c 'ls'",
    "dd if=/dev/zero of=/dev/sda",
    "crontab -r",
    "shred -u /etc/passwd",
    "mkfs.ext4 /dev/sdb",
])
def test_blacklist_blocks_inline_code_and_raw_device_ops(cmd):
    assert classify_command(cmd)[0] == "BLOCKED"


@pytest.mark.parametrize("cmd", [
    "python3 script.py",
    "python3 -m json.tool f.json",
    "node app.js",
    "grep 'rm -rf' /var/log/syslog",
    "crontab -l",
    "cat /etc/crontab",
    "dd if=/dev/urandom bs=1 count=4 | xxd",
    "ps -ef | grep python3",
    "systemctl status nginx",
    "ls 2>&1",
])
def test_blacklist_keeps_read_only_usage_allowed(cmd):
    assert classify_command(cmd)[0] == "ALLOWED"


@pytest.mark.asyncio
async def test_webfetch_rejects_bad_format_and_negative_timeout():
    from flocks.tool.registry import ToolContext
    from flocks.tool.web.webfetch import webfetch_tool
    ctx = ToolContext(session_id="s", message_id="m")
    r1 = await webfetch_tool(ctx, url="http://example.com/", format="json")
    assert r1.success is False and r1.error.startswith("Invalid format 'json'")
    r2 = await webfetch_tool(ctx, url="http://example.com/", timeout=-5)
    assert r2.success is False and r2.error.startswith("Invalid timeout -5")


@pytest.mark.asyncio
async def test_websearch_rejects_bad_type():
    from flocks.tool.registry import ToolContext
    from flocks.tool.web.websearch import websearch_tool
    r = await websearch_tool(ToolContext(session_id="s", message_id="m"), query="x", type="deep2")
    assert r.success is False and r.error.startswith("Invalid search type 'deep2'")
