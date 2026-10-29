import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.workflow import Workflow, WorkflowEngine
from flocks.workflow.repl_runtime import PythonExecRuntime


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "workflows"
    / "tdp_alert_triage"
    / "workflow.json"
)


def _load_workflow() -> dict:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_tdp_alert_triage_exposes_chinese_name() -> None:
    workflow = _load_workflow()

    assert workflow["nameCn"] == "TDP 告警调查工作流"


def test_tdp_alert_triage_has_join_before_report() -> None:
    workflow = _load_workflow()
    join_node = next(node for node in workflow["nodes"] if node["id"] == "join_results")

    assert join_node["type"] == "python"
    assert join_node["join"] is True

    incoming = {
        edge["from"] for edge in workflow["edges"] if edge["to"] == "join_results"
    }
    assert incoming == {
        "query_threat_intel",
        "query_vuln",
        "attack_analysis_result",
    }

    outgoing = [edge for edge in workflow["edges"] if edge["from"] == "join_results"]
    assert outgoing == [{"from": "join_results", "to": "generate_report", "order": 0}]


def test_all_tdp_template_nodes_compile() -> None:
    for node in _load_workflow()["nodes"]:
        if node["type"] == "python":
            compile(node["code"], f"tdp_alert_triage/{node['id']}", "exec")


@pytest.mark.parametrize("workers", [1, 3])
def test_tdp_engine_joins_all_branches_and_writes_readable_report(
    monkeypatch, tmp_path: Path, workers: int,
) -> None:
    """Exercise the shipped graph with synthetic tool/LLM replies, without APIs."""
    calls = []
    writes = []

    class OfflineTools:
        cancel_checker = None

        def run_safe(self, name, **kwargs):
            calls.append((name, kwargs))
            if name == "__mcp_vuln_query":
                assert kwargs == {"vuln_id": "CVE-2024-12345"}
                return {"success": True, "obj": {"id": "CVE-2024-12345"}}
            assert name == "threatbook_url_query"
            return {"success": True, "text": "synthetic-intel"}

        def run(self, name, **kwargs):
            assert name == "write"
            path = Path(kwargs["filePath"])
            assert path.is_relative_to(tmp_path)
            path.write_text(kwargs["content"], encoding="utf-8")
            writes.append(path)
            return "ok"

    def ask(prompt):
        assert "HTTP/1.1 403 Forbidden" in prompt
        assert "synthetic-request" in prompt
        return "<think>synthetic reasoning</think>\n攻击失败：服务器返回 403。"

    monkeypatch.setattr(
        "flocks.workflow.repl_runtime.get_lazy_llm",
        lambda **_: SimpleNamespace(ask=ask),
    )
    monkeypatch.setattr(
        "flocks.workspace.manager.WorkspaceManager.get_instance",
        lambda: SimpleNamespace(get_workspace_dir=lambda: tmp_path),
    )
    engine = WorkflowEngine(
        Workflow.from_dict(_load_workflow()),
        runtime=PythonExecRuntime(tool_registry=OfflineTools()),
        max_parallel_workers=workers,
        node_timeout_s=None,
        history_mode="full",
    )
    result = engine.run(initial_inputs={"alert_data": {
        "attacker": "192.168.1.10",
        "victim": "192.168.1.20",
        "threat": {"name": "synthetic-alert", "msg": "CVE-2024-12345"},
        "net": {"http": {
            "reqs_host": "192.168.1.20", "raw_url": "/synthetic",
            "req_body": "synthetic-request",
            "resp_line": "HTTP/1.1 403 Forbidden", "status": 403,
        }},
    }}, retain_history=True)

    assert len(result.history) == 6
    assert all(step.error is None for step in result.history)
    assert [step.node_id for step in result.history][-2:] == ["join_results", "generate_report"]
    assert len(writes) == 1
    outputs = result.history[-1].outputs
    report = writes[0].read_text(encoding="utf-8")
    assert report == outputs["final_report"]
    assert outputs["attack_verdict"] == "attack_failed"
    assert result.history[0].outputs["parsed_alert"]["vuln_id"] == "CVE-2024-12345"
    assert "synthetic-intel" in report and "CVE-2024-12345" in report
    assert "HTTP/1.1 403 Forbidden" in report
    assert "<think>" not in report
    assert "\n## 1." in report
    assert "\\n" not in report
    assert {name for name, _ in calls} == {"threatbook_url_query", "__mcp_vuln_query"}


def test_receive_alert_parses_tdp_http_sample() -> None:
    workflow = _load_workflow()
    receive_alert = next(node for node in workflow["nodes"] if node["id"] == "receive_alert")

    sample = {
        "data": [
            {
                "attacker": "10.155.58.254",
                "victim": "192.168.233.131",
                "server_ip": "192.168.233.131",
                "external_ip": "10.155.58.254",
                "external_port": 57778,
                "server_port": 1111,
                "url_host": "192.168.233.131",
                "url_path": "/webshell.aspx",
                "data": "192.168.233.131:1111/webshell.aspx",
                "net": {
                    "is_https": 0,
                    "src_ip": "10.155.58.254",
                    "src_port": 57778,
                    "dest_ip": "192.168.233.131",
                    "dest_port": 1111,
                    "app_proto": "http",
                    "http": {
                        "reqs_line": "OPTIONS /webshell.aspx HTTP/1.1",
                        "reqs_header": "Host: 192.168.233.131:1111",
                        "reqs_host": "192.168.233.131:1111",
                        "url": "/webshell.aspx",
                        "raw_url": "/webshell.aspx",
                        "resp_line": "HTTP/1.0 501 Unsupported method ('OPTIONS')",
                        "resp_header": "Content-Type: text/html;charset=utf-8",
                        "resp_body": "<html>Error response</html>",
                        "status": 501,
                        "domain": "192.168.233.131",
                    },
                },
                "assets": {
                    "ip": "192.168.233.131",
                    "name": ["ip_1_16_78_52M24"],
                },
                "threat": {
                    "name": "Webshell扫描",
                    "msg": "在HTTP流量中检测到Webshell扫描。",
                    "result": "unknown",
                    "failed_by": ["http_status_4XX"],
                    "confidence": 60,
                    "severity": 1,
                    "tag": ["Mitre: T1505.003: Web Shell"],
                },
            }
        ]
    }

    env = {"inputs": {"alert_data": sample}, "outputs": {}}
    exec(receive_alert["code"], env, env)
    parsed = env["outputs"]["parsed_alert"]

    assert parsed["src_ip"] == "10.155.58.254"
    assert parsed["dst_ip"] == "192.168.233.131"
    assert parsed["src_port"] == 57778
    assert parsed["dst_port"] == 1111
    assert parsed["protocol"] == "http"
    assert parsed["url"] == "http://192.168.233.131:1111/webshell.aspx"
    assert parsed["status"] == 501
    assert parsed["alert_type"] == "Webshell扫描"
    assert parsed["failed_by"] == ["http_status_4XX"]
    assert parsed["vuln_id"] == ""
    assert parsed["iocs"] == [
        {"type": "ip", "value": "10.155.58.254"},
        {"type": "ip", "value": "192.168.233.131"},
        {"type": "url", "value": "http://192.168.233.131:1111/webshell.aspx"},
    ]
