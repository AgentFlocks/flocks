"""Console upgrade request orchestration routes (OSS-side)."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import os
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from flocks.console.login import ConsoleLoginService
from flocks.server.auth import require_admin
from flocks.storage.storage import Storage
from flocks.updater import perform_pro_bundle_install

router = APIRouter()
_AUTO_UPGRADE_TASKS: set[asyncio.Task[None]] = set()
_AUTO_UPGRADE_REQUEST_IDS: set[str] = set()


def _console_base_url() -> str:
    raw = os.getenv("FLOCKS_CONSOLE_BASE_URL", "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    return f"https://{raw}"


class UpgradeRequestCreate(BaseModel):
    product: str = Field(default="Flocks Pro", pattern="^Flocks Pro$")
    license_type: Literal["trial_30d", "poc", "commercial"]
    request_kind: Literal["new", "trial_extension", "license_change"] = "new"
    company: str = Field(min_length=1)
    applicant_name: str = Field(min_length=1)
    applicant_email: Optional[str] = None
    applicant_phone: Optional[str] = None
    notes: Optional[str] = None


class UpgradeRequestStatus(BaseModel):
    request_id: str
    status: str
    previous_request_id: Optional[str] = None
    reason: Optional[str] = None
    suggestion: Optional[str] = None
    activate_key: Optional[str] = None
    manifest_url: Optional[str] = None
    license_id: Optional[str] = None
    license_status: Optional[str] = None
    max_admins: Optional[int] = None
    max_members: Optional[int] = None
    expires_at: Optional[int] = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


def _request_key(request_id: str) -> str:
    return f"console:upgrade_request:{request_id}"


async def _list_request_ids() -> list[str]:
    ids = await Storage.get("console:upgrade_request_ids")
    if not isinstance(ids, list):
        return []
    return [str(i) for i in ids]


async def _push_request_id(request_id: str) -> None:
    ids = await _list_request_ids()
    if request_id not in ids:
        ids.append(request_id)
        await Storage.set("console:upgrade_request_ids", ids, "json")


_INACTIVE_LICENSE_STATUSES = {"revoked", "expired", "superseded"}


def _is_approved(record: dict[str, Any]) -> bool:
    return str(record.get("status", "")).strip().lower() == "approved"


def _record_license_id(record: dict[str, Any]) -> str:
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    return str(record.get("license_id") or details.get("license_id") or "").strip()


def _record_license_status(record: dict[str, Any]) -> str:
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    return str(record.get("license_status") or details.get("license_status") or "").strip().lower()


def _apply_console_license_data(record: dict[str, Any], data: dict[str, Any]) -> None:
    if not data:
        return
    details = record.setdefault("details", {})
    effective_status = str(
        data.get("effective_status")
        or data.get("license_status")
        or data.get("status")
        or record.get("license_status")
        or ""
    ).strip()
    if data.get("revoked"):
        effective_status = "revoked"
    effective_expires_at = data.get("effective_expires_at", data.get("expires_at"))
    effective_max_admins = data.get("effective_max_admins", data.get("max_admins"))
    effective_max_members = data.get("effective_max_members", data.get("max_members"))

    for key, value in {
        "license_id": data.get("license_id"),
        "license_status": effective_status or None,
        "max_admins": effective_max_admins,
        "max_members": effective_max_members,
        "expires_at": effective_expires_at,
        "activate_key": data.get("activate_key"),
        "manifest_url": data.get("manifest_url"),
    }.items():
        if value is not None:
            record[key] = value
            details[key] = value
    if effective_expires_at is not None:
        details["license_effective_expires_at"] = effective_expires_at
    latest_patch = data.get("latest_patch") or data.get("latest_change")
    if latest_patch:
        details["latest_license_patch"] = latest_patch
    record["updated_at"] = datetime.now(UTC).isoformat()


def _record_account_key(record: dict[str, Any]) -> str:
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    return str(
        details.get("console_account_name")
        or details.get("cloud_account")
        or details.get("passport_uid")
        or details.get("account")
        or ""
    ).strip().lower()


def _console_session_account_key(console_session: dict[str, Any]) -> str:
    return str(
        console_session.get("user_display")
        or console_session.get("user_email")
        or console_session.get("passport_uid")
        or ""
    ).strip().lower()


async def _latest_usable_issued_record(
    revoked_license_ids: set[str],
    *,
    account_key: str = "",
) -> dict[str, Any] | None:
    candidates: list[tuple[dict[str, Any], bool]] = []
    for request_id in await _list_request_ids():
        raw = await Storage.get(_request_key(request_id))
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "")).strip().lower()
        license_id = _record_license_id(raw)
        if status not in {"approved", "activated"} or not license_id or not raw.get("activate_key"):
            continue
        record_account_key = _record_account_key(raw)
        if account_key and record_account_key and record_account_key != account_key:
            continue
        usable = license_id not in revoked_license_ids and _record_license_status(raw) not in _INACTIVE_LICENSE_STATUSES
        candidates.append((raw, usable))
    candidates.sort(
        key=lambda item: _parse_dt(item[0].get("created_at") or item[0].get("updated_at"))
        or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    if not candidates:
        return None
    latest, usable = candidates[0]
    return latest if usable else None


def _is_pro_component_installed() -> bool:
    return importlib.util.find_spec("flockspro") is not None


def _decode_jwt_payload_unverified(token: str) -> dict[str, Any]:
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _license_duration_seconds(record: dict[str, Any]) -> int | None:
    details = record.setdefault("details", {})
    payload = _decode_jwt_payload_unverified(str(record.get("activate_key") or details.get("activate_key") or ""))
    duration_days = payload.get("duration_days") or details.get("license_duration_days")
    if duration_days:
        try:
            return max(1, int(duration_days)) * 86400
        except (TypeError, ValueError):
            pass
    issued_at = payload.get("iat") or payload.get("issued_at")
    expires_at = payload.get("expires_at") or details.get("expires_at")
    try:
        if issued_at and expires_at:
            return max(1, int(expires_at) - int(issued_at))
    except (TypeError, ValueError):
        return None
    return None


def _enrich_record_from_install_marker(record: dict[str, Any]) -> dict[str, Any]:
    details = record.setdefault("details", {})
    marker = _read_pro_bundle_install_marker()
    if marker:
        details.setdefault("auto_install_version", marker.get("installed_version"))
        details.setdefault("auto_install_pro_version", marker.get("flockspro_component_version"))
        details.setdefault("flockspro_component_version", marker.get("flockspro_component_version"))
        details.setdefault("auto_install_build_id", marker.get("build_id"))

    activated_source = details.get("license_activated_at") or details.get("auto_install_completed_at")
    if not activated_source and marker:
        activated_source = marker.get("installed_at")
    activated_at = _parse_dt(activated_source)
    duration_seconds = _license_duration_seconds(record)
    if activated_at and duration_seconds:
        effective_expires_at = int((activated_at + timedelta(seconds=duration_seconds)).timestamp())
        details["license_effective_expires_at"] = effective_expires_at
        details["license_duration_days"] = max(1, round(duration_seconds / 86400))
    return record


async def _maybe_activate_pro_license(record: dict[str, Any], *, force: bool = False) -> None:
    activate_key = str(record.get("activate_key") or "").strip()
    if not activate_key:
        return
    details = record.setdefault("details", {})
    if details.get("license_activated_at") and not force:
        return
    try:
        from flockspro.license.runtime import get_license_checker

        checker = get_license_checker()
        activate_fn = getattr(checker, "activate", None)
        if callable(activate_fn):
            activate_fn(activate_key)
            details["license_activated_at"] = datetime.now(UTC).isoformat()
            details.pop("license_activate_error", None)
    except Exception as exc:
        details["license_activate_error"] = str(exc)
        if not _is_pro_component_installed():
            return
        _fallback_write_pro_license_state(record, activate_key, str(exc))


def _fallback_write_pro_license_state(record: dict[str, Any], activate_key: str, reason: str) -> None:
    payload = _decode_jwt_payload_unverified(activate_key)
    if not payload:
        return
    details = record.setdefault("details", {})
    now = int(time.time())
    duration_seconds = _license_duration_seconds(record)
    if duration_seconds:
        payload["expires_at"] = now + duration_seconds
        details["license_effective_expires_at"] = payload["expires_at"]
        details["license_duration_days"] = max(1, round(duration_seconds / 86400))
    try:
        from flockspro.license.cloud_checker import _machine_fingerprint
        from flockspro.license.runtime import get_license_checker

        checker = get_license_checker()
        load_install_id = getattr(checker, "_load_or_create_install_id", None)
        install_id = load_install_id() if callable(load_install_id) else str(record.get("install_id") or "")
        fingerprint = _machine_fingerprint(install_id)
    except Exception:
        install_id = ""
        fingerprint = ""

    license_path = Path(os.getenv("FLOCKS_ROOT", str(Path.home() / ".flocks"))) / "flockspro" / "license.json"
    license_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.write_text(
        json.dumps(
            {
                "license_id": payload.get("license_id"),
                "key": activate_key,
                "payload": payload,
                "bound_fingerprint": fingerprint,
                "activation_receipt": None,
                "patches": [],
                "activated_at": now,
                "install_id": install_id,
                "fingerprint": fingerprint,
                "last_sync_at": now,
                "max_observed_at": now,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    details["license_activated_at"] = datetime.now(UTC).isoformat()
    details["license_activate_fallback_reason"] = reason
    details.pop("license_activate_error", None)


async def _maybe_refresh_pro_license(record: dict[str, Any]) -> None:
    details = record.setdefault("details", {})
    try:
        from flockspro.license.runtime import get_license_checker

        checker = get_license_checker()
        refresh_fn = getattr(checker, "refresh", None)
        if callable(refresh_fn):
            await refresh_fn()  # type: ignore[misc]
            details["license_refreshed_at"] = datetime.now(UTC).isoformat()
    except Exception as exc:
        details["license_refresh_error"] = str(exc)


async def _run_auto_upgrade_install(record: dict[str, Any]) -> dict[str, Any]:
    details = record.setdefault("details", {})
    details["auto_install_result"] = "running"
    details["auto_install_started_at"] = datetime.now(UTC).isoformat()
    marker = _read_pro_bundle_install_marker()
    if _is_pro_component_installed() and marker:
        details["auto_install_result"] = "already_latest"
        details["auto_install_version"] = marker.get("installed_version")
        details["auto_install_completed_at"] = datetime.now(UTC).isoformat()
        await _report_pro_bundle_installation(record, install_result="success")
        return record

    final_stage = ""
    final_message = ""
    async for progress in perform_pro_bundle_install(restart=False):
        final_stage = progress.stage
        final_message = progress.message
        if progress.stage == "error":
            raise ValueError(progress.message)

    await _maybe_activate_pro_license(record)
    await _maybe_refresh_pro_license(record)
    marker = _read_pro_bundle_install_marker()
    details["auto_install_result"] = "done" if final_stage == "done" else "unknown"
    details["auto_install_version"] = marker.get("installed_version")
    details["auto_install_pro_version"] = marker.get("flockspro_component_version")
    details["auto_install_completed_at"] = datetime.now(UTC).isoformat()
    details["auto_install_message"] = final_message
    _enrich_record_from_install_marker(record)
    await _report_pro_bundle_installation(record, install_result="success")
    return record


def _read_pro_bundle_install_marker() -> dict[str, Any]:
    marker = Path(os.getenv("FLOCKS_ROOT", str(Path.home() / ".flocks"))) / "run" / "pro-bundle-installed.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


async def _report_pro_bundle_installation(
    record: dict[str, Any],
    *,
    install_result: str,
    error_message: str | None = None,
) -> None:
    details = record.setdefault("details", {})
    try:
        console_session = await ConsoleLoginService.require_console_session()
    except Exception as exc:
        details["install_receipt_error"] = str(exc)
        return
    marker = _read_pro_bundle_install_marker()
    payload = {
        "license_id": record.get("activate_key"),
        "fingerprint": console_session.get("fingerprint"),
        "install_id": console_session.get("install_id"),
        "installed_version": marker.get("installed_version") or details.get("auto_install_target") or details.get("auto_install_version") or "",
        "oss_version": marker.get("oss_version"),
        "flockspro_component_version": marker.get("flockspro_component_version"),
        "build_id": marker.get("build_id"),
        "install_result": install_result,
        "error_message": error_message,
        "reported_at": datetime.now(UTC).isoformat(),
    }
    console_base = _console_base_url()
    if not console_base:
        details["install_receipt_error"] = "FLOCKS_CONSOLE_BASE_URL 未配置"
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{console_base}/v1/pro-bundles/installations",
                json=payload,
                headers={"Authorization": f"Bearer {console_session['console_session_token']}"},
            )
            resp.raise_for_status()
            details["install_receipt_reported_at"] = datetime.now(UTC).isoformat()
    except Exception as exc:
        details["install_receipt_error"] = str(exc)


async def _mark_console_upgrade_activated(record: dict[str, Any]) -> None:
    request_id = str(record.get("request_id") or "").strip()
    if not request_id:
        return
    console_base = _console_base_url()
    if not console_base:
        return
    details = record.setdefault("details", {})
    try:
        console_session = await ConsoleLoginService.require_console_session()
        headers = {"Authorization": f"Bearer {console_session['console_session_token']}"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{console_base}/v1/upgrade-requests/{request_id}/activate", headers=headers)
            resp.raise_for_status()
            details["console_activated_reported_at"] = datetime.now(UTC).isoformat()
    except Exception as exc:
        details["console_activated_report_error"] = str(exc)


async def _maybe_auto_activate_upgrade(record: dict[str, Any]) -> dict[str, Any]:
    if not _is_approved(record):
        return record
    details = record.setdefault("details", {})
    if details.get("auto_install_result") in {"done", "already_latest"}:
        return record
    try:
        await _maybe_activate_pro_license(record)
        await _maybe_refresh_pro_license(record)
        await _run_auto_upgrade_install(record)
        record["status"] = "activated"
    except Exception as exc:
        details["auto_install_result"] = "failed"
        details["auto_install_error"] = str(exc)
        await _report_pro_bundle_installation(record, install_result="failed", error_message=str(exc))
    finally:
        record["updated_at"] = datetime.now(UTC).isoformat()
    return record


async def _run_auto_activate_upgrade_task(request_id: str, record: dict[str, Any]) -> None:
    try:
        updated = await _maybe_auto_activate_upgrade(record)
        await Storage.set(_request_key(request_id), updated, "json")
    except Exception as exc:
        record.setdefault("details", {})["auto_install_error"] = str(exc)
        record.setdefault("details", {})["auto_install_result"] = "failed"
        record["updated_at"] = datetime.now(UTC).isoformat()
        await Storage.set(_request_key(request_id), record, "json")
    finally:
        _AUTO_UPGRADE_REQUEST_IDS.discard(request_id)


def _schedule_auto_activate_upgrade(request_id: str, record: dict[str, Any]) -> None:
    if not _is_approved(record):
        return
    details = record.setdefault("details", {})
    if details.get("auto_install_result") in {"running", "done", "already_latest"}:
        return
    if request_id in _AUTO_UPGRADE_REQUEST_IDS:
        return
    _AUTO_UPGRADE_REQUEST_IDS.add(request_id)
    task = asyncio.create_task(_run_auto_activate_upgrade_task(request_id, dict(record)))
    _AUTO_UPGRADE_TASKS.add(task)
    task.add_done_callback(_AUTO_UPGRADE_TASKS.discard)


def _raise_console_service_error(exc: Exception) -> None:
    detail = "console 升级服务调用失败，请稍后重试"
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            payload = exc.response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("detail") or payload.get("message") or detail)
        except Exception:
            if exc.response.text:
                detail = exc.response.text
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc


@router.post("/upgrade-requests", response_model=UpgradeRequestStatus)
async def create_upgrade_request(payload: UpgradeRequestCreate, request: Request) -> UpgradeRequestStatus:
    admin_user = require_admin(request)
    try:
        console_session = await ConsoleLoginService.require_console_session()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    now = datetime.now(UTC).isoformat()
    request_id = str(uuid4())
    normalized_product = payload.product.strip() or "Flocks Pro"
    details = {
        "product": normalized_product,
        "license_type": payload.license_type,
        "request_kind": payload.request_kind,
        "company": payload.company.strip(),
        "enterprise_name": payload.company.strip(),
        "applicant_name": payload.applicant_name.strip(),
        "applicant_email": (payload.applicant_email or "").strip() or None,
        "applicant_phone": (payload.applicant_phone or "").strip() or None,
        "notes": (payload.notes or "").strip() or None,
        "idempotency_key": request_id,
        "console_account_name": console_session.get("user_display")
        or console_session.get("user_email")
        or console_session.get("passport_uid"),
        "cloud_account": console_session.get("user_display")
        or console_session.get("user_email")
        or console_session.get("passport_uid"),
        "passport_uid": console_session.get("passport_uid"),
    }
    record = {
        "request_id": request_id,
        "status": "pending",
        "previous_request_id": None,
        "reason": details["notes"],
        "suggestion": None,
        "activate_key": None,
        "manifest_url": None,
        "license_id": None,
        "license_status": None,
        "max_admins": None,
        "max_members": None,
        "expires_at": None,
        "details": details,
        "created_at": now,
        "updated_at": now,
    }

    console_base = _console_base_url()
    if console_base:
        console_payload = {
            "node_id": str(admin_user.id),
            "console_login_id": console_session.get("console_login_id"),
            "fingerprint": console_session.get("fingerprint"),
            "install_id": console_session.get("install_id"),
            "passport_uid": console_session.get("passport_uid"),
            "company_name": details["company"],
            "enterprise_name": details["enterprise_name"],
            "contact_email": details["applicant_email"] or "",
            "idempotency_key": request_id,
            "form_data": details,
        }
        headers = {"Authorization": f"Bearer {console_session['console_session_token']}"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{console_base}/v1/upgrade-requests", json=console_payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            _raise_console_service_error(exc)
        else:
            record.update(
                {
                    "request_id": data.get("request_id", request_id),
                    "status": data.get("status", "pending"),
                    "reason": data.get("reason", details["notes"]),
                    "suggestion": data.get("suggestion"),
                    "activate_key": data.get("activate_key"),
                    "manifest_url": data.get("manifest_url"),
                    "license_id": data.get("license_id"),
                    "license_status": data.get("license_status"),
                    "max_admins": data.get("max_admins"),
                    "max_members": data.get("max_members"),
                    "expires_at": data.get("expires_at"),
                    "details": data.get("form_data", details),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )

    await Storage.set(_request_key(record["request_id"]), record, "json")
    await _push_request_id(record["request_id"])
    return UpgradeRequestStatus(**record)


@router.get("/upgrade-requests", response_model=list[UpgradeRequestStatus])
async def list_upgrade_requests(request: Request) -> list[UpgradeRequestStatus]:
    require_admin(request)
    result: list[UpgradeRequestStatus] = []
    for request_id in reversed(await _list_request_ids()):
        raw = await Storage.get(_request_key(request_id))
        if raw:
            result.append(UpgradeRequestStatus(**_enrich_record_from_install_marker(raw)))
    return result


@router.get("/pro-package-status")
async def get_pro_package_status(request: Request) -> dict[str, Any]:
    require_admin(request)
    marker = _read_pro_bundle_install_marker()
    return {
        "installed": _is_pro_component_installed(),
        "installed_version": marker.get("installed_version"),
        "flockspro_component_version": marker.get("flockspro_component_version"),
        "build_id": marker.get("build_id"),
        "installed_at": marker.get("installed_at"),
    }


@router.post("/licenses/sync-revocations")
async def sync_console_license_revocations(request: Request) -> dict[str, Any]:
    require_admin(request)
    console_base = _console_base_url()
    if not console_base:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="FLOCKS_CONSOLE_BASE_URL 未配置")
    try:
        console_session = await ConsoleLoginService.require_console_session()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    headers = {"Authorization": f"Bearer {console_session['console_session_token']}"}
    account_key = _console_session_account_key(console_session)
    synced_license_ids: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{console_base}/v1/licenses/revocations", headers=headers)
            resp.raise_for_status()
            data = resp.json()

            for request_id in await _list_request_ids():
                raw = await Storage.get(_request_key(request_id))
                if not isinstance(raw, dict):
                    continue
                record_account_key = _record_account_key(raw)
                if account_key and record_account_key and record_account_key != account_key:
                    continue
                license_id = _record_license_id(raw)
                if not license_id:
                    continue
                license_resp = await client.get(f"{console_base}/v1/licenses/{license_id}", headers=headers)
                if license_resp.status_code == status.HTTP_404_NOT_FOUND:
                    continue
                license_resp.raise_for_status()
                license_data = license_resp.json()
                if isinstance(license_data, dict):
                    _apply_console_license_data(raw, license_data)
                    synced_license_ids.append(license_id)
                    await Storage.set(_request_key(request_id), raw, "json")
    except httpx.HTTPError as exc:
        _raise_console_service_error(exc)

    revoked_license_ids = data.get("revoked_license_ids", [])
    if not isinstance(revoked_license_ids, list):
        revoked_license_ids = []

    imported = False
    activated_license_id: str | None = None
    refreshed_license_id: str | None = None
    try:
        from flockspro.license.runtime import get_license_checker

        checker = get_license_checker()
        import_fn = getattr(checker, "import_revocation", None)
        if callable(import_fn):
            import_fn([str(item) for item in revoked_license_ids])
            imported = True

        current_status = checker.status() if hasattr(checker, "status") else {}
        current_license_id = str(current_status.get("license_id") or "")
        current_inactive = (
            current_license_id in {str(item) for item in revoked_license_ids}
            or str(current_status.get("license_status") or "").lower() in _INACTIVE_LICENSE_STATUSES
            or current_status.get("active") is False
        )
        if current_inactive:
            target = await _latest_usable_issued_record(
                {str(item) for item in revoked_license_ids},
                account_key=_console_session_account_key(console_session),
            )
            target_license_id = _record_license_id(target) if target else ""
            if target and target_license_id and target_license_id != current_license_id:
                await _maybe_activate_pro_license(target, force=True)
                await _maybe_refresh_pro_license(target)
                activated_license_id = target_license_id
                refreshed_license_id = target_license_id
                await Storage.set(_request_key(str(target["request_id"])), target, "json")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return {
        "revoked_license_ids": [str(item) for item in revoked_license_ids],
        "imported": imported,
        "synced_license_ids": synced_license_ids,
        "activated_license_id": activated_license_id,
        "refreshed_license_id": refreshed_license_id,
    }


@router.get("/upgrade-requests/{request_id}", response_model=UpgradeRequestStatus)
async def get_upgrade_request(request_id: str, request: Request) -> UpgradeRequestStatus:
    require_admin(request)
    raw = await Storage.get(_request_key(request_id))
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="升级申请不存在")
    return UpgradeRequestStatus(**_enrich_record_from_install_marker(raw))


@router.post("/upgrade-requests/{request_id}/refresh", response_model=UpgradeRequestStatus)
async def refresh_upgrade_request(request_id: str, request: Request) -> UpgradeRequestStatus:
    require_admin(request)
    raw = await Storage.get(_request_key(request_id))
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="升级申请不存在")

    console_base = _console_base_url()
    if console_base:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{console_base}/v1/upgrade-requests/{request_id}")
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            _raise_console_service_error(exc)
        else:
            raw.update(
                {
                    "status": data.get("status", raw["status"]),
                    "reason": data.get("reason", raw.get("reason")),
                    "suggestion": data.get("suggestion", raw.get("suggestion")),
                    "activate_key": data.get("activate_key", raw.get("activate_key")),
                    "manifest_url": data.get("manifest_url", raw.get("manifest_url")),
                    "license_id": data.get("license_id", raw.get("license_id")),
                    "license_status": data.get("license_status", raw.get("license_status")),
                    "max_admins": data.get("max_admins", raw.get("max_admins")),
                    "max_members": data.get("max_members", raw.get("max_members")),
                    "expires_at": data.get("expires_at", raw.get("expires_at")),
                    "details": data.get("form_data", raw.get("details", {})),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
    else:
        raw["updated_at"] = datetime.now(UTC).isoformat()

    _enrich_record_from_install_marker(raw)
    await Storage.set(_request_key(request_id), raw, "json")
    return UpgradeRequestStatus(**raw)


@router.post("/upgrade-requests/{request_id}/start")
async def start_upgrade_request(request_id: str, request: Request) -> StreamingResponse:
    require_admin(request)
    raw = await Storage.get(_request_key(request_id))
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="升级申请不存在")
    if not _is_approved(raw):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅已审批通过的申请可以开始升级")

    async def _stream():
        details = raw.setdefault("details", {})
        details["auto_install_result"] = "running"
        details["auto_install_started_at"] = datetime.now(UTC).isoformat()
        raw["updated_at"] = datetime.now(UTC).isoformat()
        await Storage.set(_request_key(request_id), raw, "json")
        try:
            await _maybe_activate_pro_license(raw)
            await _maybe_refresh_pro_license(raw)
            async for progress in perform_pro_bundle_install(restart=True):
                if progress.stage == "error":
                    details["auto_install_result"] = "failed"
                    details["auto_install_error"] = progress.message
                    raw["updated_at"] = datetime.now(UTC).isoformat()
                    await Storage.set(_request_key(request_id), raw, "json")
                    await _report_pro_bundle_installation(raw, install_result="failed", error_message=progress.message)
                elif progress.stage in {"done", "restarting"}:
                    marker = _read_pro_bundle_install_marker()
                    await _maybe_activate_pro_license(raw)
                    await _maybe_refresh_pro_license(raw)
                    details["auto_install_result"] = "restarting" if progress.stage == "restarting" else "done"
                    details["auto_install_version"] = marker.get("installed_version")
                    details["auto_install_pro_version"] = marker.get("flockspro_component_version")
                    details["auto_install_completed_at"] = datetime.now(UTC).isoformat()
                    details["auto_install_message"] = progress.message
                    _enrich_record_from_install_marker(raw)
                    raw["status"] = "activated"
                    raw["updated_at"] = datetime.now(UTC).isoformat()
                    await Storage.set(_request_key(request_id), raw, "json")
                    await _report_pro_bundle_installation(raw, install_result="success")
                    await _mark_console_upgrade_activated(raw)
                yield f"data: {progress.model_dump_json()}\n\n"
                await asyncio.sleep(0)
                if progress.stage == "error":
                    return
        except Exception as exc:
            details["auto_install_result"] = "failed"
            details["auto_install_error"] = str(exc)
            raw["updated_at"] = datetime.now(UTC).isoformat()
            await Storage.set(_request_key(request_id), raw, "json")
            await _report_pro_bundle_installation(raw, install_result="failed", error_message=str(exc))
            yield f"data: {json.dumps({'stage': 'error', 'message': str(exc), 'success': False})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/upgrade-requests/{request_id}/cancel", response_model=UpgradeRequestStatus)
async def cancel_upgrade_request(request_id: str, request: Request) -> UpgradeRequestStatus:
    require_admin(request)
    raw = await Storage.get(_request_key(request_id))
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="升级申请不存在")

    console_base = _console_base_url()
    if console_base:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                data: dict[str, Any] | None = None
                resp = await client.post(f"{console_base}/v1/upgrade-requests/{request_id}/withdraw")
                if resp.status_code == status.HTTP_400_BAD_REQUEST:
                    # Idempotent cancel UX: if console already changed state, sync latest instead of surfacing 400.
                    latest_resp = await client.get(f"{console_base}/v1/upgrade-requests/{request_id}")
                    if latest_resp.status_code == status.HTTP_200_OK:
                        latest_data = latest_resp.json()
                        latest_status = str(latest_data.get("status", "")).strip().lower()
                        # Console may reject withdraw for approved requests.
                        # Keep OSS UX actionable: treat this as a local cancel so user can re-apply.
                        if str(raw.get("status", "")).strip().lower() == "approved" and latest_status == "approved":
                            latest_data = {**latest_data, "status": "cancelled"}
                        data = latest_data
                    elif latest_resp.status_code == status.HTTP_404_NOT_FOUND:
                        data = {"status": "cancelled"}
                    else:
                        latest_resp.raise_for_status()
                elif resp.status_code == status.HTTP_404_NOT_FOUND:
                    # Console may have lost this request (e.g. in-memory reset). Keep local UX consistent.
                    data = {"status": "cancelled"}
                else:
                    resp.raise_for_status()
                    data = resp.json()
        except httpx.HTTPError as exc:
            _raise_console_service_error(exc)
        else:
            raw.update(
                {
                    "status": data.get("status", "cancelled"),
                    "reason": data.get("reason", raw.get("reason")),
                    "suggestion": data.get("suggestion", raw.get("suggestion")),
                    "activate_key": data.get("activate_key", raw.get("activate_key")),
                    "manifest_url": data.get("manifest_url", raw.get("manifest_url")),
                    "license_id": data.get("license_id", raw.get("license_id")),
                    "license_status": data.get("license_status", raw.get("license_status")),
                    "max_admins": data.get("max_admins", raw.get("max_admins")),
                    "max_members": data.get("max_members", raw.get("max_members")),
                    "expires_at": data.get("expires_at", raw.get("expires_at")),
                    "details": data.get("form_data", raw.get("details", {})),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
    else:
        raw["status"] = "cancelled"
        raw["updated_at"] = datetime.now(UTC).isoformat()
    await Storage.set(_request_key(request_id), raw, "json")
    return UpgradeRequestStatus(**raw)

