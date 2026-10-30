"""Workflow requirements handling."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

_CN_PYPI_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple"


def _default_cache_dir() -> Path:
    raw = os.getenv("FLOCKS_WORKFLOW_REQUIREMENTS_CACHE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".cache" / "flocks-workflow" / "requirements").resolve()


def _normalize_requirements(reqs: Iterable[str]) -> List[str]:
    out: List[str] = []
    for r in reqs:
        s = (r or "").strip()
        if not s:
            continue
        out.append(s)
    return sorted(out)


def unsatisfied_requirements(requirements: Iterable[str]) -> List[str]:
    """The requirements this interpreter does not already meet, as written.

    One that cannot be parsed, names a URL, asks for extras whose dependencies are not all
    installed, or whose version cannot be read or checked here counts as unmet: callers only
    skip what is certainly installed.
    """
    from importlib import metadata

    reqs = _normalize_requirements(requirements)
    try:
        from packaging.requirements import InvalidRequirement, Requirement
        from packaging.utils import canonicalize_name
    except ImportError:
        return reqs

    def met(req: Any, seen: set) -> bool:
        if req.url:
            return False
        try:
            installed = metadata.version(req.name)
            if req.specifier and not req.specifier.contains(installed, prereleases=True):
                return False
        except Exception:  # not installed, or a version this cannot read
            return False
        extras = {canonicalize_name(extra) for extra in req.extras}
        key = (canonicalize_name(req.name), frozenset(extras))
        if not extras or key in seen:
            return True
        seen.add(key)
        # An extra is met when what it adds to the package's own dependencies is.
        try:
            declared = [Requirement(dep) for dep in metadata.requires(req.name) or []]
        except Exception:
            return False
        for dep in declared:
            if dep.marker is None:
                continue
            try:
                added = not dep.marker.evaluate({"extra": ""}) and any(
                    dep.marker.evaluate({"extra": extra}) for extra in extras
                )
            except Exception:
                return False
            if added and not met(dep, seen):
                return False
        return True

    missing: List[str] = []
    for raw in reqs:
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            missing.append(raw)
            continue
        if req.marker is not None:
            try:
                if not req.marker.evaluate({"extra": ""}):
                    continue
            except Exception:
                pass
        if not met(req, set()):
            missing.append(raw)
    return missing


def refuse_unmet_when_managed(requirements: Iterable[str]) -> bool:
    """In a managed install, whether ``requirements`` are already met; raises when they are not.

    A managed install runs the release's own verified tree: packages installed into it are files
    the supervisor refuses to start with (where the service account can write the tree at all),
    so the release has to ship them. Returns False outside a managed install.
    """
    from flocks.updater.deploy import declared_managed

    if not declared_managed():
        return False
    missing = unsatisfied_requirements(requirements)
    if missing:
        raise RuntimeError(
            "A managed install does not install Python packages into its runtime; "
            f"not provided by this release: {', '.join(missing)}"
        )
    return True


def _is_cn_region(value: Optional[str]) -> bool:
    normalized = (value or "").strip().lower().replace("_", "-")
    return normalized in {"cn", "china", "zh", "zh-cn"}


def _is_zh_locale(value: Optional[str]) -> bool:
    normalized = (value or "").strip().lower().replace("_", "-")
    return normalized.startswith("zh")


def resolve_python_package_index_url() -> Optional[str]:
    """Return the preferred Python package index for workflow installs."""
    for env_name in (
        "FLOCKS_WORKFLOW_SERVICE_PIP_INDEX_URL",
        "FLOCKS_WORKFLOW_REQUIREMENTS_PIP_INDEX_URL",
        "PIP_INDEX_URL",
        "UV_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "FLOCKS_UV_DEFAULT_INDEX",
    ):
        value = os.getenv(env_name)
        if value and value.strip():
            return value.strip()

    if _is_cn_region(os.getenv("FLOCKS_UPDATE_REGION")):
        return _CN_PYPI_INDEX_URL
    if _is_cn_region(os.getenv("FLOCKS_INSTALL_LANGUAGE")):
        return _CN_PYPI_INDEX_URL
    for env_name in ("LANGUAGE", "LC_ALL", "LANG"):
        if _is_zh_locale(os.getenv(env_name)):
            return _CN_PYPI_INDEX_URL
    return None


def requirements_cache_key(requirements: Sequence[str], *, python_executable: Optional[str] = None) -> str:
    py = (python_executable or sys.executable or "").strip()
    norm = _normalize_requirements(requirements)
    payload = "\n".join([f"py={py}", *norm]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def requirements_from_workflow_metadata(metadata: Optional[Dict[str, Any]]) -> List[str]:
    if not metadata:
        return []
    req = metadata.get("requirements")
    if req is None:
        return []
    if isinstance(req, list):
        return _normalize_requirements([str(x) for x in req])
    if isinstance(req, dict):
        pkgs = req.get("packages")
        if isinstance(pkgs, list):
            return _normalize_requirements([str(x) for x in pkgs])
    return []


@dataclass(frozen=True)
class RequirementsInstaller:
    installer: str = "auto"
    cache_dir: Path = None  # type: ignore[assignment]
    index_url: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cache_dir", self.cache_dir or _default_cache_dir())

    def _select_installer(self) -> str:
        v = (self.installer or "auto").strip().lower()
        if v in {"pip", "uv"}:
            return v
        return "uv" if shutil.which("uv") else "pip"

    def _marker_path(self, key: str) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / f"{key}.installed"

    def ensure_installed(self, requirements: Sequence[str]) -> bool:
        reqs = _normalize_requirements(requirements)
        if not reqs:
            return False
        if refuse_unmet_when_managed(reqs):
            return False
        key = requirements_cache_key(reqs)
        marker = self._marker_path(key)
        if marker.exists():
            return False
        which = self._select_installer()
        index_url = self.index_url or resolve_python_package_index_url()
        index_args = ["--default-index", index_url] if index_url else []
        if which == "uv":
            cmd = ["uv", "pip", "install", "--python", sys.executable, *index_args, *reqs]
        else:
            pip_index_args = ["--index-url", index_url] if index_url else []
            cmd = [sys.executable, "-m", "pip", "install", *pip_index_args, *reqs]
        subprocess.run(cmd, check=True)
        marker.write_text("\n".join(reqs) + "\n", encoding="utf-8")
        return True


@dataclass(frozen=True)
class SandboxRequirementsInstaller:
    """Install workflow requirements inside sandbox container with marker cache."""

    installer: str = "auto"
    python_executable: str = "python3"
    marker_root: str = "/workspace/.flocks/workflow/requirements"
    site_packages_dir: str = "/workspace/.flocks/workflow/site-packages"
    index_url: Optional[str] = None

    def _select_installer(self) -> str:
        v = (self.installer or "auto").strip().lower()
        if v in {"pip", "uv"}:
            return v
        # Prefer pip in container; uv is not guaranteed in base image.
        return "pip"

    def _docker_base_cmd(self, sandbox: Dict[str, Any]) -> List[str]:
        container_name = str(sandbox.get("container_name") or "").strip()
        if not container_name:
            raise ValueError("sandbox.container_name is required for sandbox requirements installation")
        container_workdir = str(
            sandbox.get("container_workdir") or sandbox.get("workspace_dir") or "/workspace"
        ).strip()
        if not container_workdir:
            container_workdir = "/workspace"
        env = sandbox.get("env")
        if not isinstance(env, dict):
            env = {}

        cmd = ["docker", "exec", "-i", "-w", container_workdir]
        for k, v in env.items():
            cmd.extend(["-e", f"{k}={v}"])
        cmd.append(container_name)
        return cmd

    def _marker_path(self, key: str) -> str:
        return f"{self.marker_root.rstrip('/')}/{key}.installed"

    def _python_exists_script(self, marker_path: str) -> str:
        return (
            "from pathlib import Path\n"
            "import sys\n"
            f"sys.exit(0 if Path({json.dumps(marker_path)}).exists() else 1)\n"
        )

    def _python_write_script(self, marker_path: str, content: str) -> str:
        return (
            "from pathlib import Path\n"
            f"p = Path({json.dumps(marker_path)})\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            f"p.write_text({json.dumps(content)}, encoding='utf-8')\n"
        )

    def _python_mkdir_script(self, path: str) -> str:
        return (
            "from pathlib import Path\n"
            f"Path({json.dumps(path)}).mkdir(parents=True, exist_ok=True)\n"
        )

    def ensure_installed(self, requirements: Sequence[str], sandbox: Dict[str, Any]) -> bool:
        reqs = _normalize_requirements(requirements)
        if not reqs:
            return False
        py = self.python_executable.strip() or "python3"
        key = requirements_cache_key(reqs, python_executable=f"container:{py}")
        marker_path = self._marker_path(key)
        base_cmd = self._docker_base_cmd(sandbox)

        check_cmd = [*base_cmd, py, "-c", self._python_exists_script(marker_path)]
        marker_status = subprocess.run(check_cmd, check=False)
        if marker_status.returncode == 0:
            return False

        mkdir_cmd = [*base_cmd, py, "-c", self._python_mkdir_script(self.site_packages_dir)]
        subprocess.run(mkdir_cmd, check=True)

        which = self._select_installer()
        index_url = self.index_url or resolve_python_package_index_url()
        if which == "uv":
            index_args = ["--default-index", index_url] if index_url else []
            install_cmd = [
                *base_cmd,
                "uv",
                "pip",
                "install",
                "--python",
                py,
                "--target",
                self.site_packages_dir,
                *index_args,
                *reqs,
            ]
        else:
            index_args = ["--index-url", index_url] if index_url else []
            install_cmd = [
                *base_cmd,
                py,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--target",
                self.site_packages_dir,
                *index_args,
                *reqs,
            ]
        subprocess.run(install_cmd, check=True)

        marker_content = "\n".join(reqs) + "\n"
        write_marker_cmd = [*base_cmd, py, "-c", self._python_write_script(marker_path, marker_content)]
        subprocess.run(write_marker_cmd, check=True)
        return True
