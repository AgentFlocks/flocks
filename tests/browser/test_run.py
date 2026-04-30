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
