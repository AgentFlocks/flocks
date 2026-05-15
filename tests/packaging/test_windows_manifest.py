import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_MANIFEST = REPO_ROOT / "packaging" / "windows" / "versions.manifest.json"


def _parse_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def test_windows_bundled_uv_supports_python_downloads_json_url() -> None:
    manifest = json.loads(WINDOWS_MANIFEST.read_text(encoding="utf-8"))

    assert _parse_version(manifest["uv"]["version"]) >= (0, 7, 3)


def test_windows_manifest_pins_bundled_python_runtime() -> None:
    manifest = json.loads(WINDOWS_MANIFEST.read_text(encoding="utf-8"))

    python = manifest["python"]
    assert _parse_version(python["version"]) >= (3, 12, 0)
    assert python["python_build_standalone_release"].isdigit()
    assert python["windows_archive_name"].endswith(".tar.gz")
    assert "install_only" in python["windows_archive_name"]
    assert "windows-msvc" in python["windows_archive_name"]
