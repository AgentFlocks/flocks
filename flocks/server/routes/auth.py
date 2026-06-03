"""
Local account authentication routes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from flocks.auth.service import AuthService, TEMP_PASSWORD_TTL_HOURS
from flocks.console.login import ConsoleLoginService
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


class ConsoleLoginStartResponse(BaseModel):
    console_login_id: str
    passport_login_url: str


class ConsoleLoginFinishRequest(BaseModel):
    console_login_id: str = Field(..., min_length=1)
    state: str | None = None
    passport_uid: str | None = None


class ConsoleLoginFinishResponse(BaseModel):
    console_login_id: str
    logged_in: bool
    account_name: str | None = None
    updated_at: str | None = None


class ConsoleLoginSessionResponse(BaseModel):
    logged_in: bool
    console_login_id: str | None = None
    account_name: str | None = None
    updated_at: str | None = None


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


@router.get("/console-login/start", response_model=ConsoleLoginStartResponse, summary="发起 console 云账号登录")
async def console_login_start(request: Request, return_to: str | None = None) -> ConsoleLoginStartResponse:
    require_admin(request)
    resolved_return_to = return_to or "/flockspro-upgrade/callback"
    try:
        result = await ConsoleLoginService.start_console_login(return_to=resolved_return_to)
        return ConsoleLoginStartResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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


@router.post("/console-login/finish", response_model=ConsoleLoginFinishResponse, summary="完成 console 云账号登录 exchange")
async def console_login_finish(
    payload: ConsoleLoginFinishRequest,
    request: Request,
) -> ConsoleLoginFinishResponse:
    require_admin(request)
    try:
        result = await ConsoleLoginService.finish_console_login(
            console_login_id=payload.console_login_id,
            state=payload.state,
            passport_uid=payload.passport_uid,
        )
        try:
            await ConsoleLoginService.send_heartbeat()
            await ConsoleLoginService.sync_node_profile(force=True, source="login")
        except Exception:
            # Login success must not depend on best-effort telemetry delivery.
            pass
        account_name = result.get("user_display") or result.get("user_email") or result.get("passport_uid")
        return ConsoleLoginFinishResponse(
            console_login_id=payload.console_login_id,
            logged_in=True,
            account_name=account_name,
            updated_at=result.get("updated_at"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/console-login/session", response_model=ConsoleLoginSessionResponse, summary="查询本地 console 登录状态")
async def console_login_session(request: Request) -> ConsoleLoginSessionResponse:
    require_admin(request)
    session = await ConsoleLoginService.get_console_session()
    if not session:
        return ConsoleLoginSessionResponse(logged_in=False)
    account_name = session.get("user_display") or session.get("user_email") or session.get("passport_uid")
    if not account_name:
        # 严格二态：没有账号名时视为未登录
        return ConsoleLoginSessionResponse(logged_in=False)
    return ConsoleLoginSessionResponse(
        logged_in=True,
        console_login_id=session.get("console_login_id"),
        account_name=account_name,
        updated_at=session.get("updated_at"),
    )


@router.post("/console-login/logout", summary="退出 console 云账号登录")
async def console_login_logout(request: Request) -> dict:
    require_admin(request)
    await ConsoleLoginService.logout_console_session()
    return {"success": True}


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
