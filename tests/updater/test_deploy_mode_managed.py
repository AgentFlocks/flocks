"""Managed release packages (Go core) must never be upgraded in place by the built-in updater.

Regression for the 2026-09 full test pass: an updater/doctor run inside the Go-supervised
runtime executed ``uv sync`` against the shared project venv, downgrading pinned wheels
and uninstalling the commercial component. Under the V1.2 design commercial package
upgrades belong to the core, so every entry point that would touch the venv or the
source tree refuses when the deployment is managed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from flocks.updater import deploy
from flocks.updater import updater
from flocks.updater.models import UpdateProgress, VersionInfo

runner = CliRunner()


class _ExtensionLicenseChecker:
    """Stands in for a distribution's license-enforcement extension."""

    @classmethod
    async def is_active(cls, feature=None):
        return True

    @classmethod
    async def assert_active(cls, feature=None):
        return None

    @classmethod
    async def status(cls):
        return {"activated": True, "active": True, "status": "extension"}


@pytest.fixture
def source_layout(monkeypatch, tmp_path: Path) -> Path:
    from flocks import license as license_mod

    root = tmp_path / "flocks-root"
    root.mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.delenv("FLOCKS_DEPLOY_MODE", raising=False)
    monkeypatch.setattr(deploy.Path, "exists", lambda self: False)  # no /.dockerenv
    monkeypatch.setattr(license_mod._LicenseService, "_checker", license_mod.AlwaysOkLicenseChecker)
    deploy.detect_deploy_mode.cache_clear()
    yield root
    deploy.detect_deploy_mode.cache_clear()


def _managed_by_core(monkeypatch) -> None:
    """Simulate the managed runtime: its license-enforcement extension is registered."""
    from flocks import license as license_mod

    monkeypatch.setattr(license_mod._LicenseService, "_checker", _ExtensionLicenseChecker)
    deploy.detect_deploy_mode.cache_clear()


# --------------------------------------------------------------------------- detection
def test_license_extension_marks_deployment_managed(monkeypatch, source_layout) -> None:
    _managed_by_core(monkeypatch)

    assert deploy.detect_deploy_mode() == "managed"
    assert deploy.is_managed_deployment() is True
    assert deploy.update_allowed_for_mode("managed") is False
    assert deploy.update_refusal_reason() == deploy.MANAGED_UPGRADE_REFUSAL


def test_license_extension_is_evaluated_live_after_import(monkeypatch, source_layout) -> None:
    assert deploy.detect_deploy_mode() == "source"
    _managed_by_core(monkeypatch)  # registers after the first (cached) evaluation
    assert deploy.detect_deploy_mode() == "managed"


def test_explicit_env_wins_over_extension_signal(monkeypatch, source_layout) -> None:
    _managed_by_core(monkeypatch)
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "source")
    deploy.detect_deploy_mode.cache_clear()

    assert deploy.detect_deploy_mode() == "source"


def test_explicit_managed_env_value_is_honoured(monkeypatch, source_layout) -> None:
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    deploy.detect_deploy_mode.cache_clear()

    assert deploy.detect_deploy_mode() == "managed"


@pytest.mark.parametrize("payload, expected", [
    ('{"mode": "managed", "manager": "core"}', "managed"),
    ('{"mode": "Docker"}', "docker"),
    ('{"mode": "bogus"}', "source"),
    ("{not json", "source"),
    ("[]", "source"),
])
def test_deployment_descriptor_declares_mode(monkeypatch, source_layout: Path, payload, expected) -> None:
    descriptor = source_layout / deploy.DEPLOYMENT_DESCRIPTOR
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    descriptor.write_text(payload, encoding="utf-8")
    deploy.detect_deploy_mode.cache_clear()

    assert deploy.detect_deploy_mode() == expected


def test_plain_checkout_stays_source(source_layout) -> None:
    assert deploy.detect_deploy_mode() == "source"
    assert deploy.update_refusal_reason() is None
    assert deploy.update_allowed_for_mode("source") is True


# --------------------------------------------------------------------------- updater entry points
async def test_perform_update_refuses_before_any_network_or_config_access(monkeypatch, source_layout) -> None:
    _managed_by_core(monkeypatch)

    async def _boom(*_args, **_kwargs):
        raise AssertionError("updater must not read config, download or sync in managed mode")

    monkeypatch.setattr(updater, "_get_updater_config", _boom)
    monkeypatch.setattr(updater, "_download_with_fallback", _boom)
    monkeypatch.setattr(updater, "_run_async", _boom)

    events: list[UpdateProgress] = []
    async for progress in updater.perform_update("v2099.1.1"):
        events.append(progress)

    assert [e.stage for e in events] == ["error"]
    assert events[0].success is False
    assert events[0].message == deploy.MANAGED_UPGRADE_REFUSAL


async def test_pro_bundle_install_refuses_in_managed_mode(monkeypatch, source_layout) -> None:
    _managed_by_core(monkeypatch)

    async def _boom(*_args, **_kwargs):
        raise AssertionError("manifest must not be fetched")

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", _boom)

    events = [p async for p in updater.perform_pro_bundle_install()]

    assert [e.stage for e in events] == ["error"]
    assert events[0].message == deploy.MANAGED_UPGRADE_REFUSAL


async def test_sync_project_dependencies_never_runs_uv_sync_in_managed_mode(monkeypatch, source_layout, tmp_path: Path) -> None:
    _managed_by_core(monkeypatch)

    async def _boom(*_args, **_kwargs):
        raise AssertionError("uv sync must not be executed")

    monkeypatch.setattr(updater, "_run_async", _boom)

    result = await updater._sync_project_dependencies(uv_path="uv", install_root=tmp_path)

    assert result == deploy.MANAGED_UPGRADE_REFUSAL


async def test_install_or_repair_source_raises_in_managed_mode(monkeypatch, source_layout, tmp_path: Path) -> None:
    _managed_by_core(monkeypatch)

    async def _boom(*_args, **_kwargs):
        raise AssertionError("uv sync must not be executed")

    monkeypatch.setattr(updater, "_run_async", _boom)

    with pytest.raises(RuntimeError, match="managed release packages"):
        await updater.install_or_repair_source(tmp_path, version="v1", uv_path="uv")


async def test_check_update_reports_update_not_allowed_in_managed_mode(monkeypatch, source_layout) -> None:
    _managed_by_core(monkeypatch)

    class _Cfg:
        enabled = False
        sources = ["github"]

    async def _cfg():
        return _Cfg()

    monkeypatch.setattr(updater, "_get_updater_config", _cfg)
    monkeypatch.setattr(updater, "get_current_version", lambda: "v2026.8.17")

    info = await updater.check_update()

    assert isinstance(info, VersionInfo)
    assert info.deploy_mode == "managed"
    assert info.update_allowed is False


# --------------------------------------------------------------------------- CLI surfaces
def test_flocks_update_cli_refuses_in_managed_mode(monkeypatch, source_layout) -> None:
    import flocks.updater as updater_pkg
    from flocks.cli.commands.update import update_command

    _managed_by_core(monkeypatch)
    calls: list[str] = []

    async def _check_update(**_kwargs):
        return VersionInfo(
            current_version="v2026.8.17",
            latest_version="v2099.1.1",
            has_update=True,
            deploy_mode="managed",
            update_allowed=False,
        )

    async def _perform_update(*_args, **_kwargs):
        calls.append("perform_update")
        yield UpdateProgress(stage="fetching", message="nope")

    monkeypatch.setattr(updater_pkg, "check_update", _check_update)
    monkeypatch.setattr(updater_pkg, "perform_update", _perform_update)
    logged: list[str] = []
    monkeypatch.setattr("flocks.cli.commands.update.append_upgrade_text_log", logged.append)

    import typer

    app = typer.Typer()
    app.command()(update_command)
    result = runner.invoke(app, ["--yes"])

    assert result.exit_code == 0, result.output
    assert "managed release packages" in result.output
    assert calls == []
    assert any("update_refused" in line for line in logged)


def test_doctor_skips_core_repair_in_managed_mode(monkeypatch, source_layout, tmp_path: Path) -> None:
    from flocks.cli.commands import doctor as doctor_mod

    _managed_by_core(monkeypatch)
    monkeypatch.setattr(doctor_mod, "_find_source_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(doctor_mod, "_build_source_install_env", lambda: {})
    monkeypatch.setattr(doctor_mod, "_print_service_diagnosis", lambda: None)

    def _boom(*_args, **_kwargs):
        raise AssertionError("core repair must not run in managed mode")

    monkeypatch.setattr(doctor_mod, "_run_core_install", _boom)

    import typer

    app = typer.Typer()
    app.command()(doctor_mod.doctor_command)
    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "Skipping core installation repair" in result.output
