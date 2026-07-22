import json
from pathlib import Path


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
        "analyze_payload",
        "analyze_response",
    }

    outgoing = [edge for edge in workflow["edges"] if edge["from"] == "join_results"]
    assert outgoing == [{"from": "join_results", "to": "generate_report"}]


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
