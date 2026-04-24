"""Uvicorn log config used by ``flocks serve``."""

from flocks.cli import main as cli_main


def test_uvicorn_log_config_adds_asctime_to_formatters() -> None:
    cfg = cli_main._uvicorn_log_config()
    assert "%(asctime)s |" in cfg["formatters"]["default"]["fmt"]
    assert "%(asctime)s |" in cfg["formatters"]["access"]["fmt"]
    assert cfg["formatters"]["default"]["datefmt"] == "%Y-%m-%d %H:%M:%S"
    assert cfg["formatters"]["access"]["datefmt"] == "%Y-%m-%d %H:%M:%S"
