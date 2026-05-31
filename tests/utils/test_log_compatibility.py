"""
Test log system compatibility with TypeScript implementation
"""

import pytest
import time
import tempfile
from pathlib import Path
from flocks.utils.log import Log, Logger, LogLevel, _RotatingTextWriter, rotate_log_file


class TestLoggerCompatibility:
    """Test that Python logging matches TypeScript exactly"""
    
    def test_log_levels(self):
        """Test log level constants match TypeScript"""
        assert LogLevel.DEBUG == "DEBUG"
        assert LogLevel.INFO == "INFO"
        assert LogLevel.WARN == "WARN"
        assert LogLevel.ERROR == "ERROR"
    
    def test_logger_creation(self):
        """Test logger creation with service tag"""
        logger = Log.create(service="test")
        assert logger is not None
        assert logger._tags.get("service") == "test"
    
    def test_logger_caching(self):
        """Test that loggers are cached by service name"""
        logger1 = Log.create(service="test-cache")
        logger2 = Log.create(service="test-cache")
        
        # Should return the same instance
        assert logger1 is logger2
    
    def test_logger_tag_method(self):
        """Test tag() method adds tags"""
        logger = Log.create(service="test")
        logger.tag("key1", "value1")
        logger.tag("key2", "value2")
        
        assert logger._tags["key1"] == "value1"
        assert logger._tags["key2"] == "value2"
    
    def test_logger_clone(self):
        """Test clone() creates independent copy"""
        logger1 = Log.create(service="test")
        logger1.tag("key", "value1")
        
        logger2 = logger1.clone()
        logger2.tag("key", "value2")
        
        # Original should be unchanged
        assert logger1._tags["key"] == "value1"
        assert logger2._tags["key"] == "value2"
    
    def test_message_format(self):
        """Test log message format matches TypeScript"""
        logger = Log.create(service="test")
        
        # Capture log output
        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        
        try:
            logger.info("test message", {"key": "value"})
            output = Log._writer.getvalue()
            
            # Check format: LEVEL timestamp +Xms key=value message
            assert "INFO" in output
            # Time diff should be present (any value is fine)
            import re
            assert re.search(r'\+\d+ms', output) is not None
            assert "service=test" in output
            assert "key=value" in output
            assert "test message" in output
            
            # Check timestamp format (YYYY-MM-DDTHH:MM:SS)
            assert "T" in output
            assert "-" in output
            assert ":" in output
        finally:
            Log._writer = old_stderr
    
    def test_timer_context(self):
        """Test time() context manager"""
        logger = Log.create(service="test")
        
        # Capture log output
        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        
        try:
            with logger.time("operation", {"extra": "data"}):
                time.sleep(0.01)  # Small delay
            
            output = Log._writer.getvalue()
            
            # Should have two log lines: started and completed
            lines = [line for line in output.split("\n") if line]
            assert len(lines) == 2
            
            # Check started log
            assert "status=started" in lines[0]
            assert "operation" in lines[0]
            
            # Check completed log
            assert "status=completed" in lines[1]
            assert "duration=" in lines[1]
            assert "operation" in lines[1]
        finally:
            Log._writer = old_stderr
    
    def test_timer_stop_method(self):
        """Test timer stop() method"""
        logger = Log.create(service="test")
        
        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        
        try:
            timer = logger.time("manual")
            timer.__enter__()
            time.sleep(0.01)
            timer.stop()
            
            output = Log._writer.getvalue()
            lines = [line for line in output.split("\n") if line]
            
            assert len(lines) == 2
            assert "status=started" in lines[0]
            assert "status=completed" in lines[1]
        finally:
            Log._writer = old_stderr
    
    def test_error_formatting(self):
        """Test error formatting with cause chain"""
        logger = Log.create(service="test")
        
        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        
        try:
            # Create error with cause chain
            try:
                try:
                    raise ValueError("Inner error")
                except ValueError as e:
                    raise RuntimeError("Outer error") from e
            except RuntimeError as error:
                logger.error("failed", {"error": error})
            
            output = Log._writer.getvalue()
            
            # Should contain both errors in the cause chain
            assert "Outer error" in output
            assert "Caused by:" in output
            assert "Inner error" in output
        finally:
            Log._writer = old_stderr
    
    def test_log_level_filtering(self):
        """Test log level filtering"""
        # Set to WARN level
        Log._level = LogLevel.WARN
        
        logger = Log.create(service="test")
        
        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        
        try:
            logger.debug("debug msg")  # Should not log
            logger.info("info msg")    # Should not log
            logger.warn("warn msg")    # Should log
            logger.error("error msg")  # Should log
            
            output = Log._writer.getvalue()
            
            assert "debug msg" not in output
            assert "info msg" not in output
            assert "warn msg" in output
            assert "error msg" in output
        finally:
            Log._writer = old_stderr
            Log._level = LogLevel.INFO  # Reset
    
    def test_none_value_filtering(self):
        """Test that None values are filtered from tags"""
        logger = Log.create(service="test")
        
        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        
        try:
            logger.info("message", {"key1": "value1", "key2": None, "key3": "value3"})
            output = Log._writer.getvalue()
            
            # key2 should not appear
            assert "key1=value1" in output
            assert "key2=" not in output
            assert "key3=value3" in output
        finally:
            Log._writer = old_stderr
    
    def test_object_serialization(self):
        """Test that objects are JSON serialized"""
        logger = Log.create(service="test")
        
        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        
        try:
            logger.info("message", {"data": {"nested": "value"}})
            output = Log._writer.getvalue()
            
            # Should contain JSON serialized object
            assert 'data={"nested": "value"}' in output or 'data={"nested":"value"}' in output
        finally:
            Log._writer = old_stderr

    def test_large_object_values_are_truncated(self, monkeypatch):
        """Test large objects are bounded before being written to logs."""
        logger = Log.create(service="test")

        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        monkeypatch.setenv("FLOCKS_LOG_VALUE_MAX_CHARS", "20")

        try:
            logger.info("message", {"data": {"payload": "x" * 100}})
            output = Log._writer.getvalue()

            assert "data=" in output
            assert "<truncated" in output
            assert len(output) < 200
        finally:
            Log._writer = old_stderr

    def test_rotate_log_file_keeps_bounded_backups(self, tmp_path: Path):
        """Test oversized runtime logs rotate before another process appends."""
        log_path = tmp_path / "backend.log"
        log_path.write_text("x" * 20, encoding="utf-8")

        rotate_log_file(log_path, max_bytes=10, backup_count=2)

        assert not log_path.exists()
        assert (tmp_path / "backend.log.1").read_text(encoding="utf-8") == "x" * 20

        log_path.write_text("y" * 20, encoding="utf-8")
        rotate_log_file(log_path, max_bytes=10, backup_count=2)

        assert (tmp_path / "backend.log.1").read_text(encoding="utf-8") == "y" * 20
        assert (tmp_path / "backend.log.2").read_text(encoding="utf-8") == "x" * 20

    def test_rotating_text_writer_rotates_during_log_writes(self, tmp_path: Path):
        """Test Log's writer rotates when the next line would exceed the limit."""
        log_path = tmp_path / "session.log"
        writer = _RotatingTextWriter(log_path, max_bytes=12, backup_count=1)

        try:
            writer.write("first\n")
            writer.flush()
            writer.write("second\n")
            writer.flush()
        finally:
            writer.close()

        assert log_path.read_text(encoding="utf-8") == "second\n"
        assert (tmp_path / "session.log.1").read_text(encoding="utf-8") == "first\n"
    
    def test_time_diff_calculation(self):
        """Test time difference calculation between logs"""
        logger = Log.create(service="test")
        
        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        Log._last_time = int(time.time() * 1000)
        
        try:
            logger.info("first")
            time.sleep(0.01)  # 10ms delay
            logger.info("second")
            
            output = Log._writer.getvalue()
            lines = [line for line in output.split("\n") if line]
            
            # First log should have small time diff
            assert "+0ms" in lines[0] or "+1ms" in lines[0] or "+2ms" in lines[0]
            
            # Second log should show ~10ms+ diff
            import re
            match = re.search(r'\+(\d+)ms', lines[1])
            if match:
                diff = int(match.group(1))
                assert diff >= 5  # At least 5ms (allowing for timing variance)
        finally:
            Log._writer = old_stderr
    
    def test_default_logger(self):
        """Test that Default logger exists and works"""
        assert Log.Default is not None
        assert Log.Default._tags.get("service") == "default"
        
        import io
        old_stderr = Log._writer
        Log._writer = io.StringIO()
        
        try:
            Log.Default.info("test")
            output = Log._writer.getvalue()
            assert "service=default" in output
        finally:
            Log._writer = old_stderr


@pytest.mark.asyncio
class TestLogInitialization:
    """Test log initialization"""
    
    async def test_init_creates_log_file(self):
        """Test that init creates log file"""
        # Use a temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Override log directory
            old_home = Path.home()
            import os
            os.environ["HOME"] = tmpdir
            
            try:
                await Log.init(print=False, dev=True, level=LogLevel.INFO)
                
                log_dir = Path(tmpdir) / ".flocks" / "logs"
                assert log_dir.exists()
                
                dev_log = log_dir / "dev.log"
                assert dev_log.exists()
            finally:
                # Restore
                os.environ["HOME"] = str(old_home)
                if Log._writer:
                    Log._writer.close()
                    Log._writer = None
    
    async def test_init_print_mode(self):
        """Test that init with print=True uses stderr"""
        await Log.init(print=True, level=LogLevel.INFO)
        
        # Should not create a file writer
        assert Log._writer is None
    
    async def test_log_file_path(self):
        """Test log file path method"""
        file_path = Log.file()
        assert file_path.endswith("flocks.log") or "flocks" in file_path

    async def test_cleanup_removes_rotated_siblings_for_old_timestamp_logs(self, tmp_path: Path):
        """Test cleanup deletes rotated backups for base files outside retention."""
        for day in range(11):
            base = tmp_path / f"2026-05-{day + 1:02d}T010203.log"
            base.write_text("base", encoding="utf-8")
            (tmp_path / f"{base.name}.1").write_text("rotated", encoding="utf-8")

        await Log._cleanup(tmp_path)

        assert not (tmp_path / "2026-05-01T010203.log").exists()
        assert not (tmp_path / "2026-05-01T010203.log.1").exists()
        assert (tmp_path / "2026-05-02T010203.log").exists()
        assert (tmp_path / "2026-05-02T010203.log.1").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
