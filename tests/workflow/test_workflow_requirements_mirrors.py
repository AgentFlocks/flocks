from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from flocks.workflow.requirements import (
    RequirementsInstaller,
    SandboxRequirementsInstaller,
    resolve_python_package_index_url,
)


def test_resolve_python_package_index_uses_chinese_locale(monkeypatch) -> None:
    monkeypatch.delenv("FLOCKS_WORKFLOW_SERVICE_PIP_INDEX_URL", raising=False)
    monkeypatch.delenv("FLOCKS_WORKFLOW_REQUIREMENTS_PIP_INDEX_URL", raising=False)
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.delenv("UV_INDEX_URL", raising=False)
    monkeypatch.delenv("UV_DEFAULT_INDEX", raising=False)
    monkeypatch.delenv("FLOCKS_UV_DEFAULT_INDEX", raising=False)
    monkeypatch.delenv("FLOCKS_UPDATE_REGION", raising=False)
    monkeypatch.setenv("FLOCKS_INSTALL_LANGUAGE", "zh-CN")

    assert resolve_python_package_index_url() == "https://mirrors.aliyun.com/pypi/simple"


def test_requirements_installer_passes_index_to_pip(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("flocks.workflow.requirements.subprocess.run", fake_run)

    installed = RequirementsInstaller(
        installer="pip",
        cache_dir=tmp_path,
        index_url="https://mirror.example/simple",
    ).ensure_installed(["requests==2.32.0"])

    assert installed is True
    assert len(calls) == 1
    assert calls[0][1:] == [
        "-m",
        "pip",
        "install",
        "--index-url",
        "https://mirror.example/simple",
        "requests==2.32.0",
    ]


def test_sandbox_requirements_installer_passes_index_to_pip(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool = True, **_kwargs: Any) -> SimpleNamespace:
        calls.append(cmd)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("flocks.workflow.requirements.subprocess.run", fake_run)

    installed = SandboxRequirementsInstaller(
        installer="pip",
        index_url="https://mirror.example/simple",
    ).ensure_installed(
        ["requests==2.32.0"],
        sandbox={"container_name": "workflow-container", "container_workdir": "/workspace"},
    )

    assert installed is True
    install_cmd = calls[2]
    assert install_cmd[:5] == ["docker", "exec", "-i", "-w", "/workspace"]
    assert install_cmd[5] == "workflow-container"
    assert install_cmd[6:15] == [
        "python3",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--target",
        "/workspace/.flocks/workflow/site-packages",
        "--index-url",
    ]
    assert install_cmd[15:17] == ["https://mirror.example/simple", "requests==2.32.0"]
