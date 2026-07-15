"""Security validation for bundled Hub plugin packages."""

from __future__ import annotations

import hashlib
from pathlib import Path

from flocks.hub.models import HubPluginManifest


# Names we silently strip when copying a bundled package — generated
# bytecode caches and VCS metadata are noise rather than security risks
# and have historically caused install failures when present in the
# bundled tree (e.g. ``__pycache__`` left over from earlier dev runs).
SKIP_NAMES = {"__pycache__", ".git", ".svn", ".DS_Store"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 128), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _validate_python_syntax(package_dir: Path, manifest: HubPluginManifest) -> None:
    python_files = {
        package_dir / entrypoint
        for entrypoint in manifest.entrypoints
        if Path(entrypoint).suffix == ".py"
    }
    if manifest.type in {"tool", "device"}:
        python_files.update(
            path
            for path in package_dir.rglob("*.py")
            if not any(part in SKIP_NAMES for part in path.relative_to(package_dir).parts)
        )

    for path in sorted(python_files):
        if not path.is_file():
            continue
        relative_path = path.relative_to(package_dir).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise ValueError(f"Invalid Python source in package: {relative_path}: {exc}") from exc


def validate_package(package_dir: Path, manifest: HubPluginManifest) -> None:
    base = package_dir.resolve()
    if not base.is_dir():
        raise ValueError(f"Package directory not found: {package_dir}")

    for path in base.rglob("*"):
        rel = path.relative_to(base)
        if any(part in SKIP_NAMES for part in rel.parts):
            continue
        resolved = path.resolve()
        if base not in resolved.parents and resolved != base:
            raise ValueError(f"Path escapes package root: {rel.as_posix()}")

    for entrypoint in manifest.entrypoints:
        entry = base / entrypoint
        if not entry.exists():
            raise ValueError(f"Missing entrypoint: {entrypoint}")

    _validate_python_syntax(base, manifest)

    for rel_path, expected in manifest.checksums.items():
        if not expected:
            continue
        path = (base / rel_path).resolve()
        if base not in path.parents and path != base:
            raise ValueError(f"Checksum path escapes package root: {rel_path}")
        if not path.is_file():
            raise ValueError(f"Checksum file missing: {rel_path}")
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"Checksum mismatch for {rel_path}")
