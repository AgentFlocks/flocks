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


@pytest.mark.parametrize(
    "shell",
    [
        "ash",
        "dash",
        "ksh",
        "ksh93",
        "mksh",
        "csh",
        "tcsh",
    ],
)
def test_pty_allows_common_interactive_shells(shell: str):
    Pty._validate_interactive_shell(f"/bin/{shell}", [])


def test_pty_rejects_shell_startup_environment_injection():
    with pytest.raises(ValueError, match="not allowed"):
        Pty._prepare_environment({"BASH_ENV": "/tmp/payload.sh"})


def test_pty_filters_inherited_shell_startup_environment(monkeypatch):
    monkeypatch.setenv("BASH_ENV", "/tmp/payload.sh")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/tmp/libevil.dylib")
    monkeypatch.setenv("SAFE_VAR", "ok")

    env = Pty._prepare_environment({"CUSTOM_VAR": "custom"})

    assert "BASH_ENV" not in env
    assert "DYLD_INSERT_LIBRARIES" not in env
    assert env["SAFE_VAR"] == "ok"
    assert env["CUSTOM_VAR"] == "custom"
    assert env["TERM"] == "xterm-256color"
