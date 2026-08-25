"""Small n8n Public API client used by Flocks tools and workflows."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class N8nClientError(RuntimeError):
    """Raised when n8n returns an error or cannot be reached."""

    def __init__(self, message: str, *, status: Optional[int] = None, body: Optional[str] = None):
        self.status = status
        self.body = body
        super().__init__(message)


@dataclass(frozen=True)
class N8nConfig:
    base_url: str = "http://localhost:5678"
    api_key: Optional[str] = None
    timeout_s: float = 30.0

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


class N8nClient:
    """Minimal synchronous client for n8n Public API and webhook calls."""

    def __init__(self, config: N8nConfig):
        self.config = config

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: Optional[Dict[str, str]] = None,
        require_api_key: bool = True,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        url = path if path.startswith(("http://", "https://")) else self.config.normalized_base_url + path
        req_headers = {
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)
        data = None
        if body is not None:
            req_headers.setdefault("Content-Type", "application/json")
            data = json.dumps(body).encode("utf-8")
        if require_api_key:
            if not self.config.api_key:
                raise N8nClientError("n8n API key is required")
            req_headers["X-N8N-API-KEY"] = self.config.api_key

        request = urllib.request.Request(url, data=data, method=method.upper(), headers=req_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s or self.config.timeout_s) as response:
                raw = response.read().decode("utf-8", errors="replace")
                payload: Any
                try:
                    payload = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    payload = raw
                return {
                    "status": response.status,
                    "body": payload,
                    "raw": raw,
                    "headers": dict(response.headers.items()),
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            message = f"n8n HTTP {exc.code} for {method.upper()} {path}"
            raise N8nClientError(message, status=exc.code, body=raw) from exc
        except Exception as exc:
            raise N8nClientError(f"n8n request failed for {method.upper()} {path}: {exc}") from exc

    def health_check(self) -> Dict[str, Any]:
        return self._request("GET", "/healthz", require_api_key=False)

    def list_workflows(self, *, limit: int = 100, cursor: Optional[str] = None) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if cursor:
            query["cursor"] = cursor
        return self._request("GET", "/api/v1/workflows?" + urllib.parse.urlencode(query))

    def list_credentials(self, *, limit: int = 100, cursor: Optional[str] = None) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if cursor:
            query["cursor"] = cursor
        return self._request("GET", "/api/v1/credentials?" + urllib.parse.urlencode(query))

    def create_credential(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/v1/credentials", body=payload)

    def get_credential_schema(self, credential_type_name: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/credentials/schema/{urllib.parse.quote(credential_type_name)}",
        )

    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/v1/workflows/{urllib.parse.quote(workflow_id)}")

    def create_workflow(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/v1/workflows", body=payload)

    def update_workflow(self, workflow_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PUT", f"/api/v1/workflows/{urllib.parse.quote(workflow_id)}", body=payload)

    def activate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/v1/workflows/{urllib.parse.quote(workflow_id)}/activate", body={})

    def deactivate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/v1/workflows/{urllib.parse.quote(workflow_id)}/deactivate", body={})

    def delete_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/api/v1/workflows/{urllib.parse.quote(workflow_id)}")

    def list_executions(
        self,
        *,
        workflow_id: Optional[str] = None,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if workflow_id:
            query["workflowId"] = workflow_id
        if cursor:
            query["cursor"] = cursor
        return self._request("GET", "/api/v1/executions?" + urllib.parse.urlencode(query))

    def get_execution(self, execution_id: str, *, include_data: bool = True) -> Dict[str, Any]:
        query = urllib.parse.urlencode({"includeData": "true" if include_data else "false"})
        return self._request("GET", f"/api/v1/executions/{urllib.parse.quote(execution_id)}?{query}")

    def call_webhook(
        self,
        webhook_path: str,
        *,
        method: str = "POST",
        payload: Any = None,
        headers: Optional[Dict[str, str]] = None,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        clean_path = webhook_path.strip().lstrip("/")
        if clean_path.startswith("webhook/"):
            clean_path = clean_path[len("webhook/") :]
        path = "/webhook/" + clean_path
        request_body = payload
        if method.upper() == "GET":
            request_body = None
            if isinstance(payload, dict) and payload:
                separator = "&" if "?" in path else "?"
                path += separator + urllib.parse.urlencode(payload, doseq=True)
        return self._request(
            method,
            path,
            body=request_body,
            headers=headers,
            require_api_key=False,
            timeout_s=timeout_s,
        )

    def wait_for_recent_execution(
        self,
        *,
        workflow_id: str,
        since_epoch_s: float,
        timeout_s: float = 20.0,
        poll_interval_s: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            response = self.list_executions(workflow_id=workflow_id, limit=5)
            rows = response.get("body", {}).get("data", []) if isinstance(response.get("body"), dict) else []
            for row in rows:
                started_at = row.get("startedAt") if isinstance(row, dict) else None
                started_at_epoch = _parse_n8n_epoch(started_at)
                if started_at_epoch is None:
                    continue
                if started_at_epoch >= since_epoch_s - 2.0:
                    return row
            time.sleep(poll_interval_s)
        return None


def _parse_n8n_epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
