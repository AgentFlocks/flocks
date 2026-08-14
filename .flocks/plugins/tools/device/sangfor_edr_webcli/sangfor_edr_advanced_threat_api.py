"""Sangfor EDR advanced-threat API collection using a verified auth pair."""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Any, Iterable

import sangfor_edr_http_login as auth


DEFAULT_SECTIONS = ("warning_logs", "incidents")
DEFAULT_THREAT_LEVELS = (5, 4, 3)
DEFAULT_DISPOSAL_STATES = (0,)
DEFAULT_EVENT_TYPES = (1, 2, 3, 0)
MAX_PAGES = 1000

THREAT_LEVEL_LABELS = {5: "严重", 4: "高危", 3: "中危", 2: "低危", 1: "信息"}
DISPOSAL_STATE_LABELS = {0: "待处置", 2: "已处置", 3: "已忽略"}
EVENT_DISPOSAL_STATE_LABELS = {0: "暂不支持", 1: "未处置", 2: "自动处置中", 3: "已自动处置"}
AGENT_TYPE_LABELS = {0: "PC", 1: "服务器"}
DETECT_SOURCE_LABELS = {1: "IOC引擎", 2: "IOA引擎", 3: "SIP联动", 6: "MS引擎", 8: "AF联动"}
EVENT_TYPE_LABELS = {1: "钓鱼攻击", 2: "Web入侵", 3: "恶意病毒", 0: "其他"}
WL_SWITCH_LABELS = {0: "隐藏", 1: "显示"}


def _aliases(labels: dict[int, str], extra: dict[str, int]) -> dict[str, int]:
    values = {str(code): code for code in labels}
    values.update({label.lower(): code for code, label in labels.items()})
    values.update({key.lower(): code for key, code in extra.items()})
    return values


THREAT_LEVEL_ALIASES = _aliases(
    THREAT_LEVEL_LABELS,
    {"critical": 5, "severe": 5, "serious": 5, "high": 4, "medium": 3, "low": 2, "info": 1, "informational": 1},
)
DISPOSAL_STATE_ALIASES = _aliases(
    DISPOSAL_STATE_LABELS,
    {"pending": 0, "unhandled": 0, "handled": 2, "resolved": 2, "ignored": 3},
)
EVENT_DISPOSAL_STATE_ALIASES = _aliases(
    EVENT_DISPOSAL_STATE_LABELS,
    {"unsupported": 0, "not_supported": 0, "unhandled": 1, "auto_processing": 2, "auto_handled": 3},
)
AGENT_TYPE_ALIASES = _aliases(
    AGENT_TYPE_LABELS,
    {"pc终端": 0, "电脑": 0, "电脑终端": 0, "server": 1, "服务器终端": 1},
)
DETECT_SOURCE_ALIASES = _aliases(
    DETECT_SOURCE_LABELS,
    {"ioc": 1, "ioa": 2, "sip": 3, "ms": 6, "af": 8, "msi引擎": 6},
)
EVENT_TYPE_ALIASES = _aliases(
    EVENT_TYPE_LABELS,
    {"phishing": 1, "web intrusion": 2, "web_intrusion": 2, "malware": 3, "virus": 3, "other": 0},
)
WL_SWITCH_ALIASES = _aliases(
    WL_SWITCH_LABELS,
    {"hide": 0, "hidden": 0, "show": 1, "visible": 1},
)


def _split_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str) and ("," in value or "，" in value):
        return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
    return [value]


def _normalise_one(value: Any, aliases: dict[str, int], field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} does not accept boolean values.")
    key = str(value).strip().lower()
    if key in aliases:
        return aliases[key]
    raise ValueError(f"Unsupported {field} value: {value!r}")


def _normalise_multi(
    value: Any,
    aliases: dict[str, int],
    field: str,
    *,
    default: Iterable[int] = (),
) -> list[int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return list(default)
    values = _split_values(value)
    normalised: list[int] = []
    for item in values:
        candidate = _normalise_one(item, aliases, field)
        if candidate not in normalised:
            normalised.append(candidate)
    return normalised


def _normalise_wl_switch(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        raise ValueError("wl_switch accepts exactly one value.")
    return _normalise_one(0 if value is None else value, WL_SWITCH_ALIASES, "wl_switch")


def _normalise_timestamp(value: Any, field: str, *, end_of_day: bool = False) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an epoch timestamp or ISO date/time.")
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        timestamp = int(value)
        return timestamp * 1000 if timestamp < 100_000_000_000 else timestamp
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} cannot be empty.")
    try:
        if len(text) == 10:
            parsed_date = date.fromisoformat(text)
            parsed = datetime.combine(
                parsed_date,
                datetime_time(23, 59, 59, 999000) if end_of_day else datetime_time.min,
            )
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an epoch timestamp or ISO date/time.") from exc
    return int(parsed.timestamp() * 1000)


def _time_range(days: int, begin_time: Any = None, end_time: Any = None) -> tuple[int, int]:
    if begin_time is not None or end_time is not None:
        if begin_time is None or end_time is None:
            raise ValueError("begin_time and end_time must be provided together.")
        begin_ms = _normalise_timestamp(begin_time, "begin_time")
        end_ms = _normalise_timestamp(end_time, "end_time", end_of_day=True)
    else:
        end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999000)
        begin = (end - timedelta(days=max(1, days) - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        begin_ms, end_ms = int(begin.timestamp() * 1000), int(end.timestamp() * 1000)
    if begin_ms > end_ms:
        raise ValueError("begin_time must not be later than end_time.")
    return begin_ms, end_ms


def _request_uuid() -> str:
    """Generate the request correlation value used by the EDR frontend; it is not a device ID."""
    return f"sf-id-{secrets.randbelow(9_000_000) + 1_000}"


def _advanced_threat_request(
    cfg: auth.RuntimeConfig,
    token: str,
    section: str,
    *,
    page_no: int,
    page_limit: int,
    threat_levels: list[int],
    disposal_states: list[int],
    event_disposal_states: list[int],
    agent_types: list[int],
    detect_sources: list[int],
    event_types: list[int],
    begin_time: int,
    end_time: int,
    wl_switch: int,
    uid: str = "",
    tid: str = "0",
) -> tuple[str, dict[str, Any]]:
    if section not in DEFAULT_SECTIONS:
        raise ValueError(f"Unsupported EDR advanced-threat section: {section}")
    operation = "querywarninglogs" if section == "warning_logs" else "queryincidentinfo"
    filters: dict[str, Any] = {
        "threatLevel": threat_levels,
        "wlSwitch": wl_switch,
    }
    if section == "incidents":
        filters.update(
            {
                "disposalState": disposal_states,
                "eventType": event_types,
                "beginTime": begin_time,
                "endTime": end_time,
                "highConfidence": True,
            }
        )
        for key, values in (
            ("eventDisposalState", event_disposal_states),
            ("agentType", agent_types),
            ("detectSource", detect_sources),
        ):
            if values:
                filters[key] = values
    return (
        f"/api/edrgoweb/v1/advthreats/{operation}?_method=get&s={token}",
        {
            "method": "get",
            "pageNo": page_no,
            "pageLimit": page_limit,
            "checkCount": 501,
            "sortField": 1,
            "sortType": 0,
            "filter": filters,
            "req": {
                "uuid": _request_uuid(),
                "tid": str(tid or "0"),
                "uid": str(uid or cfg.username or ""),
                "token": token,
            },
        },
    )


def _post_json(session: Any, cfg: auth.RuntimeConfig, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = session.post(
        auth._url(cfg, path),
        headers=auth._http_headers(cfg),
        json=payload,
        timeout=cfg.timeout,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("EDR advanced-threat API returned a non-object response.")
    code = result.get("code")
    if code not in (None, 0, "0") or result.get("success") is False:
        raise RuntimeError(str(result.get("msg") or f"EDR advanced-threat API rejected request (code={code})."))
    return result


def _page_items(result: dict[str, Any], section: str) -> tuple[list[Any], int]:
    data = result.get("data")
    if not isinstance(data, dict):
        return [], 0
    key = "warningLogDatas" if section == "warning_logs" else "incidentList"
    items = data.get(key)
    try:
        total = int(data.get("totalNum") or 0)
    except (TypeError, ValueError):
        total = 0
    return (items if isinstance(items, list) else []), max(0, total)


def _collect_section(
    session: Any,
    cfg: auth.RuntimeConfig,
    token: str,
    section: str,
    *,
    page_no: int,
    page_limit: int,
    paginate: bool,
    request_kwargs: dict[str, Any],
) -> dict[str, Any]:
    current_page = page_no
    pages_requested = 0
    items: list[Any] = []
    reported_total = 0
    seen_pages: set[str] = set()
    has_more = False
    termination = "requested_page"

    while True:
        path, payload = _advanced_threat_request(
            cfg,
            token,
            section,
            page_no=current_page,
            page_limit=page_limit,
            **request_kwargs,
        )
        result = _post_json(session, cfg, path, payload)
        page_items, page_total = _page_items(result, section)
        pages_requested += 1
        reported_total = max(reported_total, page_total)
        signature = json.dumps(page_items, ensure_ascii=False, sort_keys=True, default=str)
        if page_items and signature in seen_pages:
            has_more = True
            termination = "repeated_page"
            break
        if page_items:
            seen_pages.add(signature)
            items.extend(page_items)

        if not paginate:
            has_more = bool(page_items) and (
                len(page_items) >= page_limit
                or (reported_total > 0 and current_page * page_limit < reported_total)
            )
            break
        if not page_items:
            termination = "empty_page"
            break
        if len(page_items) < page_limit:
            termination = "short_page"
            break
        if reported_total > 0 and current_page * page_limit >= reported_total:
            termination = "reported_total_reached"
            break
        if pages_requested >= MAX_PAGES:
            has_more = True
            termination = "page_safety_limit"
            break
        current_page += 1

    return {
        "items": items,
        "total_num": max(reported_total, len(items)),
        "reported_total_num": reported_total,
        "page_no": page_no,
        "page_limit": page_limit,
        "pages_requested": pages_requested,
        "last_page": current_page,
        "has_more": has_more,
        "termination": termination,
    }


def _label(labels: dict[int, str], value: Any) -> str:
    try:
        return labels.get(int(value), "未知")
    except (TypeError, ValueError):
        return "未知"


def _readable_time(value: Any) -> Any:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return value
    if timestamp < 100_000_000_000:
        timestamp *= 1000
    return datetime.fromtimestamp(timestamp / 1000).astimezone().isoformat(timespec="seconds")


def _warning_readable(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    warning = item.get("warningLogs") if isinstance(item.get("warningLogs"), dict) else item
    threat_level = warning.get("threatLevel")
    detect_source = warning.get("detectSource")
    return {
        "terminal_name": item.get("agentName") or item.get("hostName"),
        "terminal_ip": item.get("agentIp") or item.get("hostIp"),
        "terminal_group": item.get("groupName"),
        "agent_type": item.get("agentType"),
        "agent_type_label": _label(AGENT_TYPE_LABELS, item.get("agentType")),
        "warning_id": warning.get("warningId"),
        "warning_name": warning.get("warningName"),
        "warning_description": warning.get("warningDesc"),
        "warning_tag": warning.get("warningTag"),
        "threat_level": threat_level,
        "threat_level_label": _label(THREAT_LEVEL_LABELS, threat_level),
        "found_time": warning.get("foundTime"),
        "found_time_readable": _readable_time(warning.get("foundTime")),
        "matched_processes": warning.get("matchedProcs") or [],
        "attack_tag_ids": warning.get("attckTagIds") or [],
        "mitre_id": warning.get("mitreId"),
        "detect_source": detect_source,
        "detect_source_label": _label(DETECT_SOURCE_LABELS, detect_source),
        "incident_id": warning.get("incidentId"),
        "event_type": warning.get("eventType"),
        "event_type_label": _label(EVENT_TYPE_LABELS, warning.get("eventType")),
    }


def _incident_readable(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    incident = item.get("incidentInfo") if isinstance(item.get("incidentInfo"), dict) else item
    host = item.get("agentInfo") if isinstance(item.get("agentInfo"), dict) else item
    threat_level = incident.get("threatLevel")
    disposal_state = incident.get("disposalState")
    event_disposal_state = incident.get("eventDisposalState")
    detect_source = incident.get("detectSource")
    event_type = incident.get("eventType")
    agent_type = host.get("agentType", incident.get("agentType"))
    found_time = incident.get("foundTime", incident.get("lastFoundTime"))
    return {
        "incident_id": incident.get("incidentId") or incident.get("id"),
        "incident_name": incident.get("incidentName") or incident.get("name"),
        "incident_description": incident.get("incidentDesc") or incident.get("description"),
        "terminal_name": host.get("agentName") or host.get("hostName"),
        "terminal_ip": host.get("agentIp") or host.get("hostIp"),
        "threat_level": threat_level,
        "threat_level_label": _label(THREAT_LEVEL_LABELS, threat_level),
        "disposal_state": disposal_state,
        "disposal_state_label": _label(DISPOSAL_STATE_LABELS, disposal_state),
        "event_disposal_state": event_disposal_state,
        "event_disposal_state_label": _label(EVENT_DISPOSAL_STATE_LABELS, event_disposal_state),
        "agent_type": agent_type,
        "agent_type_label": _label(AGENT_TYPE_LABELS, agent_type),
        "detect_source": detect_source,
        "detect_source_label": _label(DETECT_SOURCE_LABELS, detect_source),
        "event_type": event_type,
        "event_type_label": _label(EVENT_TYPE_LABELS, event_type),
        "found_time": found_time,
        "found_time_readable": _readable_time(found_time),
    }


def collect_advanced_threat(
    cfg: auth.RuntimeConfig,
    *,
    sections: list[str],
    days: int = 7,
    begin_time: Any = None,
    end_time: Any = None,
    threat_levels: Any = None,
    disposal_states: Any = None,
    event_disposal_states: Any = None,
    agent_types: Any = None,
    detect_sources: Any = None,
    event_types: Any = None,
    wl_switch: Any = 0,
    page_no: int = 1,
    page_limit: int = 50,
    paginate: bool = True,
    uid: str = "",
    tid: str = "0",
) -> dict[str, Any]:
    selected = sections or list(DEFAULT_SECTIONS)
    selected = list(dict.fromkeys(selected))
    unknown = sorted(set(selected) - set(DEFAULT_SECTIONS))
    if unknown:
        raise ValueError(f"Unsupported EDR advanced-threat sections: {', '.join(unknown)}")
    if page_no < 1 or page_limit < 1 or page_limit > 500:
        raise ValueError("page_no must be >= 1 and page_limit must be between 1 and 500.")

    normalised = {
        "threat_levels": _normalise_multi(
            threat_levels, THREAT_LEVEL_ALIASES, "threat_levels", default=DEFAULT_THREAT_LEVELS
        ),
        "disposal_states": _normalise_multi(
            disposal_states, DISPOSAL_STATE_ALIASES, "disposal_states", default=DEFAULT_DISPOSAL_STATES
        ),
        "event_disposal_states": _normalise_multi(
            event_disposal_states, EVENT_DISPOSAL_STATE_ALIASES, "event_disposal_states"
        ),
        "agent_types": _normalise_multi(agent_types, AGENT_TYPE_ALIASES, "agent_types"),
        "detect_sources": _normalise_multi(detect_sources, DETECT_SOURCE_ALIASES, "detect_sources"),
        "event_types": _normalise_multi(
            event_types, EVENT_TYPE_ALIASES, "event_types", default=DEFAULT_EVENT_TYPES
        ),
        "wl_switch": _normalise_wl_switch(wl_switch),
    }
    begin_ms, end_ms = _time_range(days, begin_time, end_time)
    request_kwargs = {
        **normalised,
        "begin_time": begin_ms,
        "end_time": end_ms,
        "uid": uid,
        "tid": tid,
    }

    auth_result = auth.ensure_http_auth_pair(cfg)
    if not auth_result.get("success"):
        raise RuntimeError(
            "EDR authentication refresh failed: "
            f"{auth_result.get('error') or auth_result.get('reason') or auth_result.get('status')}"
        )
    state, token = auth.load_verified_auth_pair(cfg)
    session = auth.dashboard_session(cfg, state)

    raw_data: dict[str, Any] = {}
    readable_data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for section in selected:
        try:
            raw_data[section] = _collect_section(
                session,
                cfg,
                token,
                section,
                page_no=page_no,
                page_limit=page_limit,
                paginate=paginate,
                request_kwargs=request_kwargs,
            )
            converter = _warning_readable if section == "warning_logs" else _incident_readable
            readable_data[section] = [converter(item) for item in raw_data[section]["items"]]
        except Exception as exc:
            errors[section] = auth._safe_error(exc, token)

    return {
        "success": not errors,
        "status": "advanced_threat_collected" if not errors else "advanced_threat_partially_collected",
        "base_url": cfg.base_url,
        "sections": selected,
        "filters": {
            **normalised,
            "begin_time": begin_ms,
            "end_time": end_ms,
            "page_no": page_no,
            "page_limit": page_limit,
            "paginate": paginate,
        },
        "data": raw_data,
        "raw_data": raw_data,
        "readable_data": readable_data,
        "errors": errors,
        "auth_pair_verified": True,
        "authentication": {
            "status": auth_result.get("status"),
            "login_skipped": bool(auth_result.get("login_skipped")),
        },
    }


def _sections(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def run_advanced_threat(params: dict[str, Any]) -> dict[str, Any]:
    cfg = auth.resolve_runtime_config({**params, "persist_credentials": False})
    return collect_advanced_threat(
        cfg,
        sections=_sections(params.get("sections")),
        days=max(1, min(90, auth._coerce_int(params.get("days"), 7))),
        begin_time=params.get("begin_time"),
        end_time=params.get("end_time"),
        threat_levels=params.get("threat_levels"),
        disposal_states=params.get("disposal_states"),
        event_disposal_states=params.get("event_disposal_states"),
        agent_types=params.get("agent_types"),
        detect_sources=params.get("detect_sources"),
        event_types=params.get("event_types"),
        wl_switch=params.get("wl_switch", 0),
        page_no=max(1, auth._coerce_int(params.get("page_no"), 1)),
        page_limit=auth._coerce_int(params.get("page_limit"), 50),
        paginate=auth._coerce_bool(params.get("paginate"), default=True),
        uid=str(params.get("uid") or ""),
        tid=str(params.get("tid") or "0"),
    )
