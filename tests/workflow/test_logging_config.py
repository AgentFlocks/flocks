import logging
from io import StringIO
from pathlib import Path

from flocks.workflow.logging_config import setup_workflow_logging
from flocks.workflow.runner import run_workflow


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


def test_run_workflow_default_logging_suppresses_routine_execution_noise() -> None:
    stream = StringIO()
    setup_workflow_logging(stream=stream)

    logger = logging.getLogger("flocks.workflow")
    try:
        result = run_workflow(
            workflow={
                "start": "collect_messages",
                "nodes": [
                    {
                        "id": "collect_messages",
                        "type": "python",
                        "code": "outputs['ok'] = True",
                    },
                ],
                "edges": [],
            },
            ensure_requirements=False,
        )

        assert result.status == "SUCCEEDED"
        logs = stream.getvalue()
        assert "开始执行 workflow" not in logs
        assert "workflow 信息" not in logs
        assert "outputs=" not in logs
        assert "outputs_keys=['ok']" in logs
    finally:
        logger.handlers.clear()
