"""
Memory Bootstrap - Load memory files at session start

Implements filesystem-managed loading with the Hermes Agent USER/Memory split:
1. USER.md - Stable user identity and preferences (auto-injected)
2. MEMORY.md - Global cross-project memory (auto-injected)
3. projects/<project_id>/MEMORY.md - Registered Project memory (auto-injected)
4. daily/YYYY-MM-DD.md - Daily notes for any calendar date
5. memory_search tool - Search all visible memory and history
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from flocks.memory.paths import (
    GLOBAL_MEMORY_FILENAME,
    PROJECT_MEMORY_INITIAL_CONTENT,
    USER_FILENAME,
    is_registered_project_id,
)
from flocks.utils.file import File
from flocks.utils.log import Log

log = Log.create(service="memory.bootstrap")

# File names
MEMORY_FILENAME = GLOBAL_MEMORY_FILENAME
MEMORY_ALT_FILENAME = "memory.md"

INITIAL_USER_PROFILE = """# User Profile

## Identity and Context

## Communication Preferences

## Working Style

## Technical Level
"""

# Default instructions informed by Hermes Agent and MiMo-Code memory prompts.
# Uses global storage paths for Flocks
MEMORY_INSTRUCTIONS = """
## Memory System Guidance

You have access to a persistent memory system for continuity across sessions.
On-disk memory root (absolute path): `{memory_root}`.
`USER.md` and Global `MEMORY.md` follow the open-source Hermes Agent split:
USER describes the user; Memory contains the agent's durable notes.

### Memory Layers:
1. `{memory_root}/USER.md` - Who the user is: stable identity, communication preferences, expectations, working style, and technical level (already injected above)
2. `{memory_root}/MEMORY.md` - The agent's global notes: cross-project environment and tool facts, lessons and corrections, and external references (already injected above)
{project_file_instruction}
4. `{memory_root}/daily/YYYY-MM-DD.md` - Lifecycle journal used as evidence for later consolidation. It is searchable but not curated or injected.
5. Current examples: `{memory_root}/daily/{today}.md` and `{memory_root}/daily/{yesterday}.md`.

### Managing Memory Files:
- The injected USER, Global, and Project files are a snapshot for this run. Read the file again before changing it.
- Use `read`, `glob`, and `grep` to inspect Memory explicitly, and `memory_search` for indexed recall across all projects.
- Use `write` only to create a missing curated Memory file. Use `edit` for precise entry-level changes to an existing curated file.
- Never write or edit `daily/`; only the Session lifecycle may append Daily entries.
- **User profile**: Maintain `{memory_root}/USER.md` only for facts about the user.
- **Global agent notes**: Maintain `{memory_root}/MEMORY.md` only for knowledge that remains useful across projects.
{project_write_instruction}
- If the user explicitly asks you to remember something, update the narrowest appropriate curated file without interrupting the current task.

### Memory Write Decision:
- Save information that is likely to reduce future user steering or prevent the same correction from being needed again.
- Save only stable user facts, non-derivable project constraints, explicit corrections, and verified reusable experience.
- Classify each candidate in this order:
  1. If it contains secrets, credentials, guesses, transient task state, plans, one-off results, or facts that can be cheaply rediscovered from source code, configuration, or other authoritative files, do not save it.
  2. If it describes how to repeatedly perform a task, it belongs in a Skill rather than Memory.
  3. If it describes the user, including identity or preferences, store it in `USER.md`.
  4. If it applies only to the current project, store it in Project `MEMORY.md`.
  5. If it is declarative Agent or environment knowledge that applies across projects, store it in Global `MEMORY.md`.
  6. If its destination is unclear, its evidence is weak, or equivalent knowledge already exists, make no change.
- Give each accepted item exactly one canonical destination. Do not duplicate the same knowledge across `USER.md`, Global `MEMORY.md`, and Project `MEMORY.md`.
- After choosing the destination file, use exactly one section:
  - Global `MEMORY.md / Environment and Tools`: stable cross-project facts about the Agent's environment, tools, and integrations.
  - Global `MEMORY.md / Lessons and Corrections`: cross-project conventions, verified tool quirks, successful practices, corrections, and reusable lessons.
  - Global `MEMORY.md / References`: pointers to external systems or authoritative sources that apply across projects; store where to look, not copied content.
  - Project `MEMORY.md / Project Context`: current-project goals, decisions, constraints, and durable facts that are not cheaply derivable from authoritative project files.
  - Project `MEMORY.md / Lessons and Corrections`: current-project guidance, successful practices, corrections, and reusable lessons.
  - Project `MEMORY.md / References`: pointers to external systems or authoritative sources that apply only to the current project; store where to look, not copied content.
- Write declarative facts, not commands to your future self. For example, `User prefers concise answers` is better than `Always answer concisely`.
- Check existing Memory first; merge or replace equivalent entries instead of duplicating them.
- Verify stale or conflicting Memory against current authoritative evidence before replacing or removing it.

### Available Tools:
- `memory_search` - Reconcile and search indexed Memory across all projects
- `read`, `glob`, `grep` - Inspect Memory files
- `write` - Create a missing Memory file
- `edit` - Precisely update an existing Memory file
""".strip()


class MemoryBootstrap:
    """
    Bootstrap memory files at session start
    
    Uses Flocks' global memory storage: ``<data_dir>/memory`` (see ``Config.get_data_path()``).
    """
    
    def __init__(self, project_id: str = "default"):
        """Initialize Memory bootstrap for a Session project."""
        from flocks.config import Config

        self.project_id = project_id
        self.has_project_memory = is_registered_project_id(project_id)
        data_dir = Config.get_data_path()
        self.memory_dir = data_dir / "memory"
        self.daily_dir = self.memory_dir / "daily"
        self.project_memory_path = (
            self.memory_dir / "projects" / project_id / MEMORY_FILENAME
            if self.has_project_memory
            else None
        )
    
    async def load_main_memory(self) -> Optional[Dict[str, Any]]:
        """
        Load the main MEMORY.md file from the configured data Memory root.
        
        Returns:
            Dict with path and content, or None if not found
        """
        # Try MEMORY.md first, then memory.md
        for filename in [MEMORY_FILENAME, MEMORY_ALT_FILENAME]:
            file_path = self.memory_dir / filename
            
            try:
                if not file_path.exists():
                    continue
                
                file_content = await File.read(str(file_path))
                content = file_content.content if hasattr(file_content, 'content') else str(file_content)
                
                if content:
                    log.info("bootstrap.loaded_main", {
                        "path": filename,
                        "size": len(content),
                    })
                    
                    return {
                        "path": filename,
                        "abs_path": str(file_path),
                        "content": content,
                        "inject": True,  # Should be injected to system prompt
                    }
            except Exception as e:
                log.warn("bootstrap.load_main_failed", {
                    "path": str(file_path),
                    "error": str(e),
                })
        
        log.debug("bootstrap.main_not_found")
        return None

    async def load_user_profile(self) -> Optional[Dict[str, Any]]:
        """Load the stable USER.md profile for prompt injection."""
        file_path = self.memory_dir / USER_FILENAME
        try:
            if not file_path.exists():
                return None
            file_content = await File.read(str(file_path))
            content = (
                file_content.content
                if hasattr(file_content, "content")
                else str(file_content)
            )
            if not content:
                return None
            log.info(
                "bootstrap.loaded_user_profile",
                {"path": USER_FILENAME, "size": len(content)},
            )
            return {
                "path": USER_FILENAME,
                "abs_path": str(file_path),
                "content": content,
                "inject": True,
            }
        except Exception as exc:
            log.warn(
                "bootstrap.load_user_profile_failed",
                {"path": str(file_path), "error": str(exc)},
            )
            return None

    async def load_project_memory(self) -> Optional[Dict[str, Any]]:
        """Load the current registered project's MEMORY.md."""
        if self.project_memory_path is None or not self.project_memory_path.exists():
            return None
        try:
            file_content = await File.read(str(self.project_memory_path))
            content = (
                file_content.content
                if hasattr(file_content, "content")
                else str(file_content)
            )
            if not content:
                return None
            relative = f"projects/{self.project_id}/{MEMORY_FILENAME}"
            log.info(
                "bootstrap.loaded_project_memory",
                {"path": relative, "size": len(content)},
            )
            return {
                "path": relative,
                "abs_path": str(self.project_memory_path),
                "content": content,
                "inject": True,
            }
        except Exception as exc:
            log.warn(
                "bootstrap.load_project_memory_failed",
                {"path": str(self.project_memory_path), "error": str(exc)},
            )
            return None
    
    def get_daily_memory_paths(
        self,
        days_back: int = 1,
        today: Optional[str] = None,
    ) -> List[str]:
        """
        Get paths for daily memory files
        
        Args:
            days_back: Number of days back to include (default: 1 = today + yesterday)
            today: Today's date (YYYY-MM-DD), defaults to current date
            
        Returns:
            List of relative paths to daily memory files
        """
        if today is None:
            today_date = datetime.now()
        else:
            today_date = datetime.strptime(today, "%Y-%m-%d")
        
        paths = []
        
        # Generate paths for today and previous days
        for i in range(days_back + 1):
            date = today_date - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            rel_path = f"daily/{date_str}.md"
            paths.append(rel_path)
        
        return paths
    
    async def load_daily_memories(
        self,
        days_back: int = 1,
        today: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Load Daily Memory files from the configured data Memory root.
        
        Args:
            days_back: Number of days back to load
            today: Today's date (YYYY-MM-DD)
            
        Returns:
            List of dicts with path and content for each file found
        """
        paths = self.get_daily_memory_paths(days_back=days_back, today=today)
        loaded = []
        
        for rel_path in paths:
            file_path = self.memory_dir / rel_path
            
            try:
                if not file_path.exists():
                    log.debug("bootstrap.daily_not_found", {
                        "path": rel_path,
                    })
                    continue
                
                file_content = await File.read(str(file_path))
                content = file_content.content if hasattr(file_content, 'content') else str(file_content)
                
                if content:
                    loaded.append({
                        "path": rel_path,
                        "abs_path": str(file_path),
                        "content": content,
                    })
                    
                    log.info("bootstrap.loaded_daily", {
                        "path": rel_path,
                        "size": len(content),
                    })
            
            except Exception as e:
                log.warn("bootstrap.daily_load_failed", {
                    "path": rel_path,
                    "error": str(e),
                })
        
        return loaded
    
    async def create_memory_structure(self) -> None:
        """
        Create memory directory structure if it doesn't exist
        
        Registered projects also receive ``projects/<project_id>/MEMORY.md``.
        """
        try:
            # Create directories
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self.daily_dir.mkdir(parents=True, exist_ok=True)
            
            # Create MEMORY.md if it doesn't exist
            memory_file = self.memory_dir / MEMORY_FILENAME
            if not memory_file.exists():
                initial_content = """# Global Memory

## Environment and Tools

## Lessons and Corrections

## References
"""
                memory_file.write_text(initial_content, encoding='utf-8')
                log.info("bootstrap.created_memory_file", {
                    "path": MEMORY_FILENAME,
                })

            user_file = self.memory_dir / USER_FILENAME
            if not user_file.exists():
                user_file.write_text(INITIAL_USER_PROFILE, encoding="utf-8")
                log.info(
                    "bootstrap.created_user_profile",
                    {"path": USER_FILENAME},
                )

            if self.project_memory_path is not None:
                self.project_memory_path.parent.mkdir(parents=True, exist_ok=True)
                if not self.project_memory_path.exists():
                    self.project_memory_path.write_text(
                        PROJECT_MEMORY_INITIAL_CONTENT,
                        encoding="utf-8",
                    )
                    log.info(
                        "bootstrap.created_project_memory",
                        {
                            "path": (
                                f"projects/{self.project_id}/{MEMORY_FILENAME}"
                            )
                        },
                    )
            
            log.info("bootstrap.structure_ready", {
                "memory_dir": str(self.memory_dir),
                "daily_dir": str(self.daily_dir),
            })
        
        except Exception as e:
            log.error("bootstrap.create_structure_failed", {
                "error": str(e),
            })
            raise
    
    def get_agent_instructions(
        self,
        today: Optional[str] = None,
        yesterday: Optional[str] = None,
    ) -> str:
        """
        Get agent instructions with current dates filled in
        
        Args:
            today: Today's date (YYYY-MM-DD)
            yesterday: Yesterday's date (YYYY-MM-DD)
            
        Returns:
            Instructions string with dates filled in
        """
        if today is None:
            today_date = datetime.now()
        else:
            today_date = datetime.strptime(today, "%Y-%m-%d")
        
        today = today_date.strftime("%Y-%m-%d")
        if yesterday is None:
            yesterday = (today_date - timedelta(days=1)).strftime("%Y-%m-%d")

        from flocks.config import Config

        memory_root = (Config.get_data_path() / "memory").resolve()
        instructions = MEMORY_INSTRUCTIONS.replace("{memory_root}", str(memory_root))
        if self.has_project_memory:
            project_file_instruction = (
                "3. `"
                f"{memory_root}/projects/{self.project_id}/MEMORY.md"
                "` - Current project context, lessons and corrections, and "
                "external references (already injected above)"
            )
            project_write_instruction = (
                "- **Project Memory**: Maintain `"
                f"{memory_root}/projects/{self.project_id}/MEMORY.md"
                "` for current project context, lessons and corrections, and "
                "external references"
            )
        else:
            project_file_instruction = (
                "3. Project Memory is unavailable because this is not a registered "
                "project Session"
            )
            project_write_instruction = (
                "- **Project long-term**: unavailable in this default Session; "
                "do not store project-only facts in Global Memory"
            )
        instructions = instructions.replace(
            "{project_file_instruction}",
            project_file_instruction,
        )
        instructions = instructions.replace(
            "{project_write_instruction}",
            project_write_instruction,
        )
        instructions = instructions.replace("{today}", today)
        instructions = instructions.replace("{yesterday}", yesterday)

        return instructions
    
    async def bootstrap(
        self,
        load_main: bool = True,
        load_daily: bool = False,
        days_back: int = 1,
    ) -> Dict[str, Any]:
        """
        Bootstrap all memory files
        
        Args:
            load_main: Whether to load MEMORY.md
            load_daily: Whether to load daily files
            days_back: Days of daily files to load (0=only today, 1=today+yesterday)
            
        Returns:
            Dict with loaded files and instructions
        """
        await self.create_memory_structure()
        
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        
        result: Dict[str, Any] = {
            "main_memory": None,
            "user_profile": None,
            "project_memory": None,
            "daily_memories": [],
            "instructions": self.get_agent_instructions(today=today_str, yesterday=yesterday_str),
            "today": today_str,
            "yesterday": yesterday_str,
        }
        
        if load_main:
            main = await self.load_main_memory()
            result["main_memory"] = main
            result["user_profile"] = await self.load_user_profile()
            result["project_memory"] = await self.load_project_memory()
        
        if load_daily:
            dailies = await self.load_daily_memories(days_back=days_back, today=today_str)
            result["daily_memories"] = dailies
        
        log.info("bootstrap.complete", {
            "has_main": result["main_memory"] is not None,
            "has_user_profile": result["user_profile"] is not None,
            "has_project_memory": result["project_memory"] is not None,
            "daily_count": len(result["daily_memories"]),
        })
        
        return result
