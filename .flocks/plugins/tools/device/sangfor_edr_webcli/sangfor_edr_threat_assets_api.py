"""Sangfor EDR threat-asset analysis API collection."""

from __future__ import annotations

from typing import Any, Optional

import sangfor_edr_http_login as auth


DEFAULT_SECTIONS = ("risk_summary", "zones", "agent_events")
RISK_LEVEL_MAP = {
    "低": 0,
    "低风险": 0,
    "low": 0,
    "中": 1,
    "中风险": 1,
    "medium": 1,
    "高": 2,
    "高风险": 2,
    "high": 2,
}
HOST_TYPE_MAP = {
    "pc": 0,
    "pc终端": 0,
    "电脑": 0,
    "电脑终端": 0,
    "服务器": 1,
    "服务器终端": 1,
    "server": 1,
}
AGENT_STATE_MAP = {
    "全部": -1,
    "全部终端": -1,
    "全部终端状态": -1,
    "all": -1,
    "在线": 0,
    "online": 0,
    "离线": 1,
    "offline": 1,
    "已禁用": 2,
    "disabled": 2,
    "未授权": 3,
    "unauthorized": 3,
    "已卸载": 4,
    "uninstalled": 4,
    "已降级": 6,
    "downgraded": 6,
}
ALLOWED_LIMITS = (10, 20, 50, 100, 500)


def _filter_value(value: Any, default: Any = "") -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return value


def _normalise_enum(value: Any, mapping: dict[str, int], field: str, default: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field} must be one of: {', '.join(str(v) for v in sorted(set(mapping.values())))}")
    if isinstance(value, int) or (isinstance(value, str) and value.strip().lstrip("-").isdigit()):
        candidate = int(value)
        if candidate in mapping.values():
            return candidate
    key = str(value).strip().lower()
    if key in mapping:
        return mapping[key]
    raise ValueError(f"Unsupported {field} value: {value!r}")


def _normalise_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit must be one of {ALLOWED_LIMITS}") from exc
    if limit not in ALLOWED_LIMITS:
        raise ValueError(f"limit must be one of {ALLOWED_LIMITS}")
    return limit


def _threat_requests(
    cfg: auth.RuntimeConfig,
    token: str,
    *,
    days: int,
    info: Any = "",
    risk_level: Any = "",
    host_type: Any = "",
    zone_id: Any = "",
    agent_state: Any = -1,
    isolate_agent: bool = False,
    page: int = 1,
    limit: int = 50,
) -> dict[str, tuple[str, dict[str, Any]]]:
    date_range = auth._unix_date_range(days)
    return {
        "risk_summary": (
            f"/launch.php?s={token}&opr=list_risk_agent_total_count",
            {
                "app_args": {"name": "app.web.event_center.pending_event", "options": {}},
                "opr": "list_risk_agent_total_count",
                "query_id": auth._query_id(),
            },
        ),
        "zones": (
            f"/launch.php?s={token}&opr=list_zones",
            {
                "app_args": {"name": "app.web.host_mgr.host_mgr_new", "option": {}},
                "opr": "list_zones",
                "data": {"local": True},
                "query_id": auth._query_id(),
            },
        ),
        "agent_events": (
            f"/launch.php?s={token}&opr=list_agent_event",
            {
                "app_args": {"name": "app.web.event_center.pending_event", "option": {}},
                "filter": {
                    "info": _filter_value(info),
                    "host_type": _filter_value(host_type),
                    "zone_id": _filter_value(zone_id),
                    "risk_level": _filter_value(risk_level),
                    "agent_state": _filter_value(agent_state, -1),
                    "page": page,
                    "limit": limit,
                    **({"isolate_agent": True} if isolate_agent else {}),
                },
                "day_sum": date_range,
                "opr": "list_agent_event",
                "query_id": auth._query_id(),
            },
        ),
    }


def _post_json(session: Any, cfg: auth.RuntimeConfig, token: str, path: str, payload: dict[str, Any]) -> Any:
    response = session.post(
        auth._url(cfg, path),
        headers=auth._http_headers(cfg),
        json=payload,
        timeout=cfg.timeout,
    )
    response.raise_for_status()
    result = response.json()
    if isinstance(result, dict) and result.get("success") is False:
        raise RuntimeError(str(result.get("msg") or "EDR threat-asset API rejected the request."))
    return result


def _zone_entries(result: Any) -> list[dict[str, Any]]:
    def flatten(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        entries: list[dict[str, Any]] = []
        if value.get("zone_id"):
            entries.append(value)
        for key in ("zones", "children"):
            nested = value.get(key)
            if isinstance(nested, list):
                for item in nested:
                    entries.extend(flatten(item))
        return entries

    data = result.get("data") if isinstance(result, dict) else None
    entries: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            entries.extend(flatten(item))
    elif isinstance(data, dict):
        entries.extend(flatten(data))
    return entries


def _resolve_zone_id(raw_zone: Any, zones_result: Any) -> str:
    if raw_zone is None or not str(raw_zone).strip():
        return ""
    value = str(raw_zone).strip()
    entries = _zone_entries(zones_result)
    exact_id = [zone for zone in entries if str(zone.get("zone_id") or "") == value]
    if exact_id:
        return value
    matches = [
        zone
        for zone in entries
        if value in {str(zone.get("zone_name") or "").strip(), str(zone.get("full_zone_name") or "").strip()}
    ]
    if len(matches) == 1:
        return str(matches[0].get("zone_id") or "")
    if len(matches) > 1:
        raise ValueError(f"Asset zone name is ambiguous: {value!r}")
    raise ValueError(f"Asset zone was not found: {value!r}")


def _collect_agent_events(
    session: Any,
    cfg: auth.RuntimeConfig,
    token: str,
    *,
    days: int,
    info: Any,
    risk_level: Any,
    host_type: Any,
    zone_id: Any,
    agent_state: Any,
    isolate_agent: bool,
    page: int,
    limit: int,
    paginate: bool,
) -> dict[str, Any]:
    first: Optional[dict[str, Any]] = None
    items: list[Any] = []
    current_page = max(1, page)
    max_pages = 100
    while True:
        request = _threat_requests(
            cfg,
            token,
            days=days,
            info=info,
            risk_level=risk_level,
            host_type=host_type,
            zone_id=zone_id,
            agent_state=agent_state,
            isolate_agent=isolate_agent,
            page=current_page,
            limit=limit,
        )["agent_events"]
        result = _post_json(session, cfg, token, *request)
        if not isinstance(result, dict):
            return {"response": result, "pages": current_page}
        data = result.get("data")
        if not isinstance(data, dict):
            return result
        if first is None:
            first = dict(data)
        page_items = data.get("list") if isinstance(data.get("list"), list) else []
        items.extend(page_items)
        total = int(data.get("total_items") or 0)
        if not paginate or not page_items or len(items) >= total or len(page_items) < limit or current_page >= max_pages:
            first["list"] = items
            first["pages"] = current_page
            return {**result, "data": first}
        current_page += 1


def collect_threat_assets(
    cfg: auth.RuntimeConfig,
    *,
    sections: list[str],
    days: int,
    info: Any = "",
    risk_level: Any = "",
    host_type: Any = "",
    zone_id: Any = "",
    agent_state: Any = -1,
    isolate_agent: bool = False,
    page: int = 1,
    limit: int = 50,
    paginate: bool = True,
) -> dict[str, Any]:
    risk_level = _normalise_enum(risk_level, RISK_LEVEL_MAP, "risk_level", "")
    host_type = _normalise_enum(host_type, HOST_TYPE_MAP, "host_type", "")
    agent_state = _normalise_enum(agent_state, AGENT_STATE_MAP, "agent_state", -1)
    limit = _normalise_limit(limit)
    auth_result = auth.ensure_http_auth_pair(cfg)
    if not auth_result.get("success"):
        raise RuntimeError(
            "EDR authentication refresh failed: "
            f"{auth_result.get('error') or auth_result.get('reason') or auth_result.get('status')}"
        )
    state, token = auth.load_verified_auth_pair(cfg)
    session = auth.dashboard_session(cfg, state)
    selected = sections or list(DEFAULT_SECTIONS)
    unknown = sorted(set(selected) - set(DEFAULT_SECTIONS))
    if unknown:
        raise ValueError(f"Unsupported EDR threat-asset sections: {', '.join(unknown)}")

    zones_result = None
    resolved_zone_id = "" if not zone_id else None
    if zone_id:
        zone_path, zone_payload = _threat_requests(cfg, token, days=days)["zones"]
        zones_result = _post_json(session, cfg, token, zone_path, zone_payload)
        resolved_zone_id = _resolve_zone_id(zone_id, zones_result)

    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for section in selected:
        try:
            if section == "agent_events":
                data[section] = _collect_agent_events(
                    session,
                    cfg,
                    token,
                    days=days,
                    info=info,
                    risk_level=risk_level,
                    host_type=host_type,
                    zone_id=resolved_zone_id,
                    agent_state=agent_state,
                    isolate_agent=isolate_agent,
                    page=page,
                    limit=limit,
                    paginate=paginate,
                )
            else:
                path, payload = _threat_requests(cfg, token, days=days)[section]
                data[section] = zones_result if section == "zones" and zones_result is not None else _post_json(session, cfg, token, path, payload)
        except Exception as exc:
            errors[section] = auth._safe_error(exc, token)
    return {
        "success": not errors,
        "status": "threat_assets_collected" if not errors else "threat_assets_partially_collected",
        "base_url": cfg.base_url,
        "days": days,
        "sections": selected,
        "filters": {
            "risk_level": risk_level,
            "info": info,
            "host_type": host_type,
            "zone_id": resolved_zone_id,
            "agent_state": agent_state,
            "isolate_agent": isolate_agent,
            "page": page,
            "limit": limit,
            "paginate": paginate,
        },
        "data": data,
        "errors": errors,
        "auth_pair_verified": True,
        "authentication": {
            "status": auth_result.get("status"),
            "login_skipped": bool(auth_result.get("login_skipped")),
        },
    }


def run_threat_assets(params: dict[str, Any]) -> dict[str, Any]:
    cfg = auth.resolve_runtime_config({**params, "persist_credentials": False})
    raw_sections = params.get("sections")
    if isinstance(raw_sections, str):
        sections = [item.strip() for item in raw_sections.split(",") if item.strip()]
    elif isinstance(raw_sections, list):
        sections = [str(item).strip() for item in raw_sections if str(item).strip()]
    else:
        sections = []
    return collect_threat_assets(
        cfg,
        sections=sections,
        days=max(1, min(90, auth._coerce_int(params.get("days"), 7))),
        info=params.get("info", ""),
        risk_level=params.get("risk_level", ""),
        host_type=params.get("host_type", ""),
        zone_id=params.get("zone_name") or params.get("zone_id", ""),
        agent_state=params.get("agent_state", -1),
        isolate_agent=auth._coerce_bool(params.get("isolate_agent"), default=False),
        page=max(1, auth._coerce_int(params.get("page"), 1)),
        limit=_normalise_limit(params.get("limit", 50)),
        paginate=auth._coerce_bool(params.get("paginate"), default=True),
    )
