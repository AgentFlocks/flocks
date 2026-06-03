"""
Logging utility module

Provides structured logging exactly matching Flocks' TypeScript Log namespace.
This ensures complete compatibility between Python and TypeScript services.
All logs can be written to ~/.flocks/logs (or FLOCKS_LOG_DIR); init is required
for file output and is done by CLI or by server lifespan when run standalone.
"""

import os
import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional, TextIO
from datetime import date, datetime, timedelta
import json
import glob as file_glob


_DEFAULT_LOG_RETENTION_DAYS = 30
_DEFAULT_LOG_VALUE_MAX_CHARS = 8 * 1024
_MAX_STRUCTURED_ITEMS = 50
_MAX_STRUCTURED_DEPTH = 4


def _log_dir() -> Path:
    """Log directory: FLOCKS_LOG_DIR, or FLOCKS_ROOT/logs, or ~/.flocks/logs. Matches config."""
    raw = os.getenv("FLOCKS_LOG_DIR")
    if raw:
        return Path(raw)
    root = os.getenv("FLOCKS_ROOT")
    if root:
        return Path(root) / "logs"
    return Path.home() / ".flocks" / "logs"


def get_log_dir() -> Path:
    """Return the root log directory used by Flocks."""
    return _log_dir()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_log_retention_days(default: int = _DEFAULT_LOG_RETENTION_DAYS) -> int:
    """Return how long legacy timestamp logs are retained."""
    return _env_int("FLOCKS_LOG_RETENTION_DAYS", default)


class _AppendTextWriter:
    """Small line-buffered writer for Flocks daily logs."""

    def __init__(self, path: Path):
        self.path = path
        self._handle: Optional[TextIO] = None
        self._lock = threading.RLock()
        self._open()

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a", buffering=1, encoding="utf-8")

    def write(self, message: str) -> int:
        with self._lock:
            if self._handle is None:
                self._open()
            return self._handle.write(message)

    def flush(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.flush()

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None


def _truncate_for_log(value: str, max_chars: Optional[int] = None) -> str:
    limit = _env_int("FLOCKS_LOG_VALUE_MAX_CHARS", _DEFAULT_LOG_VALUE_MAX_CHARS) if max_chars is None else max_chars
    if limit <= 0 or len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}...<truncated {omitted} chars>"


def _prepare_json_value(value: Any, *, depth: int = 0, seen: Optional[set[int]] = None) -> Any:
    if isinstance(value, str):
        return _truncate_for_log(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if seen is None:
        seen = set()
    value_id = id(value)
    if value_id in seen:
        return "<cycle>"
    if depth >= _MAX_STRUCTURED_DEPTH:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        seen.add(value_id)
        prepared = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_STRUCTURED_ITEMS:
                prepared["__truncated__"] = f"{len(value) - _MAX_STRUCTURED_ITEMS} more keys"
                break
            prepared_key = key if key is None or isinstance(key, (str, int, float, bool)) else _truncate_for_log(str(key))
            prepared[prepared_key] = _prepare_json_value(item, depth=depth + 1, seen=seen)
        seen.remove(value_id)
        return prepared
    if isinstance(value, list):
        seen.add(value_id)
        prepared = [
            _prepare_json_value(item, depth=depth + 1, seen=seen)
            for item in value[:_MAX_STRUCTURED_ITEMS]
        ]
        if len(value) > _MAX_STRUCTURED_ITEMS:
            prepared.append(f"<truncated {len(value) - _MAX_STRUCTURED_ITEMS} more items>")
        seen.remove(value_id)
        return prepared
    return _truncate_for_log(str(value))


def _format_log_value(value: Any) -> str:
    if isinstance(value, Exception):
        return _truncate_for_log(Log._format_error(value))
    if isinstance(value, (dict, list)):
        try:
            return _truncate_for_log(json.dumps(_prepare_json_value(value)))
        except (TypeError, ValueError):
            return _truncate_for_log(str(value))
    return _truncate_for_log(str(value))


def append_upgrade_text_log(message: str) -> None:
    """Append timestamped upgrade lines to today's ``errors.log``.

    Used for upgrade flows so errors remain on disk when the process had no TTY
    or when structured ``Log`` output was not initialized.
    """
    try:
        log_dir = _log_dir()
        day_dir = log_dir / date.today().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / "errors.log"
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        normalized = message.replace("\r\n", "\n").replace("\r", "\n")
        with path.open("a", encoding="utf-8") as handle:
            for segment in normalized.split("\n"):
                handle.write(f"{stamp} | {segment}\n")
    except OSError:
        return


# Log levels - matches TypeScript exactly
class LogLevel:
    """Log levels"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


# Level priority for filtering
_LEVEL_PRIORITY = {
    LogLevel.DEBUG: 0,
    LogLevel.INFO: 1,
    LogLevel.WARN: 2,
    LogLevel.ERROR: 3,
}


class Logger:
    """
    Individual logger instance
    
    Matches TypeScript Logger interface exactly.
    """
    
    def __init__(self, tags: Optional[Dict[str, Any]] = None):
        """
        Initialize logger with tags
        
        Args:
            tags: Dictionary of tags to include in log messages
        """
        self._tags = tags or {}
    
    def _build_message(self, message: Any, extra: Optional[Dict[str, Any]] = None) -> str:
        """
        Build log message matching TypeScript format
        
        Format: timestamp +Xms key1=value1 key2=value2 message
        """
        # Combine tags and extra
        all_tags = {**self._tags, **(extra or {})}
        
        # Filter out None/null values
        all_tags = {k: v for k, v in all_tags.items() if v is not None}
        
        # Build prefix (key=value pairs)
        prefix_parts = []
        for key, value in all_tags.items():
            prefix_parts.append(f"{key}={_format_log_value(value)}")
        
        prefix = " ".join(prefix_parts)
        
        # Get current time
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S")
        
        # Calculate time difference from last log
        current_time_ms = int(time.time() * 1000)
        diff_ms = current_time_ms - Log._last_time
        Log._last_time = current_time_ms
        
        # Build full message
        parts = [timestamp, f"+{diff_ms}ms", prefix, _truncate_for_log(str(message)) if message else ""]
        return " ".join([p for p in parts if p]) + "\n"
    
    def debug(self, message: Any = None, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log debug message"""
        if Log._should_log(LogLevel.DEBUG):
            Log._write("DEBUG " + self._build_message(message, extra))
    
    def info(self, message: Any = None, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log info message"""
        if Log._should_log(LogLevel.INFO):
            Log._write("INFO  " + self._build_message(message, extra))
    
    def warn(self, message: Any = None, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log warning message"""
        if Log._should_log(LogLevel.WARN):
            Log._write("WARN  " + self._build_message(message, extra), error=True)
    
    def error(self, message: Any = None, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log error message"""
        if Log._should_log(LogLevel.ERROR):
            Log._write("ERROR " + self._build_message(message, extra), error=True)
    
    # Alias for compatibility with standard logging library
    warning = warn
    
    def tag(self, key: str, value: str) -> "Logger":
        """
        Add a tag to this logger
        
        Args:
            key: Tag key
            value: Tag value
            
        Returns:
            This logger instance (for chaining)
        """
        self._tags[key] = value
        return self
    
    def clone(self) -> "Logger":
        """
        Clone this logger with a copy of its tags
        
        Returns:
            New logger instance with copied tags
        """
        return Logger(tags=self._tags.copy())
    
    def time(self, message: str, extra: Optional[Dict[str, Any]] = None) -> "TimerContext":
        """
        Create a timing context manager
        
        Args:
            message: Message to log
            extra: Extra data to include
            
        Returns:
            Timer context manager
        """
        return TimerContext(self, message, extra)


class TimerContext:
    """
    Context manager for timing operations
    
    Matches TypeScript timer interface with Symbol.dispose support (via __enter__/__exit__)
    """
    
    def __init__(self, logger: Logger, message: str, extra: Optional[Dict[str, Any]] = None):
        self.logger = logger
        self.message = message
        self.extra = extra or {}
        self.start_time = 0
    
    def __enter__(self):
        self.start_time = int(time.time() * 1000)
        self.logger.info(self.message, {**self.extra, "status": "started"})
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
    
    def stop(self):
        """Stop the timer and log completion"""
        if self.start_time > 0:
            duration = int(time.time() * 1000) - self.start_time
            self.logger.info(self.message, {
                **self.extra,
                "status": "completed",
                "duration": duration
            })
            self.start_time = 0


class Log:
    """
    Log namespace - static methods for logging
    
    Exactly matches Flocks's TypeScript Log namespace.
    """
    
    # Class variables (module-level state)
    _level: str = LogLevel.INFO
    _loggers: Dict[str, Logger] = {}
    _last_time: int = int(time.time() * 1000)
    _log_file: Optional[Path] = None
    _writer: Optional[TextIO] = None
    _error_writer: Optional[TextIO] = None
    _log_dir_path: Optional[Path] = None
    _log_date: Optional[str] = None
    _state_lock = threading.RLock()
    
    # Default logger instance
    Default: Logger = None  # Will be initialized
    
    @classmethod
    def _should_log(cls, level: str) -> bool:
        """Check if a message should be logged based on level"""
        return _LEVEL_PRIORITY.get(level, 0) >= _LEVEL_PRIORITY.get(cls._level, 1)
    
    @classmethod
    def _write(cls, message: str, *, error: bool = False) -> int:
        """Write log message to file and/or stderr"""
        try:
            with cls._state_lock:
                cls._ensure_current_day()
                if cls._writer:
                    cls._writer.write(message)
                    cls._writer.flush()
                else:
                    # Fallback to stderr
                    sys.stderr.write(message)
                    sys.stderr.flush()
                if error and cls._error_writer:
                    cls._error_writer.write(message)
                    cls._error_writer.flush()
            return len(message)
        except Exception:
            # Silently fail - logging should never break the app
            return 0
    
    @classmethod
    def _format_error(cls, error: Exception, depth: int = 0) -> str:
        """
        Format error with cause chain
        
        Args:
            error: Exception to format
            depth: Current recursion depth (max 10)
            
        Returns:
            Formatted error string
        """
        result = str(error)
        if hasattr(error, "__cause__") and error.__cause__ and depth < 10:
            result += " Caused by: " + cls._format_error(error.__cause__, depth + 1)
        return result
    
    @classmethod
    async def init(
        cls,
        print: bool = False,
        dev: bool = False,
        level: str = LogLevel.INFO
    ) -> None:
        """
        Initialize logging system
        
        Args:
            print: Whether to print logs to stderr (if False, logs to file)
            dev: Kept for compatibility; file output always uses daily logs
            level: Log level (DEBUG, INFO, WARN, ERROR)
        """
        cls._level = level
        
        # Setup log directory (FLOCKS_LOG_DIR or ~/.flocks/logs)
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Cleanup old logs
        await cls._cleanup(log_dir)
        
        with cls._state_lock:
            if print:
                # Print to stderr
                if cls._writer:
                    cls._writer.close()
                if cls._error_writer:
                    cls._error_writer.close()
                cls._writer = None
                cls._error_writer = None
                cls._log_file = None
                cls._log_dir_path = None
                cls._log_date = None
                return

            if cls._writer:
                cls._writer.close()
            if cls._error_writer:
                cls._error_writer.close()

            cls._log_dir_path = log_dir
            cls._writer = None
            cls._error_writer = None
            cls._log_date = None
            cls._open_daily_writers()
        
        # Create default logger
        cls.Default = cls.create(service="default")

    @classmethod
    def _open_daily_writers(cls) -> None:
        if cls._log_dir_path is None:
            return
        today = date.today().isoformat()
        day_dir = cls._log_dir_path / today
        day_dir.mkdir(parents=True, exist_ok=True)
        cls._log_date = today
        cls._log_file = day_dir / "flocks.log"
        cls._writer = _AppendTextWriter(cls._log_file)
        cls._error_writer = _AppendTextWriter(day_dir / "errors.log")

    @classmethod
    def _ensure_current_day(cls) -> None:
        if cls._writer is None or cls._log_dir_path is None:
            return
        today = date.today().isoformat()
        if cls._log_date == today:
            return
        if cls._writer:
            cls._writer.close()
        if cls._error_writer:
            cls._error_writer.close()
        cls._writer = None
        cls._error_writer = None
        cls._open_daily_writers()
    
    @classmethod
    async def _cleanup(cls, log_dir: Path, retention_days: Optional[int] = None) -> None:
        """Clean up date directories and legacy timestamp logs by age.
        
        Args:
            log_dir: Directory containing log files
        """
        days = get_log_retention_days() if retention_days is None else retention_days
        if days <= 0:
            return
        cutoff = datetime.now() - timedelta(days=days)

        def _timestamp_from_name(path: Path) -> Optional[datetime]:
            stem = path.name.split(".log", 1)[0]
            try:
                return datetime.strptime(stem, "%Y-%m-%dT%H%M%S")
            except ValueError:
                return None

        def _date_from_dir(path: Path) -> Optional[date]:
            try:
                return datetime.strptime(path.name, "%Y-%m-%d").date()
            except ValueError:
                return None

        try:
            for path in log_dir.iterdir():
                if not path.is_dir():
                    continue
                day = _date_from_dir(path)
                if day is not None and day < cutoff.date():
                    try:
                        import shutil
                        shutil.rmtree(path)
                    except Exception:
                        pass

            # Find base log files matching pattern YYYY-MM-DDTHHMMSS.log.
            # Rotated siblings are deleted together with their base file so
            # old ``.log.1``/``.log.2`` files do not leak forever.
            pattern = str(log_dir / "????-??-??T??????.log")
            files = [Path(path) for path in sorted(file_glob.glob(pattern))]

            for path in files:
                timestamp = _timestamp_from_name(path)
                if timestamp is not None and timestamp < cutoff:
                    try:
                        path.unlink(missing_ok=True)
                        for rotated in path.parent.glob(f"{path.name}.*"):
                            rotated.unlink(missing_ok=True)
                    except Exception:
                        pass  # Silently ignore deletion errors

            rotated_pattern = str(log_dir / "????-??-??T??????.log.*")
            for rotated_path in (Path(path) for path in file_glob.glob(rotated_pattern)):
                base_name = rotated_path.name.split(".log.", 1)[0] + ".log"
                base_path = rotated_path.with_name(base_name)
                timestamp = _timestamp_from_name(base_path)
                if not base_path.exists() and timestamp is not None and timestamp < cutoff:
                    try:
                        rotated_path.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            pass  # Silently ignore cleanup errors
    
    @classmethod
    def create(cls, service: str = None, **tags) -> Logger:
        """
        Create a new logger instance
        
        Args:
            service: Service name (shorthand for tags={'service': service})
            **tags: Additional tags for this logger
            
        Returns:
            Logger instance
        """
        # Merge service into tags
        all_tags = tags.copy()
        if service:
            all_tags["service"] = service
        
        # Check cache if service is specified
        if service and service in cls._loggers:
            return cls._loggers[service]
        
        # Create new logger
        logger = Logger(tags=all_tags)
        
        # Cache by service name
        if service:
            cls._loggers[service] = logger
        
        return logger
    
    @classmethod
    def file(cls) -> str:
        """Get the current log file path"""
        if cls._log_file:
            return str(cls._log_file)
        return str(_log_dir() / date.today().isoformat() / "flocks.log")


# Initialize Default logger on module import
Log.Default = Log.create(service="default")
