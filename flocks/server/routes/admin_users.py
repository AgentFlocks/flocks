"""
Admin-only user and audit management routes.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from flocks.auth.service import AuthService
from flocks.server.auth import get_request_ip, get_request_user_agent, require_admin

router = APIRouter()


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    status: str
    must_reset_password: bool
    created_at: str
    updated_at: str
    last_login_at: Optional[str] = None


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field("member", description="admin or member")


class ResetPasswordRequest(BaseModel):
    new_password: Optional[str] = Field(None, min_length=8, max_length=128)
    force_reset: bool = True


class UpdateUserStatusRequest(BaseModel):
    status: str = Field(..., description="active or disabled")


class AuditResponse(BaseModel):
    id: str
    operator_user_id: Optional[str] = None
    target_user_id: Optional[str] = None
    action: str
    result: str
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: dict
    created_at: str


@router.get("/users", response_model=List[UserResponse], summary="管理员获取用户列表")
async def list_users(request: Request) -> List[UserResponse]:
    _admin = require_admin(request)
    users = await AuthService.list_users()
    return [UserResponse(**u.model_dump()) for u in users]


@router.post("/users", response_model=UserResponse, summary="管理员创建用户")
async def create_user(payload: CreateUserRequest, request: Request) -> UserResponse:
    admin = require_admin(request)
    try:
        user = await AuthService.create_user(
            username=payload.username,
            password=payload.password,
            role=payload.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"创建用户失败: {exc}") from exc

    await AuthService.record_audit(
        action="admin.create_user",
        result="success",
        operator_user_id=admin.id,
        target_user_id=user.id,
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
        metadata={"username": user.username, "role": user.role},
    )
    return UserResponse(**user.model_dump())


@router.post("/users/{user_id}/reset-password", summary="管理员重置密码")
async def reset_user_password(user_id: str, payload: ResetPasswordRequest, request: Request) -> dict:
    admin = require_admin(request)
    target_user = await AuthService.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    new_password = payload.new_password
    if not new_password:
        # 管理员可直接生成一次性随机密码
        import secrets

        new_password = secrets.token_urlsafe(10)

    expires_at = None
    if payload.force_reset:
        from datetime import UTC, datetime, timedelta

        expires_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()

    try:
        await AuthService.set_password(
            operator_user=admin,
            target_user_id=user_id,
            new_password=new_password,
            must_reset_password=payload.force_reset,
            temp_password_expires_at=expires_at,
            action="admin.reset_password",
            ip=get_request_ip(request),
            user_agent=get_request_user_agent(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "success": True,
        "temporary_password": new_password if payload.force_reset else None,
        "must_reset_password": payload.force_reset,
    }


@router.patch("/users/{user_id}/status", response_model=UserResponse, summary="管理员禁用/启用用户")
async def update_user_status(user_id: str, payload: UpdateUserStatusRequest, request: Request) -> UserResponse:
    admin = require_admin(request)
    try:
        user = await AuthService.update_user_status(user_id, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await AuthService.record_audit(
        action="admin.update_user_status",
        result="success",
        operator_user_id=admin.id,
        target_user_id=user.id,
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
        metadata={"status": payload.status},
    )
    return UserResponse(**user.model_dump())


@router.get("/audit-logs", response_model=List[AuditResponse], summary="管理员查看全量审计日志")
async def list_audit_logs(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> List[AuditResponse]:
    _admin = require_admin(request)
    records = await AuthService.list_audits(limit=limit, offset=offset)
    return [AuditResponse(**r.model_dump()) for r in records]
