"""
Permission routes for Flocks TUI compatibility

Provides /permission endpoints for handling tool permission requests.

Flocks expects:
- GET /permission - List pending permissions
- POST /permission/{id}/reply - Reply to a permission request
"""

from typing import Dict, List, Any, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.permission.next import PermissionNext


router = APIRouter()
log = Log.create(service="permission-routes")


class PermissionRule(BaseModel):
    """Permission rule for tool access"""
    permission: str  # "read", "write", "bash", etc.
    patterns: List[str]  # Path patterns
    always: List[str]  # Always allowed patterns


class PermissionRequest(BaseModel):
    """Permission request from a tool"""
    id: str
    sessionID: str
    messageID: str
    toolID: str
    permission: str
    patterns: List[str]
    always: List[str]
    metadata: Dict[str, Any]
    time: Dict[str, int]  # {"created": timestamp}


class PermissionReplyRequest(BaseModel):
    """Request to reply to a permission"""
    allow: bool
    always: bool = False  # Remember this decision


class PermissionInfo(BaseModel):
    """Permission info for API response"""
    id: str
    sessionID: str
    messageID: str
    toolID: str
    permission: str
    patterns: List[str]
    always: List[str]
    metadata: Dict[str, Any]
    time: Dict[str, int]


def create_permission_request(
    session_id: str,
    message_id: str,
    tool_id: str,
    permission: str,
    patterns: List[str],
    always: List[str],
    metadata: Dict[str, Any] = None,
) -> PermissionInfo:
    """Create a new permission request"""
    perm_id = Identifier.ascending("permission")
    now = int(datetime.now().timestamp() * 1000)
    
    perm_info = {
        "id": perm_id,
        "sessionID": session_id,
        "messageID": message_id,
        "toolID": tool_id,
        "permission": permission,
        "patterns": patterns,
        "always": always,
        "metadata": metadata or {},
        "time": {"created": now},
    }
    
    log.info("permission.created", {"id": perm_id, "permission": permission})
    
    return PermissionInfo(**perm_info)


def get_permission(permission_id: str) -> Optional[dict]:
    """Get a permission request by ID"""
    pending = PermissionNext._pending.get(permission_id)  # Compatibility helper
    if not pending or pending.get("info") is None:
        return None
    info = pending["info"]
    return {
        "id": info.id,
        "sessionID": info.session_id,
        "messageID": info.metadata.get("messageID", ""),
        "toolID": (info.tool or {}).get("name", info.permission),
        "permission": info.permission,
        "patterns": info.patterns,
        "always": info.always,
        "metadata": info.metadata,
        "time": info.time,
    }


async def remove_permission(permission_id: str) -> bool:
    """Remove a permission request"""
    if permission_id not in PermissionNext._pending:
        return False
    await PermissionNext.reply(permission_id, "deny")
    return True


@router.get(
    "",
    response_model=List[PermissionInfo],
    summary="List permissions",
    description="Get all pending permission requests"
)
async def list_permissions() -> List[PermissionInfo]:
    """
    List all pending permission requests.
    
    Flocks TUI uses this to display permission prompts.
    """
    pending_infos = await PermissionNext.list_pending_infos()
    return [
        PermissionInfo(
            id=info.id,
            sessionID=info.session_id,
            messageID=info.metadata.get("messageID", ""),
            toolID=(info.tool or {}).get("name", info.permission),
            permission=info.permission,
            patterns=info.patterns,
            always=info.always,
            metadata=info.metadata,
            time=info.time,
        )
        for info in pending_infos
    ]


@router.get(
    "/{permission_id}",
    response_model=PermissionInfo,
    summary="Get permission",
    description="Get a specific permission request"
)
async def get_permission_by_id(permission_id: str) -> PermissionInfo:
    """Get a specific permission request"""
    info = await PermissionNext.get_pending_info(permission_id)
    if not info:
        raise HTTPException(status_code=404, detail="Permission not found")
    return PermissionInfo(
        id=info.id,
        sessionID=info.session_id,
        messageID=info.metadata.get("messageID", ""),
        toolID=(info.tool or {}).get("name", info.permission),
        permission=info.permission,
        patterns=info.patterns,
        always=info.always,
        metadata=info.metadata,
        time=info.time,
    )


@router.post(
    "/{permission_id}/reply",
    summary="Reply to permission",
    description="Reply to a permission request (allow or deny)"
)
async def reply_permission(
    permission_id: str,
    request: PermissionReplyRequest,
) -> Dict[str, bool]:
    """
    Reply to a permission request.
    
    Args:
        permission_id: The permission request ID
        request: The reply (allow/deny)
    
    Returns:
        {"success": true}
    """
    info = await PermissionNext.get_pending_info(permission_id)
    if not info:
        raise HTTPException(status_code=404, detail="Permission not found")
    
    log.info("permission.reply", {
        "id": permission_id,
        "allow": request.allow,
        "always": request.always,
    })
    
    reply = "always" if request.allow and request.always else "allow" if request.allow else "never" if request.always else "deny"
    await PermissionNext.reply(permission_id, reply, session_id=info.session_id)
    
    return {"success": True}


# Export helper functions for use in other modules
__all__ = [
    "router",
    "create_permission_request",
    "get_permission",
    "remove_permission",
    "PermissionInfo",
    "PermissionReplyRequest",
]
