"""flocks.updater.enterprise_application: licensing offline from the Flocks Pro page.

Case list (编号 | 场景):
  LF1 a licence file in the shape Console's offline issue downloads is written where taking an approved
      application writes it, readable by this account only, its patches with it; the machine then has it
  LF2 the bare token, as an operator may copy it, is taken as the file's key
  LF3 what is no licence file is refused, saying why: empty, not JSON nor a token, no token, a token
      not Console's (not ES256), an id that is not the token's, a payload that is not the token's
  LF4 a licence no longer valid is refused: revoked by its status, past its date, revoked by the
      patch in force
  LF5 a licence bound to another machine is refused; bound to this one (by either fingerprint) it is
      taken; a host whose fingerprints cannot be read now takes it (the core judges the binding)
  LF6 another licence still good is never replaced; one no longer valid is, and a copy of it kept
  LF7 the same token again: the file stands, takes the patches it lacked, and no copy is made
  LF8 an install the enterprise core runs imports nothing here
  LF9 the same token again is judged with the patches the machine holds: a renewal it holds keeps an
      expired token good (and the answer says so), a revocation it holds keeps it revoked
  LF10 an import and a heartbeat at once: neither writes back a copy read before the other's write, so
      no patch is lost
  LF11 the same token again, bound to another machine (a data directory moved here): refused before
      anything is written, the patches it brings too
  OA1 the offline application carries the form and the machine a licence is to be bound to; nothing
      goes to Console and nothing is kept
  OA2 no offline application while the machine has a licence, while the host's identifier cannot be
      read, or on an install the enterprise core runs
"""

from __future__ import annotations

import asyncio
import base64
import json
import stat
import threading
import time
from pathlib import Path

import pytest

from flocks.storage.storage import Storage
from flocks.updater import enterprise, enterprise_application as application
from flocks.updater.machine_fingerprint import MachineIdUnreadable

pytestmark = pytest.mark.asyncio

FORM = {
    "license_type": "poc",
    "company": "Acme",
    "applicant_name": "Li Lei",
    "applicant_email": "li@acme.example",
    "applicant_phone": "+86 138 0000 0000",
    "sales_rep_name": None,
    "notes": "trial for SOC",
}
OURS = "0123456789abcdef0123456789abcdef"
LEGACY = "fedcba9876543210fedcba9876543210"


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".flocks"
    root.mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("FLOCKS_DEPLOY_MODE", "FLOCKS_OFFLINE_INSTALL", "INVOCATION_ID", "FLOCKSPRO_LICENSE_ID",
                "FLOCKS_UPDATE_CHANNEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example")
    enterprise.detect_deploy_mode.cache_clear()
    monkeypatch.setattr(application, "_lock", asyncio.Lock())
    monkeypatch.setattr(enterprise, "PREVIOUS_DISTRIBUTION", "flockspro-not-installed-here")
    monkeypatch.setattr(enterprise, "_previous_status", lambda: {})
    # This host goes by two fingerprints: its device id and the digest of its identifier.
    monkeypatch.setattr(application, "machine_fingerprints", lambda: {OURS, LEGACY})
    monkeypatch.setattr(application, "machine_identity", lambda: (OURS, "devid"))
    yield root
    enterprise.detect_deploy_mode.cache_clear()


def part(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def token(claims: dict, alg: str = "ES256") -> str:
    return f"{part({'alg': alg, 'kid': 'flocks-license-prod-2026', 'typ': 'JWT'})}.{part(claims)}.c2ln"


def claims(**over) -> dict:
    now = int(time.time())
    return {"license_id": "lic_off_1", "status": "poc", "expires_at": now + 90 * 86400, "issued_at": now,
            "max_admins": 3, "max_members": 20, **over}


def licence_file(**over) -> str:
    """The license.json Console's offline issue downloads."""
    body = {"license_id": "lic_off_1", "key": token(claims()), "payload": {}, "patches": []}
    body.update(over)
    return json.dumps(body)


def kept(root: Path) -> Path:
    return root / "flockspro" / "license.json"


async def test_lf1_a_licence_file_is_written_where_taking_one_writes_it(machine: Path):
    patch = token({"license_id": "lic_off_1", "serial": 1, "issued_at": int(time.time()), "dynamic_status": "poc",
                   "dynamic_expires_at": int(time.time()) + 200 * 86400})
    sent = licence_file(patches=[patch], fingerprint="fp-9", install_id="inst-9")
    answer = await application.import_licence(sent)
    assert answer["license_id"] == "lic_off_1" and answer["license_status"] == "poc"
    written = json.loads(kept(machine).read_text())
    # The Python package's shape, as go/internal/license/legacy.go and the installer read it.
    assert written["license_id"] == "lic_off_1" and written["key"] == json.loads(sent)["key"]
    assert written["payload"] == {} and written["patches"] == [patch]
    assert written["fingerprint"] == "fp-9" and written["install_id"] == "inst-9" and written["imported_at"]
    assert stat.S_IMODE(kept(machine).stat().st_mode) == 0o600
    assert stat.S_IMODE(kept(machine).parent.stat().st_mode) == 0o700
    # The page now moves this machine as one with a licence, with the terms of the patch in force.
    previous = enterprise.situation()["previous"]
    assert previous["license_id"] == "lic_off_1" and not previous["lapsed"]
    assert previous["max_admins"] == 3


async def test_lf2_the_bare_token_is_taken_as_the_files_key(machine: Path):
    bare = token(claims(license_id="lic_tok_2"))
    await application.import_licence(f"  {bare}\n")
    written = json.loads(kept(machine).read_text())
    assert written["license_id"] == "lic_tok_2" and written["key"] == bare and written["patches"] == []


@pytest.mark.parametrize("content, said", [
    ("   ", "空的"),
    ("hello, this is no licence", "既不是 JSON"),
    ("[1, 2]", "不是 JSON 对象"),
    ('{"license_id": "lic_x"}', "没有授权令牌"),
    (json.dumps({"key": token(claims(), alg="HS256")}), "不是 Console 签发的格式"),
    (json.dumps({"key": token(claims(license_id=""))}), "没有授权编号"),
    (json.dumps({"license_id": "lic_other", "key": token(claims())}), "对不上"),
    (json.dumps({"key": token(claims()), "payload": {"license_id": "lic_off_1", "status": "commercial"}}), "payload"),
])
async def test_lf3_what_is_no_licence_file_is_refused(machine: Path, content: str, said: str):
    with pytest.raises(enterprise.EnterpriseUpgradeError, match=said):
        await application.import_licence(content)
    assert not kept(machine).exists()


async def test_lf4_a_licence_no_longer_valid_is_refused(machine: Path):
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="已吊销"):
        await application.import_licence(licence_file(key=token(claims(status="revoked"))))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="到期"):
        await application.import_licence(licence_file(key=token(claims(expires_at=int(time.time()) - 86400))))
    # Revoked by Console after it was issued: the patch in force says so.
    revoked = token({"license_id": "lic_off_1", "serial": 2, "issued_at": int(time.time()), "dynamic_status": "revoked",
                     "dynamic_expires_at": int(time.time()) + 86400})
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="已吊销"):
        await application.import_licence(licence_file(patches=[revoked]))
    assert not kept(machine).exists()


async def test_lf5_a_licence_bound_to_another_machine_is_refused(machine: Path, monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="另一台机器"):
        await application.import_licence(licence_file(key=token(claims(machine="aaaabbbbccccddddeeeeffff00001111"))))
    assert not kept(machine).exists()
    # Bound to this host by the digest of its identifier (a licence issued before the device id).
    await application.import_licence(licence_file(key=token(claims(machine=LEGACY.upper()))))
    assert json.loads(kept(machine).read_text())["license_id"] == "lic_off_1"
    kept(machine).unlink()
    # Nothing readable to hold it to: the core judges the binding when the machine moves.
    monkeypatch.setattr(application, "machine_fingerprints", lambda: set())
    await application.import_licence(licence_file(key=token(claims(machine="aaaabbbbccccddddeeeeffff00001111"))))
    assert kept(machine).exists()


async def test_lf6_a_good_licence_is_never_replaced_one_no_longer_valid_is(machine: Path):
    kept(machine).parent.mkdir(parents=True)
    kept(machine).write_text(json.dumps({"license_id": "lic-old-7", "key": token(claims(license_id="lic-old-7"))}))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="lic-old-7"):
        await application.import_licence(licence_file())
    assert json.loads(kept(machine).read_text())["license_id"] == "lic-old-7"

    lapsed = claims(license_id="lic-old-7", expires_at=int(time.time()) - 86400)
    kept(machine).write_text(json.dumps({"license_id": "lic-old-7", "key": token(lapsed), "payload": lapsed}))
    await application.import_licence(licence_file())
    assert json.loads(kept(machine).read_text())["license_id"] == "lic_off_1"
    copies = list(kept(machine).parent.glob("license.replaced-*.json"))
    assert len(copies) == 1 and json.loads(copies[0].read_text())["license_id"] == "lic-old-7"


async def test_lf7_the_same_token_again_keeps_the_file_and_takes_the_patches_it_lacked(machine: Path):
    first = token({"license_id": "lic_off_1", "serial": 1, "issued_at": int(time.time()), "dynamic_status": "poc",
                   "dynamic_expires_at": int(time.time()) + 100 * 86400})
    second = token({"license_id": "lic_off_1", "serial": 2, "issued_at": int(time.time()), "dynamic_status": "poc",
                    "dynamic_expires_at": int(time.time()) + 300 * 86400})
    await application.import_licence(licence_file(patches=[first]))
    before = json.loads(kept(machine).read_text())
    await application.import_licence(licence_file(patches=[first, second]))
    after = json.loads(kept(machine).read_text())
    assert after["patches"] == [first, second] and after["imported_at"] == before["imported_at"]
    assert list(kept(machine).parent.glob("license.replaced-*.json")) == []
    # Nothing new: nothing written.
    stamp = kept(machine).stat().st_mtime_ns
    await application.import_licence(licence_file(patches=[second]))
    assert kept(machine).stat().st_mtime_ns == stamp


async def test_lf8_an_enterprise_install_imports_nothing_here(machine: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(enterprise, "available", lambda: False)
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="已经是企业版"):
        await application.import_licence(licence_file())
    assert not kept(machine).exists()


async def test_oa1_the_offline_application_carries_the_form_and_this_machine(machine: Path):
    before = await Storage.list_keys(prefix="console:upgrade_request")
    document = await application.offline_application(dict(FORM), "admin")
    assert document["format"] == application.OFFLINE_APPLICATION_FORMAT and document["product"] == "Flocks Pro"
    assert {key: document[key] for key in FORM} == FORM
    # Console's offline issue binds the licence to this fingerprint.
    assert (document["machine"], document["machine_kind"]) == (OURS, "devid")
    assert document["install_id"] and document["platform"] == enterprise.platform_id() and document["flocks_version"]
    assert document["applied_by"] == "admin" and document["replaces_license_id"] is None
    # Nothing kept: there is nothing at Console to follow.
    assert await Storage.list_keys(prefix="console:upgrade_request") == before


async def test_oa2_no_offline_application_where_there_should_be_none(machine: Path, monkeypatch: pytest.MonkeyPatch):
    await application.import_licence(licence_file())
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="已经有企业版授权"):
        await application.offline_application(dict(FORM), "admin")
    kept(machine).unlink()

    def unreadable() -> tuple[str, str]:
        raise MachineIdUnreadable("ioreg 没有给出 IOPlatformUUID（退出码 1）")

    monkeypatch.setattr(application, "machine_identity", unreadable)
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="读不到这台机器的标识"):
        await application.offline_application(dict(FORM), "admin")

    monkeypatch.setattr(enterprise, "available", lambda: False)
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="不能在这里申请"):
        await application.offline_application(dict(FORM), "admin")


def patch_token(serial: int, status: str = "poc", days: int = 200) -> str:
    now = int(time.time())
    return token({"license_id": "lic_off_1", "serial": serial, "issued_at": now, "dynamic_status": status,
                  "dynamic_expires_at": now + days * 86400})


async def test_lf9_the_same_token_again_is_judged_with_what_the_machine_holds(machine: Path):
    expired = token(claims(expires_at=int(time.time()) - 86400))
    renewal = patch_token(1, days=300)
    kept(machine).parent.mkdir(parents=True)
    kept(machine).write_text(json.dumps({"license_id": "lic_off_1", "key": expired, "payload": {}, "patches": [renewal]}))
    # The token by itself is past its date; the machine holds its renewal.
    answer = await application.import_licence(licence_file(key=expired))
    assert answer["license_status"] == "poc" and answer["expires_at"] > time.time() + 200 * 86400
    assert json.loads(kept(machine).read_text())["patches"] == [renewal]

    # A revocation the machine holds outranks an older good patch the file brings.
    revoked = patch_token(5, status="revoked")
    older = patch_token(2)
    kept(machine).write_text(json.dumps({"license_id": "lic_off_1", "key": expired, "payload": {}, "patches": [renewal, revoked]}))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="已吊销"):
        await application.import_licence(licence_file(key=expired, patches=[older]))
    held = json.loads(kept(machine).read_text())
    assert held["patches"] == [renewal, revoked, older] and held["key"] == expired


async def test_lf10_an_import_and_a_heartbeat_at_once_lose_no_patch(machine: Path, monkeypatch: pytest.MonkeyPatch):
    from flocks.console.login import keep_previous_licence_current

    first, second, revoke = patch_token(1), patch_token(2), patch_token(3, status="revoked")
    await application.import_licence(licence_file(patches=[first]))
    entered, release = threading.Event(), threading.Event()
    real_write = application._write_licence

    def slow_write(path: Path, content: dict) -> None:
        entered.set()
        release.wait(5)
        real_write(path, content)

    monkeypatch.setattr(application, "_write_licence", slow_write)
    failures: list[BaseException] = []

    def run_import() -> None:
        try:
            asyncio.run(application._import_licence(licence_file(patches=[first, second])))
        except BaseException as exc:  # reported below
            failures.append(exc)

    importer = threading.Thread(target=run_import)
    importer.start()
    assert entered.wait(5)
    # The heartbeat arrives while the import is between its read and its write.
    heartbeat = threading.Thread(target=keep_previous_licence_current, args=({"license_patch": revoke},))
    heartbeat.start()
    heartbeat.join(0.3)
    assert heartbeat.is_alive(), "the heartbeat must wait for the import's write"
    release.set()
    importer.join(5)
    heartbeat.join(5)
    assert not failures
    patches = json.loads(kept(machine).read_text())["patches"]
    assert set(patches) == {first, second, revoke}


async def test_lf11_the_same_token_bound_elsewhere_is_refused_before_anything_is_written(machine: Path):
    elsewhere = token(claims(machine="b" * 32, expires_at=int(time.time()) - 86400))
    kept(machine).parent.mkdir(parents=True)
    held = {"license_id": "lic_off_1", "key": elsewhere, "payload": {}, "patches": []}
    kept(machine).write_text(json.dumps(held))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="另一台机器"):
        await application.import_licence(licence_file(key=elsewhere, patches=[patch_token(1, days=300)]))
    assert json.loads(kept(machine).read_text()) == held
