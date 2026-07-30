"""
Tests for OpenClaw-style memory integration

Tests the new memory bootstrap, daily files, and flush mechanisms.
"""

import pytest
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

from flocks.memory import DailyMemory, MemoryBootstrap, MemoryFlush
from flocks.memory.config import MemoryAutoFlushConfig


class TestDailyMemory:
    """Test DailyMemory class"""
    
    @pytest.mark.asyncio
    async def test_ensure_structure(self):
        """Test directory structure creation"""
        daily = DailyMemory()
        await daily.ensure_structure()
        
        assert daily.daily_dir.exists()
        assert daily.daily_dir.is_dir()
    
    @pytest.mark.asyncio
    async def test_get_today_path(self):
        """Test today's file path generation"""
        daily = DailyMemory()
        today = datetime.now().strftime("%Y-%m-%d")
        
        path = daily.get_today_path()
        assert str(path).endswith(f"{today}.md")
        
        # Test with specific date
        path = daily.get_today_path("2026-02-09")
        assert str(path).endswith("2026-02-09.md")
    
    def test_get_relative_path(self):
        """Test relative path generation"""
        daily = DailyMemory()
        today = datetime.now().strftime("%Y-%m-%d")
        
        rel_path = daily.get_relative_path()
        assert rel_path == f"daily/{today}.md"
        
        rel_path = daily.get_relative_path("2026-02-09")
        assert rel_path == "daily/2026-02-09.md"
    
    @pytest.mark.asyncio
    async def test_write_and_read_daily(self):
        """Test writing and reading daily files"""
        daily = DailyMemory()
        test_date = "2026-02-09"
        test_content = "## Test Entry\n\nThis is a test."
        
        # Write
        rel_path = await daily.write_daily(
            content=test_content,
            date=test_date,
            append=False  # Overwrite mode
        )
        assert rel_path == f"daily/{test_date}.md"
        
        # Read
        content = await daily.read_daily(test_date)
        assert content == test_content
        
        # Test append
        append_content = "\n\n## Another Entry\n\nAppended content."
        await daily.write_daily(
            content=append_content,
            date=test_date,
            append=True
        )
        
        full_content = await daily.read_daily(test_date)
        assert test_content in full_content
        assert "Appended content" in full_content
    
    @pytest.mark.asyncio
    async def test_exists(self):
        """Test file existence check"""
        daily = DailyMemory()
        test_date = "2026-02-09"
        
        # Write a file
        await daily.write_daily("test", date=test_date, append=False)
        
        # Check existence
        assert await daily.exists(test_date)
        assert not await daily.exists("2099-01-01")
    
    def test_list_daily_files(self):
        """Test listing daily files"""
        daily = DailyMemory()
        
        # This will list actual files if any exist
        files = daily.list_daily_files()
        assert isinstance(files, list)
        
        # Files should be sorted by date (most recent first)
        if len(files) > 1:
            for i in range(len(files) - 1):
                assert files[i] >= files[i + 1]


class TestMemoryBootstrap:
    """Test MemoryBootstrap class"""
    
    @pytest.mark.asyncio
    async def test_create_memory_structure(self):
        """Test memory structure creation"""
        bootstrap = MemoryBootstrap()
        await bootstrap.create_memory_structure()
        
        assert bootstrap.memory_dir.exists()
        assert bootstrap.daily_dir.exists()
        
        # Check if MEMORY.md was created
        memory_file = bootstrap.memory_dir / "MEMORY.md"
        assert memory_file.exists()
    
    @pytest.mark.asyncio
    async def test_load_main_memory(self):
        """Test loading main MEMORY.md"""
        bootstrap = MemoryBootstrap()
        await bootstrap.create_memory_structure()
        
        result = await bootstrap.load_main_memory()
        assert result is not None
        assert "path" in result
        assert "content" in result
        assert result["inject"] is True
    
    def test_get_daily_memory_paths(self):
        """Test daily memory path generation"""
        bootstrap = MemoryBootstrap()
        
        # Get today + yesterday
        paths = bootstrap.get_daily_memory_paths(days_back=1, today="2026-02-09")
        assert len(paths) == 2
        assert "daily/2026-02-09.md" in paths  # Today
        assert "daily/2026-02-08.md" in paths  # Yesterday
        
        # Get only today
        paths = bootstrap.get_daily_memory_paths(days_back=0, today="2026-02-09")
        assert len(paths) == 1
        assert paths[0] == "daily/2026-02-09.md"
    
    @pytest.mark.asyncio
    async def test_load_daily_memories(self):
        """Test loading daily memory files"""
        bootstrap = MemoryBootstrap()
        await bootstrap.create_memory_structure()
        
        # Create some test daily files
        daily = DailyMemory()
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        await daily.write_daily("Today's notes", date=today, append=False)
        await daily.write_daily("Yesterday's notes", date=yesterday, append=False)
        
        # Load
        results = await bootstrap.load_daily_memories(days_back=1)
        assert len(results) >= 1  # At least today
        
        # Check structure
        for result in results:
            assert "path" in result
            assert "content" in result
            assert "abs_path" in result
    
    def test_get_agent_instructions(self):
        """Test agent instructions generation"""
        bootstrap = MemoryBootstrap()
        
        instructions = bootstrap.get_agent_instructions(
            today="2026-02-09",
            yesterday="2026-02-08"
        )
        
        assert "Memory System" in instructions
        assert "MEMORY.md" in instructions
        assert "daily/" in instructions
        assert "YYYY-MM-DD" in instructions
        assert "daily/2026-02-09.md" in instructions
        assert "daily/2026-02-08.md" in instructions
        assert "memory_search" in instructions
        assert "{memory_root}" not in instructions
        assert "On-disk memory root" in instructions
    
    @pytest.mark.asyncio
    async def test_bootstrap(self):
        """Test complete bootstrap process"""
        bootstrap = MemoryBootstrap()
        
        result = await bootstrap.bootstrap(
            load_main=True,
            load_daily=True,
            days_back=1
        )
        
        assert "main_memory" in result
        assert "daily_memories" in result
        assert "instructions" in result
        assert "today" in result
        assert "yesterday" in result
        
        # Check instructions
        assert result["instructions"]
        assert "Memory System" in result["instructions"]


class TestMemoryFlush:
    """Test MemoryFlush class"""
    
    def test_calculate_threshold(self):
        """Test threshold calculation"""
        threshold = MemoryFlush.calculate_threshold(
            context_window=200_000,
            reserve_tokens=2000,
            trigger_tokens=4000
        )
        assert threshold == 194_000
        
        # Edge case: threshold should not be negative
        threshold = MemoryFlush.calculate_threshold(
            context_window=1000,
            reserve_tokens=500,
            trigger_tokens=600
        )
        assert threshold == 0  # max(0, 1000 - 500 - 600)
    
    def test_should_trigger(self):
        """Test should_trigger logic"""
        config = MemoryAutoFlushConfig(
            enabled=True,
            reserve_tokens=2000,
            trigger_tokens=4000
        )
        
        # Below threshold - should not trigger
        assert not MemoryFlush.should_trigger(
            total_tokens=100_000,
            context_window=200_000,
            config=config,
            last_flush_compaction=None,
            current_compaction=0
        )
        
        # Above threshold - should trigger
        assert MemoryFlush.should_trigger(
            total_tokens=195_000,
            context_window=200_000,
            config=config,
            last_flush_compaction=None,
            current_compaction=0
        )
        
        # Already flushed in this compaction - should not trigger
        assert not MemoryFlush.should_trigger(
            total_tokens=195_000,
            context_window=200_000,
            config=config,
            last_flush_compaction=0,
            current_compaction=0
        )
        
        # New compaction cycle - should trigger again
        assert MemoryFlush.should_trigger(
            total_tokens=195_000,
            context_window=200_000,
            config=config,
            last_flush_compaction=0,
            current_compaction=1
        )
        
        # Disabled - should not trigger
        config_disabled = MemoryAutoFlushConfig(enabled=False)
        assert not MemoryFlush.should_trigger(
            total_tokens=195_000,
            context_window=200_000,
            config=config_disabled,
            last_flush_compaction=None,
            current_compaction=0
        )
    
    def test_get_flush_prompts(self):
        """Test flush prompts generation"""
        config = MemoryAutoFlushConfig(
            system_prompt="Save memories now.",
            user_prompt="Write to daily/YYYY-MM-DD.md"
        )
        
        prompts = MemoryFlush.get_flush_prompts(config, today="2026-02-09")
        
        assert prompts["system_prompt"] == "Save memories now."
        assert "2026-02-09" in prompts["user_prompt"]
        assert prompts["date"] == "2026-02-09"
    
    def test_get_stats(self):
        """Test flush statistics"""
        config = MemoryAutoFlushConfig(
            enabled=True,
            reserve_tokens=2000,
            trigger_tokens=4000
        )
        
        stats = MemoryFlush.get_stats(
            total_tokens=195_000,
            context_window=200_000,
            config=config,
            last_flush_compaction=None,
            current_compaction=0
        )
        
        assert stats["enabled"] is True
        assert stats["total_tokens"] == 195_000
        assert stats["context_window"] == 200_000
        assert stats["threshold"] == 194_000
        assert stats["remaining_tokens"] == 0  # 195k >= 194k threshold
        assert stats["should_flush"] is True
        assert stats["current_compaction"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
