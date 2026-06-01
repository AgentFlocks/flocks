"""Logging configuration for flocks.workflow.

Workflow logs go to stderr and, when file logging is enabled, to
~/.flocks/logs/workflow.log (or FLOCKS_LOG_DIR/workflow.log).
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from flocks.utils.log import get_log_backup_count, get_log_dir, get_log_max_bytes


def setup_workflow_logging(
    level: int = logging.INFO,
    format_string: Optional[str] = None,
    stream=None,
    file: bool = True,
) -> None:
    """配置 flocks.workflow 的日志输出（控制台 + 可选文件）。

    Args:
        level: 日志级别，默认为 INFO
        format_string: 日志格式字符串，如果为 None 则使用默认格式
        stream: 输出流，默认为 sys.stderr
        file: 是否同时写入 ~/.flocks/logs/workflow.log（与主 Log 同目录）

    Example:
        >>> from flocks.workflow import setup_workflow_logging
        >>> setup_workflow_logging()  # 使用默认配置
        >>> setup_workflow_logging(level=logging.DEBUG)  # 启用 DEBUG 级别
    """
    if format_string is None:
        format_string = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"

    if stream is None:
        stream = sys.stderr

    formatter = logging.Formatter(format_string, datefmt="%Y-%m-%d %H:%M:%S")

    logger = logging.getLogger("flocks.workflow")
    logger.setLevel(level)
    logger.handlers.clear()

    # Console
    console_handler = logging.StreamHandler(stream)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File (same directory as flocks.utils.log)
    if file:
        try:
            log_dir = get_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / "workflow.log",
                maxBytes=get_log_max_bytes(),
                backupCount=get_log_backup_count(),
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            pass  # Do not break if log dir is read-only or missing

    logger.propagate = False


def enable_verbose_logging() -> None:
    """启用详细日志输出（DEBUG 级别）。
    
    这是 setup_workflow_logging(level=logging.DEBUG) 的快捷方式。
    """
    setup_workflow_logging(level=logging.DEBUG)


def disable_workflow_logging() -> None:
    """禁用 flocks.workflow 的日志输出。"""
    logger = logging.getLogger("flocks.workflow")
    logger.handlers.clear()
    logger.setLevel(logging.CRITICAL + 1)  # 设置为比 CRITICAL 更高的级别
