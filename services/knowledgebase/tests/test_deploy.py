import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parents[1]
DEPLOY = SERVICE / "deploy"
COMPOSE = DEPLOY / "compose.yaml"
HELPER = DEPLOY / "init_env.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("kb_init_env", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_env(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if line and not line.startswith("#"))


@pytest.fixture(scope="module")
def compose():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(COMPOSE.read_text())


def test_generator_uses_private_unique_urlsafe_secrets_without_printing(tmp_path, capsys):
    helper = load_helper()
    target = tmp_path / ".env"
    previous_umask = os.umask(0)
    try:
        assert helper.main(["--output", str(target)]) == 0
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    values = parse_env(target)
    assert list(values) == ["KB_API_TOKEN"]
    secret = values["KB_API_TOKEN"]
    assert len(secret) >= 32 and re.fullmatch(r"[a-zA-Z0-9_-]+", secret)
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    second = tmp_path / "another.env"
    helper.generate_env(second)
    assert parse_env(second)["KB_API_TOKEN"] != secret


def test_generator_refuses_overwrite(tmp_path, capsys):
    helper = load_helper()
    target = tmp_path / ".env"
    target.write_text("existing-file-must-stay-unchanged\n")
    before = target.stat()
    assert helper.main(["--output", str(target)]) == 1
    assert target.read_text() == "existing-file-must-stay-unchanged\n"
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    assert "Refusing to overwrite" in capsys.readouterr().err


@pytest.mark.parametrize("dangling", [True, False])
def test_generator_refuses_symlink(tmp_path, dangling):
    target = tmp_path / "outside"
    if not dangling:
        target.write_text("do-not-change")
    link = tmp_path / ".env"
    link.symlink_to(target)
    assert load_helper().main(["--output", str(link)]) == 1
    assert link.is_symlink()
    assert not target.exists() if dangling else target.read_text() == "do-not-change"


def test_generator_cli_is_stdlib_only_and_defaults_beside_script(tmp_path):
    helper = tmp_path / "init_env.py"
    shutil.copyfile(HELPER, helper)
    result = subprocess.run([sys.executable, "-I", "-S", str(helper)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    target = tmp_path / ".env"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert parse_env(target)["KB_API_TOKEN"] not in result.stdout


def test_compose_publishes_only_the_api_and_expects_external_ragflow(compose):
    services = compose["services"]
    assert set(services) == {"api"}
    api = services["api"]
    assert api["ports"] == ["127.0.0.1:18767:8767"]
    assert "ragflow" not in COMPOSE.read_text()
    assert "bootstrap" not in COMPOSE.read_text()
    environment = api["environment"]
    assert "KB_API_TOKEN" in environment
    assert "KB_RAGFLOW_BASE_URL" in environment
    assert "KB_RAGFLOW_API_KEY" in environment
    assert api["user"] == "10001:10001"
    assert api["read_only"] is True
    assert "ALL" in api["cap_drop"]
