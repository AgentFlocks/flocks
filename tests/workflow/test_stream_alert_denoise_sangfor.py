from __future__ import annotations

import ast
import asyncio
import copy
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import flocks.config
import pytest

from flocks.ingest.syslog.parser import parse_syslog
from flocks.ingest.syslog.listener import SyslogUDPProtocol
from flocks.ingest.syslog.sangfor import normalize_sangfor_alert
from flocks.workflow import Workflow, WorkflowEngine
from flocks.workflow.repl_runtime import PythonExecRuntime

WORKFLOW_ROOT = Path(__file__).resolve().parents[2] / ".flocks/flockshub/plugins/workflows"

# Compact fixtures preserve the documented field types, enum differences,
# dual alarm timestamp and list-valued URL/status/IOC fields.
EVENT = {
    "eventKey": "117830036",
    "ruleId": "117830036",
    "firstTime": 1625553800,
    "log_time": 1625553809,
    "ip": "192.0.2.5",
    "src_ip": "0.0.0.0",
    "dst_ip": "192.0.2.5",
    "src_port": "",
    "dst_port": "445",
    "attack_state": 0,
    "threat_level": 3,
    "suspect_level": 2,
    "emergency": "emergent",
    "sub_attack_name": "勒索",
    "module_name": "感染恶意文件",
    "stage": 4,
    "brief": "主机访问了勒索病毒通信域名",
    "event_content": "勒索通信",
    "event_evidence": "DNS 查询 ransom.invalid |! 保留分隔符",
    "principle": "利用 CVE-2017-0199",
    "solution": "隔离并查杀",
    "tag": ["Petya", "勒索病毒"],
    "hostName": "host-a",
    "branch_name": "Asset1",
}
ALARM = {
    "alert_id": 2141000061,
    "dev_id": "1740D4E7",
    "hash_id": "rule-a",
    "attack_ip": "198.51.100.10",
    "suffer_ip": "192.0.2.5",
    "attack_port": 0,
    "suffer_port": 58669,
    "attack_state": 3,
    "first_time": 1643163080,
    "last_time": 1643163089,
    "module_type_name": "账号攻击",
    "attack_type_name": "账号爆破",
    "sub_attack_type_name": "口令暴力破解",
    "reliability": 3,
    "emergency": "important",
    "attack_direction": 2,
    "asset_direction": 1,
    "invasion_stage": 6,
    "url": [],
    "status_code": [],
    "ioc": ["c2.invalid", "203.0.113.9"],
    "event_desc": "命中自定义威胁情报指标",
    "event_evidence": "详细举证",
    "analyze_suggest": "核查终端进程",
    "suggest": "隔离主机",
    "suffer_branch_name": "Asset1",
    "tags": "自定义威胁情报",
}


def _definition(workflow_id: str = "stream_alert_denoise") -> dict:
    return json.loads((WORKFLOW_ROOT / workflow_id / "workflow.json").read_text())


def _syslog(variant: str, data: dict, suffix: str = "") -> dict:
    prefix = "2021-07-08 10:18:00" if variant == "secevent" else "2022-01-26T10:11:18.468356+08:00 2022-01-26 10: 12:15"
    return parse_syslog(f"<14>{prefix}|!{variant}|!192.0.2.10|!" + json.dumps(data) + suffix)


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class FakeConfig:
        def get_global(self):
            return SimpleNamespace(data_dir=tmp_path / "data")

    monkeypatch.setattr(flocks.config, "Config", FakeConfig)
    monkeypatch.delitem(sys.modules, "_flocks_lsh_cache_stream_alert_denoise", raising=False)
    definition = _definition()

    def run(inputs: dict) -> dict:
        # Execute the actual nodes and their checked-in strict edge mappings.
        current = copy.deepcopy(inputs)
        for node in definition["nodes"]:
            output = {}
            exec(compile(node["code"], node["id"], "exec"), {"inputs": current, "outputs": output})
            edges = [e for e in definition["edges"] if e["from"] == node["id"]]
            current = {k: output.get(v) for k, v in edges[0]["mapping"].items()} if edges else output
        return current

    yield run
    sys.modules.pop("_flocks_lsh_cache_stream_alert_denoise", None)


@pytest.mark.parametrize("variant,data", [("secevent", EVENT), ("alarm", ALARM)])
@pytest.mark.parametrize("suffix", ["", "\x00\r\n"])
def test_se_reaches_jsonl_with_fields_and_evidence(pipeline, variant, data, suffix) -> None:
    result = pipeline({"syslog_message": _syslog(variant, data, suffix)})
    assert result["stats"]["raw_count"] == 1
    assert result["stats"]["after_filter_count"] == 1
    normalized = result["enriched_alerts"][0]
    assert normalized["_source_type"] == "sangfor-sip"
    assert normalized["_sip_variant"] == variant
    assert normalized["_sip_raw"] == data
    assert normalized["device_ip"] == "192.0.2.10"
    assert normalized["dip"] == "192.0.2.5"
    assert normalized["threat_name"] == ("勒索" if variant == "secevent" else "口令暴力破解")
    assert normalized["time"] == data["log_time" if variant == "secevent" else "last_time"]
    assert normalized["direction"] == "in"
    assert normalized["net_type"] == "none"
    assert "not_http" in normalized["_process_type"]
    rows = [json.loads(line) for line in Path(result["output_path"]).read_text().splitlines()]
    assert rows[1]["_sip_raw"]["event_evidence"] == data["event_evidence"]


def test_legacy_envelope_and_structured_data_input(pipeline) -> None:
    raw = _syslog("alarm", ALARM, "\x00")["raw"]
    legacy = pipeline({"syslog_message": {"message": raw, "format": "rfc3164"}})
    assert legacy["stats"]["raw_count"] == 1
    structured = _syslog("alarm", ALARM)
    structured["message"] = "not JSON; structured data is authoritative"
    assert pipeline({"syslog_message": structured})["stats"]["raw_count"] == 1


@pytest.mark.parametrize("body", ["", '{"brief":"a\x00b"}'])
def test_corrupt_se_fails_visibly_instead_of_empty_success(pipeline, body) -> None:
    parsed = parse_syslog("2026-10-12 10:00:00|!alarm|!192.0.2.10|!" + body)
    with pytest.raises(ValueError, match="Invalid Sangfor SE"):
        pipeline({"syslog_message": parsed})


def test_legacy_se_error_without_parse_status_still_fails(pipeline) -> None:
    with pytest.raises(ValueError, match="Invalid Sangfor SE"):
        pipeline({"syslog_message": {"format": "se", "message": "{invalid}", "log_type": "alarm"}})


@pytest.mark.parametrize(
    "variant,data,expected",
    [
        ("secevent", EVENT, ["unknown", "failed", "success", "success"]),
        ("alarm", ALARM, ["failed", "unknown", "success", "success"]),
    ],
)
def test_attack_state_enums_and_dedup_identity(pipeline, variant, data, expected) -> None:
    alerts = [dict(data, attack_state=state) for state in range(4)]
    result = pipeline({"alerts": alerts})
    normalized = result["enriched_alerts"]
    assert [a["threat_result"] for a in normalized] == expected
    assert [a["attack_state"] for a in normalized] == list(range(4))
    assert len({a["dedup_key"] for a in normalized}) == 4
    repeated = pipeline({"alerts": alerts})
    assert all(a["is_duplicate"] for a in repeated["enriched_alerts"])
    assert repeated["output_paths"] == []


@pytest.mark.parametrize("asset_direction,expected", [(1, "in"), (2, "lateral"), (3, "out"), (0, "unknown")])
def test_alarm_network_direction_ignores_access_relation(asset_direction, expected) -> None:
    norm = normalize_sangfor_alert(dict(ALARM, asset_direction=asset_direction))
    assert norm["direction"] == expected


def test_event_does_not_invent_destination_for_outbound_asset() -> None:
    norm = normalize_sangfor_alert(dict(EVENT, src_ip=EVENT["ip"], dst_ip="0.0.0.0"))
    assert norm["direction"] == "out"
    assert norm["dip"] == "none"
    assert norm["asset_ip"] == EVENT["ip"]


def test_severity_certainty_and_threat_level_remain_distinct() -> None:
    norm = normalize_sangfor_alert(EVENT)
    assert norm["certainty_level"] == 3
    assert norm["threat_level"] == 2
    assert norm["threat_severity"] == "critical"


def test_mixed_batch_keeps_source_specific_dedup_and_scan_filter(pipeline) -> None:
    tdp = {
        "net_type": "http",
        "net_real_src_ip": "198.51.100.20",
        "net_dest_ip": "192.0.2.5",
        "net_http_url": "/login",
        "threat_name": "SQL注入",
    }
    skyeye = {"sip": "198.51.100.30", "dip": "192.0.2.5", "uri": "/login", "vuln_name": "SQL注入"}
    alerts = [
        tdp,
        skyeye,
        dict(ALARM, attack_state=0),
        dict(ALARM, attack_state=2),
        dict(EVENT, sub_attack_name="端口扫描"),
    ]
    result = pipeline({"alerts": alerts})
    assert result["stats"]["after_filter_count"] == 4
    assert {a["_source_type"] for a in result["enriched_alerts"]} == {"tdp", "skyeye", "sangfor-sip"}
    assert len({a["dedup_key"] for a in result["enriched_alerts"]}) == 4


def test_sip_device_identity_and_explicit_dedup_overrides(pipeline) -> None:
    first = pipeline({"syslog_message": _syslog("alarm", ALARM)})
    other = _syslog("alarm", ALARM)
    other["client_ip"] = "192.0.2.11"
    second = pipeline({"syslog_message": other})
    assert first["dedup_key"] != second["dedup_key"]
    override = pipeline(
        {
            "alerts": [dict(ALARM, attack_state=0), dict(ALARM, attack_state=2)],
            "strict_fields": ["sip", "dip"],
            "lsh_fields": ["threat_name"],
        }
    )
    assert len({a["dedup_key"] for a in override["enriched_alerts"]}) == 1


def test_sip_evidence_iocs_and_cve_reach_current_triage_adapter(pipeline) -> None:
    normalized = pipeline({"alerts": [EVENT, ALARM]})["enriched_alerts"]
    code = next(n["code"] for n in _definition("stream_alert_triage")["nodes"] if n["id"] == "concurrent_triage")
    tree = ast.parse(code)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_pick", "_parse_alert")]
    ns = {"json": json, "re": re}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "triage-adapter", "exec"), ns)
    event, alarm = [ns["_parse_alert"](a) for a in normalized]
    assert EVENT["event_evidence"] in event["log_text"]
    assert EVENT["solution"] in event["log_text"]
    assert event["vuln_id"] == "CVE-2017-0199"
    assert event["protocol"] == "unknown"
    assert {"type": "domain", "value": "c2.invalid"} in alarm["iocs"]
    assert {"type": "ip", "value": "203.0.113.9"} in alarm["iocs"]
    assert all(a["value"] not in ("none", "0.0.0.0") for a in alarm["iocs"])


def test_real_workflow_engine_executes_se_with_strict_edge_mapping(pipeline) -> None:
    workflow = Workflow.from_dict(_definition())
    engine = WorkflowEngine(
        workflow,
        runtime=PythonExecRuntime(tool_registry=SimpleNamespace(cancel_checker=None)),
        node_timeout_s=None,
        history_mode="full",
        dataflow_mode="vertex_cache",
        max_parallel_workers=1,
    )
    result = engine.run(initial_inputs={"syslog_message": _syslog("alarm", ALARM, "\x00")}, retain_history=True)
    assert len(result.history) == 4
    assert all(step.error is None for step in result.history)
    assert result.history[-1].outputs["stats"]["after_filter_count"] == 1


@pytest.mark.parametrize("variant,data", [("secevent", EVENT), ("alarm", ALARM)])
async def test_udp_listener_delivers_nul_terminated_se_to_workflow(pipeline, variant, data) -> None:
    loop = asyncio.get_running_loop()
    received = loop.create_future()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: SyslogUDPProtocol(received.set_result, "auto"),
        local_addr=("127.0.0.1", 0),
    )
    try:
        raw = _syslog(variant, data, "\x00")["raw"].encode("utf-8")
        transport.sendto(raw, transport.get_extra_info("sockname"))
        parsed = await asyncio.wait_for(received, 3)
        result = pipeline({"syslog_message": parsed})
        assert result["stats"]["after_filter_count"] == 1
        assert result["enriched_alerts"][0]["_sip_raw"]["event_evidence"] == data["event_evidence"]
    finally:
        transport.close()
