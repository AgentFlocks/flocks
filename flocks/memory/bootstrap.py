"""
Memory Bootstrap - Load memory files at session start

Implements OpenClaw-style memory loading:
1. MEMORY.md - Main long-term memory (auto-injected)
2. memory/daily/YYYY-MM-DD.md - Daily notes (agent reads today + yesterday)
3. memory_search tool - Search all history
"""

from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime, timedelta

from flocks.utils.file import File
from flocks.utils.log import Log

log = Log.create(service="memory.bootstrap")

# File names
MEMORY_FILENAME = "MEMORY.md"
MEMORY_ALT_FILENAME = "memory.md"

# Default instructions for agent (similar to OpenClaw's AGENTS.md)
# Uses global storage paths for Flocks
MEMORY_INSTRUCTIONS = """
## Memory System Guidance

You have access to a persistent memory system for continuity across sessions.
Memory is stored in a global location and accessible across all your sessions.

### Files Available:
1. `MEMORY.md` - Your long-term curated memory (already injected above)
2. `daily/{today}.md` - Today's notes (read using memory tools if needed)
3. `daily/{yesterday}.md` - Yesterday's notes (read using memory tools if needed)

### When to Write Memory:
- **Daily notes**: Use path `daily/YYYY-MM-DD.md` - Raw logs of what happened today
- **Long-term**: Use path `MEMORY.md` - Curated memories, decisions, lessons learned
- Write memories BEFORE the session ends, especially if important work was done
- If someone says "remember this", write it down immediately using memory tools

### Memory Best Practices:
- Use `daily/YYYY-MM-DD.md` for daily logs (system auto-creates if needed)
- Update `MEMORY.md` for important, lasting information
- Use `memory_search` tool to find information from all past memories
- Review old daily files and distill key points into MEMORY.md
- Don't keep secrets unless explicitly asked

### Available Tools:
- `memory_search` - Search all memories semantically
- `memory_write` - Write to memory files (daily or MEMORY.md)
- Standard `read`/`write` tools also work with memory paths
""".strip()


class MemoryBootstrap:
    """
    Bootstrap memory files at session start
    
    Uses Flocks' global memory storage: ~/.flocks/data/memory/
    """
    
    def __init__(self):
        """Initialize memory bootstrap using global storage"""
        from flocks.config import Config
        
        # Use global data directory (matching Flocks' architecture)
        data_dir = Config.get_data_path()
        self.memory_dir = data_dir / "memory"
        self.daily_dir = self.memory_dir / "daily"
    
    async def load_main_memory(self) -> Optional[Dict[str, Any]]:
        """
        Load main MEMORY.md file from .flocks/memory/
        
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
        Load daily memory files from .flocks/memory/daily/
        
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
        
        Creates:
        - .flocks/memory/
        - .flocks/memory/daily/
        - .flocks/memory/MEMORY.md (if not exists)
        """
        try:
            # Create directories
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self.daily_dir.mkdir(parents=True, exist_ok=True)
            
            # Create MEMORY.md if it doesn't exist
            memory_file = self.memory_dir / MEMORY_FILENAME
            if not memory_file.exists():
                initial_content = """# Long-Term Memory

This is your curated long-term memory file. Store important information here:

## Key Facts
- 

## Decisions & Preferences
- 

## Lessons Learned
- 

## Important Context
- 
"""
                memory_file.write_text(initial_content, encoding='utf-8')
                log.info("bootstrap.created_memory_file", {
                    "path": MEMORY_FILENAME,
                })
            
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
        
        instructions = MEMORY_INSTRUCTIONS.replace("{today}", today)
        instructions = instructions.replace("{yesterday}", yesterday)
        
        return instructions
    
    async def bootstrap(
        self,
        load_main: bool = True,
        load_daily: bool = True,
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
        
        result = {
            "main_memory": None,
            "daily_memories": [],
            "instructions": self.get_agent_instructions(today=today_str, yesterday=yesterday_str),
            "today": today_str,
            "yesterday": yesterday_str,
        }
        
        if load_main:
            main = await self.load_main_memory()
            result["main_memory"] = main
        
        if load_daily:
            dailies = await self.load_daily_memories(days_back=days_back, today=today_str)
            result["daily_memories"] = dailies
        
        log.info("bootstrap.complete", {
            "has_main": result["main_memory"] is not None,
            "daily_count": len(result["daily_memories"]),
        })
        
        return result
