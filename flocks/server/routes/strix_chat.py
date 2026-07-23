"""Authenticated Flocks proxy routes for the local Strix Chat sidecar."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from flocks.integrations.strix_chat import StrixChatClient, StrixChatClientError


router = APIRouter()


class StartStrixChatRequest(BaseModel):
    """Create one persistent Strix agent conversation."""

    message: str = Field(min_length=1, max_length=100_000)
    targets: list[str] = Field(default_factory=list, max_length=16)
    scan_mode: str = "standard"
    max_budget_usd: Optional[float] = Field(default=None, gt=0)
    model: Optional[str] = None

    @field_validator("scan_mode")
    @classmethod
    def validate_scan_mode(cls, value: str) -> str:
        if value not in {"quick", "standard", "deep"}:
            raise ValueError("scan_mode must be quick, standard, or deep")
        return value


class SendStrixMessageRequest(BaseModel):
    """Continue an existing Strix agent conversation."""

    message: str = Field(min_length=1, max_length=100_000)


@router.get("/health")
async def strix_chat_health() -> dict[str, Any]:
    return await _call(_client().health())


@router.post("")
async def start_strix_chat(request: StartStrixChatRequest) -> dict[str, Any]:
    return await _call(
        _client().start_chat(request.model_dump(exclude_none=True)),
    )


@router.get("/{chat_id}")
async def get_strix_chat(
    chat_id: str,
    after: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        operation = _client().get_chat(chat_id, after=after)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _call(operation)


@router.post("/{chat_id}/message")
async def send_strix_message(
    chat_id: str,
    request: SendStrixMessageRequest,
) -> dict[str, Any]:
    try:
        operation = _client().send_message(chat_id, request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _call(operation)


@router.delete("/{chat_id}")
async def delete_strix_chat(chat_id: str) -> dict[str, Any]:
    try:
        operation = _client().delete_chat(chat_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _call(operation)


async def _call(operation: Any) -> dict[str, Any]:
    try:
        return await operation
    except StrixChatClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _client() -> StrixChatClient:
    try:
        return StrixChatClient()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
