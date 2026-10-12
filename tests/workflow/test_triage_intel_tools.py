"""Both triage workflows must reach the ThreatBook tools that are actually registered.

MCP tools register as "<server>_<tool>" and the ThreatBook server is called
threatbook_mcp, so the old bare names silently returned no intel for every alert.
"""
from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STREAM = ROOT / ".flocks/flockshub/plugins/workflows/stream_alert_triage/workflow.json"
TDP = ROOT / ".flocks/plugins/workflows/tdp_alert_triage/workflow.json"


class FakeTools:
    """Workflow ``tool`` facade: unknown names fail the way the adapter reports them."""

    def __init__(self, registered: dict[str, object]) -> None:
        self.registered = registered
        self.calls: list[tuple[str, dict]] = []

    def run_safe(self, name: str, **kwargs: object) -> dict:
        self.calls.append((name, kwargs))
        handler = self.registered.get(name)
        if handler is None:
            # The exact text FlocksToolAdapter.run_safe returns for an unregistered name.
            return {"success": False, "text": "", "obj": None, "error": f"Tool not found: {name!r}"}
        if isinstance(handler, Exception):
            return {"success": False, "text": "", "obj": None, "error": str(handler)}
        return {"success": True, "text": f"{name}:{json.dumps(kwargs)}", "obj": handler, "error": None}


def _node_code(path: Path, node_id: str) -> str:
    workflow = json.loads(path.read_text(encoding="utf-8"))
    return next(node["code"] for node in workflow["nodes"] if node["id"] == node_id)


def _stream_intel(tools: FakeTools):
    """Load _prepare_intel and the definitions it needs from concurrent_triage."""
    tree = ast.parse(_node_code(STREAM, "concurrent_triage"))
    wanted = {"_is_public_ip", "_run_intel_tool", "_prepare_intel"}
    names = {"INTEL_TOOLS", "_intel_tool_names", "_intel_tool_lock"}
    body = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", None) in names for t in node.targets))
    ]
    namespace: dict[str, object] = {"tool": tools, "json": json, "threading": threading}
    exec("import ipaddress", namespace)
    exec(compile(ast.Module(body=body, type_ignores=[]), str(STREAM), "exec"), namespace)
    return namespace["_prepare_intel"]


ALERT = {
    "iocs": [
        {"type": "ip", "value": "8.8.8.8"},
        {"type": "ip", "value": "10.0.0.5"},
        {"type": "ip", "value": "1.1.1.1"},
        {"type": "url", "value": "http://evil.example/login.php"},
        {"type": "domain", "value": "evil.example"},
    ],
    "vuln_id": "CVE-2021-44228",
}
MCP_TOOLS = {
    "threatbook_mcp_ip_query": {"judgments": ["IDC"]},
    "threatbook_mcp_domain_query": {"judgments": ["Malware"]},
    "threatbook_mcp_vuln_query": {"vuln_id": "CVE-2021-44228", "name": "Log4Shell"},
}


def test_stream_triage_uses_the_registered_threatbook_mcp_tools() -> None:
    tools = FakeTools(dict(MCP_TOOLS))
    intel_results, intel_content, vuln_info, _ = _stream_intel(tools)(ALERT)

    assert [(item["type"], item["value"]) for item in intel_results] == [
        ("ip", "8.8.8.8"), ("ip", "1.1.1.1"), ("domain", "evil.example"),
    ]
    assert "threatbook_mcp_ip_query" in intel_content
    assert vuln_info == {"vuln_id": "CVE-2021-44228", "name": "Log4Shell"}
    # Private addresses are still skipped and the resolved tool is reused.
    assert [name for name, _ in tools.calls].count("threatbook_mcp_ip_query") == 2
    assert ("threatbook_mcp_vuln_query", {"vuln_id": "CVE-2021-44228"}) in tools.calls


def test_stream_triage_falls_back_to_the_older_threatbook_server_name() -> None:
    tools = FakeTools({"threatbook_ip_query": {"judgments": []}, "threatbook_vuln_query": {"name": "x"}})
    intel_results, _, vuln_info, _ = _stream_intel(tools)(ALERT)

    assert [item["value"] for item in intel_results] == ["8.8.8.8", "1.1.1.1"]
    assert vuln_info == {"name": "x"}
    names = [name for name, _ in tools.calls]
    # The missing name is tried once per run, not once per indicator.
    assert names.count("threatbook_mcp_ip_query") == 1
    assert names.count("threatbook_ip_query") == 2


def test_stream_triage_reports_a_missing_intel_source_once(capsys: pytest.CaptureFixture[str]) -> None:
    tools = FakeTools({})
    prepare = _stream_intel(tools)
    intel_results, intel_content, vuln_info, vuln_content = prepare(ALERT)
    prepare(ALERT)

    assert intel_results == [] and vuln_info == {}
    assert intel_content == "（无可用情报数据）" and vuln_content == "（无可用漏洞情报数据）"
    out = capsys.readouterr().out
    for kind in ("ip", "domain", "vuln"):
        assert out.count(f"no {kind} intel tool is registered") == 1
    assert "no url intel tool" not in out
    # After the first miss no lookups are repeated within the run.
    assert len(tools.calls) == 8


def test_stream_triage_keeps_a_real_intel_failure_without_retrying_other_names() -> None:
    tools = FakeTools({"threatbook_mcp_ip_query": RuntimeError("quota exceeded"),
                       "threatbook_ip_query": {"judgments": []}})
    intel_results, _, _, _ = _stream_intel(tools)({"iocs": [{"type": "ip", "value": "8.8.8.8"}]})

    assert intel_results == []
    assert [name for name, _ in tools.calls] == ["threatbook_mcp_ip_query"]


@pytest.mark.parametrize("upstream", [
    "Tool not found: internal_resource",
    "Remote error: Tool not found: 'threatbook_mcp_ip_query'",
])
def test_an_intel_service_error_is_not_mistaken_for_a_missing_tool(upstream: str) -> None:
    error = RuntimeError(upstream)
    tools = FakeTools({"threatbook_mcp_ip_query": error, "threatbook_ip_query": {"judgments": []}})
    prepare = _stream_intel(tools)
    prepare({"iocs": [{"type": "ip", "value": "8.8.8.8"}]})
    prepare({"iocs": [{"type": "ip", "value": "1.1.1.1"}]})
    # The registered tool stays in use for the rest of the run.
    assert [name for name, _ in tools.calls] == ["threatbook_mcp_ip_query", "threatbook_mcp_ip_query"]

    tdp_tools = FakeTools({"threatbook_mcp_ip_query": error, "threatbook_ip_query": {"judgments": []},
                           "threatbook_mcp_vuln_query": error, "threatbook_vuln_query": {"name": "x"}})
    _run_tdp_node("query_threat_intel", tdp_tools, {"iocs": [{"type": "ip", "value": "8.8.8.8"}]})
    _run_tdp_node("query_vuln", tdp_tools, {"parsed_alert": {"vuln_id": "CVE-2021-44228"}})
    assert [name for name, _ in tdp_tools.calls] == ["threatbook_mcp_ip_query", "threatbook_mcp_vuln_query"]


def _run_tdp_node(node_id: str, tools: FakeTools, inputs: dict) -> dict:
    namespace: dict[str, object] = {"tool": tools, "inputs": inputs, "outputs": {}}
    exec(compile(_node_code(TDP, node_id), f"{TDP}#{node_id}", "exec"), namespace)
    return namespace["outputs"]


def test_tdp_triage_queries_threatbook_mcp_intel_and_vulnerabilities() -> None:
    tools = FakeTools(dict(MCP_TOOLS))
    outputs = _run_tdp_node("query_threat_intel", tools, {"iocs": ALERT["iocs"]})

    assert [(item["type"], item["value"]) for item in outputs["intel_results"]] == [
        ("ip", "8.8.8.8"), ("ip", "1.1.1.1"), ("domain", "evil.example"),
    ]
    assert outputs["has_intel"] is True

    vuln = _run_tdp_node("query_vuln", FakeTools(dict(MCP_TOOLS)), {"parsed_alert": {"vuln_id": "CVE-2021-44228"}})
    assert vuln["vuln_info"] == {"vuln_id": "CVE-2021-44228", "name": "Log4Shell"}
    assert vuln["has_vuln_info"] is True


def test_tdp_triage_falls_back_and_records_missing_vulnerability_tools() -> None:
    legacy = FakeTools({"threatbook_vuln_query": {"name": "legacy"}})
    vuln = _run_tdp_node("query_vuln", legacy, {"parsed_alert": {"vuln_id": "CVE-2021-44228"}})
    assert vuln["vuln_info"] == {"name": "legacy"}
    assert [name for name, _ in legacy.calls] == ["threatbook_mcp_vuln_query", "threatbook_vuln_query"]

    missing = _run_tdp_node("query_vuln", FakeTools({}), {"parsed_alert": {"vuln_id": "CVE-2021-44228"}})
    assert missing["has_vuln_info"] is False
    assert missing["vuln_info"]["vuln_id"] == "CVE-2021-44228"
