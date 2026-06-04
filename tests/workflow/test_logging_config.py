import logging
from pathlib import Path

from flocks.workflow.logging_config import setup_workflow_logging


def test_workflow_file_logging_is_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(tmp_path))

    setup_workflow_logging(stream=None)

    logger = logging.getLogger("flocks.workflow")

    try:
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], logging.StreamHandler)
        assert not (tmp_path / "workflow.log").exists()
    finally:
        logger.handlers.clear()
