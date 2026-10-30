"""Prepare the locked, platform-native esbuild executable for a runtime package.

Only the executable is shipped: it is self-contained and needs neither Node.js
nor the rest of the WebUI's development dependencies at runtime.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import platform as host_platform
from pathlib import Path
import tarfile
import tempfile
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen


PLATFORMS = {
    "linux-arm64": "linux-arm64",
    "linux-amd64": "linux-x64",
    "darwin-arm64": "darwin-arm64",
    "darwin-amd64": "darwin-x64",
    "windows-amd64": "win32-x64",
    "windows-arm64": "win32-arm64",
}
MAX_ARCHIVE_SIZE = 64 * 1024 * 1024
MAX_BINARY_SIZE = 64 * 1024 * 1024


def host_target() -> str:
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}.get(host_platform.system())
    machine = host_platform.machine().lower()
    arch = {"x86_64": "amd64", "x64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    target = f"{system}-{arch}"
    if target not in PLATFORMS:
        raise ValueError(f"unsupported esbuild platform: {target}")
    return target


def _archive_details(lock: Path, platform: str, registry: str | None) -> tuple[str, str, bytes]:
    packages = json.loads(lock.read_text(encoding="utf-8"))["packages"]
    package = packages[f"node_modules/@esbuild/{PLATFORMS[platform]}"]
    version = packages["node_modules/esbuild"]["version"]
    if package["version"] != version:
        raise ValueError("esbuild and its platform package have different locked versions")
    algorithm, separator, encoded = package["integrity"].partition("-")
    if algorithm != "sha512" or not separator:
        raise ValueError("esbuild package needs a locked SHA-512 integrity value")
    expected = base64.b64decode(encoded, validate=True)
    if len(expected) != hashlib.sha512().digest_size:
        raise ValueError("invalid esbuild SHA-512 integrity value")
    url = package["resolved"]
    if registry:
        mirror = urlsplit(registry)
        original = urlsplit(url)
        if mirror.scheme not in {"http", "https"} or not mirror.netloc:
            raise ValueError("npm registry must be an HTTP(S) URL")
        url = urlunsplit((mirror.scheme, mirror.netloc, mirror.path.rstrip("/") + original.path, "", ""))
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("esbuild package URL must use HTTP(S)")
    return version, url, expected


def prepare(lock: Path, platform: str, output: Path, *, registry: str | None = None,
            archive: Path | None = None) -> str:
    version, url, expected = _archive_details(lock, platform, registry)
    if archive is None:
        with urlopen(url, timeout=60) as response:
            data = response.read(MAX_ARCHIVE_SIZE + 1)
    else:
        with archive.open("rb") as source:
            data = source.read(MAX_ARCHIVE_SIZE + 1)
    if len(data) > MAX_ARCHIVE_SIZE:
        raise ValueError("esbuild package exceeds the archive size limit")
    if not hmac.compare_digest(hashlib.sha512(data).digest(), expected):
        raise ValueError("esbuild package does not match package-lock.json integrity")

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        name = "package/esbuild.exe" if platform.startswith("windows-") else "package/bin/esbuild"
        matches = [member for member in tar if member.name == name]
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size > MAX_BINARY_SIZE:
            raise ValueError("esbuild package does not contain exactly one valid executable")
        member = tar.extractfile(matches[0])
        if member is None:
            raise ValueError("esbuild executable cannot be read")
        binary = member.read(MAX_BINARY_SIZE + 1)
    if not binary or len(binary) > MAX_BINARY_SIZE:
        raise ValueError("invalid esbuild executable size")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.is_symlink() or not output.is_file() or output.read_bytes() != binary:
            raise FileExistsError(f"{output} already exists with different content")
        if not os.access(output, os.X_OK):
            raise PermissionError(f"{output} is not executable")
        return version
    # Write a complete file before publishing it; preserve any existing output.
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".esbuild-", delete=False) as tmp:
        temp = Path(tmp.name)
        tmp.write(binary)
    try:
        temp.chmod(0o755)
        os.link(temp, output)
    finally:
        temp.unlink()
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--platform", choices=[*PLATFORMS, "auto"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registry", default=os.environ.get("FLOCKS_NPM_REGISTRY"))
    parser.add_argument("--archive", type=Path, help="use an already downloaded npm archive")
    args = parser.parse_args()
    if args.platform == "auto":
        args.platform = host_target()
    version = prepare(args.lock, args.platform, args.output, registry=args.registry, archive=args.archive)
    print(f"webui esbuild {version} ({args.platform}) -> {args.output}")


if __name__ == "__main__":
    main()
