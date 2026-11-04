"""
Session Summary module

Manages session and message summarization.
Based on Flocks' ported src/session/summary.ts
"""

from typing import List, Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field
import os
import asyncio
from pathlib import Path

from flocks.utils.log import Log
from flocks.session.prompt import SessionPrompt


log = Log.create(service="session.summary")


class FileDiff(BaseModel):
    """
    File diff information
    
    Matches TypeScript Snapshot.FileDiff structure
    """
    file: str = Field(..., description="File path (relative to worktree)")
    additions: int = Field(0, description="Lines added")
    deletions: int = Field(0, description="Lines deleted")


class SessionSummaryInfo(BaseModel):
    """
    Session summary information
    
    Matches TypeScript Session.Info.summary structure
    """
    additions: int = Field(0, description="Total lines added")
    deletions: int = Field(0, description="Total lines deleted")
    files: int = Field(0, description="Number of files changed")
    diffs: Optional[List[FileDiff]] = Field(None, description="Detailed file diffs")


class MessageSummaryInfo(BaseModel):
    """
    Message summary information
    
    Stored in MessageV2.User.summary
    """
    title: Optional[str] = Field(None, description="Message title/summary")
    diffs: List[FileDiff] = Field(default_factory=list, description="File diffs for this message")


class SessionSummary:
    """
    Session Summary namespace
    
    Handles summarization of sessions and messages,
    including title generation and diff computation.
    
    Mirrors original Flocks SessionSummary namespace from summary.ts
    """
    
    @classmethod
    async def summarize(
        cls,
        session_id: str,
        message_id: str,
        worktree: Optional[str] = None,
    ) -> None:
        """
        Summarize both session and message
        
        This is the main entry point called after each assistant response.
        It updates both session-level and message-level summaries.
        
        Args:
            session_id: Session ID
            message_id: Message ID that triggered summarization
            worktree: Git worktree root for path relativization
        """
        import asyncio
        
        # Get all messages for the session
        try:
            from flocks.session.message import Message
            messages = await Message.list(session_id)
            
            # Convert to dict format for processing
            msgs_dict = []
            for m in messages:
                # Get parts for this message
                parts = await Message.parts(m.id, session_id)
                
                # Extract parent_id based on message type
                parent_id = None
                if hasattr(m, 'parentID'):
                    parent_id = m.parentID
                
                # Convert parts to dict format
                parts_dict = []
                for p in parts:
                    part_data = {"type": p.type}
                    if hasattr(p, 'text'):
                        part_data["text"] = p.text
                    if hasattr(p, 'metadata') and p.metadata:
                        part_data.update(p.metadata)
                    parts_dict.append(part_data)
                
                msgs_dict.append({
                    "id": m.id,
                    "role": m.role.value if hasattr(m.role, 'value') else str(m.role),
                    "parts": parts_dict,
                    "parentID": parent_id,
                })
            
        except ImportError:
            log.warn("summary.import_error")
            return
        
        await asyncio.gather(
            cls._summarize_session(session_id, msgs_dict, worktree),
            cls._summarize_message(session_id, message_id, msgs_dict),
        )
    
    @classmethod
    async def record_turn_diffs(
        cls,
        session_id: str,
        user_message_id: str,
        project_id: str,
        worktree: str,
    ) -> List[Dict[str, Any]]:
        """轮次结束时按快照算文件 diff 并落库（不触发 LLM）。

        - 会话级：从会话第一个 snapshot part 到当前工作区，只保留 patch part 记录过的文件
          （把用户自己在中途改的文件排除掉），存 ``session_diff:<sid>``；
        - 消息级：从该用户消息的 snapshot part 到当前工作区，存 ``message_diff:<sid>:<mid>``。
        """
        from flocks.session.message import Message
        from flocks.snapshot.snapshot import Snapshot
        from flocks.storage.storage import Storage

        def _field(part: Any, name: str, default: Any = None) -> Any:
            return part.get(name, default) if isinstance(part, dict) else getattr(part, name, default)

        def _rel(path: str) -> str:
            try:
                return os.path.relpath(path, worktree)
            except ValueError:
                return path

        messages = await Message.list(session_id)
        first_snapshot: Optional[str] = None
        turn_snapshot: Optional[str] = None
        patched_all: set = set()
        patched_turn: set = set()
        in_turn = False
        for msg in messages:
            role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            if role == "user":
                in_turn = msg.id == user_message_id
            parts = await Message.parts(msg.id, session_id)
            for part in parts:
                ptype = _field(part, "type")
                if ptype == "snapshot":
                    snapshot = _field(part, "snapshot")
                    if snapshot:
                        first_snapshot = first_snapshot or snapshot
                        if msg.id == user_message_id:
                            turn_snapshot = snapshot
                elif ptype == "patch":
                    files = {_rel(f) for f in (_field(part, "files") or [])}
                    patched_all |= files
                    if in_turn:
                        patched_turn |= files
        if not first_snapshot:
            return []

        current = await Snapshot.track(project_id, worktree)
        if not current:
            return []

        session_diffs = [
            d.model_dump()
            for d in await Snapshot.diff_full(project_id, worktree, first_snapshot, current)
            if d.file in patched_all
        ]
        await Storage.set(f"session_diff:{session_id}", session_diffs, "session_diff")

        if turn_snapshot:
            turn_diffs = [
                d.model_dump()
                for d in await Snapshot.diff_full(project_id, worktree, turn_snapshot, current)
                if d.file in patched_turn
            ]
            await Storage.set(f"message_diff:{session_id}:{user_message_id}", turn_diffs, "session_diff")

        try:
            from flocks.session.session import Session

            await Session.update(project_id, session_id, summary={
                "additions": sum(d["additions"] for d in session_diffs),
                "deletions": sum(d["deletions"] for d in session_diffs),
                "files": len(session_diffs),
            })
        except Exception as exc:
            log.warn("summary.session.update_error", {"error": str(exc)})

        log.info("summary.turn_diffs", {
            "session_id": session_id,
            "message_id": user_message_id,
            "files": len(session_diffs),
        })
        return session_diffs

    @classmethod
    async def _summarize_session(
        cls,
        session_id: str,
        messages: List[Dict[str, Any]],
        worktree: Optional[str] = None,
    ) -> SessionSummaryInfo:
        """
        Generate session-level summary
        
        Computes total additions, deletions, and files changed
        by scanning patch parts in messages.
        
        Args:
            session_id: Session ID
            messages: All session messages
            worktree: Git worktree for path relativization
            
        Returns:
            SessionSummaryInfo
        """
        # Extract files from patch parts
        patched_files = set()
        for msg in messages:
            for part in msg.get("parts", []):
                if part.get("type") == "patch":
                    for file in part.get("files", []):
                        # Relativize path if worktree provided
                        if worktree:
                            try:
                                rel_path = os.path.relpath(file, worktree)
                                patched_files.add(rel_path)
                            except ValueError:
                                patched_files.add(file)
                        else:
                            patched_files.add(file)
        
        # Compute diffs from snapshot parts
        diffs = await cls._compute_diff_from_snapshots(messages)
        
        # Filter to only files that were patched
        relevant_diffs = [d for d in diffs if d.file in patched_files]
        
        summary = SessionSummaryInfo(
            additions=sum(d.additions for d in relevant_diffs),
            deletions=sum(d.deletions for d in relevant_diffs),
            files=len(relevant_diffs),
            diffs=relevant_diffs if relevant_diffs else None,
        )
        
        # Update session with summary
        try:
            from flocks.session.session import Session
            session = await Session.get_by_id(session_id)
            if session:
                await Session.update(session.project_id, session_id, summary={
                    "additions": summary.additions,
                    "deletions": summary.deletions,
                    "files": summary.files,
                })
        except Exception as e:
            log.warn("summary.session.update_error", {"error": str(e)})
        
        # Store diffs for later retrieval
        try:
            from flocks.storage.storage import Storage
            await Storage.set(
                f"session_diff:{session_id}",
                [d.model_dump() for d in relevant_diffs],
                "session_diff"
            )
        except Exception as e:
            log.warn("summary.session.storage_error", {"error": str(e)})
        
        log.info("summary.session", {
            "session_id": session_id,
            "additions": summary.additions,
            "deletions": summary.deletions,
            "files": summary.files,
        })
        
        return summary
    
    @classmethod
    async def _summarize_message(
        cls,
        session_id: str,
        message_id: str,
        messages: List[Dict[str, Any]],
    ) -> MessageSummaryInfo:
        """
        Generate message-level summary
        
        Computes diffs for a specific message exchange and generates title.
        
        Args:
            session_id: Session ID
            message_id: User message ID
            messages: All session messages
            
        Returns:
            MessageSummaryInfo
        """
        # Filter to the user message and its assistant responses
        relevant_msgs = [
            m for m in messages
            if m.get("id") == message_id or (
                m.get("role") == "assistant" and 
                m.get("parentID") == message_id
            )
        ]
        
        # Compute diffs for this exchange
        diffs = await cls._compute_diff_from_snapshots(relevant_msgs)
        
        # Get user message for title generation
        user_msg = next((m for m in relevant_msgs if m.get("id") == message_id), None)
        
        title = None
        if user_msg:
            # Find first non-synthetic text part
            text_part = next(
                (p for p in user_msg.get("parts", []) 
                 if p.get("type") == "text" and not p.get("synthetic")),
                None
            )
            if text_part and text_part.get("text"):
                title = await cls._generate_title_with_llm(
                    session_id=session_id,
                    text=text_part["text"],
                    model_info=user_msg.get("model"),
                )
        
        summary = MessageSummaryInfo(
            title=title,
            diffs=diffs,
        )
        
        # Update user message with summary
        # In TS this is stored in MessageV2.User.summary
        
        log.info("summary.message", {
            "session_id": session_id,
            "message_id": message_id,
            "title": title,
            "diffs": len(diffs),
        })
        
        return summary
    
    @classmethod
    async def _generate_title_with_llm(
        cls,
        session_id: str,
        text: str,
        model_info: Optional[Dict[str, str]] = None,
        max_length: int = 100,
    ) -> Optional[str]:
        """
        Generate a title using LLM
        
        Uses a "title" agent if available, otherwise falls back to
        a small/fast model for title generation.
        
        Args:
            session_id: Session ID
            text: Text to summarize into title
            model_info: Model info dict with provider_id and model_id
            max_length: Maximum title length
            
        Returns:
            Generated title or None
        """
        if not text:
            return None
        
        # Try to use LLM for title generation
        try:
            from flocks.provider.provider import Provider, ChatMessage
            from flocks.agent.registry import Agent
            
            # Try to get title agent
            title_agent = await Agent.get("title")
            
            # Determine model to use
            provider_id = None
            model_id = None
            
            if title_agent and hasattr(title_agent, "model") and title_agent.model:
                provider_id = title_agent.model.get("provider_id")
                model_id = title_agent.model.get("model_id")
            elif model_info:
                provider_id = model_info.get("provider_id") or model_info.get("providerID")
                model_id = model_info.get("model_id") or model_info.get("modelID")
            
            if not provider_id or not model_id:
                # Fall back to simple title extraction
                return cls._simple_title(text, max_length)
            
            # Generate title with LLM
            messages = [
                ChatMessage(
                    role="user",
                    content=f"Generate a short, descriptive title (max {max_length} chars) for this text. "
                            f"Output only the title, nothing else:\n\n{text[:1000]}"
                )
            ]
            
            response = await Provider.chat(
                model_id=model_id,
                messages=messages,
                max_tokens=50,
                temperature=0.7,
            )
            
            if response and response.content:
                # Clean up the title
                title = response.content.strip()
                # Remove thinking tags if present
                title = title.replace("<think>", "").replace("</think>", "").strip()
                # Get first non-empty line
                for line in title.split("\n"):
                    line = line.strip()
                    if line:
                        title = line
                        break
                
                # Truncate if needed
                if len(title) > max_length:
                    title = title[:max_length - 3] + "..."
                
                log.info("summary.title.generated", {"title": title})
                return title
                
        except Exception as e:
            log.warn("summary.title.llm_error", {"error": str(e)})
        
        # Fall back to simple extraction
        return cls._simple_title(text, max_length)
    
    @classmethod
    def _simple_title(cls, text: str, max_length: int = 100) -> Optional[str]:
        """
        Simple title extraction from first line
        
        Args:
            text: Text to extract title from
            max_length: Maximum title length
            
        Returns:
            Extracted title or None
        """
        if not text:
            return None
        
        first_line = text.strip().split('\n')[0].strip()
        
        if len(first_line) <= max_length:
            return first_line
        
        return first_line[:max_length - 3] + "..."
    
    @classmethod
    async def generate_title(
        cls,
        text: str,
        max_length: int = 50,
    ) -> Optional[str]:
        """
        Generate a title for the given text (simple version)
        
        Args:
            text: Text to generate title from
            max_length: Maximum title length
            
        Returns:
            Generated title or None
        """
        return cls._simple_title(text, max_length)
    
    @classmethod
    async def _compute_diff_from_snapshots(
        cls,
        messages: List[Dict[str, Any]],
    ) -> List[FileDiff]:
        """
        Compute file diffs from snapshot parts in messages
        
        Scans for step-start and step-finish parts to find
        snapshot boundaries, then computes diffs between them.
        
        Args:
            messages: Messages to scan
            
        Returns:
            List of FileDiff
        """
        from_snapshot: Optional[str] = None
        to_snapshot: Optional[str] = None
        
        # Scan for snapshot boundaries
        for msg in messages:
            for part in msg.get("parts", []):
                if not from_snapshot and part.get("type") == "step-start":
                    from_snapshot = part.get("snapshot")
                
                if part.get("type") == "step-finish" and part.get("snapshot"):
                    to_snapshot = part.get("snapshot")
        
        # If we have both snapshots, compute diff
        if from_snapshot and to_snapshot:
            return await cls._diff_snapshots(from_snapshot, to_snapshot)
        
        # Fall back to parsing patch parts
        return await cls._compute_diff_from_patches(messages)
    
    @classmethod
    async def _diff_snapshots(
        cls,
        from_snapshot: str,
        to_snapshot: str,
    ) -> List[FileDiff]:
        """
        Compute diff between two snapshots
        
        Args:
            from_snapshot: Starting snapshot ID
            to_snapshot: Ending snapshot ID
            
        Returns:
            List of FileDiff
        """
        # In TS this calls Snapshot.diffFull
        # For now, return empty list - full implementation would need
        # git-based snapshot diffing
        log.info("summary.diff_snapshots", {
            "from": from_snapshot,
            "to": to_snapshot,
        })
        return []
    
    @classmethod
    async def _compute_diff_from_patches(
        cls,
        messages: List[Dict[str, Any]],
    ) -> List[FileDiff]:
        """
        Compute file diffs from patch parts
        
        Scans for patch parts and extracts diff information.
        
        Args:
            messages: Messages to scan
            
        Returns:
            List of FileDiff
        """
        diffs: Dict[str, FileDiff] = {}
        
        for msg in messages:
            for part in msg.get("parts", []):
                if part.get("type") == "patch":
                    for file in part.get("files", []):
                        if file not in diffs:
                            diffs[file] = FileDiff(file=file)
                        
                        # Parse diff content if available
                        diff_content = part.get("diff", "")
                        if diff_content:
                            adds, dels = cls._parse_diff_stats(diff_content)
                            diffs[file].additions += adds
                            diffs[file].deletions += dels
        
        return list(diffs.values())
    
    @classmethod
    async def compute_diff(
        cls,
        messages: List[Dict[str, Any]],
    ) -> List[FileDiff]:
        """
        Compute file diffs from messages (public API)
        
        Args:
            messages: Messages to scan
            
        Returns:
            List of FileDiff
        """
        return await cls._compute_diff_from_patches(messages)
    
    @classmethod
    def _parse_diff_stats(cls, diff_content: str) -> Tuple[int, int]:
        """
        Parse diff content to get additions/deletions
        
        Args:
            diff_content: Diff string (unified format)
            
        Returns:
            Tuple of (additions, deletions)
        """
        additions = 0
        deletions = 0
        
        for line in diff_content.split('\n'):
            if line.startswith('+') and not line.startswith('+++'):
                additions += 1
            elif line.startswith('-') and not line.startswith('---'):
                deletions += 1
        
        return additions, deletions
    
    @classmethod
    async def diff(
        cls,
        session_id: str,
        message_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get stored session or message diff.
        
        Args:
            session_id: Session ID
            message_id: Optional message ID for message-specific diff
            
        Returns:
            List of file diff dicts
        """
        from flocks.storage.storage import Storage
        
        try:
            if message_id:
                # Get message-specific diff
                key = f"message_diff:{session_id}:{message_id}"
                data = await Storage.get(key, list)
                if data:
                    return data
            
            # Fall back to session diff
            key = f"session_diff:{session_id}"
            data = await Storage.get(key, list)
            return data if data else []
        except Exception as _e:
            log.debug("summary.diffs.get_failed", {"session_id": session_id, "error": str(_e)})
            return []
    
    @classmethod
    async def get_session_diff(
        cls,
        session_id: str,
    ) -> List[FileDiff]:
        """
        Get stored session diff (legacy API)
        
        Args:
            session_id: Session ID
            
        Returns:
            List of FileDiff
        """
        return await cls.diff(session_id)
