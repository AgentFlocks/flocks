"""Sangfor EDR dashboard API collection using the HTTP auth pair."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import sangfor_edr_http_login as auth


def _dashboard_requests(
    cfg: auth.RuntimeConfig,
    token: str,
    *,
    days: int,
) -> dict[str, tuple[str, dict[str, Any]]]:
    end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    start = (end - timedelta(days=max(1, days) - 1)).replace(hour=0, minute=0, second=0)
    unix_range = auth._unix_date_range(days)
    text_range = {
        "start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end": end.strftime("%Y-%m-%d %H:%M:%S"),
    }

    def launch(opr: str, app_name: str, extra: Optional[dict[str, Any]] = None):
        payload = {
            "app_args": {"name": app_name, "options": {}},
            "auto": 1,
            "opr": opr,
            "query_id": auth._query_id(),
        }
        payload.update(extra or {})
        return f"/launch.php?s={token}&opr={opr}", payload

    return {
        "agent_overview": launch(
            "get_agent_overview",
            "app.web.event_center.head",
            {"date_range": unix_range},
        ),
        "influenced_agent_overview": launch(
            "get_influenced_agent_overview",
            "app.web.event_center.head",
        ),
        "vulnerability_overview": (
            f"/api/edrgoweb/v1/vulner/list/homepageVulner?_method=post&s={token}&req_type=polling",
            {
                "recentCheckTime": unix_range,
                "uuid": "sf-id-6",
                "tid": "0",
                "uid": cfg.username,
                "token": token,
            },
        ),
        "ransomware_defense": launch(
            "ransom_virus_defense_interface",
            "app.web.event_center.ransom_virus_protection",
        ),
        "realtime_virus": launch(
            "real_time_virus",
            "app.web.event_center.real_time_events",
            {"date": text_range, "day_sum": days},
        ),
        "top_agents": launch(
            "get_top5_agents",
            "app.web.event_center.head",
            {"day_sum": days, "date_range": unix_range},
        ),
        "resource_usage": launch(
            "get_realtime_resource_usage",
            "app.web.event_center.head",
        ),
    }


def collect_dashboard(
    cfg: auth.RuntimeConfig,
    *,
    sections: list[str],
    days: int,
) -> dict[str, Any]:
    auth_result = auth.ensure_http_auth_pair(cfg)
    if not auth_result.get("success"):
        raise RuntimeError(
            "EDR authentication refresh failed: "
            f"{auth_result.get('error') or auth_result.get('reason') or auth_result.get('status')}"
        )
    state, token = auth.load_verified_auth_pair(cfg)
    session = auth.dashboard_session(cfg, state)
    definitions = _dashboard_requests(cfg, token, days=days)
    selected = sections or list(definitions)
    unknown = sorted(set(selected) - set(definitions))
    if unknown:
        raise ValueError(f"Unsupported EDR dashboard sections: {', '.join(unknown)}")

    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for section in selected:
        path, payload = definitions[section]
        try:
            response = session.post(
                auth._url(cfg, path),
                headers=auth._http_headers(cfg),
                json=payload,
                timeout=cfg.timeout,
            )
            response.raise_for_status()
            data[section] = response.json()
        except Exception as exc:
            errors[section] = auth._safe_error(exc, token)

    return {
        "success": not errors,
        "status": "dashboard_api_collected" if not errors else "dashboard_api_partially_collected",
        "base_url": cfg.base_url,
        "days": days,
        "sections": selected,
        "data": data,
        "errors": errors,
        "auth_pair_verified": True,
        "authentication": {
            "status": auth_result.get("status"),
            "login_skipped": bool(auth_result.get("login_skipped")),
        },
    }


def run_dashboard(params: dict[str, Any]) -> dict[str, Any]:
    cfg = auth.resolve_runtime_config({**params, "persist_credentials": False})
    raw_sections = params.get("sections")
    if isinstance(raw_sections, str):
        sections = [item.strip() for item in raw_sections.split(",") if item.strip()]
    elif isinstance(raw_sections, list):
        sections = [str(item).strip() for item in raw_sections if str(item).strip()]
    else:
        sections = []
    days = max(1, min(90, auth._coerce_int(params.get("days"), 7)))
    return collect_dashboard(cfg, sections=sections, days=days)
