"""
Daily Memory File Manager

Manages daily memory files in .flocks/memory/daily/ directory.
Files are named by date: YYYY-MM-DD.md
"""

from typing import Optional
from pathlib import Path
from datetime import datetime

from flocks.utils.file import File
from flocks.utils.log import Log

log = Log.create(service="memory.daily")


class DailyMemory:
    """
    Daily memory file manager
    
    Manages daily/YYYY-MM-DD.md files in global memory storage.
    Uses Flocks' global storage: ~/.flocks/data/memory/daily/
    """
    
    def __init__(self):
        """Initialize daily memory manager using global storage"""
        from flocks.config import Config
        
        # Use global data directory (matching Flocks' architecture)
        data_dir = Config.get_data_path()
        self.memory_dir = data_dir / "memory"
        self.daily_dir = self.memory_dir / "daily"
    
    async def ensure_structure(self) -> None:
        """
        Ensure daily/ directory structure exists in global memory storage
        
        Creates necessary directories if they don't exist.
        """
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self.daily_dir.mkdir(parents=True, exist_ok=True)
            
            log.info("daily.ensure_structure", {
                "daily_dir": str(self.daily_dir),
            })
        
        except Exception as e:
            log.error("daily.ensure_structure_failed", {
                "error": str(e),
                "daily_dir": str(self.daily_dir),
            })
            raise
    
    def get_today_path(self, date: Optional[str] = None) -> Path:
        """
        Get today's memory file path
        
        Args:
            date: Date string in YYYY-MM-DD format (default: today)
            
        Returns:
            Path to daily memory file
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        return self.daily_dir / f"{date}.md"
    
    def get_relative_path(self, date: Optional[str] = None) -> str:
        """
        Get relative path for memory system
        
        Args:
            date: Date string in YYYY-MM-DD format (default: today)
            
        Returns:
            Relative path string (from memory root)
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        return f"daily/{date}.md"
    
    async def write_daily(
        self,
        content: str,
        date: Optional[str] = None,
        append: bool = True,
    ) -> str:
        """
        Write to daily memory file
        
        Args:
            content: Content to write
            date: Date string in YYYY-MM-DD format (default: today)
            append: Whether to append (True) or overwrite (False)
            
        Returns:
            Relative path to written file
        """
        # Ensure structure exists
        await self.ensure_structure()
        
        # Get file path
        file_path = self.get_today_path(date)
        rel_path = self.get_relative_path(date)
        
        try:
            if append and file_path.exists():
                # Append mode
                file_content = await File.read(str(file_path))
                existing = file_content.content if hasattr(file_content, 'content') else str(file_content)
                
                # Add newline if existing content doesn't end with one
                if existing and not existing.endswith('\n'):
                    content = '\n' + content
                
                new_content = existing + content
                file_path.write_text(new_content, encoding='utf-8')
            else:
                # Write/overwrite mode
                file_path.write_text(content, encoding='utf-8')
            
            log.info("daily.write", {
                "path": rel_path,
                "length": len(content),
                "append": append,
            })
            
            return rel_path
        
        except Exception as e:
            log.error("daily.write_failed", {
                "path": rel_path,
                "error": str(e),
            })
            raise
    
    async def read_daily(self, date: str) -> Optional[str]:
        """
        Read specific daily file
        
        Args:
            date: Date string in YYYY-MM-DD format
            
        Returns:
            File content or None if not found
        """
        file_path = self.get_today_path(date)
        
        try:
            if not file_path.exists():
                log.debug("daily.read_not_found", {"date": date})
                return None
            
            file_content = await File.read(str(file_path))
            content = file_content.content if hasattr(file_content, 'content') else str(file_content)
            
            log.debug("daily.read", {
                "date": date,
                "length": len(content) if content else 0,
            })
            
            return content
        
        except Exception as e:
            log.error("daily.read_failed", {
                "date": date,
                "error": str(e),
            })
            return None
    
    async def exists(self, date: str) -> bool:
        """
        Check if daily file exists
        
        Args:
            date: Date string in YYYY-MM-DD format
            
        Returns:
            True if file exists
        """
        file_path = self.get_today_path(date)
        return file_path.exists()
    
    def list_daily_files(self) -> list[str]:
        """
        List all daily memory files
        
        Returns:
            List of date strings (YYYY-MM-DD)
        """
        try:
            if not self.daily_dir.exists():
                return []
            
            files = []
            for file_path in self.daily_dir.glob("*.md"):
                # Extract date from filename
                date = file_path.stem
                # Validate YYYY-MM-DD format
                if len(date) == 10 and date[4] == '-' and date[7] == '-':
                    files.append(date)
            
            # Sort by date
            files.sort(reverse=True)  # Most recent first
            
            return files
        
        except Exception as e:
            log.error("daily.list_failed", {"error": str(e)})
            return []
