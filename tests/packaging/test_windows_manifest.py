import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_MANIFEST = REPO_ROOT / "packaging" / "windows" / "versions.manifest.json"


def _parse_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def test_windows_bundled_uv_supports_python_downloads_json_url() -> None:
    manifest = json.loads(WINDOWS_MANIFEST.read_text(encoding="utf-8"))

    assert _parse_version(manifest["uv"]["version"]) >= (0, 7, 3)
