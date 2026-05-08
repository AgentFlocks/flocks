"""
Cloud upgrade request orchestration routes (OSS-side).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Optional, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from flocks.cloud.binding import CloudBindingService
from flocks.server.auth import require_admin
from flocks.storage.storage import Storage

router = APIRouter()


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

