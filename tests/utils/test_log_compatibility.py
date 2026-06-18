"""
Test log system compatibility with TypeScript implementation
"""

import pytest
import time
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import flocks.utils.log as log_module
from flocks.utils.log import Log, Logger, LogLevel


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
                
                today_dir = log_dir / datetime.now().date().isoformat()
                assert (today_dir / "flocks.log").exists()
                assert (today_dir / "errors.log").exists()
                assert not (log_dir / "dev.log").exists()
            finally:
                # Restore
                os.environ["HOME"] = str(old_home)
                if Log._writer:
                    Log._writer.close()
                    Log._writer = None
                if Log._error_writer:
                    Log._error_writer.close()
                    Log._error_writer = None

    async def test_init_uses_stable_main_log_file(self):
        """Test production logging appends to the daily flocks.log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_home = Path.home()
            import os
            os.environ["HOME"] = tmpdir

            try:
                await Log.init(print=False, dev=False, level=LogLevel.INFO)
                Log.Default.info("first")
                first_file = Path(Log.file())
                await Log.init(print=False, dev=False, level=LogLevel.INFO)
                Log.Default.info("second")

                log_dir = Path(tmpdir) / ".flocks" / "logs"
                today_dir = log_dir / datetime.now().date().isoformat()
                assert first_file == today_dir / "flocks.log"
                assert Path(Log.file()) == first_file
                assert not list(log_dir.glob("????-??-??T??????.log"))
                assert not list(log_dir.glob("*.log.1"))
                content = first_file.read_text(encoding="utf-8")
                assert "first" in content
                assert "second" in content
            finally:
                os.environ["HOME"] = str(old_home)
                if Log._writer:
                    Log._writer.close()
                    Log._writer = None
                if Log._error_writer:
                    Log._error_writer.close()
                    Log._error_writer = None

    async def test_warn_and_error_are_copied_to_errors_log(self):
        """Test warning and error lines are available in errors.log for quick triage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_home = Path.home()
            import os
            os.environ["HOME"] = tmpdir

            try:
                await Log.init(print=False, dev=False, level=LogLevel.INFO)
                logger = Log.create(service="error-copy")
                logger.info("info")
                logger.warn("warn")
                logger.error("error")

                log_dir = Path(tmpdir) / ".flocks" / "logs"
                today_dir = log_dir / datetime.now().date().isoformat()
                main_content = (today_dir / "flocks.log").read_text(encoding="utf-8")
                error_content = (today_dir / "errors.log").read_text(encoding="utf-8")
                assert "info" in main_content
                assert "warn" in main_content
                assert "error" in main_content
                assert "info" not in error_content
                assert "warn" in error_content
                assert "error" in error_content
            finally:
                os.environ["HOME"] = str(old_home)
                if Log._writer:
                    Log._writer.close()
                    Log._writer = None
                if Log._error_writer:
                    Log._error_writer.close()
                    Log._error_writer = None
    
    async def test_init_print_mode(self):
        """Test that init with print=True uses stderr"""
        await Log.init(print=True, level=LogLevel.INFO)
        
        # Should not create a file writer
        assert Log._writer is None
        assert Log._error_writer is None
    
    async def test_log_file_path(self):
        """Test log file path method"""
        file_path = Log.file()
        assert file_path.endswith("flocks.log") or "flocks" in file_path

    async def test_cleanup_removes_rotated_siblings_for_old_timestamp_logs(self, tmp_path: Path):
        """Test cleanup deletes legacy timestamp logs by age, not by file count."""
        old_stamp = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%dT%H%M%S")
        recent_stamp = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H%M%S")
        old_base = tmp_path / f"{old_stamp}.log"
        recent_base = tmp_path / f"{recent_stamp}.log"
        old_base.write_text("old", encoding="utf-8")
        (tmp_path / f"{old_base.name}.1").write_text("old rotated", encoding="utf-8")
        recent_base.write_text("recent", encoding="utf-8")
        (tmp_path / f"{recent_base.name}.1").write_text("recent rotated", encoding="utf-8")

        await Log._cleanup(tmp_path, retention_days=30)

        assert not old_base.exists()
        assert not (tmp_path / f"{old_base.name}.1").exists()
        assert recent_base.exists()
        assert (tmp_path / f"{recent_base.name}.1").exists()

    async def test_cleanup_removes_old_date_directories(self, tmp_path: Path):
        old_day = (datetime.now() - timedelta(days=31)).date().isoformat()
        recent_day = (datetime.now() - timedelta(days=1)).date().isoformat()
        old_dir = tmp_path / old_day
        recent_dir = tmp_path / recent_day
        old_dir.mkdir()
        recent_dir.mkdir()
        (old_dir / "flocks.log").write_text("old", encoding="utf-8")
        (recent_dir / "flocks.log").write_text("recent", encoding="utf-8")

        await Log._cleanup(tmp_path, retention_days=30)

        assert not old_dir.exists()
        assert recent_dir.exists()

    async def test_day_rollover_switches_writer_and_runs_cleanup(self, tmp_path: Path, monkeypatch):
        """Test long-running processes move to the new daily log and clean old days."""
        old_day = (datetime.now() - timedelta(days=31)).date().isoformat()
        first_day = (datetime.now() - timedelta(days=1)).date()
        second_day = datetime.now().date()
        old_dir = tmp_path / old_day
        old_dir.mkdir()
        (old_dir / "flocks.log").write_text("old", encoding="utf-8")

        class FakeDate:
            current = first_day

            @classmethod
            def today(cls):
                return cls.current

        monkeypatch.setenv("FLOCKS_LOG_DIR", str(tmp_path))
        monkeypatch.setattr(log_module, "date", FakeDate)

        try:
            await Log.init(print=False, dev=False, level=LogLevel.INFO)
            Log.Default.info("first day")
            first_file = Path(Log.file())

            FakeDate.current = second_day
            Log.Default.warn("second day")
            second_file = Path(Log.file())

            assert first_file == tmp_path / first_day.isoformat() / "flocks.log"
            assert second_file == tmp_path / second_day.isoformat() / "flocks.log"
            assert "first day" in first_file.read_text(encoding="utf-8")
            assert "second day" in second_file.read_text(encoding="utf-8")
            assert "second day" in (tmp_path / second_day.isoformat() / "errors.log").read_text(encoding="utf-8")
            assert not old_dir.exists()
        finally:
            if Log._writer:
                Log._writer.close()
                Log._writer = None
            if Log._error_writer:
                Log._error_writer.close()
                Log._error_writer = None
            Log._log_file = None
            Log._log_dir_path = None
            Log._log_date = None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
