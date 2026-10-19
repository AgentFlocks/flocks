"""REA plugin state shared by the routes, the ``rea_analysis`` tool and the plugin square.

One state for every entry point, derived from the real configuration, the
install / uninstall jobs and the recorded validation:

- ``not_installed``    nothing of REA is configured
- ``installing``       an install job is checking, installing or registering
- ``validating``       an install job is validating the tier
- ``ready``            the tier validation passed and the service is up
- ``install_failed``   an install or its first validation did not pass
- ``degraded``         it passed once, but the service or configuration is broken now
- ``uninstalling``     an uninstall job is running
- ``uninstall_failed`` the last uninstall left something behind

The 「新」 badge is per account: shown until that account completes a first
install, and only during the guide period, which is the Flocks version the
guide started with and ends for good with the next Flocks version.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Literal, Optional

from flocks.rea import runtime
from flocks.rea.installer import ReaInstaller
from flocks.rea.validation import hidden_details
from flocks.rea.uninstaller import ReaUninstaller, has_resources
from flocks.utils.log import Log


log = Log.create(service="rea.service")

ReaState = Literal[
    "not_installed", "installing", "validating", "ready", "install_failed", "degraded",
    "uninstalling", "uninstall_failed",
]
# States in which the badge follows the first-install and guide rules; in
# ``ready`` and ``degraded`` REA was usable, so there is nothing new to show.
BADGE_STATES = frozenset({"not_installed", "installing", "validating", "install_failed", "uninstall_failed"})

# The account's install conversation (recorded by the install tool) is what the
# entry opens while the install runs and for this long after, so a failed
# install continues there.
INSTALL_SESSION_REUSE_MS = 30 * 60 * 1000

_preferences_lock = threading.Lock()


def can_install(user: Any) -> bool:
    """Installing and uninstalling server components is limited to administrators."""

    return getattr(user, "role", None) == "admin"


def _user_key(user: Any) -> Optional[str]:
    user_id = user if isinstance(user, str) else getattr(user, "id", None)
    return str(user_id) if user_id else None


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── preferences: guide period, first installs, install conversations ──


def _preferences_path():
    return runtime.state_dir() / "preferences.json"


def _read_preferences() -> dict[str, Any]:
    try:
        data = json.loads(_preferences_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_preferences(data: dict[str, Any]) -> None:
    path = _preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def guide_active() -> bool:
    """Whether the badge's guide period is still on.

    It starts with the Flocks version that first evaluates it and ends, for
    good, once Flocks runs any other version.
    """

    current = runtime.flocks_version()
    with _preferences_lock:
        data = _read_preferences()
        guide = data.get("guide")
        if not isinstance(guide, dict) or not guide.get("version"):
            data["guide"] = {"version": current, "startedAt": _now_ms(), "endedAt": None}
            _try_write_preferences(data)
            return True
        if guide.get("endedAt"):
            return False
        if guide.get("version") != current:
            data["guide"] = {**guide, "endedAt": _now_ms(), "endedOnVersion": current}
            _try_write_preferences(data)
            return False
    return True


def _try_write_preferences(data: dict[str, Any]) -> None:
    """The badge bookkeeping must not take the status down with it (full disk, read-only data dir)."""

    try:
        _write_preferences(data)
    except OSError as exc:
        log.warning("rea.preferences.write_failed", {"error": str(exc)})


def guide_info() -> dict[str, Any]:
    guide_active()
    with _preferences_lock:
        guide = _read_preferences().get("guide")
    return dict(guide) if isinstance(guide, dict) else {}


def first_install_completed(user: Any) -> bool:
    key = _user_key(user)
    if key is None:
        return False
    with _preferences_lock:
        marks = _read_preferences().get("firstInstall")
    return isinstance(marks, dict) and key in marks


def record_first_install(user: Any) -> None:
    """The account's install passed its tier validation: no badge for it ever again.

    Uninstalling keeps the mark.
    """

    key = _user_key(user)
    if key is None:
        return
    with _preferences_lock:
        data = _read_preferences()
        marks = data.get("firstInstall")
        if not isinstance(marks, dict):
            marks = {}
        if key in marks:
            return
        marks[key] = _now_ms()
        data["firstInstall"] = marks
        _write_preferences(data)


def badge_visible(user: Any, state: str) -> bool:
    if state not in BADGE_STATES or _user_key(user) is None:
        return False
    return not first_install_completed(user) and guide_active()


def _install_sessions(data: dict[str, Any]) -> dict[str, Any]:
    sessions = data.get("installSessions")
    return sessions if isinstance(sessions, dict) else {}


def record_install_session(session_id: str, user: Any) -> None:
    key = _user_key(user)
    if key is None:
        return
    with _preferences_lock:
        data = _read_preferences()
        sessions = _install_sessions(data)
        sessions[key] = {"id": session_id, "createdAt": _now_ms()}
        data["installSessions"] = sessions
        _write_preferences(data)


def forget_install_sessions() -> None:
    """After an uninstall the next click starts over with the install guide (AC-12)."""

    with _preferences_lock:
        data = _read_preferences()
        if data.pop("installSessions", None) is not None:
            _write_preferences(data)


def _recent_session(record: Any, now: int) -> Optional[str]:
    if not isinstance(record, dict) or not isinstance(record.get("id"), str):
        return None
    created = record.get("createdAt")
    if ReaInstaller.is_running() or (isinstance(created, int) and now - created < INSTALL_SESSION_REUSE_MS):
        return record["id"]
    return None


def recent_install_session(user: Any) -> Optional[str]:
    key = _user_key(user)
    if key is None:
        return None
    with _preferences_lock:
        record = _install_sessions(_read_preferences()).get(key)
    return _recent_session(record, _now_ms())


async def live_install_session(user: Any) -> Optional[str]:
    """The account's recent install conversation, if it still exists and takes its messages."""

    session_id = recent_install_session(user)
    if session_id is None:
        return None
    from flocks.session.policy import SessionPolicy
    from flocks.session.session import Session

    try:
        session = await Session.get_by_id_unfiltered(session_id)
    except Exception as exc:
        log.debug("rea.install_session.lookup_failed", {"session_id": session_id, "error": str(exc)})
        return None
    if session is None or getattr(session, "status", "active") != "active":
        return None
    if not SessionPolicy.can_write(session, user):
        return None
    return session_id


# ── unified state ──


def service_status() -> tuple[Optional[str], int, Optional[str]]:
    """(status, tool count, error) of the ``rea`` service in Flocks' MCP manager.

    Reads the snapshot only, so a status request never starts MCP servers;
    before MCP finished starting the service counts as ``connecting``.
    """

    from flocks.mcp import MCP

    statuses, initialized = MCP.status_snapshot()
    info = statuses.get(runtime.MCP_SERVER_NAME)
    if info is None:
        return (None if initialized else "connecting"), 0, None
    raw = getattr(info, "status", None)
    status = getattr(raw, "value", raw)
    return (str(status) if status is not None else None), int(getattr(info, "tools_count", 0) or 0), getattr(
        info, "error", None,
    )


def service_healthy() -> bool:
    status, tools, _error = service_status()
    return status == "connecting" or (status == "connected" and tools > 0)


def _uninstall_is_latest() -> bool:
    """The last REA operation in this process was the uninstall (not a newer install)."""

    uninstall = ReaUninstaller.current_job()
    if uninstall is None:
        return False
    install = ReaInstaller.current_job()
    return install is None or install.startedAt <= (uninstall.finishedAt or uninstall.startedAt)


def install_state() -> ReaState:
    if ReaUninstaller.is_running():
        return "uninstalling"
    job = ReaInstaller.current_job()
    if job is not None and job.status == "running":
        return "validating" if job.stage == "validate" else "installing"
    uninstall = ReaUninstaller.current_job()
    if uninstall is not None and uninstall.status == "failed" and _uninstall_is_latest() and has_resources():
        return "uninstall_failed"
    record = runtime.read_install_record()
    if record is not None:
        if runtime.verification_passed(record):
            usable = runtime.is_configured() and service_healthy() and not runtime.full_tier_problems()
            return "ready" if usable else "degraded"
        return "degraded" if runtime.ever_verified(record) else "install_failed"
    if job is not None and job.status == "failed" and not _uninstall_is_latest():
        # An uninstall since then (that found nothing left, or cancelled this install) wins.
        return "install_failed"
    if has_resources():
        # Parts of REA without a validation (a lost record, a manual setup).
        return "install_failed"
    return "not_installed"


def degraded_problems() -> list[dict[str, Any]]:
    """What is broken about an install that passed before, as codes the WebUI localizes."""

    problems: list[dict[str, Any]] = []
    if not runtime.skill_installed():
        problems.append({"code": "skill_missing"})
    if not runtime.mcp_registered():
        problems.append({"code": "mcp_not_registered"})
    status, tools, error = service_status()
    if status not in ("connected", "connecting") or (status == "connected" and tools <= 0):
        problems.append({
            "code": "service_down",
            "status": status or "missing",
            "error": runtime.public_detail(error, 200),
        })
    problems += runtime.full_tier_problems()
    if not runtime.verification_passed():
        problems.append({"code": "validation_failed"})
    return problems


def degraded_reasons(*, details: bool = True) -> list[str]:
    """The same problems in words, for Rex (``details=False``: no paths or service errors)."""

    texts = []
    for problem in degraded_problems():
        code = problem["code"]
        if code == "skill_missing":
            texts.append(f"skill {runtime.REA_SKILL_NAME} is missing")
        elif code == "mcp_not_registered":
            texts.append(f"MCP service \"{runtime.MCP_SERVER_NAME}\" is not registered or disabled in Flocks")
        elif code == "service_down":
            error = problem.get("error") if details else None
            texts.append(
                f"MCP service \"{runtime.MCP_SERVER_NAME}\" is {problem['status']}" + (f" ({error})" if error else "")
            )
        elif code == "jdk_missing":
            where = f" ({problem['path']})" if details else ""
            texts.append(f"the JDK {runtime.JDK_MAJOR} the full tier runs with is missing{where}")
        elif code == "ghidra_missing":
            where = f" ({problem['path']})" if details else ""
            texts.append(f"Ghidra {runtime.GHIDRA_VERSION} of the full tier is missing{where}")
        elif code == "ghidra_decompiler_missing":
            texts.append(f"the Ghidra native decompiler for {problem['platform']} is missing")
        else:
            texts.append("the last validation did not pass")
    return texts


def verification_summary(*, admin: bool) -> Optional[dict[str, Any]]:
    record = runtime.read_install_record() or {}
    verification = record.get("verification")
    if not isinstance(verification, dict):
        return None
    capabilities = {}
    for key, item in (verification.get("capabilities") or {}).items():
        if not isinstance(item, dict):
            continue
        capabilities[key] = {
            "status": item.get("status"),
            "static": item.get("static"),
            "runtime": item.get("runtime"),
            "expected": item.get("expected"),
            "actual": item.get("actual") if admin else None,
            "tools": [call.get("tool") for call in item.get("calls") or [] if isinstance(call, dict)],
            "notes": item.get("notes") if admin else [],
            # What failed calls returned and how much conversations leave out: administrators read it here.
            "calls": [
                {"tool": call.get("tool"), "ok": False, "summary": call.get("summary")}
                for call in item.get("calls") or [] if isinstance(call, dict) and call.get("ok") is False
            ] if admin else [],
            "hiddenDetails": hidden_details(item) if admin else 0,
        }
    service = verification.get("service") if isinstance(verification.get("service"), dict) else {}
    return {
        "ok": verification.get("ok") is True,
        "tier": verification.get("tier"),
        "verifiedAt": verification.get("verifiedAt"),
        "service": {
            "ok": service.get("ok"),
            "serverVersion": service.get("server_version"),
            "toolsObserved": service.get("tools_observed"),
            "toolsExpected": service.get("tools_expected"),
            "requestTool": service.get("request_tool"),
            "requestOk": service.get("request_ok"),
        },
        "capabilities": capabilities,
    }


def public_install_job(job: Any, *, admin: bool) -> Optional[dict[str, Any]]:
    if job is None:
        return None
    payload = job.model_dump()
    if not admin:
        # Installer output (paths, URLs, commands) is for administrators.
        payload["errorDetail"] = None
        payload["blockers"] = []
        payload["components"] = {
            key: {"key": key, "status": value.get("status")}
            for key, value in (payload.get("components") or {}).items()
        }
        payload["validation"] = {
            key: {"key": key, "status": value.get("status"), "static": value.get("static"), "runtime": value.get("runtime")}
            for key, value in (payload.get("validation") or {}).items()
        }
        payload["service"] = None
        payload["sessionId"] = None
    else:
        for item in (payload.get("validation") or {}).values():
            if isinstance(item, dict):
                item["hiddenDetails"] = hidden_details(item)
    return payload


def public_uninstall_job(job: Any, *, admin: bool) -> Optional[dict[str, Any]]:
    if job is None:
        return None
    payload = job.model_dump()
    if not admin:
        payload["errorDetail"] = None
        payload["residues"] = []
        payload["stoppedTasks"] = []
    return payload


async def build_status(user: Any, *, refresh: bool = False) -> dict[str, Any]:
    checks = await runtime.check_environment(refresh=refresh)
    admin = can_install(user)
    state = install_state()
    record = runtime.read_install_record() or {}
    uninstall = ReaUninstaller.current_job()
    # While uninstalling, the badge keeps what it showed before the uninstall.
    badge_state = (uninstall.previousState or state) if state == "uninstalling" and uninstall else state
    return {
        "package": runtime.REA_PACKAGE,
        "version": runtime.REA_VERSION,
        "state": state,
        "installed": state == "ready",
        "tier": record.get("tier") if record.get("tier") in runtime.TIERS else None,
        "installedVersion": runtime.installed_version(),
        "canInstall": admin,
        "badge": badge_visible(user, badge_state),
        "firstInstallCompleted": first_install_completed(user),
        "installSessionId": await live_install_session(user) if admin else None,
        "removable": has_resources(),
        "degradedProblems": degraded_problems() if admin and state == "degraded" else [],
        "verification": verification_summary(admin=admin),
        "environment": {
            "ok": all(check.ok for check in checks),
            "nodeRequirement": runtime.NODE_REQUIREMENT,
            # Only the Node.js version is shown; host paths stay in the logs.
            "checks": [
                {**check.to_dict(), "detail": check.detail if check.key == "node" else None}
                for check in checks
            ],
        },
        "job": public_install_job(ReaInstaller.current_job(), admin=admin),
        "uninstallJob": public_uninstall_job(uninstall, admin=admin),
    }
