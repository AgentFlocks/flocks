import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flocks.workflow.logging_config import setup_workflow_logging


def test_workflow_file_logging_uses_rotating_handler(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("FLOCKS_LOG_MAX_BYTES", "1234")
    monkeypatch.setenv("FLOCKS_LOG_BACKUP_COUNT", "2")

    setup_workflow_logging(stream=None)

    logger = logging.getLogger("flocks.workflow")
    handlers = [handler for handler in logger.handlers if isinstance(handler, RotatingFileHandler)]

    try:
        assert len(handlers) == 1
        assert handlers[0].baseFilename == str(tmp_path / "workflow.log")
        assert handlers[0].maxBytes == 1234
        assert handlers[0].backupCount == 2
    finally:
        logger.handlers.clear()
