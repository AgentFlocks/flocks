"""Fail installation/packaging if the installed runtime cannot build WebUI plugins.

Use the target Python interpreter, with its installed dependencies, to run this
script. All page and configuration writes are confined to a temporary directory.
No running service, user plugin or network download is involved.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def check_runtime(expected_root: Path, scratch: Path) -> None:
    from flocks.contracts.webui import builder
    from flocks.contracts.webui.store import WebUIPagesStore
    from flocks.hub.installer import _copy_webui_package_with_build

    if builder._repo_root().resolve() != expected_root.resolve():
        raise RuntimeError(f"Python imports a different Flocks runtime: {builder._repo_root()}")
    executable = builder.resolve_esbuild_bin()
    if executable is None:
        raise RuntimeError("runtime is missing an executable WebUI compiler (esbuild)")
    lock = json.loads((expected_root / "webui/package-lock.json").read_text(encoding="utf-8"))
    expected_version = lock["packages"]["node_modules/esbuild"]["version"]
    version = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                             timeout=15, check=True).stdout.strip()
    if version != expected_version:
        raise RuntimeError(f"esbuild {version} does not match locked version {expected_version}")

    source = scratch / "source"
    destination = scratch / "installed"
    store = WebUIPagesStore(root=source, project_root=None, legacy_root=None)
    store.create_page(page_id="runtime-check", title="Runtime check")
    _copy_webui_package_with_build("runtime-check", source, destination)
    installed = WebUIPagesStore(root=destination, project_root=None, legacy_root=None)
    if problem := builder.prebuilt_bundle_problem(installed, "runtime-check"):
        raise RuntimeError(f"installed test page {problem}")
    old_hash = installed.read_build_meta("runtime-check").hash

    entry = store.page_dir("runtime-check") / "src" / "index.tsx"
    entry.write_text("export default function Page() { return <div>Updated runtime check</div>; }\n", encoding="utf-8")
    _copy_webui_package_with_build("runtime-check", source, destination)
    if problem := builder.prebuilt_bundle_problem(installed, "runtime-check"):
        raise RuntimeError(f"updated test page {problem}")
    if installed.read_build_meta("runtime-check").hash == old_hash:
        raise RuntimeError("updated test page was not rebuilt")
    print(f"WebUI runtime check passed: esbuild {version}, real plugin install and update")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="flocks-webui-check-") as directory:
        scratch = Path(directory)
        # Set every directory override before imports initialize configuration or
        # validate database ownership. Nonempty overrides also beat .env values.
        # This checks the compiler, so use scratch SQLite without a live database.
        os.environ.update({
            "FLOCKS_ROOT": str(scratch / "root"),
            "FLOCKS_CONFIG_DIR": str(scratch / "root/config"),
            "FLOCKS_DATA_DIR": str(scratch / "root/data"),
            "FLOCKS_LOG_DIR": str(scratch / "root/logs"),
            "FLOCKS_WORKSPACE_DIR": str(scratch / "root/workspace"),
            "FLOCKS_CONTRACTS_WEBUI_ROOT": str(scratch / "pages"),
            "FLOCKS_USER_DEFINED_PAGES_ROOT": str(scratch / "legacy-pages"),
            "XDG_CONFIG_HOME": str(scratch / "xdg/config"),
            "XDG_DATA_HOME": str(scratch / "xdg/data"),
            "XDG_STATE_HOME": str(scratch / "xdg/state"),
            "FLOCKS_DB_BACKEND": "sqlite",
            "FLOCKS_DB_DSN": "",
            "FLOCKS_DB_DSN_FILE": "",
        })
        check_runtime(args.expected_root, scratch)


if __name__ == "__main__":
    main()
