"""Validate the bundled `.flocks/flockshub` catalog."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HUB_ROOT = ROOT / ".flocks" / "flockshub"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fail(message: str) -> None:
    raise SystemExit(message)


def ensure_relative(path: str) -> None:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        fail(f"Unsafe relative path: {path}")


def main() -> int:
    index_path = HUB_ROOT / "index.json"
    taxonomy_path = HUB_ROOT / "taxonomy.json"
    if not index_path.is_file():
        fail("Missing .flocks/flockshub/index.json")
    if not taxonomy_path.is_file():
        fail("Missing .flocks/flockshub/taxonomy.json")

    index = load_json(index_path)
    taxonomy = load_json(taxonomy_path)
    categories = {item["id"] for item in taxonomy.get("categories", []) if isinstance(item, dict) and item.get("id")}
    tags = set(taxonomy.get("tags", []))
    use_cases = set(taxonomy.get("useCases", []))
    risk_levels = set(taxonomy.get("riskLevels", []))

    seen: set[tuple[str, str]] = set()
    for entry in index.get("plugins", []):
        plugin_id = entry.get("id")
        plugin_type = entry.get("type")
        if not plugin_id or not plugin_type:
            fail(f"Plugin entry missing id/type: {entry}")
        key = (plugin_type, plugin_id)
        if key in seen:
            fail(f"Duplicate plugin id: {plugin_type}:{plugin_id}")
        seen.add(key)

        manifest_rel = entry.get("manifestPath")
        if not manifest_rel:
            fail(f"Plugin missing manifestPath: {plugin_type}:{plugin_id}")
        ensure_relative(manifest_rel)
        manifest_path = HUB_ROOT / manifest_rel
        if not manifest_path.is_file():
            fail(f"Manifest not found: {manifest_rel}")

        manifest = load_json(manifest_path)
        if manifest.get("id") != plugin_id or manifest.get("type") != plugin_type:
            fail(f"Manifest id/type mismatch: {manifest_rel}")
        if manifest.get("category") not in categories:
            fail(f"Unknown category in {manifest_rel}: {manifest.get('category')}")
        unknown_tags = set(manifest.get("tags", [])) - tags
        if unknown_tags:
            fail(f"Unknown tags in {manifest_rel}: {sorted(unknown_tags)}")
        unknown_use_cases = set(manifest.get("useCases", [])) - use_cases
        if unknown_use_cases:
            fail(f"Unknown useCases in {manifest_rel}: {sorted(unknown_use_cases)}")
        risk_level = (manifest.get("risk") or {}).get("level")
        if risk_level not in risk_levels:
            fail(f"Unknown risk level in {manifest_rel}: {risk_level}")

        package_dir = manifest_path.parent
        for entrypoint in manifest.get("entrypoints", []):
            ensure_relative(entrypoint)
            if not (package_dir / entrypoint).exists():
                fail(f"Missing entrypoint {entrypoint} in {manifest_rel}")

        for path in package_dir.rglob("*"):
            rel = path.relative_to(package_dir).as_posix()
            ensure_relative(rel)
            if any(part in {".git", "__pycache__"} for part in path.relative_to(package_dir).parts):
                fail(f"Disallowed path in {manifest_rel}: {rel}")

    print(f"Validated {len(seen)} bundled Hub plugins.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
