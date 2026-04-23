"""
Admin-only user management routes.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from flocks.auth.service import AuthService
from flocks.server.auth import require_admin

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


class ResetPasswordRequest(BaseModel):
    new_password: Optional[str] = Field(None, min_length=8, max_length=128)
    force_reset: bool = True


@router.get("/users", response_model=List[UserResponse], summary="管理员获取用户列表")
async def list_users(request: Request) -> List[UserResponse]:
    _admin = require_admin(request)
    users = await AuthService.list_users()
    return [UserResponse(**u.model_dump()) for u in users]


@router.post("/users/{user_id}/reset-password", summary="管理员重置密码")
async def reset_user_password(user_id: str, payload: ResetPasswordRequest, request: Request) -> dict:
    require_admin(request)
    target_user = await AuthService.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    new_password = payload.new_password
    if not new_password:
        import secrets

        new_password = secrets.token_urlsafe(10)

    expires_at = None
    if payload.force_reset:
        from datetime import UTC, datetime, timedelta

        expires_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()

    try:
        await AuthService.set_password(
            target_user_id=user_id,
            new_password=new_password,
            must_reset_password=payload.force_reset,
            temp_password_expires_at=expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "success": True,
        "temporary_password": new_password if payload.force_reset else None,
        "must_reset_password": payload.force_reset,
    }
