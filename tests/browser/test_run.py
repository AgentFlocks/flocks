import sys
from io import StringIO
from unittest.mock import patch

from flocks.browser import run


def test_c_flag_executes_code() -> None:
    stdout = StringIO()
    with (
        patch.object(sys, "argv", ["flocks", "browser", "-c", "print('hello from -c')"]),
        patch("flocks.browser.run.ensure_daemon"),
        patch("flocks.browser.run.print_update_banner"),
        patch("sys.stdout", stdout),
    ):
        run.main(["-c", "print('hello from -c')"])
    assert stdout.getvalue().strip() == "hello from -c"


def test_c_flag_does_not_read_stdin() -> None:
    stdin_read = []
    fake_stdin = StringIO("should not be read")
    fake_stdin.read = lambda: stdin_read.append(True) or ""

    with (
        patch.object(sys, "argv", ["flocks", "browser", "-c", "x = 1"]),
        patch("flocks.browser.run.ensure_daemon"),
        patch("flocks.browser.run.print_update_banner"),
        patch("sys.stdin", fake_stdin),
    ):
        run.main(["-c", "x = 1"])

    assert not stdin_read, "stdin should not be read when -c is passed"


def test_state_show_prints_summary_without_daemon() -> None:
    stdout = StringIO()
    with (
        patch("flocks.browser.run.summarize_state", return_value={"cookies": 1}),
        patch("flocks.browser.run.ensure_daemon") as mock_daemon,
        patch("sys.stdout", stdout),
    ):
        run.main(["state", "show", "/tmp/auth-state.json"])

    assert '"cookies": 1' in stdout.getvalue()
    mock_daemon.assert_not_called()


def test_state_save_ensures_daemon_and_prints_result() -> None:
    stdout = StringIO()
    with (
        patch("flocks.browser.run.save_state", return_value={"path": "/tmp/auth-state.json", "cookies": 2}),
        patch("flocks.browser.run.ensure_daemon") as mock_daemon,
        patch("flocks.browser.run.print_update_banner") as mock_banner,
        patch("sys.stdout", stdout),
    ):
        run.main(["state", "save", "/tmp/auth-state.json"])

    assert '"cookies": 2' in stdout.getvalue()
    mock_banner.assert_called_once()
    mock_daemon.assert_called_once()


def test_state_load_passes_optional_flags() -> None:
    stdout = StringIO()
    with (
        patch("flocks.browser.run.load_state", return_value={"finalUrl": "https://example.com"}) as mock_load,
        patch("flocks.browser.run.ensure_daemon"),
        patch("flocks.browser.run.print_update_banner"),
        patch("sys.stdout", stdout),
    ):
        run.main(
            [
                "state",
                "load",
                "/tmp/auth-state.json",
                "--url",
                "https://example.com/dashboard",
                "--no-reload",
            ]
        )

    mock_load.assert_called_once_with(
        "/tmp/auth-state.json",
        url="https://example.com/dashboard",
        reload=False,
    )
    assert '"finalUrl": "https://example.com"' in stdout.getvalue()


def test_help_does_not_list_update_command() -> None:
    stdout = StringIO()
    with patch("sys.stdout", stdout):
        run.main(["--help"])

    assert "--update" not in stdout.getvalue()
