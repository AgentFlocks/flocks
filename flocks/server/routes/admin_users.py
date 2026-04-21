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
    password: Optional[str] = Field(None, min_length=8, max_length=128)
    role: str = Field("member", description="admin or member")
    force_reset: bool = True


class CreateUserResponse(UserResponse):
    temporary_password: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    new_password: Optional[str] = Field(None, min_length=8, max_length=128)
    force_reset: bool = True


class UpdateUserStatusRequest(BaseModel):
    status: str = Field(..., description="active or disabled")


class UpdateUserRoleRequest(BaseModel):
    role: str = Field(..., description="admin or member")


class AuditResponse(BaseModel):
    id: str
    operator_user_id: Optional[str] = None
    target_user_id: Optional[str] = None
    operator_username: Optional[str] = None
    target_username: Optional[str] = None
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


@router.post("/users", response_model=CreateUserResponse, summary="管理员创建用户")
async def create_user(payload: CreateUserRequest, request: Request) -> CreateUserResponse:
    admin = require_admin(request)
    password = payload.password
    if not password:
        import secrets

        password = secrets.token_urlsafe(10)
    expires_at = None
    if payload.force_reset:
        from datetime import UTC, datetime, timedelta

        expires_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
    try:
        user = await AuthService.create_user(
            username=payload.username,
            password=password,
            role=payload.role,
            must_reset_password=payload.force_reset,
            temp_password_expires_at=expires_at,
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
        metadata={"username": user.username, "role": user.role, "must_reset_password": payload.force_reset},
    )
    return CreateUserResponse(
        **user.model_dump(),
        temporary_password=password if payload.force_reset else None,
    )


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
    if admin.id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能修改当前登录账号状态")
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


@router.patch("/users/{user_id}/role", response_model=UserResponse, summary="管理员修改账号角色")
async def update_user_role(user_id: str, payload: UpdateUserRoleRequest, request: Request) -> UserResponse:
    admin = require_admin(request)
    if admin.id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能修改当前登录账号角色")
    try:
        user = await AuthService.update_user_role(user_id, payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await AuthService.record_audit(
        action="admin.update_user_role",
        result="success",
        operator_user_id=admin.id,
        target_user_id=user.id,
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
        metadata={"role": payload.role, "username": user.username},
    )
    return UserResponse(**user.model_dump())


@router.delete("/users/{user_id}", summary="管理员删除用户")
async def delete_user(user_id: str, request: Request) -> dict:
    admin = require_admin(request)
    if admin.id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除当前登录账号")
    try:
        deleted_user, retained_sessions = await AuthService.delete_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await AuthService.record_audit(
        action="admin.delete_user",
        result="success",
        operator_user_id=admin.id,
        target_user_id=deleted_user.id,
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
        metadata={
            "username": deleted_user.username,
            "role": deleted_user.role,
            "status": deleted_user.status,
            "retained_sessions": retained_sessions,
        },
    )
    return {"success": True, "retained_sessions": retained_sessions}


@router.get("/audit-logs", response_model=List[AuditResponse], summary="管理员查看全量审计日志")
async def list_audit_logs(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> List[AuditResponse]:
    _admin = require_admin(request)
    records = await AuthService.list_audits(limit=limit, offset=offset)
    return [AuditResponse(**r.model_dump()) for r in records]
