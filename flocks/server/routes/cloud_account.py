"""
Cloud account binding routes (instance-level singleton).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from flocks.auth.service import AuthService
from flocks.server.auth import get_request_ip, get_request_user_agent, require_admin, require_user

router = APIRouter()


class CloudAccountResponse(BaseModel):
    provider: str
    account_id: str
    account_name: Optional[str] = None
    token_masked: Optional[str] = None
    mcp_quota: Optional[str] = None
    api_quota: Optional[str] = None
    balance: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    bound_by: str
    bound_at: str
    updated_at: str


class CloudBindRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    account_id: str = Field(..., min_length=1, max_length=128)
    account_name: Optional[str] = Field(None, max_length=128)
    token: Optional[str] = Field(None, max_length=1024)
    mcp_quota: Optional[str] = Field(None, max_length=64)
    api_quota: Optional[str] = Field(None, max_length=64)
    balance: Optional[str] = Field(None, max_length=64)
    metadata: Dict[str, Any] = Field(default_factory=dict)


@router.get("", response_model=Optional[CloudAccountResponse], summary="获取本实例云账号绑定信息")
async def get_cloud_account(request: Request) -> Optional[CloudAccountResponse]:
    _user = require_user(request)
    binding = await AuthService.get_cloud_binding()
    if not binding:
        return None
    return CloudAccountResponse(**binding.model_dump())


@router.post("/bind", response_model=CloudAccountResponse, summary="管理员绑定或重绑云账号")
async def bind_cloud_account(payload: CloudBindRequest, request: Request) -> CloudAccountResponse:
    admin = require_admin(request)
    try:
        binding = await AuthService.bind_cloud_account(
            operator=admin,
            provider=payload.provider,
            account_id=payload.account_id,
            account_name=payload.account_name,
            token=payload.token,
            mcp_quota=payload.mcp_quota,
            api_quota=payload.api_quota,
            balance=payload.balance,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await AuthService.record_audit(
        action="cloud.bind.confirmed",
        result="success",
        operator_user_id=admin.id,
        ip=get_request_ip(request),
        user_agent=get_request_user_agent(request),
        metadata={"provider": payload.provider, "account_id": payload.account_id},
    )
    return CloudAccountResponse(**binding.model_dump())
