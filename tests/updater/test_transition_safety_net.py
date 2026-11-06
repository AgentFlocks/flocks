"""The transition package's own build backend and the watchdog it starts (packaging/transition)."""

from __future__ import annotations

import fcntl
import http.server
import importlib.util
import json
import shutil
import socket
import sys
import tarfile
import textwrap
import threading
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
    monkeypatch.setattr(watchdog, "stop_what_started", lambda *_a: None)
    monkeypatch.setattr(watchdog, "start_service", lambda *a: started.append(a) or True)
    arguments = ["/venv/bin/python", "-m", watchdog.HANDOFF, "--install-root", str(install), "--backup-path", str(backup), "--frontend-host", "0.0.0.0", "--frontend-port", "5173"]
    assert watchdog.go_back(arguments, root, {}) is True
    assert started == [("/venv/bin/python", install, "0.0.0.0", 5173)]
    assert (install / "flocks" / "__init__.py").read_text() == "old = True\n"


def test_ts9_a_service_already_answering_is_left_alone_on_its_source(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The old updater started the new version and was stopped before its last word."""
    root = tmp_path / ".flocks"
    root.mkdir()
    backup = _backup(tmp_path)
    install = tmp_path / "install"
    (install / "flocks").mkdir(parents=True)
    (install / "flocks" / "__init__.py").write_text("new = True\n")
    monkeypatch.setattr(watchdog, "answers", lambda _host, _port: True)
    monkeypatch.setattr(watchdog, "stop_what_started", lambda *a: pytest.fail("stopped"))
    monkeypatch.setattr(watchdog, "start_service", lambda *a: pytest.fail("started"))
    assert watchdog.go_back(["/p", "--install-root", str(install), "--backup-path", str(backup)], root, {}) is True
    assert (install / "flocks" / "__init__.py").read_text() == "new = True\n"


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


class _Health(http.server.BaseHTTPRequestHandler):
    body = b"{}"

    def do_GET(self):
        self.send_response(200 if self.path == "/api/health" else 404)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args):
        pass


def _serving(body: bytes):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("Health", (_Health,), {"body": body}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("body", [b'{"healthy": true, "version": "2026.7.1"}', b'{"status": "healthy", "version": "2026.11.4"}'])
def test_ts13_answers_a_flocks_service_of_any_version(watchdog, body: bytes):
    server = _serving(body)
    try:
        assert watchdog.answers("0.0.0.0", server.server_address[1]) is True
    finally:
        server.shutdown()
        server.server_close()
    assert watchdog.answers("127.0.0.1", server.server_address[1]) is False


@pytest.mark.parametrize("body", [b"<html>not flocks</html>", b'{"status": "starting"}', b"[]"])
def test_ts14_a_stranger_on_the_port_is_no_service(watchdog, body: bytes):
    server = _serving(body)
    try:
        assert watchdog.answers("127.0.0.1", server.server_address[1]) is False
    finally:
        server.shutdown()
        server.server_close()
    with socket.socket() as plain:  # holds the port, says nothing
        plain.bind(("127.0.0.1", 0))
        plain.listen(1)
        assert watchdog.answers("127.0.0.1", plain.getsockname()[1]) is False


def test_ts15_of_all_a_build_starts_one_watches(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / ".flocks"
    (root / "run").mkdir(parents=True)
    (root / "run" / "upgrade-result.json").write_text(json.dumps({"phase": "failed"}))
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setattr(watchdog, "find_handoff", lambda _start: (99999, ["/p"]))
    monkeypatch.setattr(watchdog, "alive", lambda _pid: False)
    went: list[bool] = []
    monkeypatch.setattr(watchdog, "go_back", lambda *a: went.append(True) or True)
    with (root / "run" / "transition-watchdog.lock").open("ab") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert watchdog.main(["123"]) == 0
        assert went == []
    # The first one gone, whichever comes next has it: the next attempt is watched.
    assert watchdog.main(["123"]) == 0
    assert went == [True]


def test_ts16_what_is_coming_up_stops_before_the_source_changes_under_it(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / ".flocks"
    root.mkdir()
    backup = _backup(tmp_path)
    install = tmp_path / "install"
    install.mkdir()
    order: list[str] = []
    monkeypatch.setattr(watchdog, "answers", lambda _host, _port: False)
    monkeypatch.setattr(watchdog, "stop_what_started", lambda *_a: order.append("stop"))
    real = watchdog.restore_source
    monkeypatch.setattr(watchdog, "restore_source", lambda *a: (order.append("restore"), real(*a))[1])
    monkeypatch.setattr(watchdog, "start_service", lambda *_a: order.append("start") or True)
    assert watchdog.go_back(["/p", "--install-root", str(install), "--backup-path", str(backup)], root, {}) is True
    assert order == ["stop", "restore", "start"]


def test_ts17_stopping_what_is_not_there_is_no_failure(watchdog, tmp_path: Path):
    watchdog.stop_what_started(str(tmp_path / "no-such-python"), tmp_path)


def _web(tmp_path: Path, script: str | None) -> tuple[Path, Path]:
    """An install whose webui has the old source back and the transition package's interface; a
    fake npm running script. Returns the install and the data root."""
    root = tmp_path / ".flocks"
    (root / "run").mkdir(parents=True)
    (root / "run" / "upgrade-result.json").write_text(json.dumps({"phase": "failed"}))
    install = tmp_path / "install"
    web = install / "webui"
    (web / "dist").mkdir(parents=True)
    (web / "dist" / "index.html").write_text("transition")
    (web / "node_modules").mkdir()
    (web / "package.json").write_text("{}")
    if script is not None:
        tools = tmp_path / "bin"
        tools.mkdir()
        npm = tools / "npm"
        npm.write_text("#!/bin/sh\n" + script + "\n")
        npm.chmod(0o755)
    return install, root


def _npm_only_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path))


def test_ts18_the_old_interface_is_built_again(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    install, root = _web(tmp_path, 'echo "$1 $2" > args; rm -r dist; mkdir dist; echo old > dist/index.html')
    _npm_only_at(monkeypatch, tmp_path)
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    watchdog.rebuild_interface(install, root)
    assert (install / "webui" / "dist" / "index.html").read_text().strip() == "old"
    assert (install / "webui" / "args").read_text().strip() == "run build"
    assert not (install / "webui" / "dist.transition").exists()


@pytest.mark.parametrize("script", ["exit 3", "rm -r dist; exit 3", "rm -r dist; exit 0"])
def test_ts19_when_it_does_not_build_the_interface_there_was_stays(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str):
    install, root = _web(tmp_path, script)
    _npm_only_at(monkeypatch, tmp_path)
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    watchdog.rebuild_interface(install, root)
    assert (install / "webui" / "dist" / "index.html").read_text() == "transition"
    assert not (install / "webui" / "dist.transition").exists()


def test_ts20_without_npm_or_the_old_source_nothing_is_touched(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    install, root = _web(tmp_path, None)
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setattr(watchdog.shutil, "which", lambda *_a, **_k: None)
    watchdog.rebuild_interface(install, root)
    assert (install / "webui" / "dist" / "index.html").read_text() == "transition"
    monkeypatch.undo()
    install2, root2 = _web(tmp_path / "second", "touch ran")
    (install2 / "webui" / "package.json").unlink()
    monkeypatch.setenv("PATH", f"{tmp_path / 'second' / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv("FLOCKS_ROOT", str(root2))
    watchdog.rebuild_interface(install2, root2)
    assert not (install2 / "webui" / "ran").exists()


def test_ts21_another_upgrade_beginning_stops_the_build(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    install, root = _web(tmp_path, f"echo '{{\"phase\": \"running\"}}' > {tmp_path}/.flocks/run/upgrade-result.json; sleep 60")
    _npm_only_at(monkeypatch, tmp_path)
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setattr(watchdog, "POLL_SECONDS", 0.05)
    watchdog.rebuild_interface(install, root)
    # Nothing put back or removed: the tree is the new attempt's.
    assert (install / "webui" / "dist.transition").exists()


def test_ts22_the_interface_is_built_after_the_service_is_back_and_no_lock_held(watchdog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / ".flocks"
    (root / "run").mkdir(parents=True)
    (root / "run" / "upgrade-result.json").write_text(json.dumps({"phase": "failed"}))
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setattr(watchdog, "find_handoff", lambda _start: (99999, ["/p", "--install-root", str(install)]))
    monkeypatch.setattr(watchdog, "alive", lambda _pid: False)
    monkeypatch.setattr(watchdog, "go_back", lambda *a: True)
    seen: list[tuple] = []

    def build(where, data):
        result = json.loads((root / "run" / "upgrade-result.json").read_text())
        with (root / "run" / "transition-watchdog.lock").open("ab") as probe:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if still held
            fcntl.flock(probe, fcntl.LOCK_UN)
        seen.append((where, data, result["recovered_by_watchdog"]))

    monkeypatch.setattr(watchdog, "rebuild_interface", build)
    assert watchdog.main(["123"]) == 0
    assert seen == [(install, root, True)]
    # Not back, nothing to build for; and a build that fails is no failure of the going back.
    monkeypatch.setattr(watchdog, "go_back", lambda *a: False)
    assert watchdog.main(["123"]) == 1
    assert len(seen) == 1
    monkeypatch.setattr(watchdog, "go_back", lambda *a: True)
    monkeypatch.setattr(watchdog, "rebuild_interface", lambda *a: (_ for _ in ()).throw(OSError("no room")))
    assert watchdog.main(["123"]) == 0
