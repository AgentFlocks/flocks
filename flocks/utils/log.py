"""
Logging utility module

Provides structured logging exactly matching Flocks' TypeScript Log namespace.
This ensures complete compatibility between Python and TypeScript services.
All logs can be written to ~/.flocks/logs (or FLOCKS_LOG_DIR); init is required
for file output and is done by CLI or by server lifespan when run standalone.
"""

import logging
import os
import re
import sys
import time
import threading
import traceback
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, TextIO
from datetime import date, datetime, timedelta
import json
import glob as file_glob


_DEFAULT_LOG_RETENTION_DAYS = 30
_DEFAULT_LOG_VALUE_MAX_CHARS = 8 * 1024
#: A log file is rotated (``flocks.log.1`` ...) once it reaches this size,
#: keeping this many rotations; the whole log directory is then held under a
#: total size by dropping the oldest days.  FLOCKS_LOG_MAX_FILE_MB,
#: FLOCKS_LOG_KEEP_ROTATIONS and FLOCKS_LOG_MAX_TOTAL_MB override; 0 disables.
_DEFAULT_LOG_MAX_FILE_MB = 100
_DEFAULT_LOG_KEEP_ROTATIONS = 5
_DEFAULT_LOG_MAX_TOTAL_MB = 2048

# Credentials never reach a log line (V1.2: runtime logs are cleaned before
# they are written).  Keyed values whose name says what they are, and the
# shapes credentials have wherever they appear in free text.
_SENSITIVE_KEY = (
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|"
    r"authorization|cookies?|credentials?|dsn)"
)
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_NAME = re.compile(r"(?i)^[\w.-]*?" + _SENSITIVE_KEY + r"$")
_SCRUB_PATTERNS = (
    # Schemes first, so a key=value pass after them does not split "Bearer <token>".
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + _REDACTED),
    # A cookie header carries several credentials in one value: all of it
    # goes.  A value that opens with a quote sits in a container (JSON, a dict
    # repr): the whole quoted value.  A header value never opens with a quote
    # (it starts with a cookie name), and quotes inside it belong to cookie
    # values: up to the end of the line.  Never only the blank after the colon.
    (
        re.compile(r'(?i)\b((?:set-)?cookie)(["\']?\s*[=:]\s*)(["\'])(?!' + re.escape(_REDACTED) + r')(?:\\.|(?!\3).)*\3'),
        r"\1\2\3" + _REDACTED + r"\3",
    ),
    (re.compile(r'(?i)\b((?:set-)?cookie)(["\']?\s*[=:]\s*)(?![\s"\']|' + re.escape(_REDACTED) + r')[^\r\n]+'), r"\1\2" + _REDACTED),
    # key="quoted value", "key": "value with spaces": the whole quoted value.
    (
        re.compile(r'(?i)(?<![\w.-])([\w.-]*?' + _SENSITIVE_KEY + r')(["\']?\s*[=:]\s*)(["\'])(?!' + re.escape(_REDACTED) + r')(?:\\.|(?!\3).)*\3'),
        r"\1\2\3" + _REDACTED + r"\3",
    ),
    # key=value, key: value -- the key *ends* with the sensitive word
    # (auth_token, x-api-key), so counts such as max_tokens=, tokens= or
    # token_count= stay readable.
    (
        re.compile(r'(?i)(?<![\w.-])([\w.-]*?' + _SENSITIVE_KEY + r')(["\']?\s*[=:]\s*)(?!["\']|' + re.escape(_REDACTED) + r')([^\s"\',;&}]+)'),
        r"\1\2" + _REDACTED,
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), _REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), _REDACTED),
    # Passwords in URLs: scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/@\s]+):([^@/\s]+)@"), r"\1:" + _REDACTED + "@"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), _REDACTED),
)


def scrub_credentials(text: str) -> str:
    """*text* with anything that looks like a credential replaced."""
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _scrub_arg(value: Any) -> Any:
    return scrub_credentials(value) if isinstance(value, str) else value


def scrub_log_record(record: logging.LogRecord) -> logging.LogRecord:
    """Take credentials out of a standard-library log record, message and exception alike.

    Arguments are scrubbed one by one first, so a formatter that reads them
    (uvicorn's access log unpacks the request line from them) keeps working;
    only when a credential spans the message and its arguments ("token=%s",
    secret) is the message formatted and scrubbed as a whole.
    """
    try:
        if isinstance(record.args, tuple):
            record.args = tuple(_scrub_arg(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _scrub_arg(value) for key, value in record.args.items()}
        # The format string is not scrubbed on its own ("api_key=%s" would lose
        # its placeholder); the formatted message is, whatever the message is.
        message = record.getMessage()
        scrubbed = scrub_credentials(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, ()
        if record.exc_info and not record.exc_text:
            record.exc_text = scrub_credentials("".join(traceback.format_exception(*record.exc_info)).rstrip())
        if record.stack_info:
            record.stack_info = scrub_credentials(record.stack_info)
    except Exception:
        pass  # a record that cannot be formatted fails where it always did, in its handler
    return record


_record_factory_installed = False


def install_log_record_scrubber() -> None:
    """Scrub every record the standard library creates: the root logger,
    uvicorn's error and access logs, third-party libraries.  Idempotent."""
    global _record_factory_installed
    if _record_factory_installed:
        return
    original = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        return scrub_log_record(original(*args, **kwargs))

    logging.setLogRecordFactory(factory)
    _record_factory_installed = True


def _node_tag() -> Optional[str]:
    """The node this process runs on, when the deployment names its nodes (FLOCKS_NODE_ID)."""
    value = (os.getenv("FLOCKS_NODE_ID") or "").strip()
    return value or None
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


_log_retention_override: Optional[int] = None
_log_cleanup_managed = False


def set_log_retention_days_override(days: Optional[int]) -> None:
    """Let the data retention policy in flocks.json win over FLOCKS_LOG_RETENTION_DAYS.

    ``None`` drops the override; ``0`` keeps logs forever.
    """
    global _log_retention_override
    _log_retention_override = days


def set_log_cleanup_managed(managed: bool) -> None:
    """Leave deleting old logs (startup and day rollover) to data retention.

    Its scheduler deletes expired logs under the policy as every process saved
    it last (see flocks.retention); this process's own copy of the period may
    be older, so the logger does not delete on its own meanwhile.
    """
    global _log_cleanup_managed
    _log_cleanup_managed = managed


def log_cleanup_managed() -> bool:
    """Whether data retention, not the logger, deletes this process's old logs."""
    return _log_cleanup_managed


def get_log_retention_days(default: int = _DEFAULT_LOG_RETENTION_DAYS) -> int:
    """Return how long daily log directories and legacy timestamp logs are retained."""
    if _log_retention_override is not None:
        return _log_retention_override
    return _env_int("FLOCKS_LOG_RETENTION_DAYS", default)


def _timestamp_from_log_name(path: Path) -> Optional[datetime]:
    stem = path.name.split(".log", 1)[0]
    try:
        return datetime.strptime(stem, "%Y-%m-%dT%H%M%S")
    except ValueError:
        return None


def _date_from_log_dir(path: Path) -> Optional[date]:
    try:
        return datetime.strptime(path.name, "%Y-%m-%d").date()
    except ValueError:
        return None


def list_expired_log_paths(
    log_dir: Path,
    retention_days: int,
    *,
    now: Optional[datetime] = None,
) -> list[Path]:
    """Return the daily directories and legacy timestamp logs older than ``retention_days``.

    ``retention_days <= 0`` keeps everything. The startup / day-rollover cleanup
    and the data retention preview both use this, so they count the same files.
    """
    if retention_days <= 0 or not log_dir.is_dir():
        return []
    cutoff = (now or datetime.now()) - timedelta(days=retention_days)
    expired: list[Path] = []
    for path in log_dir.iterdir():
        if not path.is_dir():
            continue
        day = _date_from_log_dir(path)
        if day is not None and day < cutoff.date():
            expired.append(path)

    # Base log files matching YYYY-MM-DDTHHMMSS.log. Rotated siblings go with
    # their base file so old ``.log.1``/``.log.2`` files do not leak forever.
    pattern = str(log_dir / "????-??-??T??????.log")
    for path in sorted(Path(item) for item in file_glob.glob(pattern)):
        timestamp = _timestamp_from_log_name(path)
        if timestamp is not None and timestamp < cutoff:
            expired.append(path)
            expired.extend(sorted(path.parent.glob(f"{path.name}.*")))

    rotated_pattern = str(log_dir / "????-??-??T??????.log.*")
    for rotated_path in sorted(Path(item) for item in file_glob.glob(rotated_pattern)):
        base_name = rotated_path.name.split(".log.", 1)[0] + ".log"
        base_path = rotated_path.with_name(base_name)
        timestamp = _timestamp_from_log_name(base_path)
        if not base_path.exists() and timestamp is not None and timestamp < cutoff:
            expired.append(rotated_path)
    return expired


class _AppendTextWriter:
    """Small line-buffered writer for Flocks daily logs."""

    def __init__(self, path: Path):
        self.path = path
        self._handle: Optional[TextIO] = None
        self._lock = threading.RLock()
        self._size = 0
        self._open()

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a", buffering=1, encoding="utf-8")
        try:
            self._size = self.path.stat().st_size
        except OSError:
            self._size = 0

    def _rotate(self) -> None:
        """``flocks.log`` -> ``flocks.log.1`` -> ... keeping the configured number."""
        keep = _env_int("FLOCKS_LOG_KEEP_ROTATIONS", _DEFAULT_LOG_KEEP_ROTATIONS)
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        if keep <= 0:
            self.path.unlink(missing_ok=True)
        else:
            self.path.with_name(f"{self.path.name}.{keep}").unlink(missing_ok=True)
            for index in range(keep - 1, 0, -1):
                older = self.path.with_name(f"{self.path.name}.{index}")
                if older.exists():
                    older.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
            if self.path.exists():
                self.path.replace(self.path.with_name(f"{self.path.name}.1"))
        self._open()

    def write(self, message: str) -> int:
        with self._lock:
            if self._handle is None:
                self._open()
            limit_mb = _env_int("FLOCKS_LOG_MAX_FILE_MB", _DEFAULT_LOG_MAX_FILE_MB)
            if limit_mb > 0 and self._size >= limit_mb * 1024 * 1024:
                try:
                    self._rotate()
                except OSError:
                    pass
            written = self._handle.write(message)
            self._size += len(message.encode("utf-8", errors="replace"))
            return written

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
            if isinstance(key, str) and _SENSITIVE_KEY_NAME.match(key) and item is not None:
                # Masked by name before it is serialized, whatever its shape.
                prepared[prepared_key] = _REDACTED
                continue
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
        normalized = scrub_credentials(message).replace("\r\n", "\n").replace("\r", "\n")
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
        node = _node_tag()
        if node and "node" not in all_tags:
            all_tags = {"node": node, **all_tags}
        
        # Build prefix (key=value pairs)
        prefix_parts = []
        for key, value in all_tags.items():
            if isinstance(key, str) and _SENSITIVE_KEY_NAME.match(key):
                prefix_parts.append(f"{key}={_REDACTED}")
                continue
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
            message = scrub_credentials(message)
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
        # Records written through the standard library never pass _write.
        install_log_record_scrubber()
        
        # Setup log directory (FLOCKS_LOG_DIR or ~/.flocks/logs)
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Cleanup old logs, unless data retention took that over (preload_log_retention);
        # the total-size cap holds either way.
        if not _log_cleanup_managed:
            await cls._cleanup(log_dir)
        else:
            cls._cap_total_size(log_dir)
        
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
        if not _log_cleanup_managed:
            cls._cleanup_sync(cls._log_dir_path)
        else:
            cls._cap_total_size(cls._log_dir_path)
    
    @classmethod
    async def _cleanup(cls, log_dir: Path, retention_days: Optional[int] = None) -> None:
        """Clean up date directories and legacy timestamp logs by age.
        
        Args:
            log_dir: Directory containing log files
        """
        cls._cleanup_sync(log_dir, retention_days=retention_days)

    @classmethod
    def _cleanup_sync(cls, log_dir: Path, retention_days: Optional[int] = None) -> None:
        """Clean up date directories and legacy timestamp logs by age, then hold
        the directory under its total size (see :meth:`_cap_total_size`)."""
        days = get_log_retention_days() if retention_days is None else retention_days
        try:
            expired = list_expired_log_paths(log_dir, days)
        except Exception:
            expired = []  # Silently ignore cleanup errors
        for path in expired:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except Exception:
                pass  # Silently ignore deletion errors
        cls._cap_total_size(log_dir)

    @classmethod
    def _cap_total_size(cls, log_dir: Path) -> None:
        """Drop the oldest day directories until the logs fit FLOCKS_LOG_MAX_TOTAL_MB.

        Today's directory is never dropped: it is being written.  This is a disk
        limit of its own, apart from the retention period: it applies even when
        data retention, not the logger, deletes expired logs.
        """
        limit_mb = _env_int("FLOCKS_LOG_MAX_TOTAL_MB", _DEFAULT_LOG_MAX_TOTAL_MB)
        if limit_mb <= 0:
            return
        try:
            days = sorted(
                path for path in log_dir.iterdir()
                if path.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name)
            )

            def _size(path: Path) -> int:
                return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

            sizes = {path: _size(path) for path in days}
            total = sum(sizes.values())
            today = date.today().isoformat()
            dropped: list[str] = []
            for path in days:
                if total <= limit_mb * 1024 * 1024 or path.name == today:
                    break
                try:
                    shutil.rmtree(path)
                    total -= sizes[path]
                    dropped.append(path.name)
                except Exception:
                    pass
            if dropped:
                # The disk budget, not the retention period, is deciding what
                # is kept: logs meant to be kept are going. Someone should know.
                cls._budget_alert(limit_mb, dropped, total)
        except Exception:
            pass

    @classmethod
    def _budget_alert(cls, limit_mb: int, dropped: list[str], remaining_bytes: int) -> None:
        from flocks.observability.alerts import raise_alert

        raise_alert(
            "log_budget_exhausted",
            f"log directory over {limit_mb} MB: dropped {len(dropped)} day(s) "
            f"({dropped[0]}..{dropped[-1]}) before their retention ran out",
            dropped_days=dropped,
            remaining_mb=round(remaining_bytes / (1024 * 1024), 1),
        )
    
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
