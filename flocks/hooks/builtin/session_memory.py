"""
Session Memory Hook - Auto-save session to memory system

Automatically saves session context to memory when /new command is triggered.
Inspired by OpenClaw's session-memory hook.
"""

from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timezone
import json

from flocks.hooks.types import HookEvent
from flocks.hooks.registry import register_hook
from flocks.session.recorder import Recorder
from flocks.memory.manager import MemoryManager
from flocks.memory.config import MemoryConfig
from flocks.config import Config
from flocks.utils.log import Log

log = Log.create(service="hooks.session_memory")


class SessionMemoryHook:
    """Session memory save hook"""
    
    # Configuration
    DEFAULT_MESSAGE_COUNT = 15  # Default: extract last 15 messages
    
    @staticmethod
    async def handler(event: HookEvent) -> None:
        """
        Hook handler
        
        Only triggers on command:new events
        """
        # Only handle command:new events
        if event.type != "command" or event.action != "new":
            return
        
        try:
            log.info("session_memory.triggered", {
                "session_id": event.session_id,
            })
            
            # Get configuration
            config = await Config.get()
            memory_config = getattr(config, 'memory', None)
            
            # Check if enabled
            if not memory_config or not memory_config.enabled:
                log.debug("session_memory.disabled")
                return
            
            # Check session_memory hook config
            hooks_config = getattr(memory_config, 'hooks', None)
            if not hooks_config:
                log.debug("session_memory.no_hooks_config")
                return
            
            hook_config = getattr(hooks_config, 'session_memory', None)
            if not hook_config or not getattr(hook_config, 'enabled', True):
                log.debug("session_memory.hook_disabled")
                return
            
            # Extract context
            context = event.context
            previous_session_id = context.get("previous_session_id")
            
            if not previous_session_id:
                log.debug("session_memory.no_previous_session")
                return
            
            # Execute save
            await SessionMemoryHook._save_session_to_memory(
                session_id=previous_session_id,
                context=context,
                config=memory_config,
                hook_config=hook_config,
            )
            
        except Exception as e:
            log.error("session_memory.handler_error", {
                "error": str(e),
                "session_id": event.session_id,
            })
    
    @staticmethod
    async def _save_session_to_memory(
        session_id: str,
        context: Dict[str, Any],
        config: Any,
        hook_config: Any,  # MemoryHooksSessionMemoryConfig
    ) -> None:
        """
        Save session to memory file
        
        Steps:
        1. Read session JSONL records
        2. Extract last N messages
        3. Generate slug using LLM
        4. Construct Markdown content
        5. Write to memory file (in ~/.flocks/data/memory/)
        """
        # 1. Read session messages
        messages = await SessionMemoryHook._read_session_messages(
            session_id=session_id,
            message_count=getattr(hook_config, 'message_count', SessionMemoryHook.DEFAULT_MESSAGE_COUNT),
        )
        
        if not messages:
            log.warn("session_memory.no_messages", {"session_id": session_id})
            return
        
        # 2. Generate slug
        slug = await SessionMemoryHook._generate_slug(
            messages=messages,
            session_id=session_id,
            config=config,
        )
        
        # 3. Construct Markdown content
        content = SessionMemoryHook._build_markdown_content(
            session_id=session_id,
            messages=messages,
            context=context,
        )
        
        # 4. Write to memory file (via MemoryManager)
        await SessionMemoryHook._write_to_memory(
            content=content,
            slug=slug,
            project_id=context.get("project_id", "default"),
            workspace_dir=context.get("workspace_dir", "."),
            config=config,
        )
    
    @staticmethod
    async def _read_session_messages(
        session_id: str,
        message_count: int,
    ) -> List[Dict[str, str]]:
        """
        Read recent messages from JSONL records
        
        Returns:
            List of {role: str, content: str}
        """
        try:
            # Get session record file path
            paths = Recorder.paths()
            session_file = paths.session_dir / f"{session_id}.jsonl"
            
            if not session_file.exists():
                log.warn("session_memory.file_not_found", {
                    "session_id": session_id,
                    "path": str(session_file),
                })
                return []
            
            # Read and parse JSONL
            messages = []
            content = session_file.read_text(encoding='utf-8')
            
            for line in content.strip().split('\n'):
                if not line.strip():
                    continue
                
                try:
                    entry = json.loads(line)
                    
                    # Extract session.message type entries
                    if entry.get('type') == 'session.message':
                        role = entry.get('role', '')
                        text = entry.get('text', '')
                        
                        # Only keep user and assistant messages
                        # Skip command messages (starting with /)
                        if role in ['user', 'assistant'] and text and not text.startswith('/'):
                            messages.append({
                                'role': role,
                                'content': text,
                            })
                
                except json.JSONDecodeError:
                    continue
            
            # Return last N messages
            recent_messages = messages[-message_count:] if messages else []
            
            log.debug("session_memory.messages_read", {
                "session_id": session_id,
                "total": len(messages),
                "recent": len(recent_messages),
            })
            
            return recent_messages
            
        except Exception as e:
            log.error("session_memory.read_messages_error", {
                "session_id": session_id,
                "error": str(e),
            })
            return []
    
    @staticmethod
    async def _generate_slug(
        messages: List[Dict[str, str]],
        session_id: str,
        config: Any,
    ) -> str:
        """
        Generate filename slug
        
        Prioritize LLM-generated descriptive name, fallback to timestamp
        
        Returns:
            slug string (e.g., "api-design" or "1430")
        """
        from flocks.hooks.builtin.slug_generator import generate_slug_via_llm
        
        # Try using LLM
        try:
            conversation = "\n".join([
                f"{msg['role']}: {msg['content']}"
                for msg in messages
            ])
            
            slug = await generate_slug_via_llm(
                conversation=conversation,
                config=config,
                session_id=session_id,
            )
            
            if slug:
                log.info("session_memory.slug_generated", {
                    "session_id": session_id,
                    "slug": slug,
                    "method": "llm",
                })
                return slug
        
        except Exception as e:
            log.warn("session_memory.slug_generation_failed", {
                "session_id": session_id,
                "error": str(e),
            })
        
        # Fallback: HHMMSS + short session hash for uniqueness
        now = datetime.now()
        session_hash = session_id[:6] if len(session_id) >= 6 else session_id
        slug = f"{now.strftime('%H%M%S')}-{session_hash}"
        
        log.info("session_memory.slug_generated", {
            "session_id": session_id,
            "slug": slug,
            "method": "timestamp",
        })
        
        return slug
    
    @staticmethod
    def _build_markdown_content(
        session_id: str,
        messages: List[Dict[str, str]],
        context: Dict[str, Any],
    ) -> str:
        """
        Construct Markdown format memory content
        
        Format (based on OpenClaw):
        # Session: YYYY-MM-DD HH:MM:SS UTC
        
        - **Session ID**: xxx
        - **Project**: xxx
        
        ## Conversation Summary
        
        user: ...
        assistant: ...
        """
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        
        # Build metadata section
        lines = [
            f"# Session: {date_str} {time_str} UTC",
            "",
            f"- **Session ID**: {session_id}",
        ]
        
        # Add optional context
        if context.get("project_id"):
            lines.append(f"- **Project**: {context['project_id']}")
        
        lines.extend(["", "## Conversation Summary", ""])
        
        # Add conversation content
        for msg in messages:
            # Truncate long messages
            content = msg['content']
            if len(content) > 2000:
                content = content[:2000] + "...[truncated]"
            
            lines.append(f"**{msg['role']}**: {content}")
            lines.append("")
        
        return "\n".join(lines)
    
    @staticmethod
    async def _write_to_memory(
        content: str,
        slug: str,
        project_id: str,
        workspace_dir: str,
        config: Any,
    ) -> None:
        """
        Write to memory file
        
        File path: ~/.flocks/data/memory/YYYY-MM-DD-slug.md
        
        Note: Flocks uses global path, different from OpenClaw's project-relative path.
        This design enables cross-project memory sharing and access.
        """
        try:
            # Ensure config is a proper MemoryConfig instance
            if isinstance(config, dict):
                memory_config = MemoryConfig(**config)
            elif isinstance(config, MemoryConfig):
                memory_config = config
            else:
                memory_config = MemoryConfig(enabled=True)

            memory_manager = MemoryManager.get_instance(
                project_id=project_id,
                workspace_dir=workspace_dir,
                config=memory_config,
            )
            
            await memory_manager.initialize()
            
            date_str = datetime.now().strftime("%Y-%m-%d")
            filename = f"{date_str}-{slug}.md"
            
            from flocks.config import Config as _Cfg
            _mem_root = _Cfg.get_data_path() / "memory"
            file_exists = (_mem_root / filename).exists()
            
            written_path = await memory_manager.write_memory(
                content=content,
                path=filename,
                append=file_exists,
            )
            
            log.info("session_memory.saved", {
                "path": written_path,
                "length": len(content),
            })
            
        except Exception as e:
            log.error("session_memory.write_error", {
                "error": str(e),
                "slug": slug,
            })


# Register hook
def register_session_memory_hook() -> None:
    """Register session memory hook"""
    register_hook(
        event_key="command:new",
        handler=SessionMemoryHook.handler,
        metadata={
            "name": "session-memory",
            "description": "Auto-save session to memory system",
            "priority": 100,
        },
    )
    
    log.info("session_memory.registered")
