"""Tier components: reuse what is there, retry the skill source, verify Ghidra before unpacking."""

from __future__ import annotations

import asyncio
import hashlib
import http.server
import io
import os
import stat
import threading
import zipfile
from pathlib import Path

import pytest

from flocks.rea import components, runtime
from tests.rea.conftest import write_skill


# ── skill ──


@pytest.mark.asyncio
async def test_skill_is_reused_when_present(fake_skill_source):
    write_skill(runtime.installed_skill_dir())
    result = await components.ensure_skill()
    assert result.status == "reused"
    assert fake_skill_source["calls"] == []


@pytest.mark.asyncio
async def test_skill_source_is_retried_as_is(fake_skill_source):
    fake_skill_source["failures"] = 2
    result = await components.ensure_skill()
    assert result.status == "installed"
    assert result.attempts == 3
    assert fake_skill_source["calls"] == [(runtime.REA_SKILL_SOURCE, "global")] * 3
    assert runtime.skill_installed()


@pytest.mark.asyncio
async def test_skill_failure_names_the_source(fake_skill_source):
    fake_skill_source["failures"] = 99
    result = await components.ensure_skill(attempts=2)
    assert result.status == "failed"
    assert result.attempts == 2
    assert runtime.REA_SKILL_SOURCE in result.detail and "unreachable" in result.detail


# ── REA in the npx cache ──


@pytest.mark.asyncio
async def test_rea_cli_is_installed_once_then_reused(fake_toolchain):
    assert components.rea_cached() is False
    first = await components.ensure_rea_cli()
    assert (first.status, first.detail) == ("installed", "rea-agents@6.4.0")
    assert components.rea_cached() is True
    second = await components.ensure_rea_cli()
    assert second.status == "reused"


@pytest.mark.asyncio
async def test_rea_cli_version_must_match(fake_toolchain, monkeypatch):
    monkeypatch.setenv("FAKE_REA_VERSION", "6.3.9")
    result = await components.ensure_rea_cli()
    assert result.status == "failed"
    assert components.rea_cached() is False  # the cache holds another version


@pytest.mark.asyncio
async def test_rea_cli_network_failure_keeps_npm_output(fake_toolchain, monkeypatch):
    monkeypatch.setenv("FAKE_NPX_MODE", "network")
    result = await components.ensure_rea_cli()
    assert result.status == "failed"
    assert "ECONNREFUSED" in result.detail


def test_npm_failures_are_summarised_to_their_cause():
    output = (
        "npm error code ECONNREFUSED\nnpm error syscall connect\nnpm error errno ECONNREFUSED\n"
        "npm error FetchError: request to http://127.0.0.1:9/rea-agents failed, reason: connect ECONNREFUSED 127.0.0.1:9\n"
        "npm error     at ClientRequest.<anonymous> (/opt/npm/minipass-fetch/lib/index.js:130:14)\n"
        "npm error   code: 'ECONNREFUSED',\nnpm error }\n"
        "npm error A complete log of this run can be found in: /home/u/.npm/_logs/x.log\n"
    )
    assert components.npm_failure_summary(output) == (
        "npm error code ECONNREFUSED / npm error FetchError: request to http://127.0.0.1:9/rea-agents failed, "
        "reason: connect ECONNREFUSED 127.0.0.1:9"
    )
    assert components.npm_failure_summary("npm error code E404\nnpm error 404 Not Found - GET https://r/x") == (
        "npm error code E404 / npm error 404 Not Found - GET https://r/x"
    )
    assert components.npm_failure_summary("something odd") == "something odd"


@pytest.mark.asyncio
async def test_rea_cli_without_npx(monkeypatch):
    monkeypatch.setattr(runtime, "npx_executable", lambda: None)
    result = await components.ensure_rea_cli()
    assert (result.status, result.detail) == ("failed", "npx not found")


# ── JDK 21 ──


@pytest.mark.asyncio
async def test_existing_jdk_is_reused(monkeypatch):
    async def found():
        return "/usr/lib/jvm/java-21-openjdk"

    monkeypatch.setattr(runtime, "find_java_home", found)
    result = await components.ensure_jdk()
    assert (result.status, result.detail) == ("reused", "/usr/lib/jvm/java-21-openjdk")


@pytest.mark.asyncio
async def test_jdk_without_dnf_or_root_is_a_blocker(monkeypatch):
    real_which = components.shutil.which
    monkeypatch.setattr(components.shutil, "which", lambda name, *a, **k: None if name == "dnf" else real_which(name, *a, **k))
    result = await components.ensure_jdk()
    assert result.status == "blocked"
    assert result.blocker["code"] == "jdk_no_dnf"
    assert "JDK 21" in components.blocker_text(result.blocker)

    monkeypatch.setattr(
        components.shutil, "which", lambda name, *a, **k: "/usr/bin/dnf" if name == "dnf" else real_which(name, *a, **k),
    )
    monkeypatch.setattr(components, "_is_root", lambda: False)
    result = await components.ensure_jdk()
    assert result.blocker == {"code": "jdk_needs_root", "command": "dnf install -y java-21-openjdk-devel"}
    assert "run `dnf install -y java-21-openjdk-devel` as root" in components.blocker_text(result.blocker)


@pytest.mark.asyncio
async def test_root_installs_jdk_with_dnf(monkeypatch):
    commands = []
    homes = iter([None, "/usr/lib/jvm/java-21-openjdk"])

    async def find():
        return next(homes)

    async def run(command, timeout, **_kwargs):
        commands.append(command)
        return 0, "Complete!"

    monkeypatch.setattr(runtime, "find_java_home", find)
    monkeypatch.setattr(runtime, "run_process", run)
    real_which = components.shutil.which
    monkeypatch.setattr(
        components.shutil, "which", lambda name, *a, **k: "/usr/bin/dnf" if name == "dnf" else real_which(name, *a, **k),
    )
    monkeypatch.setattr(components, "_is_root", lambda: True)
    result = await components.ensure_jdk()
    assert commands == [["dnf", "install", "-y", "java-21-openjdk-devel"]]
    assert (result.status, result.detail) == ("installed", "/usr/lib/jvm/java-21-openjdk")


# ── Ghidra 12.1.4 ──


def _ghidra_zip(decompiler: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        script = zipfile.ZipInfo("ghidra_12.1.4_PUBLIC/support/analyzeHeadless")
        script.external_attr = (stat.S_IFREG | 0o755) << 16
        bundle.writestr(script, "#!/bin/sh\n")
        bundle.writestr("ghidra_12.1.4_PUBLIC/Ghidra/application.properties", "application.version=12.1.4\n")
        if decompiler:
            native = zipfile.ZipInfo(f"ghidra_12.1.4_PUBLIC/Ghidra/Features/Decompiler/os/{runtime.ghidra_platform()}/decompile")
            native.external_attr = (stat.S_IFREG | 0o755) << 16
            bundle.writestr(native, "\x7fELF")
    return buffer.getvalue()


def _fake_ghidra(root: Path, *, decompiler: str | None = "os") -> Path:
    (root / "support").mkdir(parents=True)
    (root / "support" / "analyzeHeadless").write_text("", encoding="utf-8")
    (root / "Ghidra").mkdir()
    (root / "Ghidra" / "application.properties").write_text("application.version=12.1.4\n", encoding="utf-8")
    if decompiler:
        native = root / "Ghidra" / "Features" / "Decompiler" / decompiler / runtime.ghidra_platform()
        native.mkdir(parents=True)
        (native / "decompile").write_text("", encoding="utf-8")
    return root


class _Server:
    """Serves ``/official/<archive>`` and ``/mirror/<official url>``; counts requests per prefix."""

    def __init__(self, payload: bytes, mirror_payload: bytes | None = None):
        self.payload = payload
        self.mirror_payload = payload if mirror_payload is None else mirror_payload
        self.hits: list[str] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                server.hits.append(self.path)
                if self.path.startswith("/mirror/"):
                    body = server.mirror_payload
                elif self.path.startswith("/official/"):
                    body = server.payload
                else:
                    self.send_error(404)
                    return
                if body is None:
                    self.send_error(502)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def ghidra_source(monkeypatch):
    payload = _ghidra_zip()

    def configure(server: _Server, sha256: str | None = None):
        official = f"{server.base}/official/{runtime.GHIDRA_ARCHIVE}"
        monkeypatch.setattr(runtime, "GHIDRA_URL", official)
        monkeypatch.setattr(runtime, "GHIDRA_MIRROR_PREFIX", f"{server.base}/mirror/")
        monkeypatch.setattr(runtime, "GHIDRA_SHA256", sha256 or hashlib.sha256(server.payload).hexdigest())

    return payload, configure


@pytest.mark.asyncio
@pytest.mark.skipif(not components.shutil.which("curl"), reason="curl downloads the archive")
async def test_ghidra_is_downloaded_through_the_mirror_checked_and_unpacked(ghidra_source):
    payload, configure = ghidra_source
    target = Path(runtime.ghidra_target_dir())
    with _Server(payload) as server:
        configure(server)
        result = await components.ensure_ghidra()
    assert (result.status, result.detail) == ("installed", str(target))
    assert result.evidence == {"sha256": runtime.GHIDRA_SHA256, "verified": True, "source": "mirror", "rejected": []}
    assert server.hits == [f"/mirror/{runtime.GHIDRA_URL}"]
    assert runtime.ghidra_install_ok(str(target))
    assert os.access(target / "support" / "analyzeHeadless", os.X_OK)
    assert not (runtime.state_dir() / "downloads" / runtime.GHIDRA_ARCHIVE).exists()


@pytest.mark.asyncio
@pytest.mark.skipif(not components.shutil.which("curl"), reason="curl downloads the archive")
async def test_a_wrong_checksum_is_discarded_and_downloaded_again(ghidra_source):
    payload, configure = ghidra_source
    with _Server(payload, mirror_payload=b"tampered") as server:
        configure(server)
        result = await components.ensure_ghidra()
    # Mirror twice (bad checksum each time), then the official host on the last attempt.
    assert [hit.split("/")[1] for hit in server.hits] == ["mirror", "mirror", "official"]
    assert result.status == "installed"
    assert result.attempts == 3
    tampered = hashlib.sha256(b"tampered").hexdigest()
    assert result.evidence["source"] == "official" and result.evidence["rejected"] == [tampered, tampered]


@pytest.mark.asyncio
@pytest.mark.skipif(not components.shutil.which("curl"), reason="curl downloads the archive")
async def test_a_checksum_that_never_matches_is_never_unpacked(ghidra_source):
    payload, configure = ghidra_source
    with _Server(payload) as server:
        configure(server, sha256="0" * 64)
        result = await components.ensure_ghidra()
    assert result.status == "failed"
    assert "SHA-256 mismatch" in result.detail
    assert not Path(runtime.ghidra_target_dir()).exists()
    assert not (runtime.state_dir() / "downloads" / runtime.GHIDRA_ARCHIVE).exists()


@pytest.mark.asyncio
async def test_an_installed_ghidra_is_reused(monkeypatch, tmp_path):
    existing = _fake_ghidra(tmp_path / "ghidra")
    monkeypatch.setenv("FLOCKS_REA_GHIDRA_DIR", str(existing))
    result = await components.ensure_ghidra()
    assert (result.status, result.detail) == ("reused", str(existing))


@pytest.mark.asyncio
async def test_ghidra_without_a_decompiler_for_this_host_is_a_blocker(monkeypatch, tmp_path):
    existing = _fake_ghidra(tmp_path / "ghidra", decompiler=None)
    monkeypatch.setenv("FLOCKS_REA_GHIDRA_DIR", str(existing))
    result = await components.ensure_ghidra()
    assert result.status == "blocked"
    assert result.blocker == {
        "code": "ghidra_decompiler_missing",
        "platform": runtime.ghidra_platform(),
        "command": f"cd {existing / 'support' / 'gradle'} && ./gradlew buildNatives",
    }
    assert "buildNatives" in components.blocker_text(result.blocker)
    plan = await components.plan_components("full")
    assert plan["ghidra"]["action"] == "blocked"

    # A decompiler built from the release (build/os/<platform>) counts.
    built = existing / "Ghidra" / "Features" / "Decompiler" / "build" / "os" / runtime.ghidra_platform()
    built.mkdir(parents=True)
    (built / "decompile").write_text("", encoding="utf-8")
    assert (await components.ensure_ghidra()).status == "reused"


@pytest.mark.asyncio
@pytest.mark.skipif(not components.shutil.which("curl"), reason="curl downloads the archive")
async def test_unpacking_leaves_what_sits_beside_the_target_alone(ghidra_source, tmp_path, monkeypatch):
    payload, configure = ghidra_source
    target = tmp_path / "custom" / "ghidra"
    monkeypatch.setenv("FLOCKS_REA_GHIDRA_DIR", str(target))
    # An unrelated folder with the release's name already sits next to the target.
    neighbour = tmp_path / "custom" / runtime.GHIDRA_FOLDER
    neighbour.mkdir(parents=True)
    (neighbour / "mine.txt").write_text("keep", encoding="utf-8")
    with _Server(payload) as server:
        configure(server)
        result = await components.ensure_ghidra()
    assert (result.status, result.detail) == ("installed", str(target))
    assert runtime.ghidra_install_ok(str(target)) and not (target / "mine.txt").exists()
    assert (neighbour / "mine.txt").read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in neighbour.iterdir()) == ["mine.txt"]
    assert not list((tmp_path / "custom").glob(".rea-ghidra-*"))  # the staging folder is gone


@pytest.mark.asyncio
@pytest.mark.skipif(not components.shutil.which("curl"), reason="curl downloads the archive")
async def test_an_empty_target_folder_is_filled_and_a_used_one_is_left_alone(ghidra_source):
    payload, configure = ghidra_source
    target = Path(runtime.ghidra_target_dir())
    target.mkdir(parents=True)
    with _Server(payload) as server:
        configure(server)
        result = await components.ensure_ghidra()
    assert result.status == "installed" and runtime.ghidra_install_ok(str(target))

    for item in list(target.iterdir()):
        components.shutil.rmtree(item) if item.is_dir() else item.unlink()
    (target / "notes.txt").write_text("someone else's", encoding="utf-8")
    with _Server(payload) as server:
        configure(server)
        result = await components.ensure_ghidra()
        assert server.hits == []  # nothing is downloaded for a folder that cannot take it
    assert result.status == "blocked" and result.blocker["code"] == "ghidra_dir_occupied"
    assert sorted(path.name for path in target.iterdir()) == ["notes.txt"]


def test_the_build_command_quotes_the_ghidra_folder(monkeypatch):
    from pathlib import PurePosixPath, PureWindowsPath

    monkeypatch.setattr(runtime, "_is_windows", lambda: False)
    assert components._build_natives_command(PurePosixPath("/srv/my tools/ghidra")) == (
        "cd '/srv/my tools/ghidra/support/gradle' && ./gradlew buildNatives"
    )
    monkeypatch.setattr(runtime, "_is_windows", lambda: True)
    assert components._build_natives_command(PureWindowsPath("C:/my tools/ghidra")) == (
        'cd /d "C:\\my tools\\ghidra\\support\\gradle" && gradlew.bat buildNatives'
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not components.shutil.which("curl"), reason="curl downloads the archive")
async def test_a_verified_download_without_a_host_decompiler_keeps_the_evidence(monkeypatch):
    payload = _ghidra_zip(decompiler=False)
    with _Server(payload) as server:
        monkeypatch.setattr(runtime, "GHIDRA_URL", f"{server.base}/official/{runtime.GHIDRA_ARCHIVE}")
        monkeypatch.setattr(runtime, "GHIDRA_MIRROR_PREFIX", f"{server.base}/mirror/")
        monkeypatch.setattr(runtime, "GHIDRA_SHA256", hashlib.sha256(payload).hexdigest())
        result = await components.ensure_ghidra()
    assert result.status == "blocked" and result.blocker["code"] == "ghidra_decompiler_missing"
    assert result.evidence["verified"] is True
    assert runtime.ghidra_install_ok(runtime.ghidra_target_dir())  # unpacked; a retry reuses it


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [("Linux", "x86_64", "linux_x86_64"), ("Darwin", "arm64", "mac_arm_64"), ("Linux", "aarch64", "linux_arm_64"),
     ("Windows", "AMD64", "win_x86_64"), ("Plan9", "mips", None)],
)
def test_ghidra_platform_names(monkeypatch, system, machine, expected):
    import platform

    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    assert runtime.ghidra_platform() == expected


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignores directory permissions")
async def test_an_unwritable_ghidra_parent_is_a_blocker(monkeypatch, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv("FLOCKS_REA_GHIDRA_DIR", str(locked / "ghidra_12.1.4_PUBLIC"))
    try:
        result = await components.ensure_ghidra()
    finally:
        locked.chmod(0o700)
    assert result.status == "blocked"
    assert result.blocker == {"code": "ghidra_dir_not_writable", "path": str(locked), "env": "FLOCKS_REA_GHIDRA_DIR"}


def test_python_unpack_keeps_exec_bits_and_refuses_escapes(tmp_path):
    archive = tmp_path / "g.zip"
    archive.write_bytes(_ghidra_zip())
    components._unpack_zip(archive, tmp_path / "out")
    assert os.access(tmp_path / "out" / "ghidra_12.1.4_PUBLIC" / "support" / "analyzeHeadless", os.X_OK)

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr("../escaped.txt", "x")
    with pytest.raises(ValueError):
        components._unpack_zip(evil, tmp_path / "out2")
    assert not (tmp_path / "escaped.txt").exists()


# ── tiers ──


@pytest.mark.asyncio
async def test_light_tier_never_touches_jdk_or_ghidra(monkeypatch):
    seen = []

    def fake(key):
        async def ensure(*_args, **_kwargs):
            seen.append(key)
            return components.ComponentResult(key, "reused")
        return ensure

    for name, key in (("skill", "skill"), ("rea_cli", "rea"), ("jdk", "jdk"), ("ghidra", "ghidra")):
        monkeypatch.setattr(components, f"ensure_{name}", fake(key))
    done = []

    async def on_done(result):
        done.append(result.key)

    results = await components.ensure_components("light", on_done=on_done)
    assert set(results) == {"skill", "rea"} and sorted(seen) == ["rea", "skill"]
    assert sorted(done) == ["rea", "skill"]

    seen.clear()
    results = await components.ensure_components("full")
    assert set(results) == {"skill", "rea", "jdk", "ghidra"}


@pytest.mark.asyncio
async def test_components_run_in_parallel(monkeypatch):
    started = []
    release = asyncio.Event()

    def slow(key):
        async def ensure(*_args, **_kwargs):
            started.append(key)
            await release.wait()
            return components.ComponentResult(key, "installed")
        return ensure

    for key, result_key in (("skill", "skill"), ("rea_cli", "rea"), ("jdk", "jdk"), ("ghidra", "ghidra")):
        monkeypatch.setattr(components, f"ensure_{key}", slow(result_key))
    task = asyncio.create_task(components.ensure_components("full"))
    for _ in range(50):
        if len(started) == 4:
            break
        await asyncio.sleep(0.01)
    assert sorted(started) == ["ghidra", "jdk", "rea", "skill"]  # all four started before any finished
    release.set()
    assert set(await task) == {"skill", "rea", "jdk", "ghidra"}


@pytest.mark.asyncio
async def test_an_error_in_one_component_waits_for_the_others(monkeypatch):
    finished = []

    async def broken(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    def slow(key):
        async def ensure(*_args, **_kwargs):
            await asyncio.sleep(0.05)
            finished.append(key)
            return components.ComponentResult(key, "installed")
        return ensure

    monkeypatch.setattr(components, "ensure_ghidra", broken)
    for key, result_key in (("skill", "skill"), ("rea_cli", "rea"), ("jdk", "jdk")):
        monkeypatch.setattr(components, f"ensure_{key}", slow(result_key))
    with pytest.raises(OSError):
        await components.ensure_components("full")
    # No component is still writing when the install job settles.
    assert sorted(finished) == ["jdk", "rea", "skill"]


@pytest.mark.asyncio
async def test_plan_shows_reuse_installs_and_blockers(fake_toolchain, monkeypatch):
    plan = await components.plan_components("light")
    assert plan == {"skill": {"action": "install"}, "rea": {"action": "download"}}

    write_skill(runtime.installed_skill_dir())
    await components.ensure_rea_cli()
    assert await components.plan_components("light") == {"skill": {"action": "reuse"}, "rea": {"action": "reuse"}}

    real_which = components.shutil.which
    monkeypatch.setattr(components.shutil, "which", lambda name, *a, **k: None if name == "dnf" else real_which(name, *a, **k))
    full = await components.plan_components("full")
    assert full["jdk"]["action"] == "blocked" and full["jdk"]["blocker"]["code"] == "jdk_no_dnf"
    assert full["ghidra"] == {"action": "download", "path": runtime.ghidra_target_dir()}

    monkeypatch.setattr(
        components.shutil, "which", lambda name, *a, **k: "/usr/bin/dnf" if name == "dnf" else real_which(name, *a, **k),
    )
    monkeypatch.setattr(components, "_is_root", lambda: True)
    full = await components.plan_components("full")
    assert full["jdk"] == {"action": "install", "command": "dnf install -y java-21-openjdk-devel"}


def test_conversations_keep_blockers_as_codes_and_the_guides_own_values(monkeypatch):
    placeholder = runtime.PATH_PLACEHOLDER
    guide = {"code": "jdk_no_dnf", "javaHome": runtime.JAVA_HOME_DEFAULT, "env": "FLOCKS_REA_JAVA_HOME"}
    assert components.public_blocker(guide) == guide
    assert components.public_blocker({**guide, "javaHome": "/srv/jdk 21"})["javaHome"] == placeholder
    # The command is the guide's own, whatever the blocker carried.
    assert components.public_blocker({"code": "jdk_needs_root", "command": "dnf install -y /srv/pkgs/jdk.rpm"}) == {
        "code": "jdk_needs_root", "command": components.jdk_install_command(),
    }
    occupied = {"code": "ghidra_dir_occupied", "path": runtime.GHIDRA_DIR_DEFAULT, "env": "FLOCKS_REA_GHIDRA_DIR"}
    assert components.public_blocker(occupied) == occupied
    assert components.public_blocker({**occupied, "path": "/srv/my tools/ghidra"})["path"] == placeholder
    readonly = {"code": "ghidra_dir_not_writable", "path": "/opt", "env": "FLOCKS_REA_GHIDRA_DIR"}
    assert components.public_blocker(readonly) == readonly
    decompiler = {"code": "ghidra_decompiler_missing", "platform": "mac_arm_64",
                  "command": "cd '/srv/my tools/ghidra/support/gradle' && ./gradlew buildNatives"}
    monkeypatch.setattr(runtime, "_is_windows", lambda: False)
    assert components.public_blocker(decompiler) == {
        "code": "ghidra_decompiler_missing", "platform": "mac_arm_64",
        "command": f"cd {placeholder}/support/gradle && ./gradlew buildNatives",
    }
    monkeypatch.setattr(runtime, "_is_windows", lambda: True)
    assert components.public_blocker(decompiler)["command"] == (
        f'cd /d "{placeholder}\\support\\gradle" && gradlew.bat buildNatives'
    )
    assert components.public_blocker({"code": "something_new", "path": "/srv/x"}) == {"code": "something_new"}
