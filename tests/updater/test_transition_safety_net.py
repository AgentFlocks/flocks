"""The transition package's own build backend and the watchdog it starts (packaging/transition)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import sys
import tarfile
import textwrap
import zipfile
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "packaging" / "transition"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SOURCE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tree(tmp_path: Path):
    root = tmp_path / "flocks"
    root.mkdir()
    (root / "pyproject.toml").write_text(textwrap.dedent('''\
        [project]
        name = "flocks"
        version = "v2026.11.4"
        requires-python = ">=3.12,<3.13"
        dependencies = ["httpx>=0.27", "typer"]

        [project.scripts]
        flocks = "flocks.cli.main:app"
    '''))
    shutil.copy(SOURCE / "flocks_transition_build.py", root)
    shutil.copy(SOURCE / "flocks_transition_watchdog.py", root)
    spec = importlib.util.spec_from_file_location("flocks_transition_build_copy", root / "flocks_transition_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    armed: list[bool] = []
    module._arm_watchdog = lambda: armed.append(True)
    module.armed = armed
    return module, root


def test_ts1_the_backend_asks_for_nothing(tree):
    backend, _ = tree
    assert backend.get_requires_for_build_editable() == []
    assert backend.get_requires_for_build_wheel() == []
    assert backend.armed == [True]


def test_ts2_the_editable_wheel_points_at_the_tree(tree, tmp_path: Path):
    backend, root = tree
    out = tmp_path / "out"
    out.mkdir()
    name = backend.build_editable(str(out))
    assert name == "flocks-2026.11.4-py3-none-any.whl"
    with zipfile.ZipFile(out / name) as wheel:
        assert wheel.read("flocks.pth").decode().strip() == str(root)
        metadata = wheel.read("flocks-2026.11.4.dist-info/METADATA").decode()
        assert "Name: flocks" in metadata and "Version: 2026.11.4" in metadata and "Requires-Dist: typer" in metadata
        assert "flocks = flocks.cli.main:app" in wheel.read("flocks-2026.11.4.dist-info/entry_points.txt").decode()
        record = wheel.read("flocks-2026.11.4.dist-info/RECORD").decode()
        assert all(line.split(",")[0] in wheel.namelist() for line in record.splitlines())
    assert backend.armed == [True]


def test_ts3_metadata_alone(tree, tmp_path: Path):
    backend, _ = tree
    name = backend.prepare_metadata_for_build_editable(str(tmp_path))
    assert name == "flocks-2026.11.4.dist-info"
    assert (tmp_path / name / "METADATA").is_file() and (tmp_path / name / "entry_points.txt").is_file()


def test_ts4_a_watchdog_that_cannot_start_does_not_stop_the_build(tree, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    spec = importlib.util.spec_from_file_location("flocks_transition_build_real", SOURCE / "flocks_transition_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def no_processes(*_args, **_kwargs):
        raise OSError("no")

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(module.subprocess, "Popen", no_processes)
    module._arm_watchdog()  # says so, and returns


def _backup(tmp_path: Path) -> Path:
    old = tmp_path / "old-source" / "flocks"
    (old / "flocks").mkdir(parents=True)
    (old / "flocks" / "__init__.py").write_text("old = True\n")
    (old / "pyproject.toml").write_text('version = "v2026.10.19"\n')
    (old / "webui").mkdir()
    (old / "webui" / "package.json").write_text("{}\n")
    backup = tmp_path / "flocks-2026.10.19.tar.gz"
    with tarfile.open(backup, "w:gz") as archive:
        archive.add(old, arcname="flocks")
    return backup


@pytest.fixture
def watchdog():
    return _load("flocks_transition_watchdog")


def test_ts5_the_source_goes_back_over_the_new_one_and_the_interface_stays(watchdog, tmp_path: Path):
    backup = _backup(tmp_path)
    install = tmp_path / "install"
    (install / "flocks").mkdir(parents=True)
    (install / "flocks" / "__init__.py").write_text("new = True\n")
    (install / "flocks" / "only_new.py").write_text("x = 1\n")
    (install / "pyproject.toml").write_text('version = "v2026.11.4"\n')
    (install / "webui" / "dist").mkdir(parents=True)
    (install / "webui" / "dist" / "index.html").write_text("<html>")
    watchdog.restore_source(backup, install)
    assert (install / "flocks" / "__init__.py").read_text() == "old = True\n"
    assert "2026.10.19" in (install / "pyproject.toml").read_text()
    assert (install / "webui" / "package.json").is_file()
    assert (install / "webui" / "dist" / "index.html").read_text() == "<html>"
    assert (install / "flocks" / "only_new.py").is_file()


def test_ts6_a_path_out_of_the_tree_is_not_followed(watchdog, tmp_path: Path):
    payload = tmp_path / "payload.txt"
    payload.write_text("x")
    backup = tmp_path / "evil.tar.gz"
    with tarfile.open(backup, "w:gz") as archive:
        archive.add(payload, arcname="flocks/../../escaped.txt")
    install = tmp_path / "a" / "b" / "install"
    install.mkdir(parents=True)
    watchdog.restore_source(backup, install)
    assert not (tmp_path / "a" / "escaped.txt").exists() and not (tmp_path / "a" / "b" / "escaped.txt").exists()


def test_ts7_the_records_go_back_as_they_were(watchdog, tmp_path: Path):
    root = tmp_path / ".flocks"
    (root / "version").mkdir(parents=True)
    (root / "version" / ".current_version").write_text("2026.10.19")
    kept = watchdog.records(root)
    (root / "version" / ".current_version").write_text("2026.11.4.14")
    (root / "run").mkdir()
    (root / "run" / "pro-bundle-installed.json").write_text("{}")
    watchdog.restore_records(kept)
    assert (root / "version" / ".current_version").read_text() == "2026.10.19"
    assert not (root / "run" / "pro-bundle-installed.json").exists()


def test_ts8_going_back_starts_the_service_from_the_old_source(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / ".flocks"
    root.mkdir()
    backup = _backup(tmp_path)
    install = tmp_path / "install"
    install.mkdir()
    started: list[tuple] = []
    monkeypatch.setattr(watchdog, "answers", lambda _host, _port: False)
    monkeypatch.setattr(watchdog, "start_service", lambda *a: started.append(a) or True)
    arguments = ["/venv/bin/python", "-m", watchdog.HANDOFF, "--install-root", str(install), "--backup-path", str(backup), "--frontend-host", "0.0.0.0", "--frontend-port", "5173"]
    assert watchdog.go_back(arguments, root, {}) is True
    assert started == [("/venv/bin/python", install, "0.0.0.0", 5173)]
    assert (install / "flocks" / "__init__.py").read_text() == "old = True\n"


def test_ts9_something_already_answering_is_left_alone(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(watchdog, "answers", lambda _host, _port: True)
    monkeypatch.setattr(watchdog, "start_service", lambda *a: pytest.fail("started"))
    assert watchdog.go_back(["/p", "--install-root", str(install)], tmp_path, {}) is True


def test_ts10_no_handoff_above_it_nothing_happens(watchdog, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(watchdog, "find_handoff", lambda _start: None)
    monkeypatch.setattr(watchdog, "go_back", lambda *a: pytest.fail("went back"))
    assert watchdog.main(["123"]) == 0


@pytest.mark.parametrize(("result", "goes_back"), [({"phase": "done"}, False), ({"phase": "failed", "failed_stage": "install"}, True), ({"phase": "running"}, True), ({}, True)])
def test_ts11_only_an_end_that_is_not_done_goes_back(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: dict, goes_back: bool):
    root = tmp_path / ".flocks"
    (root / "run").mkdir(parents=True)
    if result:
        (root / "run" / "upgrade-result.json").write_text(json.dumps(result))
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setattr(watchdog, "find_handoff", lambda _start: (99999, ["/p"]))
    monkeypatch.setattr(watchdog, "alive", lambda _pid: False)
    went: list[bool] = []
    monkeypatch.setattr(watchdog, "go_back", lambda *a: went.append(True) or True)
    assert watchdog.main(["123"]) == 0
    assert bool(went) is goes_back
    if goes_back and result:
        assert json.loads((root / "run" / "upgrade-result.json").read_text())["recovered_by_watchdog"] is True


def test_ts12_the_handoff_is_found_among_the_ancestors(watchdog, monkeypatch: pytest.MonkeyPatch):
    tree_of = {30: (20, ["python", "backend"]), 20: (10, ["uv", "sync"]), 10: (1, ["/venv/bin/python", "-m", watchdog.HANDOFF, "--install-root", "/x"])}
    monkeypatch.setattr(watchdog, "_arguments", lambda pid: tree_of[pid][1])
    monkeypatch.setattr(watchdog, "_ps", lambda pid, _field: str(tree_of[pid][0]))
    assert watchdog.find_handoff(30) == (10, tree_of[10][1])
    monkeypatch.setattr(watchdog, "_arguments", lambda pid: ["other"])
    assert watchdog.find_handoff(30) is None


def test_ts13_answers(watchdog):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert watchdog.answers("0.0.0.0", port) is True
    assert watchdog.answers("127.0.0.1", port) is False
