"""Sangfor SIP SE payload decoding and alert normalization.

The vendor's secevent and alarm schemas have different attack-state enums.
Keep the original object alongside normalized fields so evidence and vendor
values survive denoising without guessing missing HTTP traffic.
"""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

SOURCE_TYPE = "sangfor-sip"
VARIANTS = {"secevent", "alarm"}
SEVERITIES = {
    "info": "info",
    "normal": "medium",
    "important": "high",
    "emergent": "critical",
}
SIP_STRICT_FIELDS = (
    "device_ip",
    "device_id",
    "_sip_variant",
    "asset_ip",
    "sip",
    "dip",
    "threat_rule_id",
    "threat_name",
    "attack_state",
    "threat_ioc",
)
SIP_LSH_FIELDS = ("req_http_url", "threat_msg", "threat_ioc", "_sip_raw.event_evidence")


def decode_se_json(message: str) -> tuple[str, dict[str, Any]]:
    """Remove boundary framing only; reject corrupt JSON and non-object bodies."""
    body = message.lstrip(" \t\r\n").removeprefix("\ufeff").lstrip(" \t\r\n")
    body = body.rstrip("\x00 \t\r\n")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("SE payload must be a JSON object")
    return body, data


def detect_sangfor_variant(alert: dict[str, Any]) -> str | None:
    for key in ("_sip_meta", "_syslog_meta"):
        meta = alert.get(key)
        if isinstance(meta, dict) and meta.get("log_type") in VARIANTS:
            return meta["log_type"]
    keys = set(alert)
    if len(keys & {"attack_ip", "suffer_ip", "module_type_name", "alert_id"}) >= 2:
        return "alarm"
    if "eventKey" in keys and keys & {"log_time", "event_content", "sub_attack_name"}:
        return "secevent"
    return None


def _present(value: Any) -> bool:
    return value is not None and value not in ("", "none", [], {})


def _pick(*values: Any, default: Any = "none") -> Any:
    return next((v for v in values if _present(v)), default)


def _int(value: Any, default: Any = "none") -> Any:
    try:
        return int(value) if _present(value) else default
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if _present(v)) or "none"
    return str(value) if _present(value) else "none"


def _ip(value: Any) -> str:
    try:
        addr = ipaddress.ip_address(str(value))
        return str(addr) if not addr.is_unspecified else "none"
    except ValueError:
        return "none"


def normalize_sangfor_alert(alert: dict[str, Any]) -> dict[str, Any]:
    variant = detect_sangfor_variant(alert)
    if variant is None:
        raise ValueError("Cannot identify Sangfor SIP secevent/alarm schema")
    raw = {k: v for k, v in alert.items() if k not in ("_sip_meta", "_syslog_meta")}
    meta = alert.get("_sip_meta") or alert.get("_syslog_meta") or {}
    state = _int(raw.get("attack_state"))
    norm = {
        "customer_uuid": "none",
        "device_id": _pick(raw.get("dev_id")),
        "device_ip": _pick(meta.get("client_ip"), meta.get("device_ip"), meta.get("hostname")),
        "sport": _int(raw.get("src_port") if variant == "secevent" else raw.get("attack_port")),
        "dport": _int(raw.get("dst_port") if variant == "secevent" else raw.get("suffer_port")),
        "net_type": "none",
        "net_app_proto": "none",
        "req_http_url": _text(raw.get("url")),
        "req_user_agent": "none",
        "req_host": "none",
        "req_line": "none",
        "req_header": "none",
        "req_body": "none",
        "req_cookie": "none",
        "req_body_len": "none",
        "rsp_status_code": _pick(raw.get("status_code")),
        "rsp_line": "none",
        "rsp_header": "none",
        "rsp_body": "none",
        "rsp_body_len": "none",
        "threat_rule_id": _pick(raw.get("ruleId"), raw.get("hash_id"), raw.get("eventKey")),
        "threat_ioc": _text(raw.get("ioc")),
        "threat_confidence": "none",
        "threat_severity": SEVERITIES.get(str(raw.get("emergency")), "none"),
        "connection_established": "none",
        "attack_state": state,
        "_source_type": SOURCE_TYPE,
        "_sip_variant": variant,
        "_sip_raw": raw,
    }
    if variant == "secevent":
        src, dst, local = (_ip(raw.get(k)) for k in ("src_ip", "dst_ip", "ip"))
        direction = "unknown"
        if local != "none":
            if src == local and dst != local:
                direction = "out"
            elif dst == local and src != local:
                direction = "in"
        norm.update(
            id=_pick(raw.get("eventKey")),
            time=_int(raw.get("log_time"), _int(raw.get("firstTime"))),
            sip=src,
            dip=dst,
            asset_ip=local,
            direction=direction,
            threat_name=_pick(raw.get("sub_attack_type_name"), raw.get("sub_attack_name"), raw.get("module_name")),
            threat_type=_pick(raw.get("module_name")),
            threat_msg=_pick(raw.get("event_content"), raw.get("brief")),
            threat_level=_int(raw.get("suspect_level")),
            certainty_level=_int(raw.get("threat_level")),
            threat_phase=_int(raw.get("stage")),
            threat_result={0: "unknown", 1: "failed", 2: "success", 3: "success"}.get(state, "unknown"),
            asset_group_name=_pick(raw.get("branch_name")),
            asset_name=_pick(raw.get("hostName"), raw.get("hostRisk")),
        )
    else:
        norm.update(
            id=_pick(raw.get("alert_id")),
            time=_int(raw.get("last_time"), _int(raw.get("first_time"))),
            sip=_ip(raw.get("attack_ip")),
            dip=_ip(raw.get("suffer_ip")),
            asset_ip=_ip(raw.get("suffer_ip")),
            # Access relation (attack_direction) is independent of network direction.
            direction={1: "in", 2: "lateral", 3: "out"}.get(_int(raw.get("asset_direction")), "unknown"),
            threat_name=_pick(
                raw.get("sub_attack_type_name"), raw.get("attack_type_name"), raw.get("module_type_name")
            ),
            threat_type=_pick(raw.get("module_type_name")),
            threat_msg=_pick(raw.get("event_desc"), raw.get("brief")),
            threat_level=_int(raw.get("reliability")),
            threat_phase=_int(raw.get("invasion_stage")),
            threat_result={0: "failed", 1: "unknown", 2: "success", 3: "success"}.get(state, "unknown"),
            asset_group_name=_pick(raw.get("suffer_branch_name")),
            asset_name=_pick(raw.get("hostName"), raw.get("suffer_branch_name")),
        )
    if norm["threat_severity"] == "none":
        norm["threat_severity"] = {1: "low", 2: "medium", 3: "high"}.get(norm["threat_level"], "none")
    # A URL alone does not prove that the underlying security event is HTTP.
    for key in ("net_type", "net_app_proto"):
        norm[key] = _pick(raw.get(key), raw.get("protocol"))
    for key in (
        "req_line",
        "req_header",
        "req_body",
        "req_host",
        "req_user_agent",
        "req_cookie",
        "rsp_line",
        "rsp_header",
        "rsp_body",
    ):
        norm[key] = _pick(raw.get(key))
    for key in ("_syslog_meta", "_sip_meta"):
        if isinstance(alert.get(key), dict):
            norm[key] = dict(alert[key])
    return norm


def sangfor_dedup_texts(
    alert: dict[str, Any],
    strict_fields: list[str] | None,
    lsh_fields: list[str] | None,
    max_len: int,
) -> tuple[str, str]:
    """SIP identity is exact and namespaced; URI rewriting is HTTP-specific."""
    fields = SIP_STRICT_FIELDS if strict_fields is None else strict_fields
    fuzzy = SIP_LSH_FIELDS if lsh_fields is None else lsh_fields
    # Do not truncate SIP identity: a long rule name must not hide its suffix.
    strict = json.dumps(
        ["sangfor-sip.v1", [[f, alert.get(f)] for f in fields]],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    def field_value(field: str) -> Any:
        value: Any = alert
        for key in field.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        return value

    text = ". ".join(_text(field_value(f))[:max_len] for f in fuzzy)
    return strict, text


def sangfor_triage_context(alert: dict[str, Any]) -> dict[str, Any]:
    """Expose preserved evidence to the existing triage prompt and intel adapter."""
    raw = alert.get("_sip_raw") or {}
    sections = []
    for key in (
        "brief",
        "event_evidence",
        "event_content",
        "event_desc",
        "principle",
        "solution",
        "suggest",
        "analyze_suggest",
        "damage",
        "tag",
        "tags",
    ):
        value = _pick(raw.get(key), alert.get("_sip_" + key), default="")
        if _present(value):
            sections.append(f"{key}: {_text(value)}")
    evidence = "\n".join(sections)
    values = raw.get("ioc") or alert.get("threat_ioc") or []
    if isinstance(values, str):
        values = [v.strip() for v in values.split(",")]
    iocs = []
    for value in values:
        text = str(value).strip()
        if not text or text == "none":
            continue
        try:
            addr = ipaddress.ip_address(text)
            if addr.is_unspecified:
                continue
            kind = "ip"
        except ValueError:
            kind = "url" if text.startswith(("http://", "https://")) else "domain"
        iocs.append({"type": kind, "value": text})
    return {"evidence": evidence, "iocs": iocs}


def parse_sangfor_for_triage(alert: dict[str, Any]) -> dict[str, Any]:
    context = sangfor_triage_context(alert)
    raw = alert.get("_sip_raw") or {}
    src = _pick(alert.get("sip"), default="")
    dst = _pick(alert.get("dip"), default="")
    src_port = _pick(alert.get("sport"), default=0)
    dst_port = _pick(alert.get("dport"), default=0)
    protocol = _pick(alert.get("net_app_proto"), alert.get("net_type"), default="unknown")
    severity = _pick(alert.get("threat_severity"), default="unknown")
    name = _pick(alert.get("threat_name"), default="unknown")
    message = _pick(alert.get("threat_msg"), default="")
    result = _pick(alert.get("threat_result"), default="unknown")
    urls = raw.get("url") or alert.get("req_http_url") or []
    if not isinstance(urls, list):
        urls = [urls]
    urls = [str(v) for v in urls if _present(v)]
    url = urls[0] if urls else ""
    payload = "\n".join(f"{key}: {_pick(alert.get(key), default='')}" for key in ("req_line", "req_header", "req_body"))
    response = "\n".join(
        f"{key}: {_pick(alert.get(key), default='')}" for key in ("rsp_line", "rsp_header", "rsp_body")
    )
    log_text = (
        f"[Sangfor SIP/{alert.get('_sip_variant', 'unknown')}]\n"
        f"告警类型: {name}\n严重级别: {severity}\n"
        f"源地址: {src}:{src_port}\n目的地址: {dst}:{dst_port}\n"
        f"协议: {protocol}\nURL: {url}\n"
        f"设备攻击结果: {result}\n原始 attack_state: {alert.get('attack_state')}\n"
        f"告警描述: {message}\n\n[举证与处置建议]\n{context['evidence']}\n"
        f"\n[HTTP请求内容]\n{payload}\n\n[HTTP响应内容]\n{response}"
    )
    iocs = list(context["iocs"])
    for value in (src, dst):
        if _ip(value) != "none":
            iocs.append({"type": "ip", "value": value})
    for value in urls:
        if value.startswith(("http://", "https://")):
            iocs.append({"type": "url", "value": value})
    iocs = list({(v["type"], v["value"]): v for v in iocs}.values())
    vulnerabilities = sorted(
        set(
            re.findall(
                r"\b(?:CVE|CNVD|CNNVD|XVE)-[A-Za-z0-9._-]+\b",
                message + "\n" + context["evidence"],
                flags=re.I,
            )
        )
    )
    return {
        "src_ip": src,
        "dst_ip": dst,
        "src_port": src_port,
        "dst_port": dst_port,
        "protocol": protocol,
        "payload": payload,
        "response": response,
        "url": url,
        "status": _pick(alert.get("rsp_status_code"), default=0),
        "alert_type": name,
        "severity": severity,
        "vuln_id": vulnerabilities[0] if vulnerabilities else "",
        "vuln_candidates": vulnerabilities,
        "threat_result": result,
        "threat_msg": message,
        "failed_by": [],
        "asset_ip": _pick(alert.get("asset_ip"), default=""),
        "asset_name": _pick(alert.get("asset_name"), default=""),
        "iocs": iocs,
        "log_text": log_text,
    }
