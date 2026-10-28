"""Isolation for REA tests: HOME, FLOCKS_ROOT and data dir under tmp_path."""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def rea_isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    root = home / ".flocks"
    data = root / "data"
    workspace = tmp_path / "workspace"
    for directory in (data, workspace):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("npm_config_cache", str(home / ".npm"))
    # Full-tier lookups must not find the developer's own JDK or Ghidra.
    monkeypatch.setenv("FLOCKS_REA_JAVA_HOME", str(tmp_path / "no-jdk"))
    monkeypatch.setenv("FLOCKS_REA_GHIDRA_DIR", str(tmp_path / "opt" / "ghidra_12.1.4_PUBLIC"))

    from flocks.config.config import Config
    from flocks.rea import components, runtime
    from flocks.rea.installer import ReaInstaller
    from flocks.rea.uninstaller import ReaUninstaller
    from flocks.skill.skill import Skill
    from flocks.tool import tool_loader
    from flocks.workspace.manager import WorkspaceManager

    # The tool YAML directories are fixed when tool_loader is imported; keep
    # the MCP service registration out of the real ~/.flocks.
    tools_dir = root / "plugins" / "tools"
    monkeypatch.setattr(tool_loader, "_TOOLS_SUBDIR", tools_dir)
    monkeypatch.setattr(tool_loader, "_MCP_SUBDIR", tools_dir / tool_loader.TOOL_TYPE_MCP)
    # A developer's own JDK 21 must not be found; the real lookup stays reachable for its test.
    monkeypatch.setattr(runtime, "unpatched_java_home_candidates", runtime.java_home_candidates, raising=False)
    monkeypatch.setattr(runtime, "java_home_candidates", lambda: [os.environ["FLOCKS_REA_JAVA_HOME"]])
    monkeypatch.setattr(components, "_RETRY_DELAY_SECONDS", 0.0)
    from flocks.storage.storage import Storage

    Config._global_config = None
    Config._cached_config = None
    # REA keeps a value in the key-value store (the task reference salt): each
    # test gets its own database under its data dir, as it gets its own HOME.
    Storage._initialized = False
    Storage._db_path = None
    original_workspace = WorkspaceManager._instance
    WorkspaceManager._instance = None
    runtime.reset_environment_cache()
    ReaInstaller.reset_for_testing()
    ReaUninstaller.reset_for_testing()
    Skill.clear_cache()
    yield home
    ReaInstaller.reset_for_testing()
    ReaUninstaller.reset_for_testing()
    Storage._initialized = False
    Storage._db_path = None
    runtime.reset_environment_cache()
    Skill.clear_cache()
    WorkspaceManager._instance = original_workspace
    Config._global_config = None
    Config._cached_config = None


FAKE_NODE = """\
#!/bin/sh
# Stand-in for node: ``node --version`` reports FAKE_NODE_VERSION.
if [ "$1" = "--version" ]; then echo "${FAKE_NODE_VERSION:-v26.1.0}"; exit 0; fi
exit 0
"""

FAKE_NPX = """\
#!/bin/sh
# Stand-in for npx: ``npx -y rea-agents@6.4.0 --version`` reports FAKE_REA_VERSION and
# fills the npx cache; ``... mcp doctor --json`` prints a doctor report
# (FAKE_DOCTOR_MODE ok | unhealthy | crash); FAKE_NPX_MODE=network fails like npm.
if [ "${FAKE_NPX_MODE:-ok}" = "network" ]; then
  echo "npm error code ECONNREFUSED"
  echo "npm error FetchError: request to https://user:secret@registry.example.invalid/rea-agents failed"
  exit 1
fi
pkg="${npm_config_cache:-$HOME/.npm}/_npx/fakehash/node_modules/rea-agents"
mkdir -p "$pkg"
printf '{"name":"rea-agents","version":"%s"}' "${FAKE_REA_VERSION:-6.4.0}" > "$pkg/package.json"
if [ "$3" = "mcp" ] && [ "$4" = "doctor" ]; then
  case "${FAKE_DOCTOR_MODE:-ok}" in
    crash) echo "doctor crashed"; exit 1;;
    unhealthy)
      echo '{"healthy":false,"server":{"name":"rea","version":"6.4.0"},"inventory":{"tools":{"expected":170,"observed":12}},"request_flow":{"tool":"binary_session","ok":false},"checks":[{"name":"inventory:tools","ok":false},{"name":"request-flow","ok":false}]}'
      exit 1;;
  esac
  echo '{"healthy":true,"server":{"name":"rea","version":"6.4.0"},"inventory":{"tools":{"expected":170,"observed":170}},"request_flow":{"tool":"binary_session","ok":true},"checks":[{"name":"initialize","ok":true},{"name":"inventory:tools","ok":true},{"name":"request-flow","ok":true}]}'
  exit 0
fi
for last; do :; done
if [ "$last" = "--version" ]; then echo "${FAKE_REA_VERSION:-6.4.0}"; exit 0; fi
exit 0
"""


@pytest.fixture
def fake_toolchain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put fake ``node`` and ``npx`` first on PATH (POSIX shells only)."""

    if sys.platform.startswith("win"):
        pytest.skip("fake node/npx shims are POSIX shell scripts")
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for name, body in (("node", FAKE_NODE), ("npx", FAKE_NPX)):
        script = bin_dir / name
        script.write_text(textwrap.dedent(body), encoding="utf-8")
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return bin_dir


def write_skill(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\nname: reverse-engineer-anything\ndescription: REA skill\n---\n\n# REA\n", encoding="utf-8",
    )


@pytest.fixture
def fake_skill_source(monkeypatch: pytest.MonkeyPatch):
    """SkillInstaller.install_from_source without the network; ``failures`` sets how many attempts fail."""

    from flocks.rea import runtime
    from flocks.skill.installer import SkillInstaller

    state = {"calls": [], "failures": 0}

    class _Result:
        def __init__(self, success: bool, error: str | None = None):
            self.success = success
            self.error = error

    async def install_from_source(source, scope="global", **_kwargs):
        state["calls"].append((source, scope))
        if len(state["calls"]) <= state["failures"]:
            raise RuntimeError("GitHub unreachable: Connection reset by peer")
        write_skill(runtime.installed_skill_dir())
        return _Result(True)

    monkeypatch.setattr(SkillInstaller, "install_from_source", staticmethod(install_from_source))
    return state


class FakeMcp:
    """Flocks' MCP facade without processes: connect/disconnect/remove and a status snapshot."""

    def __init__(self):
        self.statuses: dict = {}
        self.configs: dict = {}
        self.connects: list = []
        self.disconnects: list = []
        self.removed: list = []
        self.connect_ok = True
        self.tools_count = 170
        self.initialized = True

    def _info(self, status: str, tools: int = 0, error=None):
        from flocks.mcp.types import McpStatus, McpStatusInfo

        return McpStatusInfo(status=McpStatus(status), tools_count=tools, error=error)

    def set_connected(self, name: str = "rea", tools: int = 170):
        self.statuses[name] = self._info("connected", tools)

    async def connect(self, name, config):
        self.connects.append((name, config))
        self.configs[name] = config
        if self.connect_ok:
            self.statuses[name] = self._info("connected", self.tools_count)
        else:
            self.statuses[name] = self._info("failed", 0, "spawn npx ENOENT")
        return self.connect_ok

    async def disconnect(self, name):
        self.disconnects.append(name)
        if name in self.statuses:
            self.statuses[name] = self._info("disconnected")
            return True
        return False

    async def remove(self, name):
        self.removed.append(name)
        self.statuses.pop(name, None)
        self.configs.pop(name, None)
        return True

    def server_config(self, name):
        if name in self.configs:
            return self.configs[name]
        if name in self.statuses:
            from flocks.rea import runtime

            # A status set by hand: as if this process connected the current registration at startup.
            return runtime.current_mcp_config()
        return None

    async def status(self):
        return dict(self.statuses)

    def status_snapshot(self):
        return dict(self.statuses), self.initialized


@pytest.fixture
def fake_mcp(monkeypatch: pytest.MonkeyPatch) -> FakeMcp:
    from flocks.mcp import MCP

    fake = FakeMcp()
    for name in ("connect", "disconnect", "remove", "status"):
        monkeypatch.setattr(MCP, name, getattr(fake, name))
    monkeypatch.setattr(MCP, "status_snapshot", fake.status_snapshot)
    monkeypatch.setattr(MCP, "server_config", fake.server_config)
    return fake
