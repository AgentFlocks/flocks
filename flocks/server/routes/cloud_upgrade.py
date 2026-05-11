"""
Cloud upgrade request orchestration routes (OSS-side).
"""

from __future__ import annotations

import asyncio
import os
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from flocks.cloud.binding import CloudBindingService
from flocks.server.auth import require_admin
from flocks.storage.storage import Storage
from flocks.updater import check_update, perform_update

router = APIRouter()
_AUTO_UPGRADE_TASKS: set[asyncio.Task[None]] = set()
_AUTO_UPGRADE_REQUEST_IDS: set[str] = set()


def _activation_base_url() -> str:
    # Keep parity with cloud binding: default to local ACT when env is absent.
    if "FLOCKS_ACT_BASE_URL" in os.environ:
        return os.getenv("FLOCKS_ACT_BASE_URL", "").strip().rstrip("/")
    return "http://127.0.0.1:18000"


class UpgradeRequestCreate(BaseModel):
    product: str = Field(default="Flocks Pro", pattern="^Flocks Pro$")
    license_type: Literal["trial_30d", "poc", "commercial"]
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
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


def _request_key(request_id: str) -> str:
    return f"cloud:upgrade_request:{request_id}"


async def _list_request_ids() -> list[str]:
    ids = await Storage.get("cloud:upgrade_request_ids")
    if not isinstance(ids, list):
        return []
    return [str(i) for i in ids]


async def _push_request_id(request_id: str) -> None:
    ids = await _list_request_ids()
    if request_id not in ids:
        ids.append(request_id)
        await Storage.set("cloud:upgrade_request_ids", ids, "json")


def _is_approved(record: dict[str, Any]) -> bool:
    return str(record.get("status", "")).strip().lower() == "approved"


async def _maybe_activate_pro_license(record: dict[str, Any]) -> None:
    activate_key = str(record.get("activate_key") or "").strip()
    if not activate_key:
        return
    details = record.setdefault("details", {})
    if details.get("license_activated_at"):
        return
    try:
        from flockspro.license.runtime import get_license_checker

        checker = get_license_checker()
        activate_fn = getattr(checker, "activate", None)
        if callable(activate_fn):
            activate_fn(activate_key)
            details["license_activated_at"] = datetime.now(UTC).isoformat()
    except Exception as exc:
        details["license_activate_error"] = str(exc)


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
    info = await check_update()
    if info.error:
        raise ValueError(info.error)
    if not info.has_update:
        details["auto_install_result"] = "already_latest"
        details["auto_install_version"] = info.current_version
        details["auto_install_completed_at"] = datetime.now(UTC).isoformat()
        await _report_pro_bundle_installation(record, install_result="success")
        return record

    target_version = info.latest_version
    if not target_version:
        raise ValueError("missing target version")

    details["auto_install_target"] = target_version
    final_stage = ""
    final_message = ""
    async for progress in perform_update(
        target_version,
        zipball_url=info.zipball_url,
        tarball_url=info.tarball_url,
        bundle_sha256=info.bundle_sha256,
        bundle_format=info.bundle_format,
        restart=False,
    ):
        final_stage = progress.stage
        final_message = progress.message
        if progress.stage == "error":
            raise ValueError(progress.message)

    details["auto_install_result"] = "done" if final_stage == "done" else "unknown"
    details["auto_install_version"] = target_version
    details["auto_install_completed_at"] = datetime.now(UTC).isoformat()
    details["auto_install_message"] = final_message
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
        cloud_session = await CloudBindingService.require_bound_session()
    except Exception as exc:
        details["install_receipt_error"] = str(exc)
        return
    marker = _read_pro_bundle_install_marker()
    payload = {
        "license_id": record.get("activate_key"),
        "fingerprint": cloud_session.get("fingerprint"),
        "install_id": cloud_session.get("install_id"),
        "installed_version": marker.get("installed_version") or details.get("auto_install_target") or details.get("auto_install_version") or "",
        "oss_version": marker.get("oss_version"),
        "flockspro_component_version": marker.get("flockspro_component_version"),
        "build_id": marker.get("build_id"),
        "install_result": install_result,
        "error_message": error_message,
        "reported_at": datetime.now(UTC).isoformat(),
    }
    act_base = _activation_base_url()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{act_base}/v1/pro-bundles/installations",
                json=payload,
                headers={"Authorization": f"Bearer {cloud_session['cloud_session_token']}"},
            )
            resp.raise_for_status()
            details["install_receipt_reported_at"] = datetime.now(UTC).isoformat()
    except Exception as exc:
        details["install_receipt_error"] = str(exc)


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


def _raise_cloud_service_error(exc: Exception) -> None:
    detail = "云端升级服务调用失败，请稍后重试"
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
        cloud_session = await CloudBindingService.require_bound_session()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    now = datetime.now(UTC).isoformat()
    request_id = str(uuid4())
    normalized_product = payload.product.strip() or "Flocks Pro"
    details = {
        "product": normalized_product,
        "license_type": payload.license_type,
        "company": payload.company.strip(),
        "applicant_name": payload.applicant_name.strip(),
        "applicant_email": (payload.applicant_email or "").strip() or None,
        "applicant_phone": (payload.applicant_phone or "").strip() or None,
        "notes": (payload.notes or "").strip() or None,
    }
    record = {
        "request_id": request_id,
        "status": "pending",
        "previous_request_id": None,
        "reason": details["notes"],
        "suggestion": None,
        "activate_key": None,
        "manifest_url": None,
        "details": details,
        "created_at": now,
        "updated_at": now,
    }

    act_base = _activation_base_url()
    if act_base:
        cloud_payload = {
            "node_id": str(admin_user.id),
            "binding_id": cloud_session.get("binding_id"),
            "fingerprint": cloud_session.get("fingerprint"),
            "install_id": cloud_session.get("install_id"),
            "passport_uid": cloud_session.get("passport_uid"),
            "company_name": details["company"],
            "contact_email": details["applicant_email"] or "",
            "form_data": details,
        }
        headers = {"Authorization": f"Bearer {cloud_session['cloud_session_token']}"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{act_base}/v1/upgrade-requests", json=cloud_payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            _raise_cloud_service_error(exc)
        else:
            record.update(
                {
                    "request_id": data.get("request_id", request_id),
                    "status": data.get("status", "pending"),
                    "reason": data.get("reason", details["notes"]),
                    "suggestion": data.get("suggestion"),
                    "activate_key": data.get("activate_key"),
                    "manifest_url": data.get("manifest_url"),
                    "details": data.get("form_data", details),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )

    await Storage.set(_request_key(record["request_id"]), record, "json")
    await _push_request_id(record["request_id"])
    _schedule_auto_activate_upgrade(record["request_id"], record)
    await Storage.set(_request_key(record["request_id"]), record, "json")
    return UpgradeRequestStatus(**record)


@router.get("/upgrade-requests", response_model=list[UpgradeRequestStatus])
async def list_upgrade_requests(request: Request) -> list[UpgradeRequestStatus]:
    require_admin(request)
    result: list[UpgradeRequestStatus] = []
    for request_id in reversed(await _list_request_ids()):
        raw = await Storage.get(_request_key(request_id))
        if raw:
            result.append(UpgradeRequestStatus(**raw))
    return result


@router.get("/upgrade-requests/{request_id}", response_model=UpgradeRequestStatus)
async def get_upgrade_request(request_id: str, request: Request) -> UpgradeRequestStatus:
    require_admin(request)
    raw = await Storage.get(_request_key(request_id))
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="升级申请不存在")
    return UpgradeRequestStatus(**raw)


@router.post("/upgrade-requests/{request_id}/refresh", response_model=UpgradeRequestStatus)
async def refresh_upgrade_request(request_id: str, request: Request) -> UpgradeRequestStatus:
    require_admin(request)
    raw = await Storage.get(_request_key(request_id))
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="升级申请不存在")

    act_base = _activation_base_url()
    if act_base:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{act_base}/v1/upgrade-requests/{request_id}")
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            _raise_cloud_service_error(exc)
        else:
            raw.update(
                {
                    "status": data.get("status", raw["status"]),
                    "reason": data.get("reason", raw.get("reason")),
                    "suggestion": data.get("suggestion", raw.get("suggestion")),
                    "activate_key": data.get("activate_key", raw.get("activate_key")),
                    "manifest_url": data.get("manifest_url", raw.get("manifest_url")),
                    "details": data.get("form_data", raw.get("details", {})),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
    else:
        raw["updated_at"] = datetime.now(UTC).isoformat()

    await Storage.set(_request_key(request_id), raw, "json")
    _schedule_auto_activate_upgrade(request_id, raw)
    await Storage.set(_request_key(request_id), raw, "json")
    return UpgradeRequestStatus(**raw)


@router.post("/upgrade-requests/{request_id}/cancel", response_model=UpgradeRequestStatus)
async def cancel_upgrade_request(request_id: str, request: Request) -> UpgradeRequestStatus:
    require_admin(request)
    raw = await Storage.get(_request_key(request_id))
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="升级申请不存在")

    act_base = _activation_base_url()
    if act_base:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                data: dict[str, Any] | None = None
                resp = await client.post(f"{act_base}/v1/upgrade-requests/{request_id}/withdraw")
                if resp.status_code == status.HTTP_404_NOT_FOUND:
                    # Backward-compatible with older ACT endpoint naming.
                    resp = await client.post(f"{act_base}/v1/upgrade-requests/{request_id}/cancel")
                if resp.status_code == status.HTTP_400_BAD_REQUEST:
                    # Idempotent cancel UX: if cloud already changed state, sync latest instead of surfacing 400.
                    latest_resp = await client.get(f"{act_base}/v1/upgrade-requests/{request_id}")
                    if latest_resp.status_code == status.HTTP_200_OK:
                        latest_data = latest_resp.json()
                        latest_status = str(latest_data.get("status", "")).strip().lower()
                        # Cloud may reject withdraw for approved requests.
                        # Keep OSS UX actionable: treat this as a local cancel so user can re-apply.
                        if str(raw.get("status", "")).strip().lower() == "approved" and latest_status == "approved":
                            latest_data = {**latest_data, "status": "cancelled"}
                        data = latest_data
                    elif latest_resp.status_code == status.HTTP_404_NOT_FOUND:
                        data = {"status": "cancelled"}
                    else:
                        latest_resp.raise_for_status()
                elif resp.status_code == status.HTTP_404_NOT_FOUND:
                    # Cloud may have lost this request (e.g. in-memory reset). Keep local UX consistent.
                    data = {"status": "cancelled"}
                else:
                    resp.raise_for_status()
                    data = resp.json()
        except httpx.HTTPError as exc:
            _raise_cloud_service_error(exc)
        else:
            raw.update(
                {
                    "status": data.get("status", "cancelled"),
                    "reason": data.get("reason", raw.get("reason")),
                    "suggestion": data.get("suggestion", raw.get("suggestion")),
                    "activate_key": data.get("activate_key", raw.get("activate_key")),
                    "manifest_url": data.get("manifest_url", raw.get("manifest_url")),
                    "details": data.get("form_data", raw.get("details", {})),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
    else:
        raw["status"] = "cancelled"
        raw["updated_at"] = datetime.now(UTC).isoformat()
    await Storage.set(_request_key(request_id), raw, "json")
    return UpgradeRequestStatus(**raw)

