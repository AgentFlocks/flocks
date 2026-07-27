from __future__ import annotations

import ast
import inspect
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from flocks.tool.registry import ToolContext, ToolResult
from flocks.tool.tool_loader import yaml_to_tool


_ROOT = Path(__file__).resolve().parents[2]
_DEVICE_ROOT = _ROOT / ".flocks" / "flockshub" / "plugins" / "tools" / "device"
_HUAWEI_DIR = _DEVICE_ROOT / "huaweicloud_waf_v39"
_HUORONG_DIR = _DEVICE_ROOT / "huorong_edr_v1_0"


def _installed_tool(
    source_dir: Path,
    yaml_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project_root = tmp_path / "project"
    install_dir = project_root / ".flocks" / "plugins" / "tools" / "device" / source_dir.name
    shutil.copytree(source_dir, install_dir)
    monkeypatch.chdir(project_root)
    yaml_path = install_dir / yaml_name
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return raw, yaml_to_tool(raw, yaml_path)


def _script_globals(tool) -> dict[str, Any]:
    return inspect.getclosurevars(tool.handler).nonlocals["fn"].__globals__


def _ctx() -> ToolContext:
    return ToolContext(session_id="device-plugin-test", message_id="device-plugin-test")


def test_huawei_manifests_separate_dispatch_action_from_api_payload():
    event = yaml.safe_load((_HUAWEI_DIR / "hw_waf_event.yaml").read_text(encoding="utf-8"))
    event_properties = event["inputSchema"]["properties"]

    assert "event_list" in event_properties["action"]["enum"]
    assert event_properties["event_action"]["enum"] == ["block", "log"]

    policy = yaml.safe_load((_HUAWEI_DIR / "hw_waf_policy.yaml").read_text(encoding="utf-8"))
    policy_properties = policy["inputSchema"]["properties"]

    assert "policy_list" in policy_properties["action"]["enum"]
    assert policy_properties["rule_action"]["type"] == "object"


@pytest.mark.parametrize(
    "handler_path",
    [_HUAWEI_DIR / "hw_waf.handler.py", _HUORONG_DIR / "huorong.handler.py"],
)
def test_tool_result_calls_use_supported_fields(handler_path: Path):
    module = ast.parse(handler_path.read_text(encoding="utf-8"), filename=str(handler_path))
    unsupported: list[tuple[int, str]] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "ToolResult":
            continue
        unsupported.extend(
            (node.lineno, keyword.arg)
            for keyword in node.keywords
            if keyword.arg is not None and keyword.arg not in ToolResult.model_fields
        )

    assert unsupported == []


@pytest.mark.parametrize(
    "yaml_name",
    [
        "hw_waf_host.yaml",
        "hw_waf_policy.yaml",
        "hw_waf_event.yaml",
        "hw_waf_overview.yaml",
    ],
)
@pytest.mark.asyncio
async def test_huawei_handlers_execute_through_script_loader(
    yaml_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, tool = _installed_tool(_HUAWEI_DIR, yaml_name, tmp_path, monkeypatch)
    globals_ = _script_globals(tool)
    monkeypatch.setitem(globals_, "_load_config", lambda *_: SimpleNamespace(project_id="project-1"))

    result = await tool.handler(_ctx(), action="unsupported")

    assert result.success is False
    assert result.error == "Unknown action: unsupported"


@pytest.mark.asyncio
async def test_huawei_event_action_maps_to_api_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, tool = _installed_tool(_HUAWEI_DIR, "hw_waf_event.yaml", tmp_path, monkeypatch)
    globals_ = _script_globals(tool)
    calls: list[dict[str, Any]] = []

    async def fake_request(cfg, method, path, query=None, body=None):
        calls.append({"method": method, "path": path, "query": query, "body": body})
        return ToolResult(success=True, output={})

    monkeypatch.setitem(globals_, "_load_config", lambda *_: SimpleNamespace(project_id="project-1"))
    monkeypatch.setitem(globals_, "_request", fake_request)

    result = await tool.handler(
        _ctx(),
        action="event_list",
        event_action="block",
        page=1,
    )

    assert result.success is True
    assert calls[0]["query"] == {"page": 1, "action": "block"}


@pytest.mark.asyncio
async def test_huawei_rule_action_maps_to_api_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, tool = _installed_tool(_HUAWEI_DIR, "hw_waf_policy.yaml", tmp_path, monkeypatch)
    globals_ = _script_globals(tool)
    calls: list[dict[str, Any]] = []

    async def fake_request(cfg, method, path, query=None, body=None):
        calls.append({"method": method, "path": path, "query": query, "body": body})
        return ToolResult(success=True, output={})

    monkeypatch.setitem(globals_, "_load_config", lambda *_: SimpleNamespace(project_id="project-1"))
    monkeypatch.setitem(globals_, "_request", fake_request)
    rule_action = {"category": "block"}

    result = await tool.handler(
        _ctx(),
        action="cc_rule_create",
        policy_id="policy-1",
        url="/login",
        rule_action=rule_action,
    )

    assert result.success is True
    assert calls[0]["body"] == {"url": "/login", "action": rule_action}


@pytest.mark.parametrize(
    "yaml_name",
    ["huorong_group.yaml", "huorong_clnts.yaml", "huorong_task.yaml"],
)
@pytest.mark.asyncio
async def test_huorong_handlers_execute_through_script_loader(
    yaml_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, tool = _installed_tool(_HUORONG_DIR, yaml_name, tmp_path, monkeypatch)
    globals_ = _script_globals(tool)
    monkeypatch.setitem(
        globals_,
        "_resolve_runtime_config",
        lambda: ("https://huorong.example.com", 30, "secret-id", "secret-key", False),
    )

    result = await tool.handler(_ctx(), action="unsupported")

    assert result.success is False
    assert result.error == "Unknown action: unsupported"
