import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from flocks.contracts.webui import builder


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_webui_runtime", ROOT / "scripts/check_webui_runtime.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
NATIVE_COMPILER = builder.resolve_esbuild_bin()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    webui = root / "webui"
    webui.mkdir(parents=True)
    shutil.copyfile(ROOT / "webui/package-lock.json", webui / "package-lock.json")
    monkeypatch.setattr(builder, "_repo_root", lambda: root)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "user-data"))
    return root


def test_gate_rejects_missing_compiler(runtime, tmp_path):
    with pytest.raises(RuntimeError, match="missing an executable WebUI compiler"):
        gate.check_runtime(runtime, tmp_path / "scratch")
    assert not (tmp_path / "scratch").exists()


def test_gate_rejects_another_runtime(runtime, tmp_path):
    with pytest.raises(RuntimeError, match="different Flocks runtime"):
        gate.check_runtime(tmp_path / "other", tmp_path / "scratch")


def test_gate_rejects_wrong_compiler_version(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "resolve_esbuild_bin", lambda: Path("compiler"))
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "0.0.1\n", ""))
    with pytest.raises(RuntimeError, match="does not match locked version"):
        gate.check_runtime(runtime, tmp_path / "scratch")
    assert not (tmp_path / "scratch").exists()


@pytest.mark.skipif(NATIVE_COMPILER is None, reason="requires the locked native compiler (provided by CI)")
def test_gate_builds_and_updates_using_only_a_packaged_compiler(runtime, tmp_path, monkeypatch):
    target = runtime / "webui/node_modules/.bin" / ("esbuild.exe" if sys.platform == "win32" else "esbuild")
    target.parent.mkdir(parents=True)
    shutil.copy2(NATIVE_COMPILER, target)
    # Native esbuild and all imports must work without npm/Node on PATH.
    monkeypatch.setenv("PATH", str(tmp_path / "no-executables"))
    gate.check_runtime(runtime, tmp_path / "scratch")
    assert (tmp_path / "scratch/installed/runtime-check/dist/page.js").is_file()
    assert not (tmp_path / "user-data/plugins").exists()


@pytest.mark.parametrize("configuration", ["explicit", "xdg", "dotenv"])
def test_main_isolates_existing_postgres_data_and_dotenv(tmp_path, monkeypatch, configuration):
    from dotenv import load_dotenv
    from flocks import db_backend

    data = tmp_path / "customer-data"
    config = tmp_path / "customer-config"
    data.mkdir()
    config.mkdir()
    marker = data / "storage-backend.json"
    marker.write_text(json.dumps({"version": 1, "backend": "postgres"}))
    (config / "database.json").write_text(json.dumps({"backend": "postgres"}))
    external = {
        "FLOCKS_ROOT": str(tmp_path / "customer-root"),
        "FLOCKS_CONFIG_DIR": str(config),
        "FLOCKS_DATA_DIR": str(data),
        "FLOCKS_LOG_DIR": str(tmp_path / "customer-logs"),
        "FLOCKS_WORKSPACE_DIR": str(tmp_path / "customer-workspace"),
        "FLOCKS_CONTRACTS_WEBUI_ROOT": str(tmp_path / "customer-pages"),
        "FLOCKS_USER_DEFINED_PAGES_ROOT": str(tmp_path / "customer-legacy-pages"),
        "XDG_CONFIG_HOME": str(tmp_path / "customer-xdg-config"),
        "XDG_DATA_HOME": str(tmp_path / "customer-xdg-data"),
        "XDG_STATE_HOME": str(tmp_path / "customer-xdg-state"),
        "FLOCKS_DB_DSN": "postgresql://example.invalid/runtime-check",
        "FLOCKS_DB_DSN_FILE": str(tmp_path / "customer-dsn"),
    }
    # Use a copy so the script's process-level environment changes stay in this test.
    environ = {key: value for key, value in os.environ.items()
               if key not in external and key != "FLOCKS_DB_BACKEND"}
    if configuration != "dotenv":
        environ.update(external)
    if configuration == "xdg":
        environ.pop("FLOCKS_DATA_DIR")
        xdg_data = Path(external["XDG_DATA_HOME"]) / "flocks"
        xdg_data.mkdir(parents=True)
        shutil.copyfile(marker, xdg_data / marker.name)
    monkeypatch.setattr(os, "environ", environ)
    monkeypatch.setattr(sys, "argv", [str(SPEC.origin), "--expected-root", str(ROOT)])
    dotenv = tmp_path / "runtime.env"
    dotenv.write_text("\n".join(f"{key}={value}" for key, value in external.items()))
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    checked = []

    def check(expected_root, scratch):
        # Match flocks.startup_env.load: importing Flocks may load the source .env.
        load_dotenv(dotenv, override=False)
        assert expected_root == ROOT
        for key in external:
            if not key.startswith("FLOCKS_DB_"):
                assert Path(os.environ[key]).is_relative_to(scratch), key
        assert db_backend.data_dir().is_relative_to(scratch)
        assert db_backend.config_dir().is_relative_to(scratch)
        choice = db_backend.backend_choice()
        assert choice.is_sqlite
        db_backend._validate_commitment(choice)
        assert os.environ["FLOCKS_DB_DSN"] == os.environ["FLOCKS_DB_DSN_FILE"] == ""
        checked.append(scratch)

    monkeypatch.setattr(gate, "check_runtime", check)
    gate.main()
    assert len(checked) == 1
    assert not checked[0].exists()
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
