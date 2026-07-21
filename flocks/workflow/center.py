"""Workflow center for skill-generated workflow registration and publishing.

Service driver selection (env var FLOCKS_WORKFLOW_SERVICE_DRIVER):
  - "local"  (default): run workflow service as a local subprocess using the
    current Python environment – fast startup, no Docker required.
  - "docker": run inside a Docker container (requires Docker daemon).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse
from urllib import error as url_error
from urllib import request as url_request

from flocks.config.config import Config
from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT
from flocks.sandbox.docker import docker_container_state, exec_docker
from flocks.workflow.store import WorkflowStore
from flocks.utils.log import Log
from flocks.workflow.models import Workflow
from flocks.workflow.requirements import resolve_python_package_index_url
from flocks.workflow.visibility import is_hidden_workflow

log = Log.create(service="workflow.center")

_REGISTRY_PREFIX = "workflow_registry/"
_RELEASE_PREFIX = "workflow_release/"
_RUNTIME_PREFIX = "workflow_runtime/"
_API_SERVICE_PREFIX = "workflow_api_service/"
_SERVICE_DATA_DIR = "workflow-services"
_DEFAULT_PORT_START = 19000
_DEFAULT_PORT_END = 19999
_SERVICE_CONTAINER_PORT = 8000
_DEFAULT_IMAGE = "python:3.12-slim"
_DEFAULT_HEALTH_RETRIES = 20
_DEFAULT_HEALTH_INTERVAL_S = 2.0
_DEFAULT_RUNTIME_INSTALL_HEALTH_RETRIES = 450  # 450 × 2s = 15 minutes
_DEFAULT_STOP_TIMEOUT_S = 15.0
_DEFAULT_LOCAL_STOP_GRACE_S = 5.0
_SERVICE_API_KEY_ENV = "FLOCKS_WORKFLOW_SERVICE_API_KEY"
_PORT_RESERVATION_TTL_S = 30 * 60

# Service driver: "local" runs as a subprocess; "docker" runs in a container.
_DEFAULT_SERVICE_DRIVER = "local"
_PORT_ALLOCATION_LOCK = asyncio.Lock()
_IN_FLIGHT_PORT_RESERVATIONS: Dict[int, float] = {}


class WorkflowCenterError(Exception):
    """Base workflow center exception."""


class WorkflowNotFoundError(WorkflowCenterError):
    """Raised when workflow registry entry is missing."""


class WorkflowNotPublishedError(WorkflowCenterError):
    """Raised when invoking a workflow that is not published."""


def _key_to_string(key: Any) -> str:
    if isinstance(key, list):
        return "/".join(str(part) for part in key)
    return str(key)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _registry_key(workflow_id: str) -> str:
    return f"{_REGISTRY_PREFIX}{workflow_id}"


def _release_key(workflow_id: str, release_id: str) -> str:
    return f"{_RELEASE_PREFIX}{workflow_id}/{release_id}"


def _active_release_key(workflow_id: str) -> str:
    return f"{_RELEASE_PREFIX}{workflow_id}/active"


def _runtime_key(workflow_id: str) -> str:
    return f"{_RUNTIME_PREFIX}{workflow_id}"


def _normalize_workflow_id(path: Path) -> str:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return digest[:24]


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 128), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _generate_api_key() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _workflow_service_auth_headers(runtime: Dict[str, Any]) -> Optional[Dict[str, str]]:
    api_key = runtime.get("apiKey")
    if not api_key:
        return None
    return {"x-api-key": str(api_key)}


GLOBAL_WORKFLOW_ROOT: Path = Path.home() / ".flocks" / "workflow"


def _resolve_global_workflow_root() -> Path:
    """~/.flocks/plugins/workflows/ — canonical global (user-level) workflow storage."""
    return Path.home() / ".flocks" / "plugins" / "workflows"


def resolve_global_workflow_roots() -> list[Path]:
    """Return all global workflow scan directories (lowest → highest priority).

    Covers legacy paths for backward-read compatibility and the new canonical
    plugins/workflows/ path.
    """
    home = Path.home() / ".flocks"
    return [
        home / "plugins" / "workflow",  # legacy compat (read-only)
        home / "workflow",  # legacy compat (read-only)
        home / "plugins" / "workflows",  # new canonical (read + write)
    ]


def resolve_project_workflow_roots(base_dir: Optional[Path] = None) -> list[Path]:
    """Return all project-level workflow scan directories (lowest → highest priority).

    Covers legacy paths for backward-read compatibility and the new canonical
    plugins/workflows/ path.
    """
    root = base_dir or Path.cwd()
    flocks = root / ".flocks"
    return [
        flocks / "plugins" / "workflow",  # legacy compat (read-only)
        flocks / "workflow",  # legacy compat (read-only)
        flocks / "plugins" / "workflows",  # new canonical (read + write)
    ]


def _resolve_project_workflow_root(base_dir: Optional[Path] = None) -> Path:
    """<cwd>/.flocks/plugins/workflows/ — canonical project-level workflow storage."""
    root = base_dir or Path.cwd()
    return root / ".flocks" / "plugins" / "workflows"


def _is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _port_from_service_url(value: Any) -> Optional[int]:
    if not value:
        return None
    try:
        parsed = urlparse(str(value))
        return parsed.port
    except (TypeError, ValueError):
        return None


def _ports_from_service_record(record: Any) -> set[int]:
    if not isinstance(record, dict):
        return set()

    ports: set[int] = set()
    for key in ("hostPort", "port"):
        try:
            port = int(record.get(key) or 0)
        except (TypeError, ValueError):
            port = 0
        if 1 <= port <= 65535:
            ports.add(port)

    for key in ("serviceUrl", "invokeUrl"):
        port = _port_from_service_url(record.get(key))
        if port is not None:
            ports.add(port)
    return ports


async def _reserved_service_ports() -> set[int]:
    ports: set[int] = set()
    for prefix in (_RUNTIME_PREFIX, _API_SERVICE_PREFIX, _REGISTRY_PREFIX):
        try:
            keys = await WorkflowStore.kv_list_keys(prefix)
        except Exception as exc:
            log.warning("workflow.port.list_reserved_failed", {"prefix": prefix, "error": str(exc)})
            continue

        for key in keys:
            try:
                record = await WorkflowStore.kv_get(key)
            except Exception as exc:
                log.warning("workflow.port.read_reserved_failed", {"key": _key_to_string(key), "error": str(exc)})
                continue
            ports.update(_ports_from_service_record(record))
    return ports


def _reserved_in_flight_ports() -> set[int]:
    now = time.time()
    expired = [port for port, expires_at in _IN_FLIGHT_PORT_RESERVATIONS.items() if expires_at <= now]
    for port in expired:
        _IN_FLIGHT_PORT_RESERVATIONS.pop(port, None)
    return set(_IN_FLIGHT_PORT_RESERVATIONS)


def _release_port_reservation(port: Optional[int]) -> None:
    if port is not None:
        _IN_FLIGHT_PORT_RESERVATIONS.pop(port, None)


async def _allocate_port() -> int:
    start = int(os.getenv("FLOCKS_WORKFLOW_SERVICE_PORT_START", str(_DEFAULT_PORT_START)))
    end = int(os.getenv("FLOCKS_WORKFLOW_SERVICE_PORT_END", str(_DEFAULT_PORT_END)))
    if start > end:
        raise WorkflowCenterError("Invalid workflow service port range")
    async with _PORT_ALLOCATION_LOCK:
        reserved_ports = await _reserved_service_ports()
        reserved_ports.update(_reserved_in_flight_ports())
        for port in range(start, end + 1):
            if port not in reserved_ports and _is_port_available(port):
                _IN_FLIGHT_PORT_RESERVATIONS[port] = time.time() + _PORT_RESERVATION_TTL_S
                return port
    raise WorkflowCenterError("No available workflow service port")


async def _read_registry(workflow_id: str) -> Dict[str, Any]:
    data = await WorkflowStore.kv_get(_registry_key(workflow_id))
    if not data:
        raise WorkflowNotFoundError(f"Workflow not registered: {workflow_id}")
    return data


async def _scan_workflow_dir(
    workflow_root: Path,
    source_type: str,
    by_id: Dict[str, Dict[str, Any]],
) -> None:
    """Scan a single workflow directory and upsert entries into *by_id*.

    Later calls overwrite earlier ones for the same workflow_id (last wins),
    so callers should invoke this for lower-priority directories first.
    """
    if not workflow_root.is_dir():
        return
    for workflow_path in sorted(workflow_root.glob("*/workflow.json")):
        try:
            raw = json.loads(workflow_path.read_text(encoding="utf-8"))
            meta_path = workflow_path.parent / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else None
            if is_hidden_workflow(raw, meta):
                continue
            Workflow.from_dict(raw)
        except Exception as exc:
            log.warning(
                "workflow.center.scan.skip_invalid",
                {"path": str(workflow_path), "error": str(exc)},
            )
            continue

        workflow_id = _normalize_workflow_id(workflow_path)
        fp = _fingerprint(workflow_path)
        now_ms = _now_ms()
        existing = await WorkflowStore.kv_get(_registry_key(workflow_id)) or {}
        created_at = existing.get("registeredAt", now_ms)
        draft_changed = bool(existing) and existing.get("fingerprint") != fp
        entry = {
            "workflowId": workflow_id,
            "name": raw.get("name") or workflow_path.parent.name,
            "description": raw.get("description") or "",
            "sourceType": source_type,
            # native: project-level workflows are part of the project deployment;
            # global (~/.flocks/plugins/workflows/) are user-level customizations.
            "native": source_type == "project",
            "workflowPath": str(workflow_path),
            "fingerprint": fp,
            "publishStatus": existing.get("publishStatus", "unpublished"),
            "registeredAt": created_at,
            "updatedAt": now_ms,
            "draftChanged": draft_changed,
            "activeReleaseId": existing.get("activeReleaseId"),
            "serviceKey": existing.get("serviceKey"),
            "serviceUrl": existing.get("serviceUrl"),
        }
        await WorkflowStore.kv_put(_registry_key(workflow_id), entry)
        by_id[workflow_id] = entry


async def scan_skill_workflows(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Scan global and project workflow directories and register entries.

    Scan order (lowest → highest priority), see resolve_global_workflow_roots /
    resolve_project_workflow_roots:
      1. ~/.flocks/plugins/workflow/        (global legacy, sourceType="global")
      2. ~/.flocks/workflow/                (global compat, sourceType="global")
      3. ~/.flocks/plugins/workflows/       (global canonical, sourceType="global")
      4. <cwd>/.flocks/plugins/workflow/    (project legacy, sourceType="project")
      5. <cwd>/.flocks/workflow/            (project compat, sourceType="project")
      6. <cwd>/.flocks/plugins/workflows/   (project canonical, sourceType="project")

    When two directories contain a workflow with the same ID, the later
    (higher-priority) entry wins.
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for path in resolve_global_workflow_roots():
        await _scan_workflow_dir(path, "global", by_id)
    for path in resolve_project_workflow_roots(base_dir):
        await _scan_workflow_dir(path, "project", by_id)

    entries = list(by_id.values())
    entries.sort(key=lambda item: item.get("updatedAt", 0), reverse=True)
    return entries


def format_workflow_entries(
    entries: List[Dict[str, Any]],
    *,
    markdown: bool = False,
) -> str:
    """Render scan_skill_workflows() entries as a human-readable text block.

    Args:
        entries: Raw workflow dicts from scan_skill_workflows().
        markdown: When True, apply markdown bold/backtick formatting.

    Returns:
        Formatted body string (no header, no footer). Empty string when entries is empty.
    """
    if not entries:
        return ""

    lines: List[str] = []
    for idx, entry in enumerate(entries, start=1):
        name = entry.get("name") or "(unnamed)"
        desc = entry.get("description") or ""
        path = entry.get("workflowPath") or ""
        source = entry.get("sourceType") or "project"
        status = entry.get("publishStatus") or "unpublished"

        name_str = f"**{name}**" if markdown else name
        lines.append(f"{idx}. {name_str} [{source}] ({status})")
        if desc:
            lines.append(f"   {desc}")
        if path:
            path_str = f"`{path}`" if markdown else path
            lines.append(f"   Path: {path_str}")

    return "\n".join(lines)


async def list_registry_entries() -> List[Dict[str, Any]]:
    """List registered skill workflows."""
    keys = await WorkflowStore.kv_list(_REGISTRY_PREFIX)
    items: List[Dict[str, Any]] = []
    for raw_key in keys:
        key = _key_to_string(raw_key)
        entry = await WorkflowStore.kv_get(key)
        if entry:
            items.append(entry)
    items.sort(key=lambda item: item.get("updatedAt", 0), reverse=True)
    return items


def _service_release_file(workflow_id: str, release_id: str) -> Path:
    base = Config.get_data_path() / _SERVICE_DATA_DIR / "releases" / workflow_id
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{release_id}.json"


def _service_cache_dir(name: str) -> Path:
    cache_dir = Config.get_data_path() / _SERVICE_DATA_DIR / "cache" / name
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _workflow_container_name(workflow_id: str, release_id: str) -> str:
    return f"flocks-wf-{workflow_id[:8]}-{release_id[:8]}"


async def _write_release_snapshot(workflow_id: str, release_id: str, workflow_json: Dict[str, Any]) -> Path:
    release_file = _service_release_file(workflow_id, release_id)
    release_file.write_text(json.dumps(workflow_json), encoding="utf-8")
    return release_file


async def _write_requirements_snapshot(release_dir: Path) -> bool:
    """Generate requirements.txt from the current environment into release_dir.

    Uses 'pip freeze' (via the running Python) so the container can pre-install
    all dependencies before building the wheel, making startup significantly faster.
    Returns True on success, False if the snapshot could not be created.
    """
    import sys

    req_file = release_dir / "requirements.txt"
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "freeze",
            "--exclude-editable",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0 and stdout:
            req_file.write_bytes(stdout)
            return True
    except Exception as exc:
        log.warning("workflow.center.requirements.snapshot.failed", {"error": str(exc)})
    return False


def _host_service_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _json_post(
    url: str,
    payload: Dict[str, Any],
    timeout_s: float = 10.0,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = url_request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with url_request.urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _json_get(url: str, timeout_s: float = 5.0) -> Dict[str, Any]:
    request = url_request.Request(url=url, method="GET")
    with url_request.urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


async def _wait_service_healthy(service_url: str, retries: int = 20, interval_s: float = 0.5) -> bool:
    for _ in range(retries):
        try:
            payload = await asyncio.to_thread(_json_get, f"{service_url}/health", 2.0)
            if payload.get("ok") is True:
                return True
        except Exception:
            await asyncio.sleep(interval_s)
            continue
        await asyncio.sleep(interval_s)
    return False


def _docker_proxy_env_value(env_value: str) -> str:
    """Translate host loopback proxy URLs so containers can reach them."""
    raw = (env_value or "").strip()
    if not raw:
        return env_value

    has_scheme = "://" in raw
    parse_input = raw if has_scheme else f"http://{raw}"
    try:
        parsed = urlparse(parse_input)
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            return env_value

        userinfo = ""
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo += f":{parsed.password}"
            userinfo += "@"
        netloc = f"{userinfo}host.docker.internal"
        if parsed.port:
            netloc += f":{parsed.port}"
        rewritten = urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
        if not has_scheme and rewritten.startswith("http://"):
            return rewritten[len("http://") :]
        return rewritten
    except Exception:
        return env_value


def _docker_proxy_uses_host_gateway(env_value: str) -> bool:
    raw = (env_value or "").strip()
    if not raw:
        return False
    parse_input = raw if "://" in raw else f"http://{raw}"
    try:
        return (urlparse(parse_input).hostname or "").lower() == "host.docker.internal"
    except Exception:
        return False


async def _docker_logs_tail(container_name: str, *, lines: int = 80) -> str:
    stdout, stderr, _ = await exec_docker(
        ["logs", "--tail", str(lines), container_name],
        allow_failure=True,
        timeout_s=10,
    )
    return (stdout or stderr or "").strip()


async def _wait_docker_service_healthy(
    service_url: str,
    container_name: str,
    *,
    retries: int,
    interval_s: float,
) -> bool:
    for _ in range(retries):
        try:
            payload = await asyncio.to_thread(_json_get, f"{service_url}/health", 2.0)
            if payload.get("ok") is True:
                return True
        except Exception:
            pass

        state = await docker_container_state(container_name)
        if state.get("exists") and not state.get("running"):
            logs = await _docker_logs_tail(container_name)
            detail = logs or "container exited before reporting healthy"
            raise WorkflowCenterError(
                f"Published workflow service container exited before health check passed: {detail}"
            )
        await asyncio.sleep(interval_s)
    return False


async def _stop_and_remove_container(container_name: str) -> bool:
    _, stderr, code = await exec_docker(
        ["rm", "-f", container_name],
        allow_failure=True,
        timeout_s=float(os.getenv("FLOCKS_WORKFLOW_SERVICE_STOP_TIMEOUT_S", str(_DEFAULT_STOP_TIMEOUT_S))),
    )
    if code != 0:
        log.warning("workflow.container.stop_failed", {"container": container_name, "error": stderr})
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Local-process service driver
# ─────────────────────────────────────────────────────────────────────────────

_LOCAL_PID_PREFIX = "workflow_local_pid/"


def _local_pid_key(workflow_id: str) -> str:
    return f"{_LOCAL_PID_PREFIX}{workflow_id}"


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_pid_exit(pid: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + max(timeout_s, 0.0)
    while time.monotonic() < deadline:
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                return True
        except ChildProcessError:
            pass
        if not _pid_is_running(pid):
            return True
        await asyncio.sleep(0.1)
    return not _pid_is_running(pid)


def _signal_local_process(pid: int, sig: signal.Signals, process_group_id: Optional[int] = None) -> None:
    if process_group_id:
        try:
            os.killpg(int(process_group_id), sig)
            return
        except (ProcessLookupError, OSError):
            pass
    try:
        os.kill(int(pid), sig)
    except (ProcessLookupError, OSError):
        pass


async def _stop_local_service(workflow_id: str) -> None:
    """Kill a previously started local workflow service process."""
    pid_record = await WorkflowStore.kv_get(_local_pid_key(workflow_id))
    if not pid_record:
        return
    pid = pid_record.get("pid")
    if not pid:
        await WorkflowStore.kv_remove(_local_pid_key(workflow_id))
        return
    pid_int = int(pid)
    process_group_id = pid_record.get("processGroupId")
    try:
        _signal_local_process(pid_int, signal.SIGTERM, process_group_id)
        grace_s = float(os.getenv("FLOCKS_WORKFLOW_LOCAL_STOP_GRACE_S", str(_DEFAULT_LOCAL_STOP_GRACE_S)))
        exited = await _wait_for_pid_exit(pid_int, grace_s)
        if not exited:
            log.warning("workflow.local.force_kill", {"workflow_id": workflow_id, "pid": pid_int})
            _signal_local_process(pid_int, signal.SIGKILL, process_group_id)
            await _wait_for_pid_exit(pid_int, 1.0)
    finally:
        await WorkflowStore.kv_remove(_local_pid_key(workflow_id))


async def _stop_local_runtime(workflow_id: str, runtime: Dict[str, Any]) -> bool:
    """Stop a local workflow service using the persisted runtime record."""
    pid_raw = runtime.get("containerId") or runtime.get("pid")
    try:
        pid = int(pid_raw)
    except (TypeError, ValueError):
        await _stop_local_service(workflow_id)
        return False

    process_group_id = runtime.get("processGroupId") or pid
    _signal_local_process(pid, signal.SIGTERM, process_group_id)
    grace_s = float(os.getenv("FLOCKS_WORKFLOW_LOCAL_STOP_GRACE_S", str(_DEFAULT_LOCAL_STOP_GRACE_S)))
    exited = await _wait_for_pid_exit(pid, grace_s)
    if not exited:
        log.warning("workflow.local.force_kill", {"workflow_id": workflow_id, "pid": pid})
        _signal_local_process(pid, signal.SIGKILL, process_group_id)
        exited = await _wait_for_pid_exit(pid, 1.0)
    await WorkflowStore.kv_remove(_local_pid_key(workflow_id))
    return exited


def _runtime_driver(runtime: Optional[Dict[str, Any]]) -> str:
    """Resolve the driver for an already-published runtime record."""
    if not runtime:
        return _service_driver()
    driver = str(runtime.get("driver") or "").strip().lower()
    if driver in {"local", "docker"}:
        return driver
    image = str(runtime.get("image") or "").strip().lower()
    container_name = str(runtime.get("containerName") or "").strip().lower()
    if image == "local" or container_name.startswith("local-"):
        return "local"
    return "docker"


async def _mark_release_inactive(workflow_id: str, release_id: Optional[Any]) -> None:
    if not release_id:
        return
    release_record = await WorkflowStore.kv_get(_release_key(workflow_id, str(release_id))) or {}
    if release_record:
        release_record["status"] = "inactive"
        release_record["deactivatedAt"] = _now_ms()
        await WorkflowStore.kv_put(_release_key(workflow_id, str(release_id)), release_record)


async def _stop_runtime_record(
    workflow_id: str,
    runtime: Dict[str, Any],
    *,
    update_registry: bool,
    clear_runtime_keys: bool = True,
) -> Dict[str, Any]:
    """Stop the concrete runtime instance recorded in storage."""
    registry = await _read_registry(workflow_id)
    driver = _runtime_driver(runtime)
    stopped = False
    if driver == "local":
        stopped = await _stop_local_runtime(workflow_id, runtime)
    else:
        container_name = runtime.get("containerName")
        if container_name:
            stopped = await _stop_and_remove_container(str(container_name))
            if not stopped:
                raise WorkflowCenterError(f"Failed to stop Docker container: {container_name}")

    active = (await WorkflowStore.kv_get(_active_release_key(workflow_id)) or {}) if clear_runtime_keys else {}
    release_id = runtime.get("releaseId") or active.get("releaseId")
    await _mark_release_inactive(workflow_id, release_id)
    if clear_runtime_keys:
        await WorkflowStore.kv_remove(_runtime_key(workflow_id))
        await WorkflowStore.kv_remove(_active_release_key(workflow_id))

    if update_registry:
        registry["publishStatus"] = "stopped"
        registry["updatedAt"] = _now_ms()
        registry["serviceUrl"] = None
        await WorkflowStore.kv_put(_registry_key(workflow_id), registry)

    return {
        "workflowId": workflow_id,
        "status": "stopped",
        "stopped": stopped,
        "driver": driver,
    }


async def _stop_existing_runtime_for_publish(workflow_id: str) -> None:
    """Best-effort cleanup before starting a replacement service."""
    runtime = await WorkflowStore.kv_get(_runtime_key(workflow_id))
    if isinstance(runtime, dict) and runtime:
        await _stop_runtime_record(workflow_id, runtime, update_registry=False)
    else:
        await _stop_local_service(workflow_id)


async def publish_workflow_local(workflow_id: str, *, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Publish a workflow as a local subprocess using the current Python env.

    This is the default driver for development: no Docker, instant startup,
    uses the same .venv as the main server.
    """
    registry = await _read_registry(workflow_id)
    workflow_path = Path(str(registry["workflowPath"]))
    if not workflow_path.exists():
        raise WorkflowCenterError(f"Workflow file not found: {workflow_path}")

    workflow_json = json.loads(workflow_path.read_text(encoding="utf-8"))
    Workflow.from_dict(workflow_json)

    release_id = uuid.uuid4().hex
    now_ms = _now_ms()
    registry["publishStatus"] = "publishing"
    registry["updatedAt"] = now_ms
    await WorkflowStore.kv_put(_registry_key(workflow_id), registry)

    release_snapshot_file = await _write_release_snapshot(workflow_id, release_id, workflow_json)

    await _stop_existing_runtime_for_publish(workflow_id)

    host_port = await _allocate_port()
    service_url = _host_service_url(host_port)
    service_key = workflow_id
    runtime_api_key = api_key or _generate_api_key()

    proc: asyncio.subprocess.Process | None = None
    try:
        env = os.environ.copy()
        env[_SERVICE_API_KEY_ENV] = runtime_api_key
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "flocks.workflow.service_runtime",
            "--workflow",
            str(release_snapshot_file),
            "--workflow-id",
            workflow_id,
            "--release-id",
            release_id,
            "--host",
            "127.0.0.1",
            "--port",
            str(host_port),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )

        await WorkflowStore.kv_put(
            _local_pid_key(workflow_id),
            {
                "pid": proc.pid,
                "processGroupId": proc.pid,
                "port": host_port,
            },
        )

        health_retries = int(os.getenv("FLOCKS_WORKFLOW_SERVICE_HEALTH_RETRIES", str(_DEFAULT_HEALTH_RETRIES)))
        health_interval_s = float(
            os.getenv("FLOCKS_WORKFLOW_SERVICE_HEALTH_INTERVAL_S", str(_DEFAULT_HEALTH_INTERVAL_S))
        )

        healthy = await _wait_service_healthy(service_url, retries=health_retries, interval_s=health_interval_s)
        if not healthy:
            try:
                await _stop_local_service(workflow_id)
            except Exception:
                pass
            raise WorkflowCenterError("Local workflow service failed health check")

        active_record = {
            "releaseId": release_id,
            "workflowId": workflow_id,
            "serviceKey": service_key,
            "containerName": f"local-{workflow_id[:8]}-{release_id[:8]}",
            "containerId": str(proc.pid),
            "processGroupId": proc.pid,
            "image": "local",
            "hostPort": host_port,
            "serviceUrl": service_url,
            "status": "active",
            "updatedAt": _now_ms(),
            "driver": "local",
            "apiKey": runtime_api_key,
        }
        await WorkflowStore.kv_put(_active_release_key(workflow_id), active_record)
        await WorkflowStore.kv_put(_runtime_key(workflow_id), active_record)
        _release_port_reservation(host_port)
    except Exception as exc:
        _release_port_reservation(host_port)
        if proc is not None:
            try:
                _signal_local_process(proc.pid, signal.SIGTERM, proc.pid)
                await _wait_for_pid_exit(proc.pid, 1.0)
            except Exception:
                pass
        registry["publishStatus"] = "failed"
        registry["updatedAt"] = _now_ms()
        await WorkflowStore.kv_put(_registry_key(workflow_id), registry)
        if isinstance(exc, WorkflowCenterError):
            raise
        raise WorkflowCenterError(str(exc)) from exc

    registry["publishStatus"] = "active"
    registry["activeReleaseId"] = release_id
    registry["serviceKey"] = service_key
    registry["serviceUrl"] = service_url
    registry["updatedAt"] = _now_ms()
    await WorkflowStore.kv_put(_registry_key(workflow_id), registry)

    log.info("workflow.local.published", {"id": workflow_id, "port": host_port, "pid": proc.pid})
    return active_record


async def stop_local_service(workflow_id: str) -> Dict[str, Any]:
    """Stop a local workflow service process."""
    await _stop_local_service(workflow_id)
    registry = await _read_registry(workflow_id)
    await WorkflowStore.kv_remove(_runtime_key(workflow_id))
    await WorkflowStore.kv_remove(_active_release_key(workflow_id))
    registry["publishStatus"] = "stopped"
    registry["updatedAt"] = _now_ms()
    registry["serviceUrl"] = None
    await WorkflowStore.kv_put(_registry_key(workflow_id), registry)
    return {"workflowId": workflow_id, "status": "stopped", "stopped": True}


# ─────────────────────────────────────────────────────────────────────────────
# Unified publish / stop entry points (driver-aware)
# ─────────────────────────────────────────────────────────────────────────────


def _service_driver() -> str:
    return os.getenv("FLOCKS_WORKFLOW_SERVICE_DRIVER", _DEFAULT_SERVICE_DRIVER).lower()


async def publish_workflow(
    workflow_id: str,
    image: Optional[str] = None,
    driver: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish a workflow using the configured service driver (local or docker)."""
    resolved_driver = (driver or _service_driver()).strip().lower()
    if resolved_driver == "docker":
        return await _publish_workflow_docker(workflow_id, image=image, api_key=api_key)
    if resolved_driver != "local":
        raise WorkflowCenterError(f"Unsupported workflow service driver: {resolved_driver}")
    return await publish_workflow_local(workflow_id, api_key=api_key)


async def stop_workflow_service(workflow_id: str) -> Dict[str, Any]:
    """Stop a published workflow service (driver-aware)."""
    runtime = await WorkflowStore.kv_get(_runtime_key(workflow_id))
    if isinstance(runtime, dict) and runtime:
        return await _stop_runtime_record(workflow_id, runtime, update_registry=True)
    active = await WorkflowStore.kv_get(_active_release_key(workflow_id))
    if isinstance(active, dict) and active:
        return await _stop_runtime_record(workflow_id, active, update_registry=True)

    # Fallback for legacy local records that predate workflow_runtime/.
    return await stop_local_service(workflow_id)


async def _publish_workflow_docker(
    workflow_id: str,
    image: Optional[str] = None,
    *,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish a registered workflow as a Docker service container."""
    registry = await _read_registry(workflow_id)
    workflow_path = Path(str(registry["workflowPath"]))
    if not workflow_path.exists():
        raise WorkflowCenterError(f"Workflow file not found: {workflow_path}")

    workflow_json = json.loads(workflow_path.read_text(encoding="utf-8"))
    Workflow.from_dict(workflow_json)

    release_id = uuid.uuid4().hex
    now_ms = _now_ms()
    registry["publishStatus"] = "publishing"
    registry["updatedAt"] = now_ms
    await WorkflowStore.kv_put(_registry_key(workflow_id), registry)

    release_snapshot_file = await _write_release_snapshot(workflow_id, release_id, workflow_json)
    release_runtime_dir = release_snapshot_file.parent
    has_requirements_snapshot = await _write_requirements_snapshot(release_runtime_dir)
    fp = _fingerprint(workflow_path)
    release_record = {
        "releaseId": release_id,
        "workflowId": workflow_id,
        "fingerprint": fp,
        "status": "publishing",
        "workflowSnapshotPath": str(release_snapshot_file),
        "createdAt": now_ms,
        "activatedAt": None,
        "deactivatedAt": None,
    }
    await WorkflowStore.kv_put(_release_key(workflow_id, release_id), release_record)

    previous_runtime = await WorkflowStore.kv_get(_runtime_key(workflow_id)) or {}
    previous_active = await WorkflowStore.kv_get(_active_release_key(workflow_id)) or {}
    previous_container_name = previous_active.get("containerName")
    previous_release_id = previous_active.get("releaseId")

    host_port = await _allocate_port()
    container_name = _workflow_container_name(workflow_id, release_id)
    image_name = image or os.getenv("FLOCKS_WORKFLOW_SERVICE_IMAGE", _DEFAULT_IMAGE)
    runtime_install = os.getenv("FLOCKS_WORKFLOW_SERVICE_RUNTIME_INSTALL", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    health_interval_s = float(os.getenv("FLOCKS_WORKFLOW_SERVICE_HEALTH_INTERVAL_S", str(_DEFAULT_HEALTH_INTERVAL_S)))
    default_retries = _DEFAULT_RUNTIME_INSTALL_HEALTH_RETRIES if runtime_install else _DEFAULT_HEALTH_RETRIES
    health_retries = int(os.getenv("FLOCKS_WORKFLOW_SERVICE_HEALTH_RETRIES", str(default_retries)))
    project_root = Path.cwd().resolve()
    user_config_dir = Config.get_config_path().resolve()
    pip_cache_dir = _service_cache_dir("pip")
    uv_cache_dir = _service_cache_dir("uv")
    service_key = workflow_id
    runtime_api_key = api_key or _generate_api_key()

    cmd = [
        "run",
        "-d",
        "--name",
        container_name,
        "-p",
        f"{host_port}:{_SERVICE_CONTAINER_PORT}",
        "-v",
        f"{project_root}:/app:ro",
        "-v",
        f"{release_runtime_dir}:/runtime",
        "-v",
        f"{pip_cache_dir}:/root/.cache/pip",
        "-v",
        f"{uv_cache_dir}:/root/.cache/uv",
        "-w",
        "/runtime",
        "-e",
        "PYTHONPATH=/app",
        "-e",
        "FLOCKS_CONFIG_DIR=/runtime/.flocks-config",
        "-e",
        "FLOCKS_CONFIG=/runtime/.flocks-config/flocks.json",
        "-e",
        f"{_SERVICE_API_KEY_ENV}={runtime_api_key}",
        "-e",
        "UV_CACHE_DIR=/root/.cache/uv",
        image_name,
    ]
    if user_config_dir.exists():
        cmd[cmd.index(image_name) : cmd.index(image_name)] = [
            "-v",
            f"{user_config_dir}:/runtime/.flocks-config:ro",
        ]

    proxy_env_names = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ]
    proxy_injections: List[str] = []
    needs_host_gateway = False
    for env_name in proxy_env_names:
        env_value = os.getenv(env_name)
        if env_value:
            docker_env_value = _docker_proxy_env_value(env_value)
            if _docker_proxy_uses_host_gateway(docker_env_value):
                needs_host_gateway = True
            proxy_injections.extend(["-e", f"{env_name}={docker_env_value}"])
    if proxy_injections:
        if needs_host_gateway:
            cmd[cmd.index(image_name) : cmd.index(image_name)] = [
                "--add-host",
                "host.docker.internal:host-gateway",
            ]
        cmd[cmd.index(image_name) : cmd.index(image_name)] = proxy_injections
    python_index_url = resolve_python_package_index_url()
    if python_index_url:
        cmd[cmd.index(image_name) : cmd.index(image_name)] = [
            "-e",
            f"PIP_INDEX_URL={python_index_url}",
            "-e",
            f"UV_DEFAULT_INDEX={python_index_url}",
        ]
    if runtime_install:
        uv_bootstrap_cmd = "python -m pip install uv"
        if has_requirements_snapshot:
            # Pre-install all pinned deps from requirements.txt (no resolver = fast),
            # then install the project itself without re-resolving deps.
            install_cmd = (
                f"{uv_bootstrap_cmd} && "
                "uv pip install --system -r /runtime/requirements.txt && "
                "uv pip install --system --no-deps /app"
            )
        else:
            install_cmd = f"{uv_bootstrap_cmd} && uv pip install --system /app"
        service_cmd = (
            f"{install_cmd} && "
            "python -m flocks.workflow.service_runtime "
            f"--workflow /runtime/{release_snapshot_file.name} "
            f"--workflow-id {workflow_id} "
            f"--release-id {release_id} "
            "--host 0.0.0.0 "
            f"--port {_SERVICE_CONTAINER_PORT}"
        )
        cmd.extend(["sh", "-lc", service_cmd])
    else:
        cmd.extend(
            [
                "python",
                "-m",
                "flocks.workflow.service_runtime",
                "--workflow",
                f"/runtime/{release_snapshot_file.name}",
                "--workflow-id",
                workflow_id,
                "--release-id",
                release_id,
                "--host",
                "0.0.0.0",
                "--port",
                str(_SERVICE_CONTAINER_PORT),
            ]
        )

    try:
        stdout, _, _ = await exec_docker(cmd)
        container_id = stdout.strip()
        service_url = _host_service_url(host_port)
        healthy = await _wait_docker_service_healthy(
            service_url,
            container_name,
            retries=health_retries,
            interval_s=health_interval_s,
        )
        if not healthy:
            raise WorkflowCenterError("Published workflow service failed health check")

        release_record["status"] = "active"
        release_record["activatedAt"] = _now_ms()
        await WorkflowStore.kv_put(_release_key(workflow_id, release_id), release_record)

        active_record = {
            "releaseId": release_id,
            "workflowId": workflow_id,
            "serviceKey": service_key,
            "containerName": container_name,
            "containerId": container_id,
            "image": image_name,
            "hostPort": host_port,
            "serviceUrl": service_url,
            "status": "active",
            "updatedAt": _now_ms(),
            "driver": "docker",
            "apiKey": runtime_api_key,
        }
        await WorkflowStore.kv_put(_active_release_key(workflow_id), active_record)
        await WorkflowStore.kv_put(_runtime_key(workflow_id), active_record)
        _release_port_reservation(host_port)

        registry["publishStatus"] = "active"
        registry["activeReleaseId"] = release_id
        registry["serviceKey"] = service_key
        registry["serviceUrl"] = service_url
        registry["updatedAt"] = _now_ms()
        await WorkflowStore.kv_put(_registry_key(workflow_id), registry)

        if isinstance(previous_runtime, dict) and previous_runtime:
            await _stop_runtime_record(
                workflow_id,
                previous_runtime,
                update_registry=False,
                clear_runtime_keys=False,
            )
        elif previous_container_name and previous_container_name != container_name:
            await _stop_and_remove_container(previous_container_name)
            if previous_release_id:
                await _mark_release_inactive(workflow_id, previous_release_id)

        return active_record
    except Exception as exc:
        _release_port_reservation(host_port)
        await _stop_and_remove_container(container_name)
        release_record["status"] = "failed"
        release_record["deactivatedAt"] = _now_ms()
        await WorkflowStore.kv_put(_release_key(workflow_id, release_id), release_record)

        registry["publishStatus"] = "failed"
        registry["updatedAt"] = _now_ms()
        await WorkflowStore.kv_put(_registry_key(workflow_id), registry)
        raise WorkflowCenterError(str(exc)) from exc


async def _stop_workflow_service_docker(workflow_id: str) -> Dict[str, Any]:
    """Stop a published workflow Docker service container."""
    registry = await _read_registry(workflow_id)
    runtime = await WorkflowStore.kv_get(_runtime_key(workflow_id)) or await WorkflowStore.kv_get(
        _active_release_key(workflow_id)
    )
    if not runtime:
        registry["publishStatus"] = "stopped"
        registry["updatedAt"] = _now_ms()
        await WorkflowStore.kv_put(_registry_key(workflow_id), registry)
        return {"workflowId": workflow_id, "status": "stopped", "stopped": False}

    container_name = runtime.get("containerName")
    if container_name:
        stopped = await _stop_and_remove_container(str(container_name))
        if not stopped:
            raise WorkflowCenterError(f"Failed to stop Docker container: {container_name}")

    active = await WorkflowStore.kv_get(_active_release_key(workflow_id)) or {}
    release_id = active.get("releaseId")
    if release_id:
        release_record = await WorkflowStore.kv_get(_release_key(workflow_id, release_id)) or {}
        if release_record:
            release_record["status"] = "inactive"
            release_record["deactivatedAt"] = _now_ms()
            await WorkflowStore.kv_put(_release_key(workflow_id, str(release_id)), release_record)

    await WorkflowStore.kv_remove(_runtime_key(workflow_id))
    await WorkflowStore.kv_remove(_active_release_key(workflow_id))
    registry["publishStatus"] = "stopped"
    registry["updatedAt"] = _now_ms()
    registry["serviceUrl"] = None
    await WorkflowStore.kv_put(_registry_key(workflow_id), registry)
    return {"workflowId": workflow_id, "status": "stopped", "stopped": True}


async def get_workflow_health(workflow_id: str) -> Dict[str, Any]:
    """Get workflow container and HTTP health status."""
    _ = await _read_registry(workflow_id)
    runtime = await WorkflowStore.kv_get(_runtime_key(workflow_id))
    if not runtime:
        return {"workflowId": workflow_id, "published": False, "containerRunning": False, "ok": False}

    container_name = str(runtime.get("containerName", ""))
    service_url = str(runtime.get("serviceUrl", ""))
    if runtime.get("driver") == "local":
        pid_raw = runtime.get("containerId")
        try:
            pid = int(pid_raw)
        except (TypeError, ValueError):
            pid = 0
        process_running = bool(pid and _pid_is_running(pid))
        endpoint_ok = False
        endpoint_payload: Dict[str, Any] = {}
        if process_running and service_url:
            try:
                endpoint_payload = await asyncio.to_thread(_json_get, f"{service_url}/health", 2.0)
                endpoint_ok = bool(endpoint_payload.get("ok"))
            except Exception:
                endpoint_ok = False
        return {
            "workflowId": workflow_id,
            "published": True,
            "containerName": container_name,
            "serviceUrl": service_url,
            "containerExists": process_running,
            "containerRunning": process_running,
            "endpointOk": endpoint_ok,
            "endpoint": endpoint_payload,
            "ok": bool(process_running and endpoint_ok),
            "driver": "local",
        }

    docker_state = (
        await docker_container_state(container_name) if container_name else {"exists": False, "running": False}
    )

    endpoint_ok = False
    endpoint_payload: Dict[str, Any] = {}
    if docker_state.get("running") and service_url:
        try:
            endpoint_payload = await asyncio.to_thread(_json_get, f"{service_url}/health", 2.0)
            endpoint_ok = bool(endpoint_payload.get("ok"))
        except Exception:
            endpoint_ok = False

    return {
        "workflowId": workflow_id,
        "published": True,
        "containerName": container_name,
        "serviceUrl": service_url,
        "containerExists": docker_state.get("exists", False),
        "containerRunning": docker_state.get("running", False),
        "endpointOk": endpoint_ok,
        "endpoint": endpoint_payload,
        "ok": bool(docker_state.get("running") and endpoint_ok),
        "driver": "docker",
    }


async def invoke_published_workflow(
    workflow_id: str,
    *,
    inputs: Optional[Dict[str, Any]] = None,
    timeout_s: Optional[float] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Invoke active published workflow service by workflow_id."""
    _ = await _read_registry(workflow_id)
    runtime = await WorkflowStore.kv_get(_runtime_key(workflow_id))
    if not runtime:
        raise WorkflowNotPublishedError(f"Workflow not published: {workflow_id}")

    service_url = runtime.get("serviceUrl")
    if not service_url:
        raise WorkflowNotPublishedError(f"Workflow not published: {workflow_id}")

    payload = {"inputs": inputs or {}, "request_id": request_id}
    if timeout_s is not None:
        payload["timeout_s"] = timeout_s

    try:
        result = await asyncio.to_thread(
            _json_post,
            f"{service_url}/invoke",
            payload,
            timeout_s or 30.0,
            _workflow_service_auth_headers(runtime),
        )
        result.setdefault("workflowId", workflow_id)
        result.setdefault("releaseId", runtime.get("releaseId"))
        return result
    except url_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise WorkflowCenterError(f"Workflow service HTTP error {exc.code}: {body}") from exc
    except Exception as exc:
        raise WorkflowCenterError(f"Workflow service invoke failed: {exc}") from exc


async def list_workflow_releases(workflow_id: str) -> List[Dict[str, Any]]:
    """List release history for one workflow."""
    _ = await _read_registry(workflow_id)
    keys = await WorkflowStore.kv_list(f"{_RELEASE_PREFIX}{workflow_id}/")
    releases: List[Dict[str, Any]] = []
    for raw_key in keys:
        key = _key_to_string(raw_key)
        if key.endswith("/active"):
            continue
        release = await WorkflowStore.kv_get(key)
        if release:
            releases.append(release)
    releases.sort(key=lambda item: item.get("createdAt", 0), reverse=True)
    return releases
