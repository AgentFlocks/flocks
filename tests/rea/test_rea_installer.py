"""The install job: components, a single MCP registration, the tier validation and the record."""

from __future__ import annotations

import asyncio

import pytest

from flocks.rea import components, installer, runtime, service, validation
from flocks.rea.installer import ReaInstaller, ReaInstallError


def _capability(key, status="passed", runtime_status="passed"):
    return {
        "key": key, "status": status, "static": status, "runtime": runtime_status,
        "sample": {"path": f"/data/rea/validation/x/{key}/sample", "sha256": "ab" * 32},
        "expected": "calc(5) = 22", "actual": "22", "calls": [{"tool": f"rea_{key}", "args": {}, "ok": True, "summary": ""}],
        "notes": [],
    }


@pytest.fixture
def tier_validation(monkeypatch):
    """The tier validation without REA: ``results`` overrides capability outcomes."""

    state = {"calls": [], "results": {}}

    async def run_validation(tier, *, session_id, on_result=None):
        state["calls"].append((tier, session_id))
        capabilities = {}
        for key in validation.tier_capabilities(tier):
            item = state["results"].get(key) or _capability(key)
            capabilities[key] = item
            if on_result is not None:
                await on_result(validation.CapabilityResult(**{**item, "calls": []}))
        return {
            "tier": tier,
            "ok": validation.tier_passed(tier, capabilities),
            "root": str(runtime.validation_root() / "run"),
            "capabilities": capabilities,
        }

    monkeypatch.setattr(validation, "run_validation", run_validation)
    return state


@pytest.fixture
def ready_host(fake_toolchain, fake_skill_source, fake_mcp, tier_validation):
    return {"skill": fake_skill_source, "mcp": fake_mcp, "validation": tier_validation}


async def _install(tier="light", user_id="admin-1", session_id="ses_install"):
    await ReaInstaller.start(tier, user_id=user_id, session_id=session_id)
    return await ReaInstaller.wait()


@pytest.mark.asyncio
async def test_light_install_registers_once_validates_and_records(ready_host):
    from flocks.config.config_writer import ConfigWriter
    from flocks.tool.tool_loader import _MCP_SUBDIR

    stages = []
    original = ReaInstaller._set

    async def spy(job_id, **changes):
        if "stage" in changes:
            stages.append(changes["stage"])
        await original(job_id, **changes)

    ReaInstaller._set = spy  # type: ignore[method-assign]
    try:
        job = await _install()
    finally:
        ReaInstaller._set = original  # type: ignore[method-assign]

    assert job.status == "succeeded", job
    assert stages == ["components", "register", "validate", "done"]
    assert {key: item["status"] for key, item in job.components.items()} == {"skill": "installed", "rea": "installed"}
    assert job.registration == "written"
    config = ConfigWriter.get_mcp_server("rea")
    assert config["command"][-3:] == ["-y", "rea-agents@6.4.0", "--mcp"]
    assert "environment" not in config  # the light tier writes no environment
    assert (_MCP_SUBDIR / "rea.yaml").exists() or any(_MCP_SUBDIR.glob("rea*"))
    mcp = ready_host["mcp"]
    assert mcp.disconnects == ["rea"] and [name for name, _ in mcp.connects] == ["rea"]
    assert ready_host["validation"]["calls"] == [("light", "ses_install")]

    record = runtime.read_install_record()
    assert record["tier"] == "light" and record["version"] == "6.4.0"
    assert record["verification"]["ok"] is True
    assert record["verification"]["service"]["tools_observed"] == 170
    assert set(record["verification"]["capabilities"]) == {"javascript", "dotnet", "archive"}
    assert record["mcp"]["registration"] == "written"
    assert isinstance(record["firstPassedAt"], int)
    assert service.first_install_completed("admin-1") is True
    assert runtime.is_ready() is True


@pytest.mark.asyncio
async def test_a_second_install_reuses_everything(ready_host):
    first = await _install()
    first_record = runtime.read_install_record()
    mcp = ready_host["mcp"]
    connects = len(mcp.connects)

    second = await _install(user_id="admin-2")
    assert second.status == "succeeded"
    assert {key: item["status"] for key, item in second.components.items()} == {"skill": "reused", "rea": "reused"}
    assert second.registration == "reused"
    assert len(mcp.connects) == connects  # connected and listing tools: not started again
    record = runtime.read_install_record()
    assert record["firstPassedAt"] == first_record["firstPassedAt"]
    assert record["mcp"]["registeredAt"] == first_record["mcp"]["registeredAt"]
    assert first.id != second.id
    # Validated through reuse: the second account's first install counts too.
    assert service.first_install_completed("admin-2") is True


@pytest.mark.asyncio
async def test_a_registered_but_stopped_service_is_reconnected_not_rewritten(ready_host):
    from flocks.config.config_writer import ConfigWriter

    await _install()
    written = ConfigWriter.get_mcp_server("rea")
    ready_host["mcp"].statuses.clear()
    job = await _install()
    assert job.registration == "reused"
    assert ConfigWriter.get_mcp_server("rea") == written
    assert len(ready_host["mcp"].connects) == 2


@pytest.mark.asyncio
async def test_full_tier_passes_jdk_and_ghidra_to_the_service(ready_host, monkeypatch):
    from flocks.config.config_writer import ConfigWriter

    async def jdk():
        return components.ComponentResult("jdk", "reused", detail="/usr/lib/jvm/java-21-openjdk")

    async def ghidra():
        return components.ComponentResult("ghidra", "installed", detail="/opt/ghidra_12.1.4_PUBLIC")

    monkeypatch.setattr(components, "ensure_jdk", jdk)
    monkeypatch.setattr(components, "ensure_ghidra", ghidra)
    await _install("light")
    job = await _install("full")
    assert job.status == "succeeded"
    assert job.registration == "written"  # the light registration had no environment
    assert ConfigWriter.get_mcp_server("rea")["environment"] == {
        "GHIDRA_INSTALL_DIR": "/opt/ghidra_12.1.4_PUBLIC",
        "JAVA_HOME": "/usr/lib/jvm/java-21-openjdk",
    }
    assert ready_host["validation"]["calls"][-1][0] == "full"
    assert runtime.read_install_record()["tier"] == "full"


@pytest.mark.asyncio
async def test_a_failed_component_stops_before_registration(ready_host):
    from flocks.config.config_writer import ConfigWriter

    ready_host["skill"]["failures"] = 99
    job = await _install()
    assert job.status == "failed" and job.stage == "components"
    assert job.errorCode == "component_failed"
    assert runtime.REA_SKILL_SOURCE in job.errorDetail
    assert job.components["rea"]["status"] == "installed"  # the other component still went in
    assert ConfigWriter.get_mcp_server("rea") is None
    assert runtime.read_install_record() is None
    assert service.first_install_completed("admin-1") is False


@pytest.mark.asyncio
async def test_blockers_are_reported_with_the_action_for_the_user(ready_host, monkeypatch):
    real_which = components.shutil.which
    monkeypatch.setattr(
        components.shutil, "which", lambda name, *a, **k: "/usr/bin/dnf" if name == "dnf" else real_which(name, *a, **k),
    )
    monkeypatch.setattr(components, "_is_root", lambda: False)

    async def ghidra():
        return components.ComponentResult("ghidra", "reused", detail="/opt/ghidra_12.1.4_PUBLIC")

    monkeypatch.setattr(components, "ensure_ghidra", ghidra)
    job = await _install("full")
    assert job.status == "failed" and job.errorCode == "blocked"
    assert job.blockers == [{"code": "jdk_needs_root", "command": "dnf install -y java-21-openjdk-devel"}]
    assert job.errorDetail is None  # the blocker carries the action
    assert job.components["skill"]["status"] == "installed"

    # A blocker next to a real failure: the failure names the code and the detail.
    ready_host["skill"]["failures"] = 99
    import shutil as _shutil

    _shutil.rmtree(runtime.installed_skill_dir())
    job = await _install("full")
    assert job.errorCode == "component_failed"
    assert job.errorDetail.startswith("skill: ") and "jdk" not in job.errorDetail
    assert job.blockers == [{"code": "jdk_needs_root", "command": "dnf install -y java-21-openjdk-devel"}]


@pytest.mark.asyncio
async def test_a_service_that_does_not_start_fails_the_install(ready_host):
    ready_host["mcp"].connect_ok = False
    job = await _install()
    assert job.status == "failed" and job.stage == "register"
    assert job.errorCode == "service_failed" and "ENOENT" in job.errorDetail
    assert runtime.read_install_record() is None


@pytest.mark.asyncio
async def test_an_unhealthy_doctor_fails_validation(ready_host, monkeypatch):
    monkeypatch.setenv("FAKE_DOCTOR_MODE", "unhealthy")
    job = await _install()
    assert job.status == "failed" and job.stage == "validate"
    assert job.errorCode == "validation_failed"
    assert "inventory:tools" in job.errorDetail
    assert job.service["tools_observed"] == 12
    assert ready_host["validation"]["calls"] == []  # samples only after a healthy service


@pytest.mark.asyncio
async def test_partial_validation_is_recorded_but_not_a_first_install(ready_host):
    ready_host["validation"]["results"]["dotnet"] = _capability("dotnet", status="failed")
    job = await _install()
    assert job.status == "failed" and job.errorCode == "validation_failed"
    assert "dotnet" in job.errorDetail
    record = runtime.read_install_record()
    assert record["verification"]["ok"] is False
    assert record["verification"]["capabilities"]["javascript"]["status"] == "passed"
    assert record["firstPassedAt"] is None
    assert service.first_install_completed("admin-1") is False
    assert service.install_state() == "install_failed"


@pytest.mark.asyncio
async def test_a_failing_revalidation_keeps_the_first_pass(ready_host):
    await _install()
    first_passed = runtime.read_install_record()["firstPassedAt"]
    ready_host["validation"]["results"]["archive"] = _capability("archive", status="failed")
    job = await _install()
    assert job.status == "failed"
    record = runtime.read_install_record()
    assert record["firstPassedAt"] == first_passed
    assert service.install_state() == "degraded"


@pytest.mark.asyncio
async def test_start_while_running_returns_the_same_job(ready_host, monkeypatch):
    gate = asyncio.Event()
    original = components.ensure_components

    async def slow(tier, on_done=None):
        await gate.wait()
        return await original(tier, on_done=on_done)

    monkeypatch.setattr(components, "ensure_components", slow)
    first = await ReaInstaller.start("light", user_id="a")
    second = await ReaInstaller.start("full", user_id="b")
    assert second.id == first.id and second.tier == "light"
    assert service.install_state() == "installing"
    gate.set()
    assert (await ReaInstaller.wait()).status == "succeeded"


@pytest.mark.asyncio
async def test_no_install_starts_while_uninstalling(ready_host, monkeypatch):
    from flocks.rea.uninstaller import ReaUninstaller

    monkeypatch.setattr(ReaUninstaller, "is_running", classmethod(lambda cls: True))
    with pytest.raises(ReaInstallError) as raised:
        await ReaInstaller.start("light")
    assert raised.value.code == "uninstalling"


@pytest.mark.asyncio
async def test_cancel_and_a_dead_worker_settle_as_interrupted(ready_host, monkeypatch):
    gate = asyncio.Event()

    async def hang(tier, on_done=None):
        await gate.wait()

    monkeypatch.setattr(components, "ensure_components", hang)
    await ReaInstaller.start("light")
    await asyncio.sleep(0)
    assert ReaInstaller.cancel() is True
    job = await ReaInstaller.wait()
    assert job.status == "failed" and job.errorCode == "interrupted"

    # The worker is gone (its loop closed) while the job still says running.
    finished = asyncio.get_running_loop().create_future()
    finished.set_result(None)
    ReaInstaller._job = job.model_copy(update={"status": "running", "errorCode": None, "finishedAt": None})
    ReaInstaller._task = finished
    settled = ReaInstaller.current_job()
    assert settled.status == "failed" and settled.errorCode == "interrupted" and settled.finishedAt
    assert ReaInstaller.is_running() is False


@pytest.mark.asyncio
async def test_error_details_are_cleaned_for_history(ready_host, monkeypatch):
    from pathlib import Path

    async def failing(tier, on_done=None):
        raise ReaInstallError("component_failed", f"npm error https://u:p@registry.local/x at {Path.home()}/.npm\n")

    monkeypatch.setattr(components, "ensure_components", failing)
    job = await _install()
    assert "u:p@" not in job.errorDetail and str(Path.home()) not in job.errorDetail
    assert "\n" not in job.errorDetail


def test_stage_progress_is_monotonic():
    values = [installer.STAGE_PROGRESS[stage] for stage in ("check", "components", "register", "validate", "done")]
    assert values == sorted(values) and values[-1] == 100


@pytest.mark.asyncio
async def test_install_events_say_only_that_something_changed(monkeypatch):
    from flocks.rea.installer import ReaInstallJob

    sent = []

    async def publish(name, payload):
        sent.append((name, payload))

    monkeypatch.setattr("flocks.server.routes.event.publish_event", publish)
    ReaInstaller._job = ReaInstallJob(
        id="reainst_x", status="failed", stage="components", progress=30, tier="full", startedAt=1,
        errorDetail="/srv/secret", blockers=[{"code": "ghidra_dir_occupied", "path": "/srv/secret"}],
    )
    await ReaInstaller._publish()
    assert sent == [("rea.install.updated", {"job": {
        "id": "reainst_x", "status": "failed", "stage": "components", "progress": 30, "tier": "full",
    }})]
