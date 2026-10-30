"""
Tests for startup readiness (flocks.plugin.readiness).

The point of this check is the case the loader cannot see: a required package
that was never installed produces no entry point and therefore no error, so the
process would otherwise start with that extension's behaviour simply absent.
"""

import json

import pytest

from flocks.plugin.readiness import (
    ENV_REQUIRED_CAPABILITIES,
    ENV_REQUIRED_PLUGINS,
    ENV_REQUIREMENTS_FILE,
    ReadinessReport,
    StartupRequirements,
    StartupRequirementsUnmet,
    check_startup_readiness,
    enforce_startup_readiness,
)


@pytest.fixture(autouse=True)
def _clean_registries():
    from flocks.content import reset_sources
    from flocks.plugin.readiness import reset_entrypoint_cache
    from flocks.transform import reset_response_transform

    reset_sources()
    reset_response_transform()
    reset_entrypoint_cache()
    yield
    reset_sources()
    reset_response_transform()
    reset_entrypoint_cache()


# ---------------------------------------------------------------------------
# R1-R3: plugin presence
# ---------------------------------------------------------------------------

def test_no_requirements_is_ready():
    """R1: a stock install declares nothing and must start normally."""
    report = check_startup_readiness(env={})

    assert report.satisfied
    assert report.describe() == "startup requirements satisfied"


def test_missing_required_plugin_is_reported():
    """R2: the gap the loader cannot see -- a package that is simply absent."""
    report = check_startup_readiness(env={ENV_REQUIRED_PLUGINS: "not_installed_pkg"})

    assert not report.satisfied
    assert report.missing_plugins == frozenset({"not_installed_pkg"})
    assert "not_installed_pkg" in report.describe()


def test_installed_plugin_satisfies_requirement(monkeypatch):
    """R3: a declared plugin that is present satisfies the requirement."""
    monkeypatch.setattr(
        "flocks.plugin.readiness.installed_plugin_entrypoints",
        lambda: frozenset({"present_pkg"}),
    )

    report = check_startup_readiness(env={ENV_REQUIRED_PLUGINS: "present_pkg"})

    assert report.satisfied


# ---------------------------------------------------------------------------
# R4-R6: capability slots
# ---------------------------------------------------------------------------

def test_missing_capability_is_reported():
    """R4: requiring a slot means the built-in default is not enough."""
    report = check_startup_readiness(env={ENV_REQUIRED_CAPABILITIES: "response_transform"})

    assert not report.satisfied
    assert report.missing_capabilities == frozenset({"response_transform"})


def test_registered_capability_satisfies_requirement():
    """R5: once something is registered for the slot, the requirement is met."""
    from flocks.transform import register_response_transform

    class _Transform:
        name = "test"

        async def open_stream(self, metadata):
            raise NotImplementedError

        async def transform_text(self, text, metadata):
            return text

        async def transform_value(self, value, metadata):
            return value

    register_response_transform(_Transform())

    report = check_startup_readiness(env={ENV_REQUIRED_CAPABILITIES: "response_transform"})

    assert report.satisfied


def test_unknown_capability_name_is_not_ignored():
    """R6: a typo in a capability name must not read as 'requirement met'.

    Silently ignoring an unrecognised name is the same failure this module
    exists to prevent, one level up.
    """
    report = check_startup_readiness(env={ENV_REQUIRED_CAPABILITIES: "auth_backned"})

    assert not report.satisfied
    assert report.unknown_capabilities == frozenset({"auth_backned"})


# ---------------------------------------------------------------------------
# R7-R9: the manifest a packaged delivery ships
# ---------------------------------------------------------------------------

def test_missing_manifest_is_a_failure_not_an_empty_requirement(tmp_path):
    """R7: a bad path must not quietly turn into 'nothing required'."""
    report = check_startup_readiness(
        env={ENV_REQUIREMENTS_FILE: str(tmp_path / "absent.json")}
    )

    assert not report.satisfied
    assert any("不存在" in err for err in report.errors)


def test_malformed_manifest_is_a_failure(tmp_path):
    """R8: unparseable requirements are unmet requirements."""
    path = tmp_path / "req.json"
    path.write_text("{not json", encoding="utf-8")

    report = check_startup_readiness(env={ENV_REQUIREMENTS_FILE: str(path)})

    assert not report.satisfied
    assert any("无法解析" in err for err in report.errors)


def test_manifest_and_env_are_merged(tmp_path):
    """R9: a delivery ships a manifest; an operator may add to it via env."""
    path = tmp_path / "req.json"
    path.write_text(
        json.dumps({"plugins": ["from_file"], "capabilities": ["audit_sink"]}),
        encoding="utf-8",
    )

    requirements = StartupRequirements.from_environment(
        {ENV_REQUIREMENTS_FILE: str(path), ENV_REQUIRED_PLUGINS: "from_env"}
    )

    assert requirements.plugins == frozenset({"from_file", "from_env"})
    assert requirements.capabilities == frozenset({"audit_sink"})


def test_manifest_with_wrong_field_type_is_rejected(tmp_path):
    """R8: a manifest whose fields are not string lists is not usable."""
    path = tmp_path / "req.json"
    path.write_text(json.dumps({"plugins": "should_be_a_list"}), encoding="utf-8")

    report = check_startup_readiness(env={ENV_REQUIREMENTS_FILE: str(path)})

    assert not report.satisfied


# ---------------------------------------------------------------------------
# R10-R11: enforcement
# ---------------------------------------------------------------------------

def test_enforcement_raises_on_unmet_requirements():
    """R10: startup stops; there is no degraded mode to fall into."""
    report = check_startup_readiness(env={ENV_REQUIRED_PLUGINS: "not_installed_pkg"})

    with pytest.raises(StartupRequirementsUnmet) as excinfo:
        enforce_startup_readiness(report)

    assert "not_installed_pkg" in str(excinfo.value)


def test_enforcement_passes_when_satisfied():
    """R10: nothing declared, nothing raised."""
    enforce_startup_readiness(check_startup_readiness(env={}))


def test_failed_entrypoints_make_the_instance_not_ready():
    """R11: a plugin that loaded and failed also blocks readiness."""
    report = check_startup_readiness(env={}, failed_entrypoints=("broken_pkg",))

    assert not report.satisfied
    assert "broken_pkg" in report.describe()


def test_report_serializes_for_the_readiness_endpoint():
    """R12 support: the endpoint returns this dict, so it must be complete."""
    report = check_startup_readiness(env={ENV_REQUIRED_PLUGINS: "not_installed_pkg"})

    payload = report.to_dict()

    assert payload["satisfied"] is False
    assert payload["missing_plugins"] == ["not_installed_pkg"]
    assert payload["required_plugins"] == ["not_installed_pkg"]


def test_failed_entrypoints_do_not_abort_startup():
    """A plugin that loaded and failed is not this check's call to make.

    The loader already signals that condition, and ingress refuses on it. If
    this check also aborted startup, it would convert a "stop serving
    messages" signal into "the process does not come up", which is a different
    decision belonging to a different layer.
    """
    report = check_startup_readiness(env={}, failed_entrypoints=("broken_pkg",))

    assert report.requirements_met is True  # nothing was declared
    assert report.satisfied is False  # ...but do not route traffic here
    enforce_startup_readiness(report)  # must not raise


def test_declared_requirement_still_aborts_startup():
    """Requirements this module owns do stop the process."""
    report = check_startup_readiness(env={ENV_REQUIRED_PLUGINS: "not_installed_pkg"})

    assert report.requirements_met is False
    with pytest.raises(StartupRequirementsUnmet):
        enforce_startup_readiness(report)


def test_a_mandatory_plugin_that_failed_to_register_aborts_startup():
    """Declaring a plugin mandatory asks for its behaviour, not its package.

    An installed distribution whose registration raised leaves the process
    running with the extension absent -- the same end state as not installing
    it, which this check already refuses. Treating the two differently let a
    deployment that declared an extension mandatory come up without it.
    """
    report = check_startup_readiness(
        env={ENV_REQUIRED_PLUGINS: "needed_pkg"},
        failed_entrypoints=("needed_pkg: ImportError libpq.so",),
    )

    assert report.failed_required_entrypoints == ("needed_pkg: ImportError libpq.so",)
    assert report.requirements_met is False
    with pytest.raises(StartupRequirementsUnmet):
        enforce_startup_readiness(report)


def test_an_unrelated_failed_plugin_still_does_not_abort_startup():
    """Only the ones the deployment named are its own call to make."""
    report = check_startup_readiness(
        env={ENV_REQUIRED_PLUGINS: "needed_pkg"},
        failed_entrypoints=("some_other_pkg: boom",),
    )

    assert report.failed_required_entrypoints == ()
    # needed_pkg is still missing from this environment, so check the narrower
    # claim directly rather than through requirements_met.
    assert "some_other_pkg" not in " ".join(report.failed_required_entrypoints)
