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
from pathlib import Path
from typing import Any, Dict, Optional, TextIO
from datetime import datetime
import json
import glob as file_glob


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
    """Return the log directory for file handlers (e.g. workflow). Same as Log.init() uses."""
    return _log_dir()


def append_upgrade_text_log(message: str) -> None:
    """Append timestamped lines to ``update.log`` under the configured log directory.

    Used for upgrade flows so errors remain on disk when the process had no TTY
    or when structured ``Log`` output went to a different file than ``backend.log``.
    """
    try:
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "update.log"
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
            if isinstance(value, Exception):
                # Format error with message and cause chain
                prefix_parts.append(f"{key}={Log._format_error(value)}")
            elif isinstance(value, dict):
                # JSON stringify objects
                prefix_parts.append(f"{key}={json.dumps(value)}")
            else:
                prefix_parts.append(f"{key}={value}")
        
        prefix = " ".join(prefix_parts)
        
        # Get current time
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S")
        
        # Calculate time difference from last log
        current_time_ms = int(time.time() * 1000)
        diff_ms = current_time_ms - Log._last_time
        Log._last_time = current_time_ms
        
        # Build full message
        parts = [timestamp, f"+{diff_ms}ms", prefix, str(message) if message else ""]
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
            Log._write("WARN  " + self._build_message(message, extra))
    
    def error(self, message: Any = None, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log error message"""
        if Log._should_log(LogLevel.ERROR):
            Log._write("ERROR " + self._build_message(message, extra))
    
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
    
    # Default logger instance
    Default: Logger = None  # Will be initialized
    
    @classmethod
    def _should_log(cls, level: str) -> bool:
        """Check if a message should be logged based on level"""
        return _LEVEL_PRIORITY.get(level, 0) >= _LEVEL_PRIORITY.get(cls._level, 1)
    
    @classmethod
    def _write(cls, message: str) -> int:
        """Write log message to file and/or stderr"""
        try:
            if cls._writer:
                cls._writer.write(message)
                cls._writer.flush()
            else:
                # Fallback to stderr
                sys.stderr.write(message)
                sys.stderr.flush()
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
            dev: Whether in development mode (affects filename)
            level: Log level (DEBUG, INFO, WARN, ERROR)
        """
        cls._level = level
        
        # Setup log directory (FLOCKS_LOG_DIR or ~/.flocks/logs)
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Cleanup old logs
        await cls._cleanup(log_dir)
        
        if print:
            # Print to stderr
            cls._writer = None
            return
        
        # Setup log file
        if dev:
            filename = "dev.log"
        else:
            # Format: YYYY-MM-DDTHHMMSS.log
            filename = datetime.now().strftime("%Y-%m-%dT%H%M%S") + ".log"
        
        cls._log_file = log_dir / filename
        
        # Truncate if exists
        if cls._log_file.exists():
            cls._log_file.write_text("")
        
        # Open for writing
        cls._writer = open(cls._log_file, "a", buffering=1)  # Line buffered
        
        # Create default logger
        cls.Default = cls.create(service="default")
    
    @classmethod
    async def _cleanup(cls, log_dir: Path) -> None:
        """
        Clean up old log files, keeping only the 10 most recent
        
        Args:
            log_dir: Directory containing log files
        """
        try:
            # Find all log files matching pattern YYYY-MM-DDTHHMMSS.log
            pattern = str(log_dir / "????-??-??T??????.log")
            files = sorted(file_glob.glob(pattern))
            
            # Keep only the 10 most recent
            if len(files) > 10:
                files_to_delete = files[:-10]
                for file_path in files_to_delete:
                    try:
                        Path(file_path).unlink()
                    except Exception:
                        pass  # Silently ignore deletion errors
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
        return str(_log_dir() / "flocks.log")


# Initialize Default logger on module import
Log.Default = Log.create(service="default")
