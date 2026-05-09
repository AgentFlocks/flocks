from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from flocks.server.routes import find as find_routes


class _RunResult:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.returncode = 0


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".flocks").mkdir()
    (project / "README.md").write_text("hello\n", encoding="utf-8")
    return project


@pytest.mark.asyncio
async def test_find_text_rejects_directory_outside_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    project = _make_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(project)

    with pytest.raises(HTTPException) as exc_info:
        await find_routes.find_text(pattern="secret", directory=str(outside))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_find_text_passes_leading_dash_pattern_after_separator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    project = _make_project(tmp_path)
    monkeypatch.chdir(project)
    commands: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        commands.append(cmd)
        assert kwargs["cwd"] == str(project.resolve())
        match = {
            "type": "match",
            "data": {
                "path": {"text": "README.md"},
                "line_number": 1,
                "lines": {"text": "hello\n"},
            },
        }
        return _RunResult(stdout=json.dumps(match))

    monkeypatch.setattr(find_routes.subprocess, "run", _fake_run)

    results = await find_routes.find_text(pattern="--pre=/tmp/evil.sh", directory=str(project))

    assert commands[0][-2:] == ["--", "--pre=/tmp/evil.sh"]
    assert results[0].file == "README.md"


@pytest.mark.asyncio
async def test_find_files_passes_leading_dash_query_after_separator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    project = _make_project(tmp_path)
    monkeypatch.chdir(project)
    commands: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        commands.append(cmd)
        assert kwargs["cwd"] == str(project.resolve())
        return _RunResult(stdout="README.md\n")

    monkeypatch.setattr(find_routes.subprocess, "run", _fake_run)

    results = await find_routes.find_files(query="--exec", directory=str(project))

    assert commands[0][-2:] == ["--", "--exec"]
    assert results == ["README.md"]
