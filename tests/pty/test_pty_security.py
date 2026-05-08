import pytest

from flocks.pty.pty import Pty


def test_pty_rejects_shell_command_execution_flag():
    with pytest.raises(ValueError, match="arguments"):
        Pty._validate_interactive_shell("/bin/sh", ["-c", "id"])


def test_pty_rejects_non_shell_command():
    with pytest.raises(ValueError, match="approved interactive shell"):
        Pty._validate_interactive_shell("/usr/bin/python3", [])


def test_pty_allows_interactive_shell_flags():
    Pty._validate_interactive_shell("/bin/zsh", ["-l"])
