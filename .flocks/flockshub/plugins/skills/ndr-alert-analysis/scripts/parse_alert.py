"""Small helper used by the bundled NDR alert analysis skill examples."""

from __future__ import annotations

from typing import Any


def pick(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def extract_basic_fields(alert: dict[str, Any]) -> dict[str, Any]:
    net = alert.get("net", {}) or {}
    http = net.get("http", {}) or {}
    threat = alert.get("threat", {}) or {}
    return {
        "src_ip": pick(alert.get("attacker"), net.get("src_ip"), alert.get("src_ip")),
        "dst_ip": pick(alert.get("victim"), net.get("dest_ip"), alert.get("dst_ip")),
        "url": pick(http.get("url"), http.get("raw_url"), alert.get("url")),
        "alert_type": pick(threat.get("name"), alert.get("alert_type"), "unknown"),
        "severity": pick(threat.get("severity"), alert.get("severity"), "medium"),
    }
