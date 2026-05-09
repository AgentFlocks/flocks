"""
Local account authentication routes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from flocks.auth.service import AuthService, TEMP_PASSWORD_TTL_HOURS
from flocks.cloud.binding import CloudBindingService
from flocks.server.auth import (
    clear_session_cookie,
    require_admin,
    require_user,
    set_session_cookie,
    should_use_secure_cookie,
)

router = APIRouter()


def _parse_event_type(event_type: str) -> tuple[str, str]:
    if "." in event_type:
        category, action = event_type.split(".", 1)
        return category, action
    return event_type, "event"


async def _emit_auth_audit_fallback(event_type: str, payload: dict[str, Any]) -> None:
    """Persist auth audit directly when flocks audit sink is still no-op."""
    try:
        from flocks.audit import NullAuditSink, get_sink

        sink_cls = get_sink()
        if sink_cls is not NullAuditSink:
            return
    except Exception:
        return

    try:
        from flockspro.audit.service import AuditEvent
        from flockspro.audit.sinks import SqliteAuditSink
    except Exception:
        # OSS or flockspro not installed: nothing to persist.
        return

    category, action = _parse_event_type(event_type)
    failed = "failed" in action or bool(payload.get("error") or payload.get("reason"))
    user_id = payload.get("user_id")
    username = payload.get("username")
    session_id = payload.get("session_id")
    event = AuditEvent(
        event_type=event_type,
        category=category,
        action=action,
        status="error" if failed else "ok",
        result="failed" if failed else "success",
        user_id=str(user_id) if user_id else None,
        user_name=str(username) if username else None,
        resource_type="session",
        resource_id=str(session_id) if session_id else None,
        session_id=str(session_id) if session_id else None,
        ip=str(payload.get("ip")) if payload.get("ip") else None,
        payload=payload,
        metadata=payload,
    )
    await SqliteAuditSink().write(event)


async def _emit_auth_audit(event_type: str, payload: dict) -> None:
    try:
        from flocks.audit import emit_audit_event

        await emit_audit_event(event_type, payload)
    except Exception:
        # Audit failures must not block auth flow.
        pass
    try:
        await _emit_auth_audit_fallback(event_type, payload)
    except Exception:
        pass


class BootstrapStatusResponse(BaseModel):
    bootstrapped: bool


class BootstrapAdminRequest(BaseModel):
    username: str = Field("admin", min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class MeResponse(BaseModel):
    id: str
    username: str
    role: str
    status: str
    must_reset_password: bool
    created_at: str | None = None
    updated_at: str | None = None
    last_login_at: str | None = None


def _to_me_response(user) -> MeResponse:
    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        status=user.status,
        must_reset_password=user.must_reset_password,
        created_at=getattr(user, "created_at", None),
        updated_at=getattr(user, "updated_at", None),
        last_login_at=getattr(user, "last_login_at", None),
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class ResetOwnPasswordResponse(BaseModel):
    success: bool
    temporary_password: str | None = None
    must_reset_password: bool


class CloudBindingInitResponse(BaseModel):
    binding_id: str
    portal_login_url: str


class CloudBindingExchangeResponse(BaseModel):
    binding_id: str
    cloud_session_token: str
    fingerprint: str
    install_id: str


class CloudBindingSessionResponse(BaseModel):
    bound: bool
    binding_id: str | None = None
    account_name: str | None = None
    updated_at: str | None = None


class CloudSyncNowResponse(BaseModel):
    success: bool
    synced_at: str | None = None
    detail: str | None = None


@router.get("/bootstrap-status", response_model=BootstrapStatusResponse, summary="获取本地账号初始化状态")
async def bootstrap_status() -> BootstrapStatusResponse:
    status_obj = await AuthService.get_bootstrap_status()
    return BootstrapStatusResponse(**status_obj)


@router.post("/bootstrap-admin", response_model=MeResponse, summary="初始化管理员账号")
async def bootstrap_admin(payload: BootstrapAdminRequest, response: Response, request: Request) -> MeResponse:
    try:
        await AuthService.bootstrap_admin(payload.username, payload.password)
        user, session_id = await AuthService.login(
            payload.username,
            payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    set_session_cookie(response, session_id, secure=should_use_secure_cookie(request))
    return _to_me_response(user)


@router.post("/login", response_model=MeResponse, summary="登录本地账号")
async def login(payload: LoginRequest, response: Response, request: Request) -> MeResponse:
    try:
        user, session_id = await AuthService.login(
            payload.username,
            payload.password,
        )
    except ValueError as exc:
        await _emit_auth_audit(
            "account.login_failed",
            {
                "username": payload.username,
                "reason": str(exc),
                "ip": getattr(getattr(request, "client", None), "host", None),
            },
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    set_session_cookie(response, session_id, secure=should_use_secure_cookie(request))
    await _emit_auth_audit(
        "account.login",
        {
            "actor_id": user.username,
            "actor_name": user.username,
            "user_id": user.id,
            "user_name": user.username,
            "username": user.username,
            "role": user.role,
            "session_id": session_id,
            "ip": getattr(getattr(request, "client", None), "host", None),
        },
    )
    return _to_me_response(user)


@router.get("/cloud/login", response_model=CloudBindingInitResponse, summary="发起云账号绑定")
async def cloud_login_init(request: Request, return_to: str | None = None) -> CloudBindingInitResponse:
    require_admin(request)
    resolved_return_to = return_to or "/auth/cloud/return"
    result = await CloudBindingService.init_binding(return_to=resolved_return_to)
    return CloudBindingInitResponse(**result)


@router.post("/logout", summary="退出登录")
async def logout(response: Response, request: Request) -> dict:
    user = require_user(request)
    session_id = request.cookies.get("flocks_session")
    if session_id:
        await AuthService.revoke_session(session_id)
    clear_session_cookie(response)
    await _emit_auth_audit(
        "account.logout",
        {
            "actor_id": user.username,
            "actor_name": user.username,
            "user_id": user.id,
            "user_name": user.username,
            "username": user.username,
            "role": user.role,
            "session_id": session_id,
            "ip": getattr(getattr(request, "client", None), "host", None),
        },
    )
    return {"success": True}


@router.get("/me", response_model=MeResponse, summary="获取当前登录用户")
async def me(request: Request) -> MeResponse:
    user = require_user(request)
    full_user = await AuthService.get_user_by_id(user.id)
    return _to_me_response(full_user or user)


@router.get("/cloud/return", response_model=CloudBindingExchangeResponse, summary="完成云账号绑定 exchange")
async def cloud_login_return(
    binding_id: str,
    request: Request,
    passport_uid: str | None = None,
) -> CloudBindingExchangeResponse:
    require_admin(request)
    try:
        result = await CloudBindingService.exchange_binding(
            binding_id=binding_id,
            passport_uid=passport_uid,
        )
        return CloudBindingExchangeResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/cloud/session", response_model=CloudBindingSessionResponse, summary="查询本地云绑定状态")
async def cloud_session_status(request: Request) -> CloudBindingSessionResponse:
    require_admin(request)
    session = await CloudBindingService.get_bound_session()
    if not session:
        return CloudBindingSessionResponse(bound=False)
    account_name = session.get("user_display") or session.get("user_email") or session.get("passport_uid")
    if not account_name:
        # 严格二态：没有账号名时视为未绑定
        return CloudBindingSessionResponse(bound=False)
    return CloudBindingSessionResponse(
        bound=True,
        binding_id=session.get("binding_id"),
        account_name=account_name,
        updated_at=session.get("updated_at"),
    )


@router.post("/cloud/unbind", summary="解除本地云绑定")
async def cloud_session_unbind(request: Request) -> dict:
    require_admin(request)
    await CloudBindingService.clear_bound_session()
    return {"success": True}


@router.post("/cloud/sync-now", response_model=CloudSyncNowResponse, summary="立即同步节点信息到云端")
async def cloud_sync_now(request: Request) -> CloudSyncNowResponse:
    require_admin(request)
    try:
        heartbeat_result = await CloudBindingService.send_heartbeat()
        result = await CloudBindingService.sync_node_profile(force=True, source="manual")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    synced_at = None
    if isinstance(result, dict):
        node = result.get("node")
        if isinstance(node, dict):
            synced_at = node.get("received_at") or node.get("sent_at")
    if not synced_at and isinstance(heartbeat_result, dict):
        hb_node = heartbeat_result.get("node")
        if isinstance(hb_node, dict):
            synced_at = hb_node.get("received_at") or hb_node.get("sent_at")
    return CloudSyncNowResponse(
        success=True,
        synced_at=synced_at,
        detail="ok",
    )


@router.post("/change-password", summary="修改当前用户密码")
async def change_password(payload: ChangePasswordRequest, response: Response, request: Request) -> dict:
    user = require_user(request)
    try:
        await AuthService.change_password(
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
        _, session_id = await AuthService.login(
            user.username,
            payload.new_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    set_session_cookie(response, session_id, secure=should_use_secure_cookie(request))
    return {"success": True}


@router.post("/reset-password", response_model=ResetOwnPasswordResponse, summary="重置当前用户密码")
async def reset_own_password(response: Response, request: Request) -> ResetOwnPasswordResponse:
    user = require_user(request)
    import secrets
    from datetime import UTC, datetime, timedelta

    new_password = secrets.token_urlsafe(10)
    expires_at = (datetime.now(UTC) + timedelta(hours=TEMP_PASSWORD_TTL_HOURS)).isoformat()
    try:
        await AuthService.set_password(
            target_user_id=user.id,
            new_password=new_password,
            must_reset_password=True,
            temp_password_expires_at=expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    clear_session_cookie(response)
    return ResetOwnPasswordResponse(
        success=True,
        temporary_password=new_password,
        must_reset_password=True,
    )
