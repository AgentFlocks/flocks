"""Read-only developer preflight: check the selected checkout before testing.

Run from either repository (no install, fetch, repair, restart or schema writes):
  python3 scripts/dev_baseline.py --frontend
  python3 ../flocks/scripts/dev_baseline.py --pro . --frontend --require-test-pg

Preparation, when needed, is explicit: `uv sync --group dev --frozen` in Core,
`uv sync --extra test --frozen` in Pro, and `npm ci` in Core/webui. Set only
FLOCKS_TEST_PG_DSN for disposable test PostgreSQL; production DSNs are not used.
Core CI runs tests/scripts, tests/hub, tests/net, tests/db and tests/voice;
WebUI CI runs lint, UI spec, `npm run test:run`, and build. Pro CI selects its
Core repository/SHA through .github/flocks-core-pair.json or workflow_dispatch inputs.
Dirty checkouts are valid local work, but cannot identify a remote CI pair.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=30,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                               "UV_PROJECT_ENVIRONMENT": str(cwd / ".venv")})


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def emit(self, status: str, name: str, detail: str) -> None:
        self.failures += status == "FAIL"
        print(f"{status} {name}: {detail}")


def checkout(root: Path, report: Report, require_clean: bool) -> None:
    head = run(["git", "rev-parse", "HEAD"], root)
    if head.returncode:
        report.emit("FAIL", root.name, "not a Git checkout")
        return
    status = run(["git", "status", "--porcelain", "--untracked-files=normal"], root)
    dirty = bool(status.stdout.strip())
    report.emit("FAIL" if dirty and require_clean else "PASS", root.name,
                f"root={root} HEAD={head.stdout.strip()} dirty={dirty}")
    upstream = run(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], root)
    if upstream.returncode:
        report.emit("INFO", f"{root.name} upstream", "none (detached CI checkouts are expected)")
    else:
        counts = run(["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"], root)
        report.emit("INFO", f"{root.name} upstream",
                    f"{upstream.stdout.strip()} ahead/behind={counts.stdout.strip()} (cached refs; no fetch)")
    if dirty:
        report.emit("INFO", f"{root.name} reproducibility", "local changes are not represented by HEAD; not a remote pair")


def python_path(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def check_import(root: Path, module: str, expected: Path, report: Report) -> None:
    python = python_path(root)
    if not python.is_file():
        report.emit("FAIL", f"{root.name} Python", "missing project .venv; run the documented frozen dependency install")
        return
    # Isolated Python bypasses cwd/PYTHONPATH, so an accidentally installed old
    # wheel cannot be hidden by importing the source directory from cwd.
    code = (
        "import importlib.util,json,sys; "
        f"spec=importlib.util.find_spec({module!r}); "
        "print(json.dumps({'origin':spec.origin if spec else None,'python':list(sys.version_info[:2])}))"
    )
    result = run([str(python), "-I", "-B", "-c", code], root)
    if result.returncode:
        report.emit("FAIL", f"{root.name} {module} import", "unable to inspect module origin")
        return
    data = json.loads(result.stdout)
    origin = Path(data["origin"]).resolve() if data["origin"] else None
    ok = origin is not None and origin.is_relative_to(expected.resolve()) and data["python"] == [3, 12]
    report.emit("PASS" if ok else "FAIL", f"{root.name} {module} import",
                f"{origin}; Python={'.'.join(map(str, data['python']))}; expected={expected.resolve()}")


def check_lock(root: Path, report: Report) -> None:
    if not shutil.which("uv"):
        report.emit("FAIL", f"{root.name} lock", "uv unavailable")
        return
    result = run(["uv", "lock", "--check", "--offline"], root)
    # Do not echo resolver errors: dependency URLs can contain credentials.
    report.emit("PASS" if result.returncode == 0 else "FAIL", f"{root.name} lock",
                "uv lock --check --offline" + (" (inspect the lock/dependency cache locally)" if result.returncode else ""))


def check_environment(root: Path, report: Report, *, pro: bool) -> None:
    # Reuse uv's non-mutating lock comparison, including editable source
    # overrides; pip check alone cannot establish the installed lock profile.
    command = ["uv", "sync", "--check", "--frozen", "--offline"]
    command.extend(["--extra", "test"] if pro else ["--group", "dev"])
    result = run(command, root)
    profile = "Pro test extra" if pro else "Core dev group"
    report.emit("PASS" if result.returncode == 0 else "FAIL", f"{root.name} installed environment",
                f"uv sync --check: {profile}" +
                (" (outdated or unavailable dependencies; run the documented frozen install)" if result.returncode else ""))


def frontend_issues(webui: Path, pro: Path | None) -> list[str]:
    issues = []
    package = json.loads((webui / "package.json").read_text())
    lock = json.loads((webui / "package-lock.json").read_text())
    root_lock = lock.get("packages", {}).get("", {})
    for group in ("dependencies", "devDependencies"):
        if package.get(group, {}) != root_lock.get(group, {}):
            issues.append(f"package.json {group} differs from package-lock.json")
        for name in package.get(group, {}):
            installed = webui / "node_modules" / name / "package.json"
            wanted = lock.get("packages", {}).get("node_modules/" + name, {}).get("version")
            if not installed.is_file() or json.loads(installed.read_text()).get("version") != wanted:
                issues.append(f"locked dependency not installed: {name}")
    if pro:
        modules = pro / "webui-overlay" / "node_modules"
        if not modules.is_dir() or modules.resolve() != (webui / "node_modules").resolve():
            issues.append("overlay node_modules must resolve to the selected Core webui/node_modules (build-webui.sh prepares a missing link)")
    return issues


def check_frontend(core: Path, pro: Path | None, report: Report) -> None:
    if not shutil.which("node"):
        report.emit("FAIL", "frontend", "Node.js 22+ unavailable")
        return
    version = run(["node", "--version"], core)
    major = int(version.stdout.strip().lstrip("v").split(".")[0])
    report.emit("PASS" if major >= 22 else "FAIL", "Node.js", version.stdout.strip())
    for issue in frontend_issues(core / "webui", pro):
        report.emit("FAIL", "frontend dependencies", issue)
    if report.failures == 0:
        report.emit("PASS", "frontend dependencies", "direct dependency versions match Core package-lock.json; overlay path checked when selected")


def check_test_postgres(core: Path, environment: Path, report: Report, required: bool) -> None:
    if not os.environ.get("FLOCKS_TEST_PG_DSN"):
        report.emit("FAIL" if required else "SKIP", "test PostgreSQL",
                    "FLOCKS_TEST_PG_DSN is unset; production DSNs are never substituted")
        return
    python = python_path(environment)
    probe = core / "packaging" / "linux" / "installer" / "postgres_preflight.py"
    code = (
        "import asyncio,os,runpy; "
        f"probe=runpy.run_path({str(probe)!r}); "
        "asyncio.run(probe['check_connection'](os.environ['FLOCKS_TEST_PG_DSN']))"
    )
    result = run([str(python), "-I", "-B", "-c", code], environment)
    report.emit("PASS" if result.returncode == 0 else "FAIL", "test PostgreSQL",
                "installer check_connection: read-only SELECT 1" if result.returncode == 0
                else "connection probe failed (details withheld to avoid exposing credentials)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pro", type=Path, help="paired FlocksPro checkout; verifies its installed Core import path")
    parser.add_argument("--frontend", action="store_true", help="also require Node and locked WebUI dependencies")
    parser.add_argument("--require-test-pg", action="store_true", help="fail instead of skipping an unset test DSN")
    parser.add_argument("--require-clean", action="store_true", help="reject local modifications (normally used by CI)")
    args = parser.parse_args()
    core = Path(__file__).resolve().parents[1]
    pro = args.pro.resolve() if args.pro else None
    report = Report()
    report.emit("INFO", "cwd", str(Path.cwd().resolve()))
    try:
        for root in [core] + ([pro] if pro else []):
            checkout(root, report, args.require_clean)
            check_lock(root, report)
        if pro:
            check_environment(pro, report, pro=True)
            check_import(pro, "flocks", core, report)
            check_import(pro, "flockspro", pro / "src", report)
        else:
            check_environment(core, report, pro=False)
            check_import(core, "flocks", core, report)
        if args.frontend:
            check_frontend(core, pro, report)
        check_test_postgres(core, pro or core, report, args.require_test_pg)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report.emit("FAIL", "preflight", f"{type(error).__name__}; check local paths and tool availability")
    print(f"baseline: {report.failures} failure(s); no installation, service restart or database writes performed")
    return int(report.failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
