import pytest

from flocks.pty.pty import Pty


def test_pty_accepts_process_arguments_without_command_authorization():
    Pty._validate_process_arguments("/usr/bin/python3", ["-c", "id"])


def test_pty_rejects_nul_process_arguments():
    with pytest.raises(ValueError, match="argument"):
        Pty._validate_process_arguments("/bin/sh", ["-c", "echo\x00bad"])


def test_pty_preserves_environment_without_command_security_filtering(monkeypatch):
    monkeypatch.setenv("BASH_ENV", "/tmp/payload.sh")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/tmp/libevil.dylib")
    monkeypatch.setenv("SAFE_VAR", "ok")

    env = Pty._prepare_environment({"CUSTOM_VAR": "custom"})

    assert env["BASH_ENV"] == "/tmp/payload.sh"
    assert env["DYLD_INSERT_LIBRARIES"] == "/tmp/libevil.dylib"
    assert env["SAFE_VAR"] == "ok"
    assert env["CUSTOM_VAR"] == "custom"
    assert env["TERM"] == "xterm-256color"
