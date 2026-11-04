"""Default Python environment must follow the Flocks checkout, not audit input."""

from pathlib import Path

import pytest

import flocks.session.prompt as prompt_module
from flocks.session.prompt import SessionPrompt


@pytest.mark.parametrize("platform_name, executable", [("linux", "bin/python"), ("win32", "Scripts/python.exe")])
def test_minimal_environment_selects_flocks_source_venv(tmp_path, monkeypatch, platform_name, executable):
    source = tmp_path / "flocks source"
    module = source / "flocks" / "session" / "prompt.py"
    module.parent.mkdir(parents=True)
    module.touch()
    (source / "pyproject.toml").touch()
    python = source / ".venv" / executable
    python.parent.mkdir(parents=True)
    python.touch()
    target = tmp_path / "audit target"
    target.mkdir()
    (target / ".venv").mkdir()
    monkeypatch.chdir(target)
    monkeypatch.setattr(prompt_module, "__file__", str(module))
    monkeypatch.setattr(prompt_module.sys, "platform", platform_name)

    environment = SessionPrompt._build_minimal_environment(str(target))

    assert f"Current working directory: {target}" in environment
    assert f"Default host Python interpreter: {python}" in environment
    assert "Flocks source .venv available: yes" in environment
    assert "-m pip" in environment
    assert "Container commands use the container's Python" in environment


def test_minimal_environment_reports_missing_source_venv(tmp_path, monkeypatch):
    source = tmp_path / "flocks"
    module = source / "flocks" / "session" / "prompt.py"
    module.parent.mkdir(parents=True)
    module.touch()
    (source / "pyproject.toml").touch()
    monkeypatch.setattr(prompt_module, "__file__", str(module))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "unrelated-venv"))

    environment = SessionPrompt._build_minimal_environment(str(tmp_path))

    # An installed release has no source .venv: no interpreter path is offered to run
    # (R04, 2026-10-26: a subagent ran the named .venv/bin/python and it did not exist).
    assert "Flocks source .venv available: no" in environment
    assert str(source / ".venv") not in environment
    assert "Default host Python interpreter" not in environment
    assert "report the missing environment" in environment
    assert "unrelated-venv" not in environment
    assert not (source / ".venv").exists()
