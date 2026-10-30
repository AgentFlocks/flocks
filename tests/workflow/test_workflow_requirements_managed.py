"""Workflow requirements in a managed install: never installed into the release's own runtime."""

from __future__ import annotations

import subprocess

import pytest

from flocks.workflow.requirements import RequirementsInstaller, unsatisfied_requirements

_ABSENT = "flocks-surely-not-installed-xyz"


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    return tmp_path


def _fake_distribution(site, name, version, *requires):
    """An installed distribution only as metadata, which is all the check reads."""
    info = site / f"{name.replace('-', '_')}-0.dist-info"
    info.mkdir(parents=True)
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    lines += [f"Requires-Dist: {req}" for req in requires]
    (info / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def fake_site(monkeypatch, tmp_path):
    site = tmp_path / "site"
    _fake_distribution(
        site,
        "flocks-fake-extras",
        "1.0",
        "packaging",
        f'{_ABSENT}; extra == "more"',
        'packaging>=1; extra == "ok"',
        f'{_ABSENT}; extra == "other" and python_version < "3"',
    )
    _fake_distribution(site, "flocks-fake-badver", "not-a-version")
    monkeypatch.syspath_prepend(str(site))
    return site


def test_extras_count_only_when_what_they_add_is_installed(fake_site):
    assert unsatisfied_requirements(["flocks-fake-extras", "flocks-fake-extras[ok]"]) == []
    assert unsatisfied_requirements(["flocks-fake-extras[more]"]) == ["flocks-fake-extras[more]"]
    assert unsatisfied_requirements(["flocks-fake-extras[ok,more]"]) == ["flocks-fake-extras[ok,more]"]
    # What an extra adds only on another Python is not asked for here.
    assert unsatisfied_requirements(["flocks-fake-extras[other]"]) == []


def test_a_version_that_cannot_be_read_counts_as_unmet_when_a_range_is_asked(fake_site):
    assert unsatisfied_requirements(["flocks-fake-badver>=1"]) == ["flocks-fake-badver>=1"]
    assert unsatisfied_requirements(["flocks-fake-badver"]) == []


def test_a_managed_install_refuses_an_extra_the_release_lacks(root, fake_site, monkeypatch):
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    installer = RequirementsInstaller(installer="uv", cache_dir=root / "cache")

    with pytest.raises(RuntimeError, match=r"flocks-fake-extras\[more\]"):
        installer.ensure_installed(["flocks-fake-extras[more]"])
    assert installer.ensure_installed(["flocks-fake-extras[ok]"]) is False
    assert calls == []


def test_unsatisfied_requirements_reads_names_ranges_markers_and_urls():
    assert unsatisfied_requirements(["packaging", "packaging>=1", "  "]) == []
    assert unsatisfied_requirements(["packaging<1"]) == ["packaging<1"]
    assert unsatisfied_requirements([_ABSENT]) == [_ABSENT]
    # A marker that does not hold here asks for nothing.
    assert unsatisfied_requirements([f"{_ABSENT}; python_version < '3'"]) == []
    # What cannot be confirmed counts as missing.
    assert unsatisfied_requirements(["packaging @ https://example.com/packaging.whl"]) == [
        "packaging @ https://example.com/packaging.whl"
    ]
    assert unsatisfied_requirements(["not a requirement!"]) == ["not a requirement!"]


def test_a_managed_install_with_the_requirements_met_runs_no_installer(root, monkeypatch):
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    installer = RequirementsInstaller(installer="uv", cache_dir=root / "cache")

    assert installer.ensure_installed(["packaging"]) is False
    assert calls == []
    assert not (root / "cache").exists() or not any((root / "cache").iterdir())


def test_a_managed_install_refuses_what_the_release_lacks(root, monkeypatch):
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    installer = RequirementsInstaller(installer="uv", cache_dir=root / "cache")

    with pytest.raises(RuntimeError, match=f"managed install.*{_ABSENT}"):
        installer.ensure_installed(["packaging", _ABSENT])
    assert calls == []


def test_a_managed_descriptor_counts_like_the_environment(root, monkeypatch):
    monkeypatch.delenv("FLOCKS_DEPLOY_MODE", raising=False)
    config = root / "root" / "config"
    config.mkdir(parents=True)
    (config / "deployment.json").write_text('{"mode": "managed"}', encoding="utf-8")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))

    with pytest.raises(RuntimeError, match="managed install"):
        RequirementsInstaller(installer="uv", cache_dir=root / "cache").ensure_installed([_ABSENT])
    assert calls == []


def test_outside_a_managed_install_the_installer_runs_as_before(root, monkeypatch):
    monkeypatch.delenv("FLOCKS_DEPLOY_MODE", raising=False)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd))
    installer = RequirementsInstaller(installer="uv", cache_dir=root / "cache")

    assert installer.ensure_installed([_ABSENT]) is True
    assert calls and calls[0][:3] == ["uv", "pip", "install"]
    assert _ABSENT in calls[0]
