from unittest.mock import patch

from typer.testing import CliRunner

from flocks.cli.main import app


runner = CliRunner()


def test_browser_help_is_available() -> None:
    result = runner.invoke(app, ["browser", "--help"])

    assert result.exit_code == 0
    assert "Direct browser control via the built-in CDP runtime" in result.stdout


def test_browser_command_forwards_raw_args() -> None:
    with patch("flocks.cli.commands.browser.browser_run.main") as mock_main:
        result = runner.invoke(app, ["browser", "-c", "print('hi')"])

    assert result.exit_code == 0
    mock_main.assert_called_once_with(["-c", "print('hi')"])
