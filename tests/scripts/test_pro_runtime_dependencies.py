"""Third-party packages Flocks Pro's engines import come only from this project's lock.

Pro installs its wheel with --no-deps (flocks/updater/updater.py) and loads its
obfuscated engines from wheels it unpacks itself, so a package those engines
import reaches a deployment only through pyproject.toml / uv.lock: the online
upgrade, the offline installer and the Docker image all sync from them.
"""

import importlib.util
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[2]

# distribution -> module the engine imports. Without python-docx the code audit
# engine answers every Word report download with docx_export_unavailable.
PRO_RUNTIME_REQUIREMENTS = {"python-docx": "docx"}


def test_core_lock_provides_pro_engine_runtime_requirements() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {Requirement(item).name for item in project["dependencies"]}
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {package["name"] for package in lock["package"]}

    for distribution, module in PRO_RUNTIME_REQUIREMENTS.items():
        assert distribution in declared
        assert distribution in locked
        assert importlib.util.find_spec(module) is not None
