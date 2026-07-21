"""Runtime helpers for the bundled WhatsApp bridge."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .config import coerce_int

NODE_USE_BUNDLED_CA_OPTION = "--use-bundled-ca"


def file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def config_hash(values: dict[str, Any]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def append_node_options(env: dict[str, str], *options: str) -> None:
    raw = str(env.get("NODE_OPTIONS") or "").strip()
    try:
        current = shlex.split(raw) if raw else []
    except ValueError:
        current = raw.split()

    additions = [option for option in options if option and option not in current]
    if not additions:
        return
    env["NODE_OPTIONS"] = " ".join([part for part in [raw, *additions] if part]).strip()


async def ensure_bridge_deps(bridge_dir: Path) -> None:
    package_json = bridge_dir / "package.json"
    if not package_json.exists():
        raise RuntimeError(f"WhatsApp bridge package.json not found: {package_json}")
    node_modules = bridge_dir / "node_modules"
    stamp = node_modules / ".flocks-pkg-hash"
    pkg_hash = file_hash(package_json)
    deps_fresh = False
    if node_modules.exists():
        try:
            deps_fresh = stamp.read_text(encoding="utf-8").strip() == pkg_hash and bool(pkg_hash)
        except OSError:
            deps_fresh = False
    if deps_fresh:
        return

    from .config import find_executable

    npm = find_executable("npm")
    if not npm:
        raise RuntimeError("npm is required to install WhatsApp bridge dependencies")
    install_cmd = [npm, "ci", "--silent"] if (bridge_dir / "package-lock.json").exists() else [npm, "install", "--silent"]
    result = await asyncio.to_thread(
        subprocess.run,
        install_cmd,
        cwd=str(bridge_dir),
        capture_output=True,
        text=True,
        timeout=coerce_int(os.getenv("FLOCKS_WHATSAPP_NPM_INSTALL_TIMEOUT"), 300),
    )
    if result.returncode != 0:
        raise RuntimeError(f"WhatsApp bridge npm install failed: {result.stderr or result.stdout}")
    if pkg_hash:
        try:
            stamp.write_text(pkg_hash, encoding="utf-8")
        except OSError:
            pass
