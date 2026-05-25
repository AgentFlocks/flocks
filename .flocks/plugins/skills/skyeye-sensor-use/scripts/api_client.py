"""Minimal SkyEye Sensor API client for the skill-local CLI."""

from __future__ import annotations

import json
import random
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from config import (
    AUTH_STATE_FILE,
    BASE_URL,
    COOKIE_FILE,
    CSRF_TOKEN,
    DEFAULT_HEADERS,
    SSL_VERIFY,
    TIMEOUT,
)


class SkyeyeSensorAPIError(Exception):
    """SkyEye Sensor API error."""


def _domain_match(host: str, cookie_domain: str) -> bool:
    pure_domain = cookie_domain.lstrip(".")
    return bool(pure_domain) and (host == pure_domain or host.endswith(f".{pure_domain}"))


def _load_auth_state(auth_file: Path) -> Dict[str, Any]:
    if not auth_file.exists():
        return {"cookies": [], "origins": []}
    return json.loads(auth_file.read_text(encoding="utf-8"))


def _extract_cookies(auth_file: Path) -> list[Dict[str, Any]]:
    auth_data = _load_auth_state(auth_file)
    if isinstance(auth_data, dict):
        cookies = auth_data.get("cookies", [])
        return cookies if isinstance(cookies, list) else []
    if isinstance(auth_data, list):
        return [item for item in auth_data if isinstance(item, dict)]
    return []


def _build_cookie_header(url: str, auth_file: Path) -> str:
    cookies = _extract_cookies(auth_file)
    host = urlparse(url).hostname or url
    pairs = []
    for cookie in cookies:
        if not cookie.get("name") or not cookie.get("value"):
            continue
        domain = str(cookie.get("domain", ""))
        if domain and not _domain_match(host, domain):
            continue
        pairs.append(f"{cookie['name']}={cookie['value']}")
    return "; ".join(pairs)


def _get_csrf_token(auth_file: Optional[Path], fallback_token: str = "") -> Optional[str]:
    if auth_file is None:
        return fallback_token or None
    for cookie in _extract_cookies(auth_file):
        if cookie.get("name") == "csrfToken":
            return cookie.get("value")
    return fallback_token or None


def _get_time_range(days: int = 7, hours: Optional[int] = None) -> Dict[str, int]:
    now = datetime.now()
    end_time = int(now.timestamp() * 1000)
    start_time = int((now - (timedelta(hours=hours) if hours is not None else timedelta(days=days))).timestamp() * 1000)
    return {"start_time": start_time, "end_time": end_time}


def _build_alarm_filter_value(days: int = 7, hours: Optional[int] = None, **filters: Any) -> Dict[str, Any]:
    value = {
        "host_state": "",
        "status": "",
        "ioc": "",
        "threat_name": "",
        "attack_stage": "",
        "x_forwarded_for": "",
        "host": "",
        "status_http": "",
        "alarm_source": "",
        "uri": "",
        "attck_org": "",
        "attck": "",
        "alert_rule": "",
        "attack_dimension": "",
        "is_web_attack": "",
        "user_label": "",
        "sip": "",
        "dip": "",
        "ip_labels": "",
        "gre_key": "",
        "sport": "",
        "dport": "",
        "dst_mac": "",
        "src_mac": "",
        "vlan_id": "",
        "marks": "",
        "vxlan_id": "",
        "start_update_time": "",
        "end_update_time": "",
        "alert_source": "",
        "pcap_filename": "",
        "pcap_id": "",
        "threat_type": "",
        "hazard_level": "",
        "alarm_sip": "",
        "attack_sip": "",
        "alarm_id": "",
        "file_name": "",
        "file_md5": "",
        "file_type": "",
        "proto": "",
        "attack_result": "",
        "attack_type": "",
        "is_read": "",
    }
    value.update(_get_time_range(days=days, hours=hours))
    value.update({key: val for key, val in filters.items() if val is not None})
    return value


class SkyeyeSensorClient:
    """Minimal SkyEye Sensor HTTP client."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        auth_file: Optional[Path] = None,
        csrf_token: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_file = auth_file or AUTH_STATE_FILE
        self.csrf_token = csrf_token or CSRF_TOKEN
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._last_token: Optional[str] = None
        self._refresh_auth_headers()

    def _refresh_auth_headers(self) -> None:
        csrf_token = _get_csrf_token(self.auth_file, self.csrf_token)
        if csrf_token:
            self.session.headers["csrfToken"] = csrf_token
        else:
            self.session.headers.pop("csrfToken", None)

        if self.auth_file and self.auth_file.exists():
            cookie_header = _build_cookie_header(self.base_url, self.auth_file)
            if cookie_header:
                self.session.headers["Cookie"] = cookie_header
            else:
                self.session.headers.pop("Cookie", None)
        else:
            self.session.headers.pop("Cookie", None)

    def _add_common_params(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        request_params = {
            "csrf_token": self._last_token or self.csrf_token or "",
            "r": round(random.random(), 16),
        }
        if params:
            request_params.update(params)
        return request_params

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        form_data: bool = True,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        self._refresh_auth_headers()
        request_params = self._add_common_params(params)

        try:
            if method.upper() == "GET":
                response = self.session.request(
                    method="GET",
                    url=url,
                    params=request_params,
                    timeout=TIMEOUT,
                    verify=SSL_VERIFY,
                )
            else:
                if form_data and data:
                    body = urllib.parse.urlencode(data)
                    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
                else:
                    body = json.dumps(data) if data else None
                    headers = {"Content-Type": "application/json; charset=UTF-8"}
                response = self.session.request(
                    method=method.upper(),
                    url=url,
                    params=request_params,
                    data=body,
                    headers=headers,
                    timeout=TIMEOUT,
                    verify=SSL_VERIFY,
                )

            if response.status_code == 404:
                return {"error": "Not Found", "status_code": 404, "status": 404}
            response.raise_for_status()
            try:
                result = response.json()
                if isinstance(result, dict) and "token" in result:
                    self._last_token = result["token"]
                return result
            except json.JSONDecodeError:
                return {"data": response.text, "status": 200}
        except requests.exceptions.RequestException as exc:
            raise SkyeyeSensorAPIError(f"Request failed: {exc}") from exc

    def get_alarm_count_filtered(
        self,
        *,
        days: int = 7,
        hours: Optional[int] = None,
        **filters: Any,
    ) -> Dict[str, Any]:
        params = {"data_source": "1"}
        params.update(_build_alarm_filter_value(days=days, hours=hours, **filters))
        return self.request("GET", "/skyeye/alarm/get_alarm_count", params=params)

    def get_alarm_list(
        self,
        *,
        days: int = 7,
        hours: Optional[int] = None,
        page: int = 1,
        page_size: int = 10,
        order_by: str = "access_time:desc",
        is_accurate: int = 0,
        **filters: Any,
    ) -> Dict[str, Any]:
        params = {
            "offset": page,
            "limit": page_size,
            "order_by": order_by,
            "is_accurate": is_accurate,
            "data_source": "1",
        }
        params.update(_build_alarm_filter_value(days=days, hours=hours, **filters))
        return self.request("GET", "/skyeye/alarm/alert_list", params=params)
