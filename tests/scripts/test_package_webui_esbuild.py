import base64
import hashlib
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "package_webui_esbuild.py"
spec = importlib.util.spec_from_file_location("package_webui_esbuild", SCRIPT)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


@pytest.fixture
def inputs(tmp_path):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        info = tarfile.TarInfo("package/bin/esbuild")
        binary = b"test executable"
        info.size = len(binary)
        tar.addfile(info, io.BytesIO(binary))
    archive = tmp_path / "esbuild.tgz"
    archive.write_bytes(data.getvalue())
    lock = tmp_path / "package-lock.json"
    lock.write_text(json.dumps({"packages": {
        "node_modules/esbuild": {"version": "0.27.2"},
        "node_modules/@esbuild/linux-arm64": {
            "version": "0.27.2",
            "resolved": "https://registry.npmjs.org/@esbuild/linux-arm64/-/linux-arm64-0.27.2.tgz",
            "integrity": "sha512-" + base64.b64encode(hashlib.sha512(data.getvalue()).digest()).decode(),
        },
    }}))
    return lock, archive, tmp_path / "runtime" / "webui" / "node_modules" / ".bin" / "esbuild"


def test_ships_only_the_locked_executable_and_can_repack(inputs):
    lock, archive, output = inputs
    assert package.prepare(lock, "linux-arm64", output, archive=archive) == "0.27.2"
    assert output.read_bytes() == b"test executable"
    assert os.access(output, os.X_OK)
    assert package.prepare(lock, "linux-arm64", output, archive=archive) == "0.27.2"


def test_rejects_corrupted_archive_before_writing_runtime(inputs):
    lock, archive, output = inputs
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        package.prepare(lock, "linux-arm64", output, archive=archive)
    assert not output.exists()


def test_rejects_incompatible_versions_and_preserves_existing_output(inputs):
    lock, archive, output = inputs
    record = json.loads(lock.read_text())
    record["packages"]["node_modules/esbuild"]["version"] = "0.0.1"
    lock.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="different locked versions"):
        package.prepare(lock, "linux-arm64", output, archive=archive)
    record["packages"]["node_modules/esbuild"]["version"] = "0.27.2"
    lock.write_text(json.dumps(record))
    output.parent.mkdir(parents=True)
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        package.prepare(lock, "linux-arm64", output, archive=archive)
    assert output.read_bytes() == b"existing"


def test_rejects_a_symlink_in_the_archive(inputs):
    lock, archive, output = inputs
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        info = tarfile.TarInfo("package/bin/esbuild")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../outside"
        tar.addfile(info)
    archive.write_bytes(data.getvalue())
    record = json.loads(lock.read_text())
    record["packages"]["node_modules/@esbuild/linux-arm64"]["integrity"] = (
        "sha512-" + base64.b64encode(hashlib.sha512(data.getvalue()).digest()).decode()
    )
    lock.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="exactly one valid executable"):
        package.prepare(lock, "linux-arm64", output, archive=archive)
    assert not output.exists()


@pytest.mark.parametrize("target", package.PLATFORMS)
def test_extracts_the_correct_member_for_each_release_platform(tmp_path, target):
    data = io.BytesIO()
    name = "package/esbuild.exe" if target.startswith("windows-") else "package/bin/esbuild"
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        member = tarfile.TarInfo(name)
        member.size = 6
        tar.addfile(member, io.BytesIO(b"native"))
    archive = tmp_path / "compiler.tgz"
    archive.write_bytes(data.getvalue())
    lock = tmp_path / "package-lock.json"
    lock.write_text(json.dumps({"packages": {
        "node_modules/esbuild": {"version": "0.27.2"},
        f"node_modules/@esbuild/{package.PLATFORMS[target]}": {
            "version": "0.27.2", "resolved": "https://example.invalid/compiler.tgz",
            "integrity": "sha512-" + base64.b64encode(hashlib.sha512(data.getvalue()).digest()).decode(),
        },
    }}))
    output = tmp_path / Path(name).name
    assert package.prepare(lock, target, output, archive=archive) == "0.27.2"
    assert output.read_bytes() == b"native"


@pytest.mark.parametrize("system,machine,target", [
    ("Linux", "x86_64", "linux-amd64"),
    ("Linux", "aarch64", "linux-arm64"),
    ("Darwin", "arm64", "darwin-arm64"),
    ("Darwin", "x86_64", "darwin-amd64"),
    ("Windows", "AMD64", "windows-amd64"),
    ("Windows", "ARM64", "windows-arm64"),
])
def test_auto_target_matches_the_host(monkeypatch, system, machine, target):
    monkeypatch.setattr(package.host_platform, "system", lambda: system)
    monkeypatch.setattr(package.host_platform, "machine", lambda: machine)
    assert package.host_target() == target
