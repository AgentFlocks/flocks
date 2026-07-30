"""
Session revert functionality

Handles reverting sessions to previous states using snapshots.
Based on Flocks' ported src/session/revert.ts
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.snapshot import Snapshot, SnapshotPatch
from flocks.session.session import Session, SessionInfo, SessionRevert as SessionRevertModel
from flocks.session.message import Message, MessageInfo

log = Log.create(service="session.revert")


class RevertInput(BaseModel):
    """Input for revert operation"""
    session_id: str = Field(..., alias="sessionID", description="Session ID")
    message_id: str = Field(..., alias="messageID", description="Message ID to revert to")
    part_id: Optional[str] = Field(None, alias="partID", description="Part ID for partial revert")
    
    model_config = {"populate_by_name": True}


class SessionRevert:
    """
    Session revert namespace (Flocks compatible alias)
    
    Provides simplified API for route handlers.
    """

    @classmethod
    async def ensure_replayable(cls, session: SessionInfo, message_id: str) -> None:
        """Reject replay across the latest project-move boundary."""

        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        move_metadata = metadata.get("projectMove")
        if not isinstance(move_metadata, dict):
            return
        boundary_message_id = move_metadata.get("boundaryMessageID")
        if not boundary_message_id:
            return

        messages = await Message.list(session.id, include_archived=True)
        message_ids = [message.id for message in messages]
        if boundary_message_id not in message_ids or message_id not in message_ids:
            raise ValueError(
                "移动项目前的历史消息不能重发或重新生成"
            )
        if message_ids.index(message_id) <= message_ids.index(boundary_message_id):
            raise ValueError(
                "移动项目前的历史消息不能重发或重新生成"
            )
    
    @classmethod
    async def revert(
        cls,
        session_id: str,
        message_id: str,
        part_id: Optional[str] = None,
    ) -> SessionInfo:
        """
        Revert session to a specific message point.
        
        Args:
            session_id: Session ID
            message_id: Message ID to revert to
            part_id: Optional part ID for partial revert
            
        Returns:
            Updated session info
        """
        session = await Session.get_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        async with Session.active_operation(session_id) as active_session:
            if part_id is not None:
                parts = await Message.parts(message_id, session_id)
                part_belongs_to_message = any(
                    (
                        part.get("id")
                        if isinstance(part, dict)
                        else getattr(part, "id", None)
                    ) == part_id
                    for part in parts
                )
                if not part_belongs_to_message:
                    raise ValueError(
                        f"Part {part_id} does not belong to message {message_id}"
                    )

            await cls.ensure_replayable(active_session, message_id)

            input_obj = RevertInput(
                session_id=session_id,
                message_id=message_id,
                part_id=part_id,
            )

            return await SessionRevertManager.revert(
                project_id=active_session.project_id,
                input=input_obj,
                worktree=active_session.directory,
            )
    
    @classmethod
    async def unrevert(cls, session_id: str) -> SessionInfo:
        """
        Cancel revert and restore session.
        
        Args:
            session_id: Session ID
            
        Returns:
            Updated session info
        """
        session = await Session.get_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        return await SessionRevertManager.unrevert(
            project_id=session.project_id,
            session_id=session_id,
            worktree=session.directory,
        )
    
    @classmethod
    async def cleanup(cls, session: SessionInfo) -> None:
        """
        Clean up after confirmed revert.
        
        Args:
            session: Session info
        """
        await SessionRevertManager.cleanup(session.project_id, session)


class SessionRevertManager:
    """
    Session revert manager namespace
    
    Provides functionality to revert sessions to previous states,
    including file changes tracked via snapshots.
    """
    
    @classmethod
    async def revert(
        cls,
        project_id: str,
        input: RevertInput,
        worktree: str,
        vcs: str = "git"
    ) -> Optional[SessionInfo]:
        """
        Revert session to a specific message/part
        
        This will:
        1. Mark the session with revert state
        2. Collect file patches from messages after the revert point
        3. Revert those file changes using snapshots
        
        Args:
            project_id: Project ID
            input: Revert input with session/message/part IDs
            worktree: Working tree directory
            vcs: VCS type
            
        Returns:
            Updated session info or None if failed
        """
        # Get session
        session = await Session.get(project_id, input.session_id)
        if not session:
            log.warn("revert.session_not_found", {"session_id": input.session_id})
            return None
        
        # Get all messages
        messages = await Message.list(input.session_id)
        
        # Find the revert point and collect patches
        last_user_msg: Optional[MessageInfo] = None
        revert_info: Optional[Dict[str, Any]] = None
        patches: List[SnapshotPatch] = []
        
        for msg in messages:
            if msg.role == "user":
                last_user_msg = msg
            
            remaining_parts = []
            msg_parts = await Message.parts(msg.id, input.session_id)
            
            for part in msg_parts:
                if revert_info:
                    # After revert point, collect patches
                    if isinstance(part, dict) and part.get("type") == "patch":
                        patches.append(SnapshotPatch(
                            hash=part.get("hash", ""),
                            files=part.get("files", [])
                        ))
                    continue
                
                # Check if this is the revert point
                is_requested_part = (
                    input.part_id is not None
                    and msg.id == input.message_id
                    and (
                        (hasattr(part, "id") and part.id == input.part_id)
                        or (isinstance(part, dict) and part.get("id") == input.part_id)
                    )
                )
                if (msg.id == input.message_id and not input.part_id) or is_requested_part:
                    # Check if remaining parts have useful content
                    has_useful = any(
                        (isinstance(p, dict) and p.get("type") in ["text", "tool"]) or
                        (hasattr(p, "type") and p.type in ["text", "tool"])
                        for p in remaining_parts
                    )
                    
                    part_id = input.part_id if has_useful else None
                    
                    revert_info = {
                        "messageID": msg.id if part_id or not last_user_msg else last_user_msg.id,
                        "partID": part_id,
                    }
                
                remaining_parts.append(part)
        
        if not revert_info:
            log.warn("revert.point_not_found", {
                "session_id": input.session_id,
                "message_id": input.message_id,
            })
            return session
        
        # Create or preserve snapshot
        if session.revert and session.revert.snapshot:
            revert_info["snapshot"] = session.revert.snapshot
        else:
            snapshot_hash = await Snapshot.track(project_id, worktree, vcs)
            if snapshot_hash:
                revert_info["snapshot"] = snapshot_hash
        
        # Revert file changes
        if patches:
            await Snapshot.revert(project_id, worktree, patches)
        
        # Get diff if we have a snapshot
        if revert_info.get("snapshot"):
            diff = await Snapshot.diff(project_id, worktree, revert_info["snapshot"])
            revert_info["diff"] = diff
        
        # Update session with revert state
        updated_session = await Session.update(
            project_id,
            input.session_id,
            revert=revert_info
        )
        
        log.info("revert.completed", {
            "session_id": input.session_id,
            "message_id": revert_info["messageID"],
            "patches": len(patches),
        })
        
        return updated_session
    
    @classmethod
    async def unrevert(
        cls,
        project_id: str,
        session_id: str,
        worktree: str
    ) -> Optional[SessionInfo]:
        """
        Cancel revert and restore files to original state
        
        Args:
            project_id: Project ID
            session_id: Session ID
            worktree: Working tree directory
            
        Returns:
            Updated session info or None if failed
        """
        log.info("unrevert.starting", {"session_id": session_id})
        
        session = await Session.get(project_id, session_id)
        if not session:
            return None
        
        if not session.revert:
            return session
        
        # Restore files from snapshot
        if session.revert.snapshot:
            await Snapshot.restore(project_id, worktree, session.revert.snapshot)
        
        # Clear revert state
        updated_session = await Session.clear_revert(project_id, session_id)
        if updated_session:
            return await Session.get(project_id, session_id)
        
        return session
    
    @classmethod
    async def cleanup(
        cls,
        project_id: str,
        session: SessionInfo
    ) -> None:
        """
        Clean up after a confirmed revert
        
        Removes messages and parts after the revert point.
        
        Args:
            project_id: Project ID
            session: Session info with revert state
        """
        if not session.revert:
            return
        
        session_id = session.id
        message_id = session.revert.message_id
        part_id = session.revert.part_id
        
        # Get all messages
        messages = await Message.list(session_id)
        
        # Split into preserve and remove
        preserve = []
        remove = []
        found_revert_point = False
        
        for msg in messages:
            if msg.id == message_id:
                found_revert_point = True
                preserve.append(msg)
                continue
            
            if found_revert_point:
                remove.append(msg)
            else:
                preserve.append(msg)
        
        # Remove messages after revert point (via Message API, not raw Storage keys)
        for msg in remove:
            await Message.delete(session_id, msg.id)
            log.info("cleanup.message_removed", {
                "session_id": session_id,
                "message_id": msg.id,
            })
        
        # Handle partial revert (remove parts after part_id)
        if part_id and preserve:
            last_msg = preserve[-1]
            last_msg_parts = await Message.parts(last_msg.id, session_id)
            found_part = False
            
            for part in last_msg_parts:
                part_obj_id = part.id if hasattr(part, "id") else part.get("id") if isinstance(part, dict) else None
                
                if part_obj_id == part_id:
                    found_part = True
                    # Remove the revert point part itself (matching original behavior)
                
                if found_part:
                    await Message.remove_part(session_id, last_msg.id, part_obj_id)
                    log.info("cleanup.part_removed", {
                        "session_id": session_id,
                        "message_id": last_msg.id,
                        "part_id": part_obj_id,
                    })
        
        # Clear revert state
        await Session.clear_revert(project_id, session_id)
        
        log.info("cleanup.completed", {
            "session_id": session_id,
            "removed_messages": len(remove),
        })
