"""
Local account authentication routes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from flocks.auth.service import AuthService
from flocks.server.auth import (
    clear_session_cookie,
    get_request_ip,
    get_request_user_agent,
    require_user,
    set_session_cookie,
    should_use_secure_cookie,
)

router = APIRouter()


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


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


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
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    set_session_cookie(response, session_id, secure=should_use_secure_cookie(request))
    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        status=user.status,
        must_reset_password=user.must_reset_password,
    )


@router.post("/login", response_model=MeResponse, summary="登录本地账号")
async def login(payload: LoginRequest, response: Response, request: Request) -> MeResponse:
    try:
        user, session_id = await AuthService.login(
            payload.username,
            payload.password,
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    set_session_cookie(response, session_id, secure=should_use_secure_cookie(request))
    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        status=user.status,
        must_reset_password=user.must_reset_password,
    )


@router.post("/logout", summary="退出登录")
async def logout(response: Response, request: Request) -> dict:
    user = require_user(request)
    session_id = request.cookies.get("flocks_session")
    if session_id:
        await AuthService.revoke_session(session_id)
    clear_session_cookie(response)
    await AuthService.record_audit(
        action="auth.logout",
        result="success",
        operator_user_id=user.id,
        target_user_id=user.id,
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
    )
    return {"success": True}


@router.get("/me", response_model=MeResponse, summary="获取当前登录用户")
async def me(request: Request) -> MeResponse:
    user = require_user(request)
    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        status=user.status,
        must_reset_password=user.must_reset_password,
    )


@router.post("/change-password", summary="修改当前用户密码")
async def change_password(payload: ChangePasswordRequest, request: Request) -> dict:
    user = require_user(request)
    try:
        await AuthService.change_password(
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"success": True}
