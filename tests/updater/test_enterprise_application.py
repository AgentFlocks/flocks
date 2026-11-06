"""flocks.updater.enterprise_application: applying for a licence from the Flocks Pro page.

Case list (编号 | 场景):
  EA1 an application goes to Console with the signed-in account's identity and this machine's,
      the host's fingerprint as the enterprise core computes it among them, and its id is kept;
      none while the machine has a licence or another one is still open, none without Console's
      address or a sign-in, none while the host's identifier cannot be read
  EA2 the listing: newest first, the open ones as Console has them now, the closed ones as kept;
      the licence token never leaves the server; ids kept by the page of 2026.10.19 come back;
      Console out of reach leaves the kept ones and says why
  EA3 a withdrawal goes to Console for an application this machine sent, and only for one
  EA4 taking an approved licence writes it where the Python enterprise package kept its own, in
      that file's shape, readable by this account only, and tells Console; the machine then counts
      as one with a licence; another licence already there is never overwritten; nothing to take
      before approval; Console not hearing of it leaves the licence in place
  EA5 a submission whose outcome is unknown (no answer, a 5xx from a proxy, a body that does not
      read, an application that could not be kept here): the listing says so, and the next one goes
      with the same idempotency key, so Console hands back the application it made; one kept here,
      or refused with a 4xx, leaves no key behind
  EA6 two administrators sending at once: one application goes to Console, the other is told one
      is open; a record whose id the index lost is listed and the index mended
"""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from flocks.storage.storage import Storage
from flocks.updater import enterprise, enterprise_application as application

_REMEMBER = application._remember

pytestmark = pytest.mark.asyncio

SESSION = {
    "console_session_token": "tok-1",
    "console_login_id": "cl-1",
    "fingerprint": "fp-1",
    "install_id": "inst-1",
    "passport_uid": "pp-1",
    "user_display": "ops@example.com",
}
FORM = {
    "license_type": "poc",
    "company": "Acme",
    "applicant_name": "Li Lei",
    "applicant_email": "li@acme.example",
    "applicant_phone": "+86 138 0000 0000",
    "sales_rep_name": None,
    "notes": "trial for SOC",
}


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
    # A lock bound to no other test's event loop.
    monkeypatch.setattr(application, "_lock", asyncio.Lock())
    monkeypatch.setattr(enterprise, "PREVIOUS_DISTRIBUTION", "flockspro-not-installed-here")
    monkeypatch.setattr(enterprise, "_previous_status", lambda: {})

    from flocks.console.login import ConsoleLoginService

    async def signed_in(cls=None):
        return dict(SESSION)

    monkeypatch.setattr(ConsoleLoginService, "require_console_session", classmethod(lambda cls: signed_in()))
    yield root
    enterprise.detect_deploy_mode.cache_clear()


# Taken before any test replaces it: a test may change Console's answers more than once.
_REAL_CLIENT = httpx.AsyncClient


@pytest_asyncio.fixture(autouse=True)
async def _nothing_kept(machine: Path):
    """No application kept by another test (the listing reads every kept record, indexed or not)."""
    for key in await Storage.list_keys(prefix="console:upgrade_request"):
        await Storage.delete(key)
    yield


def console(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """A Console answering through handler (replacing any earlier one); the requests it got."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    class Client(_REAL_CLIENT):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(record)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(application.httpx, "AsyncClient", Client)
    return seen


def approved(request_id: str = "req_1", license_id: str = "lic_1") -> dict:
    return {"request_id": request_id, "status": "approved", "activate_key": "h.p.s", "license_id": license_id,
            "license_status": "poc", "expires_at": 1798761600, "reason": "ok"}


async def test_ea1_an_application_goes_to_console_and_its_id_is_kept(machine: Path, monkeypatch: pytest.MonkeyPatch):
    seen = console(monkeypatch, lambda r: httpx.Response(200, json={"request_id": "req_1", "status": "pending"}))
    monkeypatch.setattr(application, "machine_fingerprint", lambda: "0123456789abcdef0123456789abcdef")
    sent = await application.apply(dict(FORM), "admin")
    assert sent["request_id"] == "req_1" and sent["status"] == "pending" and sent["company"] == "Acme"
    body = json.loads(seen[0].content)
    assert seen[0].url == "https://console.example/v1/upgrade-requests"
    assert seen[0].headers["Authorization"] == "Bearer tok-1"
    # The machine is the node: Console holds one open application per node.
    assert (body["node_id"], body["fingerprint"], body["install_id"], body["passport_uid"]) == ("inst-1", "fp-1", "inst-1", "pp-1")
    assert body["form_data"]["applied_by"] == "admin" and body["idempotency_key"] == body["form_data"]["idempotency_key"]
    # Console binds the licence it issues to the host the application came from.
    assert body["form_data"]["machine"] == "0123456789abcdef0123456789abcdef"
    assert await Storage.get("console:upgrade_request_pending") is None
    assert body["company_name"] == "Acme" and body["contact_email"] == "li@acme.example"
    assert body["form_data"]["license_type"] == "poc" and body["form_data"]["applicant_phone"] == "+86 138 0000 0000"
    assert await Storage.get("console:upgrade_request_ids") == ["req_1"]

    # Still open: no second one.
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="已经有一份申请"):
        await application.apply(dict(FORM), "admin")
    assert len(seen) == 1

    # A machine with a licence asks for none.
    await Storage.set("console:upgrade_request_ids", [], "json")
    await Storage.delete("console:upgrade_request:req_1")
    licence = machine / "flockspro" / "license.json"
    licence.parent.mkdir(parents=True)
    licence.write_text(json.dumps({"license_id": "lic-old-7"}))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="已经有企业版授权"):
        await application.apply(dict(FORM), "admin")
    licence.unlink()

    # No Console address, or no sign-in.
    monkeypatch.delenv("FLOCKS_CONSOLE_BASE_URL")
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="FLOCKS_CONSOLE_BASE_URL"):
        await application.apply(dict(FORM), "admin")
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example")
    from flocks.console.login import ConsoleLoginService

    async def not_signed_in():
        raise ValueError("云账号未登录")

    monkeypatch.setattr(ConsoleLoginService, "require_console_session", classmethod(lambda cls: not_signed_in()))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="云账号登录"):
        await application.apply(dict(FORM), "admin")
    assert len(seen) == 1


async def test_ea1b_none_while_the_hosts_identifier_cannot_be_read(machine: Path, monkeypatch: pytest.MonkeyPatch):
    seen = console(monkeypatch, lambda r: httpx.Response(200, json={"request_id": "req_1", "status": "pending"}))
    from flocks.updater.machine_fingerprint import MachineIdUnreadable

    def unreadable() -> str:
        raise MachineIdUnreadable("ioreg 没有给出 IOPlatformUUID（退出码 1）")

    monkeypatch.setattr(application, "machine_fingerprint", unreadable)
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="读不到这台机器的标识"):
        await application.apply(dict(FORM), "admin")
    assert seen == [] and await Storage.get("console:upgrade_request_pending") is None

    # A host that keeps none (a container without /etc/machine-id) applies, and Console shows it.
    monkeypatch.setattr(application, "machine_fingerprint", lambda: "")
    await application.apply(dict(FORM), "admin")
    assert json.loads(seen[0].content)["form_data"]["machine"] is None


async def test_ea2_the_listing(machine: Path, monkeypatch: pytest.MonkeyPatch):
    # Kept by the page of 2026.10.19 under the same keys: one closed, one waiting.
    await Storage.set("console:upgrade_request:old-1", {"request_id": "old-1", "status": "rejected", "reason": "no",
                                                        "details": {"company": "Acme", "license_type": "commercial"}}, "json")
    await Storage.set("console:upgrade_request:req_2", {"request_id": "req_2", "status": "pending",
                                                        "details": {"company": "Acme", "license_type": "poc"}}, "json")
    await Storage.set("console:upgrade_request_ids", ["old-1", "req_2"], "json")
    seen = console(monkeypatch, lambda r: httpx.Response(200, json=approved("req_2", "lic_2")))
    listing = await application.applications()
    assert [item["request_id"] for item in listing["items"]] == ["req_2", "old-1"]
    assert listing["note"] is None
    # Only the open one asked for again.
    assert [str(r.url) for r in seen] == ["https://console.example/v1/upgrade-requests/req_2"]
    first = listing["items"][0]
    assert first["status"] == "approved" and first["license_id"] == "lic_2" and first["can_take"] and first["can_withdraw"]
    assert "activate_key" not in json.dumps(listing) and "h.p.s" not in json.dumps(listing)
    assert listing["items"][1]["status"] == "rejected" and not listing["items"][1]["can_take"]

    # Console out of reach: the kept ones, and why.
    console(monkeypatch, lambda r: httpx.Response(503, json={"detail": "maintenance"}))
    listing = await application.applications()
    assert [item["status"] for item in listing["items"]] == ["approved", "rejected"]
    assert "503" in listing["note"]

    # Too slow in all: given up on, the kept ones shown.
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=approved("req_2", "lic_2"))

    console(monkeypatch, slow)
    monkeypatch.setattr(application, "_REFRESH_SECONDS", 0.1)
    listing = await application.applications()
    assert [item["status"] for item in listing["items"]] == ["approved", "rejected"] and "太慢" in listing["note"]


async def test_ea3_withdrawal(machine: Path, monkeypatch: pytest.MonkeyPatch):
    await Storage.set("console:upgrade_request:req_3", {"request_id": "req_3", "status": "pending", "details": {}}, "json")
    await Storage.set("console:upgrade_request_ids", ["req_3"], "json")
    seen = console(monkeypatch, lambda r: httpx.Response(200, json={"request_id": "req_3", "status": "cancelled"}))
    withdrawn = await application.withdraw("req_3")
    assert withdrawn["status"] == "cancelled" and not withdrawn["can_withdraw"]
    assert str(seen[0].url) == "https://console.example/v1/upgrade-requests/req_3/withdraw"
    # Not one this machine sent.
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="没有发过"):
        await application.withdraw("req_other")
    assert len(seen) == 1


async def test_ea4_taking_the_licence(machine: Path, monkeypatch: pytest.MonkeyPatch):
    await Storage.set("console:upgrade_request:req_4", {"request_id": "req_4", "status": "pending", "details": {}}, "json")
    await Storage.set("console:upgrade_request_ids", ["req_4"], "json")

    # Not approved yet: nothing to take.
    console(monkeypatch, lambda r: httpx.Response(200, json={"request_id": "req_4", "status": "reviewing"}))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="还没有审核通过"):
        await application.take("req_4")
    assert not (machine / "flockspro" / "license.json").exists()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/activate"):
            return httpx.Response(200, json={"request_id": "req_4", "status": "activated"})
        return httpx.Response(200, json=approved("req_4", "lic_4"))

    seen = console(monkeypatch, answer)
    taken = await application.take("req_4")
    assert taken["status"] == "activated" and not taken["can_take"] and not taken["can_withdraw"]
    assert [r.url.path for r in seen] == ["/v1/upgrade-requests/req_4", "/v1/upgrade-requests/req_4/activate"]
    licence = machine / "flockspro" / "license.json"
    written = json.loads(licence.read_text())
    # The Python package's shape: what go/internal/license/legacy.go and the installer read.
    assert written["license_id"] == "lic_4" and written["key"] == "h.p.s" and written["payload"] == {}
    assert written["patches"] == [] and written["fingerprint"] == "fp-1" and written["install_id"] == "inst-1"
    assert stat.S_IMODE(licence.stat().st_mode) == 0o600
    assert stat.S_IMODE(licence.parent.stat().st_mode) == 0o700
    # The page now moves this machine as one with a licence.
    assert enterprise.previous_licence_id() == "lic_4"
    assert enterprise.situation()["previous"]["license_id"] == "lic_4"

    # Another licence on the machine is never overwritten.
    licence.write_text(json.dumps({"license_id": "lic-old-7", "key": "x.y.z"}))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="lic-old-7"):
        await application.take("req_4")
    assert json.loads(licence.read_text())["license_id"] == "lic-old-7"

    # Console not hearing of it: the licence stays, the application is still approved there.
    licence.unlink()

    def unreported(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/activate"):
            return httpx.Response(500)
        return httpx.Response(200, json=approved("req_4", "lic_4"))

    console(monkeypatch, unreported)
    await Storage.set("console:upgrade_request:req_4", {"request_id": "req_4", "status": "approved", "details": {}}, "json")
    taken = await application.take("req_4")
    assert taken["status"] == "approved" and json.loads(licence.read_text())["license_id"] == "lic_4"


async def test_ea5_a_submission_whose_answer_was_lost(machine: Path, monkeypatch: pytest.MonkeyPatch):
    made: dict[str, dict] = {}

    def lost(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        made.setdefault(body["idempotency_key"], {"request_id": "req_5", "status": "pending"})
        raise httpx.ReadTimeout("no answer", request=request)

    seen = console(monkeypatch, lost)
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="不会重复"):
        await application.apply(dict(FORM), "admin")
    first_key = json.loads(seen[0].content)["idempotency_key"]
    assert (await Storage.get("console:upgrade_request_pending"))["idempotency_key"] == first_key
    assert await Storage.get("console:upgrade_request_ids") in (None, [])
    listing = await application.applications()
    assert listing["items"] == [] and listing["pending_submission"] is True

    # Console answers the same node and key with the application it already made.
    def answered(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json=made.get(body["idempotency_key"]) or {"request_id": "req_other", "status": "pending"})

    seen = console(monkeypatch, answered)
    got = await application.apply(dict(FORM), "admin")
    assert json.loads(seen[0].content)["idempotency_key"] == first_key and got["request_id"] == "req_5"
    assert await Storage.get("console:upgrade_request_pending") is None
    assert (await application.applications())["pending_submission"] is False

    # Outcomes that leave it unknown whether Console made it: the key stays each time.
    async def unkept(record):
        raise OSError("disk full")

    for handler, patch in (
        (lambda r: httpx.Response(504, text="gateway timeout"), None),
        (lambda r: httpx.Response(200, text="<html>not json</html>"), None),
        (lambda r: httpx.Response(200, json={"request_id": "req_6", "status": "pending"}), unkept),
    ):
        await Storage.set("console:upgrade_request_ids", [], "json")
        for key in await Storage.list_keys(prefix="console:upgrade_request:"):
            await Storage.delete(key)
        await Storage.set("console:upgrade_request_pending", {"idempotency_key": "key-unknown"}, "json")
        seen = console(monkeypatch, handler)
        if patch:
            monkeypatch.setattr(application, "_remember", patch)
        with pytest.raises((enterprise.EnterpriseUpgradeError, OSError)):
            await application.apply(dict(FORM), "admin")
        assert json.loads(seen[0].content)["idempotency_key"] == "key-unknown"
        assert (await Storage.get("console:upgrade_request_pending"))["idempotency_key"] == "key-unknown"
    monkeypatch.setattr(application, "_remember", _REMEMBER)

    # A try refused while that key is kept (Console's sign-in ran out, say) proves nothing about
    # the submission whose answer was lost: the key stays, and the try after signing in again
    # gets that application back.
    await Storage.set("console:upgrade_request_ids", [], "json")
    await Storage.delete("console:upgrade_request:req_5")
    made["key-unknown"] = {"request_id": "req_7", "status": "pending"}
    console(monkeypatch, lambda r: httpx.Response(401, json={"detail": "session expired"}))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="登录已失效"):
        await application.apply(dict(FORM), "admin")
    assert (await Storage.get("console:upgrade_request_pending"))["idempotency_key"] == "key-unknown"
    assert (await application.applications())["pending_submission"] is True
    seen = console(monkeypatch, answered)
    got = await application.apply(dict(FORM), "admin")
    assert json.loads(seen[0].content)["idempotency_key"] == "key-unknown" and got["request_id"] == "req_7"
    assert await Storage.get("console:upgrade_request_pending") is None

    # A new key refused with an answer: nothing was made, nothing left to resend.
    await Storage.set("console:upgrade_request_ids", [], "json")
    await Storage.delete("console:upgrade_request:req_7")
    console(monkeypatch, lambda r: httpx.Response(400, json={"detail": "当前节点已有进行中的升级申请"}))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="进行中"):
        await application.apply(dict(FORM), "admin")
    assert await Storage.get("console:upgrade_request_pending") is None


async def test_ea6_one_at_a_time_and_a_lost_index(machine: Path, monkeypatch: pytest.MonkeyPatch):
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"request_id": f"req_{len(seen)}", "status": "pending"})

    seen = console(monkeypatch, slow)
    results = await asyncio.gather(application.apply(dict(FORM), "admin"), application.apply(dict(FORM), "ops"),
                                   return_exceptions=True)
    assert len(seen) == 1
    assert sum(isinstance(r, dict) for r in results) == 1
    refused = [r for r in results if isinstance(r, Exception)]
    assert len(refused) == 1 and "已经有一份申请" in str(refused[0])
    assert await Storage.get("console:upgrade_request_ids") == ["req_1"]

    # A record whose id never made it into the index.
    await Storage.set("console:upgrade_request:req_lost", {"request_id": "req_lost", "status": "rejected", "details": {}}, "json")
    console(monkeypatch, lambda r: httpx.Response(200, json={"request_id": "req_1", "status": "pending"}))
    listing = await application.applications()
    assert {item["request_id"] for item in listing["items"]} == {"req_1", "req_lost"}
    assert "req_lost" in await Storage.get("console:upgrade_request_ids")


async def test_ea7_a_sign_in_console_refuses_is_forgotten(machine: Path, monkeypatch: pytest.MonkeyPatch):
    """Console answers 401 (the sign-in was revoked): this install forgets that sign-in, so the page offers
    a new one. A 403 (a refusal of a sign-in Console takes) or a 5xx forgets nothing."""
    forgotten: list[str] = []

    async def forget(token: str) -> None:
        forgotten.append(token)

    monkeypatch.setattr(enterprise, "_forget_console_session", forget)
    console(monkeypatch, lambda r: httpx.Response(401, json={"detail": "session revoked"}))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="登录已失效"):
        await application.apply(dict(FORM), "admin")
    assert forgotten == ["tok-1"]
    console(monkeypatch, lambda r: httpx.Response(403, json={"detail": "scope missing"}))
    with pytest.raises(enterprise.EnterpriseUpgradeError, match="HTTP 403"):
        await application.apply(dict(FORM), "admin")
    console(monkeypatch, lambda r: httpx.Response(503))
    with pytest.raises((enterprise.EnterpriseUpgradeError, OSError)):
        await application.apply(dict(FORM), "admin")
    assert forgotten == ["tok-1"]


async def test_ea7_every_refusal_of_a_listing_is_looked_at(machine: Path, monkeypatch: pytest.MonkeyPatch):
    """Two open applications, Console unavailable for one and refusing the sign-in for the other: the
    sign-in is forgotten even though the first trouble is what the page says."""
    forgotten: list[str] = []

    async def forget(token: str) -> None:
        forgotten.append(token)

    monkeypatch.setattr(enterprise, "_forget_console_session", forget)
    for request_id in ("req_a", "req_b"):
        await Storage.set(f"console:upgrade_request:{request_id}", {"request_id": request_id, "status": "pending",
                                                                    "details": {}}, "json")
    await Storage.set("console:upgrade_request_ids", ["req_a", "req_b"], "json")
    console(monkeypatch, lambda r: httpx.Response(503) if r.url.path.endswith("req_b") else httpx.Response(401))
    listing = await application.applications()
    assert listing["note"] and forgotten == ["tok-1"]
