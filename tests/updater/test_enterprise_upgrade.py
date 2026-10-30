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
  EU5 the installer runs apart, without this runtime's Python settings, and the page reads how it
      ended (exit status, log), also after this server is gone
  EU6 the whole of it: a systemd machine ends with root's command for the downloaded file, an
      account's own install with the installer running; one at a time
"""

from __future__ import annotations

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


def test_eu5_a_run_whose_process_is_gone_without_a_word_failed(machine: Path):
    enterprise._write_state({"phase": "installing", "pid": 2**22 + 12345, "log": ""})
    assert enterprise.read_state()["phase"] == "failed"
    enterprise._write_state({"phase": "downloading", "received": 10})
    state = enterprise.read_state()
    assert state["phase"] == "failed" and "重新点升级" in state["message"]


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
