"""flocks.updater.enterprise: the move from this open install to the enterprise edition.

Case list (编号 | 场景):
  EU1 the Python enterprise package of before is found by its distribution or its licence file;
      nothing in an install the enterprise core runs, nothing on a plain open install
  EU1b the licence the page shows is the one Console is asked for: under FLOCKS_ROOT, under the
       home's .flocks (the old offline checker), or only in the old package's own status
  EU2 how this account may install: root's one-time command for /opt/flockspro, root for a
      systemd machine, the page itself otherwise
  EU3 Console's manifest: this platform's installer, asked with the licence id (and the Console
      session when there is one); a sign-in asked for, no installer, an incomplete one, one not over
      https are each said as they are
  EU4 the download is checked against Console's digest: a mismatch leaves nothing to run
  EU4b what Console says of the release is kept for the edition the installer puts in place, once
       the download is checked; nothing is kept of what Console says nothing about, and not being
       able to keep it does not stop the upgrade
  EU5 the installer runs apart, without this runtime's Python settings, and the page reads how it
      ended (exit status, log), also after this server is gone
  EU5b a finished install the enterprise edition has since gone back from (its downgrade record is
       newer) is no upgrade any more: the page does not say it is installed
  EU6 the whole of it: a systemd machine ends with root's command for the downloaded file, an
      account's own install with the installer running; one at a time
  EU7 what the enterprise edition's downgrades kept aside is listed from its record (when, still
      there, size), and nothing of it is removed
  EU8 Windows: the platform is windows-amd64 and the installer an .exe in Program Files' place;
      the page may start it (it asks for an administrator itself), from a process that is gone at
      once (no descendant of this server), and tells it this install's data and flocks command;
      a run is looked at without os.kill (which ends a process there), one at a time by msvcrt
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

from flocks.updater import enterprise

PLATFORM = enterprise.platform_id()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".flocks"
    root.mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("FLOCKS_DEPLOY_MODE", "FLOCKS_OFFLINE_INSTALL", "INVOCATION_ID", "FLOCKSPRO_LICENSE_ID",
                "FLOCKS_CONSOLE_BASE_URL", "FLOCKS_UPDATE_CHANNEL"):
        monkeypatch.delenv(key, raising=False)
    enterprise.detect_deploy_mode.cache_clear()
    install_root = tmp_path / "opt-flockspro"
    install_root.mkdir()
    monkeypatch.setattr(enterprise, "INSTALL_ROOT", install_root)
    monkeypatch.setattr(enterprise, "PREVIOUS_DISTRIBUTION", "flockspro-not-installed-here")
    monkeypatch.setattr(enterprise, "_previous_status", lambda: {})
    yield root
    enterprise.detect_deploy_mode.cache_clear()


def write_licence(root: Path, licence_id: str = "lic-old-7") -> None:
    path = root / "flockspro" / "license.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"license_id": licence_id, "key": "a.b.c", "payload": {"license_id": licence_id}}))


def console(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """A Console answering through handler; the requests it got."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real = httpx.AsyncClient

    class Client(real):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(record)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(enterprise.httpx, "AsyncClient", Client)
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example")
    return seen


def manifest_with(installer: bytes, **entry) -> dict:
    return {
        "installers": [
            {"platform": "other-arch", "url": "https://console.example/other.run", "sha256": "0" * 64, "release_id": "x", "version": "1.0"},
            {
                "platform": PLATFORM,
                "url": "https://console.example/flockspro-2026.11.5.run",
                "sha256": hashlib.sha256(installer).hexdigest(),
                "release_id": f"2026.11.5-{PLATFORM}",
                "version": "2026.11.5",
                **entry,
            },
        ]
    }


def test_eu1_the_previous_edition_is_found_by_its_package_or_its_licence(machine: Path, monkeypatch: pytest.MonkeyPatch):
    assert enterprise.previous_edition() is None

    write_licence(machine)
    previous = enterprise.previous_edition()
    assert previous is not None and previous["license_id"] == "lic-old-7" and previous["version"] is None

    # The package itself, with its own word on the licence.
    monkeypatch.setattr(enterprise, "PREVIOUS_DISTRIBUTION", "pytest")
    monkeypatch.setattr(enterprise, "_previous_status", lambda: {"license_status": "commercial", "pro_enabled": True, "expires_at": "2027-01-27"})
    previous = enterprise.previous_edition()
    assert previous["version"] and previous["license_status"] == "commercial" and previous["pro_enabled"] is True

    # In an install the enterprise core runs, the flockspro package is the new one: nothing here.
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    enterprise.detect_deploy_mode.cache_clear()
    assert enterprise.previous_edition() is None and enterprise.available() is False


async def test_eu1b_the_page_and_console_go_by_one_licence(machine: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    seen = console(monkeypatch, lambda request: httpx.Response(200, json=manifest_with(b"x")))
    monkeypatch.setattr(enterprise, "_console_session_token", _token(""))

    # The old offline checker kept it under the home's .flocks, whatever FLOCKS_ROOT said.
    home_licence = tmp_path / "home" / ".flocks"
    write_licence(home_licence, "lic-home-1")
    assert enterprise.previous_edition()["license_id"] == "lic-home-1"
    await enterprise.fetch_offer()
    assert seen[-1].headers["x-license-id"] == "lic-home-1"

    # No file at all: what the old package itself says.
    (home_licence / "flockspro" / "license.json").unlink()
    monkeypatch.setattr(enterprise, "PREVIOUS_DISTRIBUTION", "pytest")
    monkeypatch.setattr(enterprise, "_previous_status", lambda: {"license_id": "lic-status-2", "license_status": "commercial"})
    assert enterprise.previous_edition()["license_id"] == "lic-status-2"
    await enterprise.fetch_offer()
    assert seen[-1].headers["x-license-id"] == "lic-status-2"


@pytest.mark.parametrize("licence_id", ["ubuntu-nn", "zj6.29-centos9", "new-pro-7.1zj", "lic_ee11597f36ec4e4b", "Ubuntu 22/测试 授权"])
async def test_eu1c_a_licence_id_is_whatever_console_issued(machine: Path, monkeypatch: pytest.MonkeyPatch, licence_id: str):
    """Old licences have no fixed format: the page and Console go by the id as it is, whatever it looks like."""
    seen = console(monkeypatch, lambda request: httpx.Response(200, json=manifest_with(b"x")))
    monkeypatch.setattr(enterprise, "_console_session_token", _token("session-token"))
    write_licence(machine, licence_id)

    assert enterprise.previous_edition()["license_id"] == licence_id
    offer = await enterprise.fetch_offer()

    assert offer.version == "2026.11.5"
    assert seen[-1].url.params["license_id"] == licence_id
    # A header carries only ASCII: an id that is not goes by the query alone, instead of failing the request.
    if licence_id.isascii():
        assert seen[-1].headers["x-license-id"] == licence_id
    else:
        assert "x-license-id" not in seen[-1].headers


def _unsigned_token(claims: dict) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'ES256', 'kid': 'flocks-license-test'})}.{part(claims)}.c2ln"


def test_eu1d_the_page_reads_the_terms_of_a_licence_with_no_package_to_answer_for_it(machine: Path):
    """A licence taken here keeps an empty payload beside its token (enterprise_application.take)."""
    path = machine / "flockspro" / "license.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "license_id": "ubuntu-nn",
        "key": _unsigned_token({"license_id": "ubuntu-nn", "status": "commercial", "expires_at": 1825321652, "max_admins": 3, "max_members": 20}),
        "payload": {},
        "activated_at": 1793811952,
        "install_id": "inst-1",
        "fingerprint": "fp-1",
    }))

    previous = enterprise.previous_edition()

    assert previous == {
        "version": None, "license_id": "ubuntu-nn", "license_status": "commercial", "pro_enabled": False,
        "expires_at": 1825321652, "max_admins": 3, "max_members": 20, "last_sync_at": 1793811952,
        "install_id": "inst-1", "fingerprint": "fp-1",
    }


@pytest.mark.parametrize("content", ["not json", "[]", json.dumps({"license_id": "x", "key": "only.two"}), json.dumps({"license_id": "x", "key": "a.%%%.c"})])
def test_eu1e_a_licence_file_that_does_not_read_leaves_its_terms_out(machine: Path, content: str):
    path = machine / "flockspro" / "license.json"
    path.parent.mkdir(parents=True)
    path.write_text(content)

    previous = enterprise.previous_edition()

    if previous is not None:  # the file named an id, and nothing more that reads
        assert previous["license_id"] == "x" and previous["license_status"] is None and previous["expires_at"] is None


def test_eu1e2_the_terms_shown_are_those_of_the_licence_shown(machine: Path, monkeypatch: pytest.MonkeyPatch):
    """Another licence named by the environment: the file's terms are not that licence's."""
    path = machine / "flockspro" / "license.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"license_id": "lic-file", "key": _unsigned_token({"status": "expired", "expires_at": 1, "max_admins": 9}), "payload": {}}))
    monkeypatch.setenv("FLOCKSPRO_LICENSE_ID", "ubuntu-nn")

    previous = enterprise.previous_edition()

    assert previous["license_id"] == "ubuntu-nn"
    assert (previous["license_status"], previous["expires_at"], previous["max_admins"]) == (None, None, None)


def test_eu1e3_a_file_with_no_licence_does_not_hide_the_one_after_it(machine: Path, tmp_path: Path):
    (machine / "flockspro").mkdir(parents=True)
    (machine / "flockspro" / "license.json").write_text("{}")
    home = tmp_path / "home" / ".flocks" / "flockspro"
    home.mkdir(parents=True)
    (home / "license.json").write_text(json.dumps({"license_id": "ubuntu-nn", "key": _unsigned_token({"status": "commercial", "max_members": 7}), "payload": {}}))

    previous = enterprise.previous_edition()

    assert (previous["license_id"], previous["license_status"], previous["max_members"]) == ("ubuntu-nn", "commercial", 7)


def test_eu1f_the_python_packages_word_on_its_licence_comes_first(machine: Path, monkeypatch: pytest.MonkeyPatch):
    """What an old package reports (/api/flockspro/license/status on 2026.10.19) fills the licence card."""
    write_licence(machine, "zj6.29-centos9")
    monkeypatch.setattr(enterprise, "PREVIOUS_DISTRIBUTION", "pytest")
    monkeypatch.setattr(enterprise, "_previous_status", lambda: {
        "license_id": "zj6.29-centos9", "license_status": "poc", "pro_enabled": True, "expires_at": 1825321652,
        "max_admins": 5, "max_members": 50, "fingerprint": "fp-old", "install_id": "inst-old", "last_heartbeat_ok_at": 1793811952,
    })

    previous = enterprise.previous_edition()

    assert previous["license_id"] == "zj6.29-centos9" and previous["license_status"] == "poc"
    assert (previous["max_admins"], previous["max_members"]) == (5, 50)
    assert previous["last_sync_at"] == 1793811952
    assert (previous["install_id"], previous["fingerprint"]) == ("inst-old", "fp-old")


def test_eu2_an_account_that_cannot_write_the_default_directory_gets_its_own(machine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine set up without root needs none for this either (installer.sh chooses the same)."""
    home = machine.parent / "account-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(enterprise, "_euid", lambda: 1000)
    monkeypatch.setattr(enterprise, "INSTALL_ROOT", machine.parent / "missing")

    situation = enterprise.situation()

    assert situation["install_root"] == str(home / "flockspro")
    assert situation["can_start"] is True and situation["prepare_command"] is None


def test_eu2_an_install_at_the_default_directory_stays_there(machine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = machine.parent / "account-home"
    home.mkdir()
    default = machine.parent / "default-root"
    (default / "current").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(enterprise, "_euid", lambda: 1000)
    monkeypatch.setattr(enterprise, "INSTALL_ROOT", default)

    assert enterprise.situation()["install_root"] == str(default)


@pytest.mark.skipif(os.geteuid() == 0, reason="as an account of its own")
def test_eu2_how_this_account_may_install(machine: Path, monkeypatch: pytest.MonkeyPatch):
    situation = enterprise.situation()
    assert situation["can_start"] is True and situation["prepare_command"] is None
    assert situation["manual_command"] == f"bash flockspro-<版本>-{PLATFORM}.run"

    # /opt/flockspro not there: root makes it for this account, once.
    monkeypatch.setattr(enterprise, "INSTALL_ROOT", machine.parent / "missing")
    situation = enterprise.situation()
    user, group = enterprise._account()
    assert situation["can_start"] is False
    assert situation["prepare_command"] == f"sudo install -d -o {user} -g {group} -m 0755 {machine.parent / 'missing'}"

    # Run by systemd (the offline package): root runs the installer.
    monkeypatch.setenv("INVOCATION_ID", "0123")
    situation = enterprise.situation()
    assert situation["systemd"] is True and situation["can_start"] is False and situation["prepare_command"] is None
    assert situation["manual_command"].startswith("sudo bash ")


@pytest.mark.skipif(os.geteuid() == 0, reason="as an account of its own")
@pytest.mark.parametrize(("says", "runs"), [(b"relocatable=1", True), (b"relocatable=0", False), (b"", False)])
async def test_eu2_an_installer_for_the_default_directory_only_asks_root_for_it(
    machine: Path, monkeypatch: pytest.MonkeyPatch, says: bytes, runs: bool
) -> None:
    home = machine.parent / "account-home"
    home.mkdir()
    closed = machine.parent / "closed"
    closed.mkdir()
    closed.chmod(0o500)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(enterprise, "INSTALL_ROOT", closed / "flockspro")
    installer = machine.parent / "x.run"
    installer.write_bytes(b"#!/bin/sh\n" + says + b"\n")
    offer = enterprise.InstallerOffer(url="https://console/x.run", sha256="a" * 64, release_id="r1", version="2026.11.5", size=1)

    async def fetch() -> enterprise.InstallerOffer:
        return offer

    async def download(_offer, _progress) -> Path:
        return installer

    started: list[Path] = []
    monkeypatch.setattr(enterprise, "fetch_offer", fetch)
    monkeypatch.setattr(enterprise, "download", download)
    monkeypatch.setattr(enterprise, "start_installer", lambda path, _offer: started.append(path) or {"phase": "installing"})

    async def progress(_event) -> None:
        return None

    try:
        state = await enterprise.upgrade(progress)
    finally:
        closed.chmod(0o700)

    if runs:
        assert started == [installer] and state["phase"] == "installing"
    else:
        assert started == [] and state["phase"] == "command"
        assert state["prepare_command"].startswith("sudo install -d") and str(closed / "flockspro") in state["prepare_command"]


async def test_eu3_console_offers_this_platforms_installer(machine: Path, monkeypatch: pytest.MonkeyPatch):
    write_licence(machine)
    seen = console(monkeypatch, lambda request: httpx.Response(200, json=manifest_with(b"installer")))
    monkeypatch.setattr(enterprise, "_console_session_token", _token("session-token"))

    offer = await enterprise.fetch_offer()

    assert offer.version == "2026.11.5" and offer.release_id == f"2026.11.5-{PLATFORM}"
    assert offer.filename == f"flockspro-2026.11.5-{PLATFORM}.run"
    request = seen[0]
    assert request.url.path == "/v1/manifest/latest"
    assert dict(request.url.params) == {"channel": "flockspro", "license_id": "lic-old-7"}
    assert request.headers["x-license-id"] == "lic-old-7"
    assert request.headers["authorization"] == "Bearer session-token"


def _token(value: str):
    async def token() -> str:
        return value

    return token


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (httpx.Response(401), "要先登录"),
        (httpx.Response(200, json={"release_bundles": []}), "还没有给授权 lic-old-7 的新企业版安装包"),
        (httpx.Response(200, json=manifest_with(b"x", sha256="not-a-digest")), "信息不完整"),
        (httpx.Response(200, json=manifest_with(b"x", url="http://mirror.example/flockspro.run")), "不是 https"),
        (httpx.Response(502), "HTTP 502"),
        (httpx.Response(200, text="<html>"), "不是 JSON"),
    ],
)
async def test_eu3_what_console_says_is_said_as_it_is(machine: Path, monkeypatch: pytest.MonkeyPatch, answer, says):
    write_licence(machine)
    console(monkeypatch, lambda request: answer)
    monkeypatch.setattr(enterprise, "_console_session_token", _token(""))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match=says):
        await enterprise.fetch_offer()


async def test_eu3_a_sign_in_console_refuses_is_forgotten(machine: Path, monkeypatch: pytest.MonkeyPatch):
    """Console revoked the sign-in (401: a sign-out in the enterprise edition, say): the page must offer a
    new one. A 403 refuses a sign-in Console takes (a licence of another account): that one stays."""
    write_licence(machine)
    forgotten: list[str] = []

    async def forget(token: str) -> None:
        forgotten.append(token)

    monkeypatch.setattr(enterprise, "_forget_console_session", forget)
    monkeypatch.setattr(enterprise, "_console_session_token", _token("revoked-token"))
    answer = [httpx.Response(401)]
    console(monkeypatch, lambda request: answer[0])
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="要先登录"):
        await enterprise.fetch_offer()
    assert forgotten == ["revoked-token"]
    answer[0] = httpx.Response(403, json={"detail": "license_id 不属于当前账号"})
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="不属于当前账号"):
        await enterprise.fetch_offer()
    assert forgotten == ["revoked-token"]
    # Asked with no sign-in at all: nothing to forget.
    monkeypatch.setattr(enterprise, "_console_session_token", _token(""))
    answer[0] = httpx.Response(401)
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="要先登录"):
        await enterprise.fetch_offer()
    assert forgotten == ["revoked-token"]


async def test_eu3_without_console_or_licence_there_is_nothing_to_ask(machine: Path, monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="没有配置 Console"):
        await enterprise.fetch_offer()
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example")
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="没有企业版授权"):
        await enterprise.fetch_offer()


async def test_eu4_the_download_is_checked_against_consoles_digest(machine: Path, monkeypatch: pytest.MonkeyPatch):
    body = b"#!/bin/sh\necho installer\n" * 1000
    console(monkeypatch, lambda request: httpx.Response(200, content=body))
    good = enterprise.InstallerOffer(url="https://console.example/r.run", sha256=hashlib.sha256(body).hexdigest(), release_id="r1", version="2026.11.5")
    heard: list[tuple[int, int | None]] = []

    async def progress(received: int, total: int | None) -> None:
        heard.append((received, total))

    path = await enterprise.download(good, progress)
    assert path.read_bytes() == body and (path.stat().st_mode & 0o777) == 0o700
    assert heard[-1][0] == len(body)
    assert (enterprise.work_dir().stat().st_mode & 0o777) == 0o700

    path.unlink()
    bad = enterprise.InstallerOffer(url=good.url, sha256="0" * 64, release_id="r1", version="2026.11.5")
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="校验不对"):
        await enterprise.download(bad)
    assert sorted(p.name for p in enterprise.work_dir().iterdir()) == []


def _notes_file(machine: Path) -> Path:
    return machine / "run" / "managed-release-notes.json"


async def test_eu3_the_offer_says_what_console_says_of_the_release(machine: Path, monkeypatch: pytest.MonkeyPatch):
    write_licence(machine)
    manifest = manifest_with(b"installer", release_notes="## 更新内容\n- 新功能")
    console(monkeypatch, lambda request: httpx.Response(200, json=manifest))
    assert (await enterprise.fetch_offer()).release_notes == "## 更新内容\n- 新功能"


@pytest.mark.parametrize("given", [{}, {"release_notes": 42}, {"release_notes": None}])
async def test_eu3_nothing_said_of_the_release_is_no_notes(machine: Path, monkeypatch: pytest.MonkeyPatch, given):
    write_licence(machine)
    manifest = manifest_with(b"installer", **given)
    console(monkeypatch, lambda request: httpx.Response(200, json=manifest))
    assert (await enterprise.fetch_offer()).release_notes is None


def _offer_of(body: bytes, **changes) -> enterprise.InstallerOffer:
    return enterprise.InstallerOffer(url="https://console.example/r.run", sha256=hashlib.sha256(body).hexdigest(),
                                     release_id="r1", version="2026.11.5", **changes)


async def test_eu4b_the_notes_are_kept_for_the_edition_it_installs(machine: Path, monkeypatch: pytest.MonkeyPatch):
    body = b"installer"
    console(monkeypatch, lambda request: httpx.Response(200, content=body))
    await enterprise.download(_offer_of(body, release_notes="这一版的说明"))
    kept = _notes_file(machine)
    # flockspro.updater.managed reads this: the same file and fields on both sides.
    assert json.loads(kept.read_text(encoding="utf-8")) == {"release_id": "r1", "version": "2026.11.5", "release_notes": "这一版的说明"}
    assert (kept.stat().st_mode & 0o777) == 0o644
    assert [path.name for path in kept.parent.iterdir() if path.name.endswith(".tmp")] == []


async def test_eu4b_a_download_that_does_not_check_out_keeps_nothing(machine: Path, monkeypatch: pytest.MonkeyPatch):
    console(monkeypatch, lambda request: httpx.Response(200, content=b"tampered"))
    with pytest.raises(enterprise.EnterpriseUpgradeError):
        await enterprise.download(_offer_of(b"installer", release_notes="说明"))
    assert not _notes_file(machine).exists()


async def test_eu4b_an_older_console_leaves_it_to_the_edition_to_ask(machine: Path, monkeypatch: pytest.MonkeyPatch):
    body = b"installer"
    console(monkeypatch, lambda request: httpx.Response(200, content=body))
    await enterprise.download(_offer_of(body))
    assert not _notes_file(machine).exists()


async def test_eu4b_a_release_published_without_notes_says_so(machine: Path, monkeypatch: pytest.MonkeyPatch):
    body = b"installer"
    console(monkeypatch, lambda request: httpx.Response(200, content=body))
    await enterprise.download(_offer_of(body, release_notes=""))
    assert json.loads(_notes_file(machine).read_text(encoding="utf-8"))["release_notes"] == ""


async def test_eu4b_notes_that_cannot_be_kept_do_not_stop_the_upgrade(machine: Path, monkeypatch: pytest.MonkeyPatch):
    body = b"installer"
    console(monkeypatch, lambda request: httpx.Response(200, content=body))
    (machine / "run").mkdir(exist_ok=True)
    _notes_file(machine).mkdir()  # os.replace onto it fails
    path = await enterprise.download(_offer_of(body, release_notes="说明"))
    assert path.read_bytes() == body
    assert not (machine / "run" / "managed-release-notes.json.tmp").exists()


def fake_installer(machine: Path, script: str) -> Path:
    path = machine / "fake.run"
    path.write_text("#!/bin/bash\n" + script)
    path.chmod(0o700)
    return path


def wait_for(predicate, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.mark.skipif(sys.platform == "win32", reason="the installer is a shell one")
def test_eu5_the_installer_runs_apart_and_says_how_it_ended(machine: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONPATH", "/somewhere/this/runtime")
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/venv")
    out = machine / "env-seen"
    installer = fake_installer(machine, f'echo "pythonpath=${{PYTHONPATH-unset}} venv=${{VIRTUAL_ENV-unset}} root=$FLOCKS_ROOT sid=$(ps -o sid= -p $$ 2>/dev/null)" > {out}\necho step one\nexit 3\n')
    offer = enterprise.InstallerOffer(url="https://x/r.run", sha256="0" * 64, release_id="r1", version="2026.11.5")

    state = enterprise.start_installer(installer, offer)

    assert state["phase"] == "installing" and state["pid"] > 0
    ended = wait_for(lambda: (s := enterprise.read_state()) and s["phase"] != "installing" and s)
    assert ended["phase"] == "failed" and ended["exit_code"] == 3
    assert "step one" in ended["log_tail"]
    seen = out.read_text()
    assert "pythonpath=unset venv=unset" in seen and f"root={machine}" in seen
    # Its own session: the open edition's supervisor stopping this server's group leaves it running.
    assert str(os.getsid(0)) not in seen.split("sid=")[1].split()

    installer = fake_installer(machine, "echo all done\nexit 0\n")
    enterprise.start_installer(installer, offer)
    ended = wait_for(lambda: (s := enterprise.read_state()) and s["phase"] != "installing" and s)
    assert ended["phase"] == "finished" and ended["exit_code"] == 0


@pytest.mark.skipif(sys.platform == "win32", reason="the installer is a shell one")
def test_eu5_the_installer_is_no_part_of_this_servers_process_tree(machine: Path):
    """install-managed stops the open edition, and `flocks stop` takes the server's whole process
    tree (by parent pid, sessions or not): the installer must not be in it, or it stops itself."""
    from flocks.cli.service_manager import collect_process_tree_pids

    installer = fake_installer(machine, "sleep 2\necho done\nexit 0\n")
    offer = enterprise.InstallerOffer(url="https://x/r.run", sha256="0" * 64, release_id="r1", version="2026.11.5")

    state = enterprise.start_installer(installer, offer)

    tree = collect_process_tree_pids(os.getpid())
    assert enterprise._pid_alive(state["pid"])
    assert state["pid"] not in tree
    ended = wait_for(lambda: (s := enterprise.read_state()) and s["phase"] != "installing" and s)
    assert ended["phase"] == "finished" and "done" in ended["log_tail"]


def test_eu5_a_run_whose_process_is_gone_without_a_word_failed(machine: Path):
    enterprise._write_state({"phase": "installing", "pid": 2**22 + 12345, "log": ""})
    assert enterprise.read_state()["phase"] == "failed"
    enterprise._write_state({"phase": "downloading", "received": 10})
    state = enterprise.read_state()
    assert state["phase"] == "failed" and "重新点升级" in state["message"]


def test_eu5b_a_finished_install_that_went_back_is_no_upgrade_any_more(machine: Path):
    enterprise._write_state({"phase": "installing", "pid": 2**22 + 12345, "version": "2026.11.29", "log": ""})
    exit_file = enterprise.work_dir() / "exit-status"
    exit_file.write_text("0\n", encoding="utf-8")
    finished = time.time() - 100
    os.utime(exit_file, (finished, finished))
    assert enterprise.read_state()["phase"] == "finished"
    # The enterprise edition's downgrade writes its record into the runtime root each time.
    record = machine / enterprise.DOWNGRADE_RECORD
    record.write_text('{"version": 1, "kept": []}', encoding="utf-8")
    os.utime(record, (finished - 50, finished - 50))  # a downgrade before this install
    assert enterprise.read_state()["phase"] == "finished"
    os.utime(record, (finished + 50, finished + 50))  # and one after it
    assert enterprise.read_state() is None
    assert enterprise.situation()["upgrade"] is None


async def test_eu6_a_systemd_machine_ends_with_roots_command(machine: Path, monkeypatch: pytest.MonkeyPatch):
    write_licence(machine)
    body = b"#!/bin/sh\nexit 0\n"
    console(monkeypatch, lambda request: httpx.Response(200, json=manifest_with(body)) if "manifest" in request.url.path else httpx.Response(200, content=body))
    monkeypatch.setattr(enterprise, "_console_session_token", _token(""))
    monkeypatch.setenv("INVOCATION_ID", "0123")
    events: list[dict] = []

    async def progress(event: dict) -> None:
        events.append(event)

    state = await enterprise.upgrade(progress)

    assert state["phase"] == "command"
    installer = Path(state["installer"])
    assert installer.read_bytes() == body
    expected = f"bash {installer}" if os.geteuid() == 0 else f"sudo bash {installer}"
    assert state["command"] == expected
    assert [e["stage"] for e in events][0] == "downloading" and events[-1]["stage"] == "downloaded"
    assert enterprise.read_state()["phase"] == "command"


@pytest.mark.skipif(sys.platform == "win32", reason="the installer is a shell one")
async def test_eu6_an_accounts_own_install_runs_the_installer_once(machine: Path, monkeypatch: pytest.MonkeyPatch):
    write_licence(machine)
    body = b"#!/bin/bash\nsleep 1\nexit 0\n"
    console(monkeypatch, lambda request: httpx.Response(200, json=manifest_with(body)) if "manifest" in request.url.path else httpx.Response(200, content=body))
    monkeypatch.setattr(enterprise, "_console_session_token", _token(""))

    async def progress(event: dict) -> None:
        pass

    state = await enterprise.upgrade(progress)
    assert state["phase"] == "installing"
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="已经在运行"):
        await enterprise.upgrade(progress)
    ended = wait_for(lambda: (s := enterprise.read_state()) and s["phase"] != "installing" and s)
    assert ended["phase"] == "finished"


def test_eu7_what_downgrades_kept_is_listed_and_left_alone(machine: Path, tmp_path: Path):
    database = tmp_path / "var" / "flockspro-pg.managed-20261030T133242Z-4d9e3c"
    (database / "base").mkdir(parents=True)
    (database / "base" / "1").write_bytes(b"x" * 3000)
    (database / "PG_VERSION").write_bytes(b"16\n")
    config = machine / "config" / "database.json.managed-20261030T145208Z-00607d"
    config.parent.mkdir(parents=True)
    config.write_text('{"backend": "postgres"}', encoding="utf-8")
    gone = machine / "data" / "storage-backend.json.managed-20261030T140501Z-9ebc72"
    (machine / enterprise.DOWNGRADE_RECORD).write_text(json.dumps(
        {"version": 1, "kept": [str(database), str(config), str(gone), ""], "updated_at": "2026-10-30T14:52:08Z"}))

    items = enterprise.kept_after_downgrades()
    assert [(i["path"], i["kept_at"], i["present"], i["size"]) for i in items] == [
        (str(database), "20261030T133242Z", True, 3003),
        (str(config), "20261030T145208Z", True, len('{"backend": "postgres"}')),
        (str(gone), "20261030T140501Z", False, None),
    ]
    assert database.is_dir() and config.is_file()  # listed, not touched
    (machine / enterprise.DOWNGRADE_RECORD).unlink()
    assert enterprise.kept_after_downgrades() == []


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a directory this account may not enter")
def test_eu7_a_path_this_account_cannot_look_at_does_not_stop_the_list(machine: Path, tmp_path: Path):
    locked = tmp_path / "locked"
    locked.mkdir()
    hidden = locked / "flockspro-pg.managed-20261030T133242Z-4d9e3c"
    hidden.mkdir()
    visible = machine / "config" / "deployment.json.managed-20261030T133242Z-d6588e"
    visible.parent.mkdir(parents=True)
    visible.write_text("{}", encoding="utf-8")
    (machine / enterprise.DOWNGRADE_RECORD).write_text(json.dumps({"kept": [str(hidden), str(visible)]}))
    locked.chmod(0)
    try:
        items = enterprise.kept_after_downgrades()
    finally:
        locked.chmod(0o755)
    assert [(i["path"], i["present"], i["size"]) for i in items] == [(str(hidden), None, None), (str(visible), True, 2)]


@pytest.fixture
def windows(machine: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This machine taken for a Windows one, as far as the module asks."""
    monkeypatch.setattr(enterprise, "_WINDOWS", True)
    monkeypatch.setattr(enterprise, "INSTALLER_SUFFIX", ".exe")
    monkeypatch.setattr(enterprise, "INSTALL_ROOT", Path("C:/Program Files/FlocksPro"))
    monkeypatch.setattr(enterprise.sys, "platform", "win32")
    monkeypatch.setattr(enterprise.platform, "machine", lambda: "AMD64")
    return machine


def test_eu8_windows_names_its_platform_and_installer(windows: Path):
    assert enterprise.platform_id() == "windows-amd64"
    offer = enterprise.InstallerOffer(url="https://x/i", sha256="0" * 64, release_id="2026.12.30-windows-amd64", version="2026.12.30")
    assert offer.filename == "flockspro-2026.12.30-windows-amd64.exe"
    situation = enterprise.situation()
    assert situation["available"] and situation["can_start"] and situation["prepare_command"] is None
    # By hand too, the installer is told this install's data directory: the administrator who
    # confirms may have another profile, FLOCKS_ROOT another directory.
    # (A temporary directory under a profile with a space in it is quoted for the program.)
    arguments = enterprise.subprocess.list2cmdline(["--open-data", str(enterprise.runtime_root())])
    assert situation["manual_command"] == (
        f"Start-Process -Verb RunAs -FilePath 'flockspro-<版本>-windows-amd64.exe' -ArgumentList '{arguments}'")


def test_eu8_the_manual_command_quotes_as_powershell_and_windows_read_it(windows: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOCKS_ROOT", "C:\\Users\\Li Lei\\.flocks")
    command = enterprise.installer_command("C:/Users/O\u2019Neil's/x.exe")
    # Every single quote PowerShell knows is doubled; the argument with a space is quoted for the program.
    assert command == ("Start-Process -Verb RunAs -FilePath 'C:/Users/O\u2019\u2019Neil''s/x.exe' "
                       "-ArgumentList '--open-data \"C:\\Users\\Li Lei\\.flocks\"'")
    assert enterprise._powershell_quote("a\u2018b\u201ac\u201bd") == "'a\u2018\u2018b\u201a\u201ac\u201b\u201bd'"


def test_eu8_the_page_starts_the_windows_installer_from_a_process_gone_at_once(windows: Path, monkeypatch: pytest.MonkeyPatch):
    started: list[dict] = []

    class Launcher:
        def __init__(self, argv, **kwargs):
            started.append({"argv": argv, **kwargs})

        def communicate(self, timeout=None):
            return b"4242\n", None

    monkeypatch.setattr(enterprise.subprocess, "Popen", Launcher)
    (Path(os.environ["HOME"]) / ".local" / "bin").mkdir(parents=True)
    (Path(os.environ["HOME"]) / ".local" / "bin" / "flocks.cmd").write_text("@echo off\n")
    installer = enterprise.work_dir() / "flockspro-2026.12.30-windows-amd64.exe"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_bytes(b"MZ")
    offer = enterprise.InstallerOffer(url="https://x/i", sha256="0" * 64, release_id="2026.12.30-windows-amd64", version="2026.12.30")

    state = enterprise.start_installer(installer, offer)

    assert state["phase"] == "installing" and state["pid"] == 4242
    [launch] = started
    argv = launch["argv"]
    # This interpreter starts the installer and exits: the installer is nobody's child here.
    assert argv[:4] == [sys.executable, "-I", "-c", enterprise._WINDOWS_DETACH]
    log_file, exit_file = str(enterprise.work_dir() / "install.log"), str(enterprise.work_dir() / "exit-status")
    assert argv[4:] == [log_file, str(installer), "--no-pause", "--log", log_file, "--exit-status", exit_file,
                        "--open-data", str(enterprise.runtime_root()),
                        "--open-cli", str(Path(os.environ["HOME"]) / ".local" / "bin" / "flocks.cmd")]
    assert launch["stdout"] is enterprise.subprocess.PIPE and launch["stdin"] is enterprise.subprocess.DEVNULL
    # The go-between gives the installer the log for its output, nothing of its own pipe, and a
    # group and console apart, and says the pid.
    seen: dict = {}

    class Child:
        pid = 777

    fake = type(sys)("subprocess")
    fake.DEVNULL = -3
    fake.Popen = lambda argv, **kwargs: seen.update(argv=argv, **kwargs) or Child()
    printed: list[str] = []
    monkeypatch.setitem(sys.modules, "subprocess", fake)
    # As `python -I -c <it> <log> <installer> ...` sees its arguments.
    monkeypatch.setattr(sys, "argv", ["-c", *argv[4:]])
    exec(compile(enterprise._WINDOWS_DETACH, "<detach>", "exec"),
         {"__name__": "__main__", "print": lambda *a: printed.append(" ".join(map(str, a)))})
    monkeypatch.setitem(sys.modules, "subprocess", enterprise.subprocess)
    assert seen["argv"] == argv[5:] and seen["close_fds"] is True and seen["creationflags"] == 0x208
    assert seen["stdout"] is seen["stderr"] and seen["stdout"].name == log_file and seen["stdin"] == -3
    seen["stdout"].close()
    assert printed == ["777"]
    assert "PYTHONPATH" not in launch["env"]
    # How it ended is the installer's word: its exit status, where the page reads it.
    Path(exit_file).write_text("0\n")
    assert enterprise.read_state()["phase"] == "finished"


def test_eu8_a_run_is_looked_at_without_os_kill_on_windows(windows: Path, monkeypatch: pytest.MonkeyPatch):
    def kill(*_args):
        raise AssertionError("os.kill ends the process it names on Windows")

    monkeypatch.setattr(enterprise.os, "kill", kill)
    asked: list[int] = []
    monkeypatch.setattr(enterprise, "_windows_pid_alive", lambda pid: asked.append(pid) or False)
    enterprise._write_state({"phase": "installing", "pid": 31337, "log": ""})
    assert enterprise.read_state()["phase"] == "failed"
    assert asked == [31337]


def test_eu8_one_upgrade_at_a_time_on_windows_too(windows: Path, monkeypatch: pytest.MonkeyPatch):
    import types

    held: set[str] = set()

    def locking(fd, mode, size):
        # Byte 0 of upgrade.lock, held until the process lets go (this one never does here).
        if mode == fake.LK_NBLCK and "upgrade.lock" in held:
            raise OSError(13, "Permission denied")
        held.add("upgrade.lock")

    fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    with enterprise.upgrade_lock():
        with pytest.raises(enterprise.EnterpriseUpgradeError, match="已经在升级了"):
            with enterprise.upgrade_lock():
                pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows processes and msvcrt locks")
def test_eu8_on_windows_a_run_is_looked_at_and_the_lock_held_across_processes(machine: Path):
    import subprocess

    assert enterprise._windows_pid_alive(os.getpid())
    done = subprocess.run([sys.executable, "-c", "pass"])
    assert done.returncode == 0
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert enterprise._pid_alive(child.pid)
    finally:
        child.kill()
        child.wait()
    assert not enterprise._pid_alive(child.pid)
    with enterprise.upgrade_lock():
        other = subprocess.run([sys.executable, "-c",
                                "from flocks.updater import enterprise\n"
                                "try:\n    enterprise.upgrade_lock().__enter__()\nexcept enterprise.EnterpriseUpgradeError:\n    raise SystemExit(3)\n"])
        assert other.returncode == 3
    with enterprise.upgrade_lock():
        pass
