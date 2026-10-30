"""One plugin state for every entry point, and the per-account 「新」 badge."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from flocks.rea import runtime, service
from flocks.rea.installer import ReaInstaller, ReaInstallJob
from flocks.rea.uninstaller import ReaUninstaller, ReaUninstallJob
from tests.rea.conftest import write_skill

ADMIN = SimpleNamespace(id="admin-1", role="admin")
ADMIN_2 = SimpleNamespace(id="admin-2", role="admin")
MEMBER = SimpleNamespace(id="member-1", role="member")


def _configure(tier="light", *, ok=True, first_passed=True):
    from flocks.config.config_writer import ConfigWriter

    write_skill(runtime.installed_skill_dir())
    ConfigWriter.add_mcp_server(runtime.MCP_SERVER_NAME, runtime.mcp_server_config(tier))
    runtime.write_install_record({
        "version": runtime.REA_VERSION,
        "tier": tier,
        "verification": {
            "ok": ok, "tier": tier, "verifiedAt": 5,
            "service": {"ok": True, "server_version": "6.4.0", "tools_observed": 170, "tools_expected": 170,
                        "request_tool": "binary_session", "request_ok": True},
            "capabilities": {
                "javascript": {"status": "passed", "static": "passed", "runtime": "passed", "expected": "calc(5) = 22",
                               "actual": "22", "calls": [{"tool": "rea_analyze_javascript_application"}],
                               "notes": [f"located at {runtime.validation_root()}/x/index.js"]},
            },
        },
        "firstPassedAt": 4 if first_passed else None,
    })


def _job(**changes):
    values = {"id": "reainst_1", "status": "running", "stage": "components", "progress": 10, "startedAt": 1}
    values.update(changes)
    return ReaInstallJob(**values)


@pytest.fixture
def running_job(monkeypatch):
    def apply(**changes):
        ReaInstaller._job = _job(**changes)
        monkeypatch.setattr(ReaInstaller, "current_job", classmethod(lambda cls: cls._job))
    return apply


# ── unified state ──


def test_nothing_configured_is_not_installed(fake_mcp):
    assert service.install_state() == "not_installed"


def test_installing_and_validating_follow_the_running_job(fake_mcp, running_job):
    running_job(stage="components")
    assert service.install_state() == "installing"
    running_job(stage="register")
    assert service.install_state() == "installing"
    running_job(stage="validate", progress=65)
    assert service.install_state() == "validating"


def test_ready_needs_configuration_validation_and_a_live_service(fake_toolchain, fake_mcp):
    _configure()
    fake_mcp.set_connected()
    assert service.install_state() == "ready"

    fake_mcp.statuses["rea"] = fake_mcp._info("failed", 0, "spawn npx ENOENT")
    assert service.install_state() == "degraded"
    assert any("failed (spawn npx ENOENT)" in reason for reason in service.degraded_reasons())
    # A conversation gets the state without the service's own error text.
    assert 'MCP service "rea" is failed' in service.degraded_reasons(details=False)

    fake_mcp.statuses["rea"] = fake_mcp._info("connecting")
    assert service.install_state() == "ready"  # still starting, not broken
    fake_mcp.statuses.clear()
    fake_mcp.initialized = False
    assert service.install_state() == "ready"  # MCP has not started its servers yet
    fake_mcp.initialized = True
    assert service.install_state() == "degraded"  # started without REA


def test_a_removed_skill_or_registration_is_degraded(fake_toolchain, fake_mcp):
    from flocks.config.config_writer import ConfigWriter
    import shutil

    _configure()
    fake_mcp.set_connected()
    shutil.rmtree(runtime.installed_skill_dir())
    assert service.install_state() == "degraded"
    assert any("skill" in reason for reason in service.degraded_reasons())
    write_skill(runtime.installed_skill_dir())
    ConfigWriter.remove_mcp_server(runtime.MCP_SERVER_NAME)
    assert service.install_state() == "degraded"


def test_failed_validations_are_install_failed_until_they_once_passed(fake_toolchain, fake_mcp):
    _configure(ok=False, first_passed=False)
    assert service.install_state() == "install_failed"
    _configure(ok=False, first_passed=True)
    assert service.install_state() == "degraded"


def test_leftovers_without_a_record_need_repair(fake_toolchain, fake_mcp, running_job):
    write_skill(runtime.installed_skill_dir())
    assert service.install_state() == "install_failed"
    import shutil

    shutil.rmtree(runtime.installed_skill_dir())
    running_job(status="failed", stage="components", errorCode="component_failed", finishedAt=2)
    assert service.install_state() == "install_failed"


def test_uninstall_states(fake_toolchain, fake_mcp, monkeypatch):
    _configure()
    fake_mcp.set_connected()
    monkeypatch.setattr(ReaUninstaller, "is_running", classmethod(lambda cls: True))
    assert service.install_state() == "uninstalling"
    monkeypatch.setattr(ReaUninstaller, "is_running", classmethod(lambda cls: False))
    ReaUninstaller._job = ReaUninstallJob(id="u1", status="failed", startedAt=10, finishedAt=11, residues=["x"])
    assert service.install_state() == "uninstall_failed"


# ── badge ──


def test_badge_shows_until_the_account_completes_a_first_install(fake_mcp):
    assert service.badge_visible(ADMIN, "not_installed") is True
    assert service.badge_visible(MEMBER, "not_installed") is True
    for state in ("installing", "validating", "install_failed", "uninstall_failed"):
        assert service.badge_visible(ADMIN, state) is True
    for state in ("ready", "degraded"):
        assert service.badge_visible(ADMIN, state) is False

    service.record_first_install(ADMIN.id)
    # Permanently gone for this account, whatever the state becomes.
    for state in ("not_installed", "install_failed", "uninstall_failed"):
        assert service.badge_visible(ADMIN, state) is False
    assert service.badge_visible(ADMIN_2, "not_installed") is True
    first = json.loads((runtime.state_dir() / "preferences.json").read_text(encoding="utf-8"))["firstInstall"]
    service.record_first_install(ADMIN.id)
    again = json.loads((runtime.state_dir() / "preferences.json").read_text(encoding="utf-8"))["firstInstall"]
    assert first == again  # the first time is kept


def test_the_guide_ends_with_the_next_flocks_version_for_good(monkeypatch):
    monkeypatch.setattr(runtime, "flocks_version", lambda: "2026.10.13")
    assert service.guide_active() is True
    assert service.guide_info()["version"] == "2026.10.13"
    assert service.badge_visible(ADMIN, "not_installed") is True

    monkeypatch.setattr(runtime, "flocks_version", lambda: "2026.11.1")
    assert service.guide_active() is False
    assert service.badge_visible(ADMIN, "not_installed") is False
    info = service.guide_info()
    assert info["endedAt"] and info["endedOnVersion"] == "2026.11.1"

    # A later version, or going back, never opens it again.
    monkeypatch.setattr(runtime, "flocks_version", lambda: "2026.10.13")
    assert service.guide_active() is False
    monkeypatch.setattr(runtime, "flocks_version", lambda: "2026.12.1")
    assert service.guide_active() is False


def test_a_corrupt_preferences_file_is_treated_as_empty():
    path = runtime.state_dir() / "preferences.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert service.first_install_completed(ADMIN) is False
    assert service.badge_visible(ADMIN, "not_installed") is True
    service.record_first_install(ADMIN)
    assert service.first_install_completed(ADMIN) is True
    assert not list(path.parent.glob(".preferences.json.*.tmp"))


@pytest.mark.asyncio
async def test_status_badge_keeps_the_pre_uninstall_state(fake_toolchain, fake_mcp, monkeypatch):
    monkeypatch.setattr(ReaUninstaller, "is_running", classmethod(lambda cls: True))
    ReaUninstaller._job = ReaUninstallJob(id="u1", status="running", startedAt=10, previousState="ready")
    status = await service.build_status(ADMIN)
    assert status["state"] == "uninstalling" and status["badge"] is False
    ReaUninstaller._job = ReaUninstallJob(id="u1", status="running", startedAt=10, previousState="install_failed")
    assert (await service.build_status(ADMIN))["badge"] is True


# ── install conversation ──


def test_recorded_install_conversation_expires_unless_the_install_runs(monkeypatch):
    service.record_install_session("ses_install", ADMIN)
    assert service.recent_install_session(ADMIN) == "ses_install"
    assert service.recent_install_session(ADMIN_2) is None
    later = service._now_ms() + service.INSTALL_SESSION_REUSE_MS + 1
    monkeypatch.setattr(service, "_now_ms", lambda: later)
    assert service.recent_install_session(ADMIN) is None
    monkeypatch.setattr(ReaInstaller, "is_running", classmethod(lambda cls: True))
    assert service.recent_install_session(ADMIN) == "ses_install"


# ── status payload ──


@pytest.mark.asyncio
async def test_status_payload_for_admins_and_members(fake_toolchain, fake_mcp, running_job):
    _configure()
    fake_mcp.set_connected()
    running_job(
        status="failed", stage="components", errorCode="blocked", finishedAt=9, startedBy="admin-1",
        errorDetail="jdk: needs root", blockers=[{"code": "jdk_needs_root", "command": "dnf install -y java-21-openjdk-devel"}],
        components={"skill": {"key": "skill", "status": "reused", "detail": "/home/x/.flocks/plugins/skills/rea"}},
        validation={"javascript": {"key": "javascript", "status": "passed", "static": "passed", "runtime": "passed",
                                   "notes": ["/home/x/sample.js"]}},
        sessionId="ses_install",
    )
    admin = await service.build_status(ADMIN)
    assert admin["state"] == "ready" and admin["installed"] is True and admin["tier"] == "light"
    assert admin["canInstall"] is True and admin["badge"] is False
    assert admin["removable"] is True
    assert admin["job"]["errorDetail"] == "jdk: needs root" and admin["job"]["blockers"]
    assert admin["verification"]["service"]["toolsObserved"] == 170
    assert admin["verification"]["capabilities"]["javascript"]["tools"] == ["rea_analyze_javascript_application"]
    assert admin["verification"]["capabilities"]["javascript"]["notes"]
    # What conversations leave out is counted for the plugin square's 「查看详情」.
    assert admin["verification"]["capabilities"]["javascript"]["hiddenDetails"] == 1
    assert admin["job"]["validation"]["javascript"]["hiddenDetails"] == 1

    member = await service.build_status(MEMBER)
    assert member["canInstall"] is False and member["installSessionId"] is None
    assert member["job"]["errorDetail"] is None and member["job"]["blockers"] == []
    assert member["job"]["components"] == {"skill": {"key": "skill", "status": "reused"}}
    assert "notes" not in member["job"]["validation"]["javascript"]
    assert member["job"]["sessionId"] is None
    assert member["verification"]["capabilities"]["javascript"]["notes"] == []
    assert member["verification"]["capabilities"]["javascript"]["actual"] is None
    assert member["verification"]["capabilities"]["javascript"]["hiddenDetails"] == 0
    assert member["verification"]["capabilities"]["javascript"]["calls"] == []
    # Only the Node.js version is shown, never host paths.
    checks = {check["key"]: check for check in member["environment"]["checks"]}
    assert checks["npx"]["detail"] is None and checks["storage"]["detail"] is None


@pytest.mark.asyncio
async def test_degraded_problems_are_codes_for_admins(fake_toolchain, fake_mcp):
    _configure()
    fake_mcp.statuses["rea"] = fake_mcp._info("failed", 0, "boom")
    assert (await service.build_status(ADMIN))["degradedProblems"] == [
        {"code": "service_down", "status": "failed", "error": "boom"},
    ]
    assert (await service.build_status(MEMBER))["degradedProblems"] == []


def _full_tier_host(tmp_path):
    """A JDK 21 and a Ghidra with this host's decompiler, as a full-tier install left them."""

    jdk = tmp_path / "jdk-21"
    (jdk / "bin").mkdir(parents=True)
    for name in ("java", "javac"):
        (jdk / "bin" / name).write_text("", encoding="utf-8")
    ghidra = tmp_path / "ghidra_12.1.4_PUBLIC"
    (ghidra / "support").mkdir(parents=True)
    (ghidra / "support" / "analyzeHeadless").write_text("", encoding="utf-8")
    (ghidra / "Ghidra").mkdir()
    (ghidra / "Ghidra" / "application.properties").write_text("application.version=12.1.4\n", encoding="utf-8")
    native = ghidra / "Ghidra" / "Features" / "Decompiler" / "os" / (runtime.ghidra_platform() or "linux_x86_64")
    native.mkdir(parents=True)
    (native / "decompile").write_text("", encoding="utf-8")
    return jdk, ghidra


def test_a_full_tier_without_its_jdk_or_ghidra_is_degraded(fake_toolchain, fake_mcp, tmp_path):
    from flocks.config.config_writer import ConfigWriter

    jdk, ghidra = _full_tier_host(tmp_path)
    _configure("full")
    ConfigWriter.add_mcp_server(
        runtime.MCP_SERVER_NAME, runtime.mcp_server_config("full", java_home=str(jdk), ghidra_dir=str(ghidra)),
    )
    fake_mcp.set_connected()
    assert service.install_state() == "ready"

    # The service still lists its tools, but native decompilation cannot work any more.
    (jdk / "bin" / "javac").unlink()
    assert service.install_state() == "degraded"
    assert [problem["code"] for problem in service.degraded_problems()] == ["jdk_missing"]
    assert any(str(jdk) in reason for reason in service.degraded_reasons())
    assert service.degraded_reasons(details=False) == ["the JDK 21 the full tier runs with is missing"]
    (jdk / "bin" / "javac").write_text("", encoding="utf-8")
    for item in sorted(ghidra.rglob("decompile")):
        item.unlink()
    assert service.install_state() == "degraded"
    assert [problem["code"] for problem in service.degraded_problems()] == ["ghidra_decompiler_missing"]
    assert any("decompiler" in reason for reason in service.degraded_reasons())


def test_a_successful_uninstall_outranks_an_earlier_failed_install(fake_mcp):
    ReaInstaller._job = _job(status="failed", stage="components", tier="light", startedAt=10, finishedAt=20)
    assert service.install_state() == "install_failed"
    ReaUninstaller._job = ReaUninstallJob(
        id="reauninst_1", status="succeeded", startedAt=30, finishedAt=31, previousState="install_failed",
    )
    assert service.install_state() == "not_installed"
    # An install started after the uninstall counts again.
    ReaInstaller._job = _job(status="failed", stage="components", tier="light", startedAt=40, finishedAt=41)
    assert service.install_state() == "install_failed"


@pytest.mark.asyncio
async def test_status_survives_a_preferences_write_failure(fake_mcp, monkeypatch):
    def broken(_data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(service, "_write_preferences", broken)
    status = await service.build_status(ADMIN)
    assert status["state"] == "not_installed" and status["badge"] is True


def test_is_ready_used_by_the_plugin_square_and_uploads_follows_the_full_tier(fake_toolchain, fake_mcp, tmp_path):
    from flocks.config.config_writer import ConfigWriter

    jdk, ghidra = _full_tier_host(tmp_path)
    _configure("full")
    ConfigWriter.add_mcp_server(
        runtime.MCP_SERVER_NAME, runtime.mcp_server_config("full", java_home=str(jdk), ghidra_dir=str(ghidra)),
    )
    assert runtime.is_ready() is True
    (jdk / "bin" / "javac").unlink()
    assert runtime.is_ready() is False


def test_the_plugin_square_gets_what_failed_calls_returned(fake_toolchain, fake_mcp):
    _configure()
    record = runtime.read_install_record()
    record["verification"]["capabilities"]["native"] = {
        "status": "failed", "static": "failed", "runtime": "incomplete", "expected": "calc(5) = 22", "actual": "",
        "calls": [{"tool": "rea_open_binary", "ok": True, "summary": "{...}"},
                  {"tool": "rea_procedure_pseudo_code", "ok": False, "summary": "provider_unavailable at /srv/ghidra"}],
        "notes": [],
    }
    runtime.write_install_record(record)
    native = service.verification_summary(admin=True)["capabilities"]["native"]
    assert native["calls"] == [{"tool": "rea_procedure_pseudo_code", "ok": False, "summary": "provider_unavailable at /srv/ghidra"}]
    assert native["hiddenDetails"] == 1
    member = service.verification_summary(admin=False)["capabilities"]["native"]
    assert member["calls"] == [] and member["hiddenDetails"] == 0


@pytest.mark.asyncio
async def test_each_process_follows_the_registration_another_one_changed(fake_toolchain, fake_mcp):
    # An install or uninstall in another Flocks process writes flocks.json but
    # connects or removes the service only there.
    from flocks.config.config_writer import ConfigWriter

    _configure()
    await service.reconcile_service()
    assert fake_mcp.connects and fake_mcp.connects[-1][1] == ConfigWriter.get_mcp_server("rea")

    await service.reconcile_service()  # in line already: nothing to do
    assert len(fake_mcp.connects) == 1

    ConfigWriter.add_mcp_server("rea", runtime.mcp_server_config("full"))  # reinstalled with the full tier
    await service.reconcile_service()
    assert len(fake_mcp.connects) == 2 and fake_mcp.connects[-1][1]["environment"]

    ConfigWriter.remove_mcp_server("rea")  # uninstalled
    await service.reconcile_service()
    assert fake_mcp.removed == ["rea"] and "rea" not in fake_mcp.statuses


@pytest.mark.asyncio
async def test_calls_of_reas_tools_first_follow_the_registration(monkeypatch):
    from flocks.hooks.pipeline import HookContext
    from flocks.rea.service_guard import ReaServiceGuard

    calls = []

    async def sync(*, strict=False):
        calls.append(strict)

    monkeypatch.setattr(service, "sync_with_host", sync)
    guard = ReaServiceGuard()

    def context(name):
        return HookContext(stage="tool.execute.before", input={"tool_execution": {"tool": {"name": name}}})

    for name in ("rea_open_binary", "rea_analysis", "bash"):
        ctx = context(name)
        await guard.tool_before(ctx)
        assert "execution" not in ctx.output
    assert calls == [True]  # only the service's own tools

    # Not knowing whether REA is still there, or while it is being uninstalled: the call is stopped.
    async def unreadable(*, strict=False):
        raise OSError("flocks.json could not be read")

    monkeypatch.setattr(service, "sync_with_host", unreadable)
    ctx = context("rea_open_binary")
    await guard.tool_before(ctx)
    assert ctx.output["execution"]["stop"] is True and "could not be checked" in ctx.output["execution"]["detail"]

    monkeypatch.setattr(service, "sync_with_host", sync)
    ReaUninstaller._job = ReaUninstallJob(id="reauninst_x", status="running", startedAt=1)
    ReaUninstaller._starting = True
    try:
        ctx = context("rea_open_binary")
        await guard.tool_before(ctx)
        assert ctx.output["execution"]["stop"] is True and "uninstalled" in ctx.output["execution"]["detail"]
    finally:
        ReaUninstaller._starting = False
