import asyncio
import json
import os

import httpx
import pytest

from flocks.console import login as login_mod
from flocks.console import outbox
from flocks.console.login import ConsoleLoginService
from flocks.updater.updater import _put_success_files

SESSION = {"console_session_token": "cs_outbox", "fingerprint": "fp_outbox", "install_id": "inst_outbox"}
BASE = "https://console.example.com"


@pytest.fixture(autouse=True)
def _runtime_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))


class _Response:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=httpx.Request("POST", BASE), response=httpx.Response(self.status_code))

    def json(self):
        return self._data


class _Console:
    """Records what reaches the Console; answers by URL."""

    def __init__(self, answers=None, on_post=None):
        self.posts: list[dict] = []
        self.answers = answers or {}
        self.on_post = on_post

    async def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        if self.on_post is not None:
            self.on_post(url, json)
        answer = self.answers.get(url, 201)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer()
        return _Response(answer, {"id": "inst-1"})


def _deliver(console):
    return asyncio.run(outbox.deliver_reports(client=console, session=SESSION, console_base=BASE + "/"))


def test_a_report_waits_until_the_console_takes_it():
    entry = outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"result": "done", "fingerprint": "stale"})
    assert oct(entry.stat().st_mode & 0o777) == "0o600"

    refusing = _Console(answers={BASE + "/v1/receipts": 503})
    assert _deliver(refusing) == 0
    assert outbox.pending_reports() == [entry]

    console = _Console()
    assert _deliver(console) == 1
    assert outbox.pending_reports() == []
    assert console.posts == [{
        "url": BASE + "/v1/receipts",
        # The node's identity is the session's, as in a heartbeat.
        "json": {"result": "done", "fingerprint": "fp_outbox", "install_id": "inst_outbox"},
        "headers": {"Authorization": "Bearer cs_outbox"},
    }]


def test_reports_go_out_oldest_first_and_wait_while_the_console_is_unreachable():
    first = outbox.enqueue_report(kind="a", path="/v1/a", payload={"n": 1})
    second = outbox.enqueue_report(kind="b", path="/v1/b", payload={"n": 2})
    assert outbox.pending_reports() == [first, second]

    unreachable = _Console(answers={BASE + "/v1/a": httpx.ConnectError("down")})
    assert _deliver(unreachable) == 0
    # The Console is down: the second report is not even tried.
    assert [post["url"] for post in unreachable.posts] == [BASE + "/v1/a"]
    assert outbox.pending_reports() == [first, second]

    # Refused on its own: set aside, and it does not hold the next one back.
    rejecting_first = _Console(answers={BASE + "/v1/a": 409})
    assert _deliver(rejecting_first) == 1
    assert outbox.pending_reports() == []
    assert (outbox.outbox_dir() / "rejected" / first.name).is_file()


@pytest.mark.parametrize("status", [500, 503, 429, 401])
def test_a_newer_report_never_goes_out_before_an_older_one_the_console_could_not_take(status):
    first = outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": 1})
    outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": 2})
    taken: list[int] = []
    answers = iter([status])

    def _answer():
        return next(answers, 201)

    class _Ordered(_Console):
        async def post(self, url, json=None, headers=None):
            response = await super().post(url, json=json, headers=headers)
            if response.status_code in (200, 201, 202):
                taken.append(json["n"])
            return response

    console = _Ordered(answers={BASE + "/v1/receipts": _answer})
    assert _deliver(console) == 0
    # Not even tried: it would have been taken before the first one.
    assert len(console.posts) == 1
    assert outbox.pending_reports()[0] == first

    assert _deliver(console) == 2
    assert taken == [1, 2]


def test_an_unexpected_send_error_keeps_the_report_first_in_line():
    """Neither taken nor refused: it stays first, and nothing after it goes out before it."""
    outbox.enqueue_report(kind="ext.receipt", path="/v1/first", payload={"n": 1})
    outbox.enqueue_report(kind="ext.receipt", path="/v1/second", payload={"n": 2})
    console = _Console(answers={BASE + "/v1/first": ValueError("client bug")})

    assert _deliver(console) == 0
    assert [post["url"] for post in console.posts] == [BASE + "/v1/first"]
    assert len(outbox.pending_reports()) == 2

    console.answers = {}
    assert _deliver(console) == 2
    assert [post["url"] for post in console.posts][1:] == [BASE + "/v1/first", BASE + "/v1/second"]


def test_a_report_written_while_another_is_sent_is_kept():
    first = outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": 1})
    written: list = []

    def _meanwhile(url, body):
        if not written:
            written.append(outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": 2}))

    console = _Console(on_post=_meanwhile)
    assert _deliver(console) == 1
    assert not first.exists()
    assert outbox.pending_reports() == written

    later = _Console()
    assert _deliver(later) == 1
    assert [post["json"]["n"] for post in later.posts] == [2]


def test_a_report_is_only_ever_sent_to_a_path_on_the_console(tmp_path):
    for path in ("https://elsewhere.example/v1", "//elsewhere.example/v1", "@elsewhere.example/v1", "v1/receipts", "/v1/a b"):
        with pytest.raises(ValueError):
            outbox.enqueue_report(kind="ext.receipt", path=path, payload={})
    with pytest.raises(ValueError):
        outbox.enqueue_report(kind="", path="/v1/receipts", payload={})

    # One that got onto disk anyway is set aside, never sent.
    outbox.outbox_dir().mkdir(parents=True)
    planted = outbox.outbox_dir() / "00000000T000000000000-planted.json"
    planted.write_text(json.dumps({"kind": "x", "path": "@elsewhere.example/steal", "payload": {}}), encoding="utf-8")
    broken = outbox.outbox_dir() / "00000000T000000000001-broken.json"
    broken.write_text("{not json", encoding="utf-8")
    console = _Console()
    assert _deliver(console) == 0
    assert console.posts == []
    assert outbox.pending_reports() == []
    assert sorted(p.name for p in (outbox.outbox_dir() / "invalid").iterdir()) == [planted.name, broken.name]


def test_withdrawing_takes_back_only_that_kind():
    install = outbox.enqueue_report(kind="ext.install", path="/v1/receipts", payload={"result": "success"})
    downgrade = outbox.enqueue_report(kind="ext.downgrade", path="/v1/receipts", payload={"result": "downgraded"})

    assert outbox.withdraw_reports("ext.install", reason="downgraded") == 1
    assert outbox.pending_reports() == [downgrade]
    kept = json.loads((outbox.outbox_dir() / "withdrawn" / install.name).read_text(encoding="utf-8"))
    assert kept["withdraw_reason"] == "downgraded" and kept["payload"] == {"result": "success"}
    assert outbox.withdraw_reports("ext.install", reason="again") == 0


def test_a_withdrawal_can_pick_reports_by_payload_and_leave_the_rest_in_line():
    outbox.enqueue_report(kind="ext.receipt", path="/v1/r", payload={"result": "failed", "n": 1})
    picked = outbox.enqueue_report(kind="ext.receipt", path="/v1/r", payload={"result": "success", "n": 2})
    outbox.enqueue_report(kind="ext.receipt", path="/v1/r", payload={"result": "failed", "n": 3})

    taken = outbox.withdraw_reports("ext.receipt", reason="superseded", where=lambda payload: payload.get("result") == "success")

    assert taken == 1
    assert [json.loads(e.read_text())["payload"]["n"] for e in outbox.pending_reports()] == [1, 3]
    assert (outbox.outbox_dir() / "withdrawn" / picked.name).exists()


def test_a_staged_report_joins_the_outbox_only_when_the_install_puts_it_in_place(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    staged, destination = outbox.stage_report(work, kind="ext.install", path="/v1/receipts", payload={"result": "success"})
    assert staged.parent == work and destination.parent == outbox.outbox_dir()
    assert outbox.pending_reports() == []

    _put_success_files([(staged, destination)])
    assert outbox.pending_reports("ext.install") == [destination]
    assert oct(destination.stat().st_mode & 0o777) == "0o600"


def _fake_heartbeat_client(monkeypatch, console):
    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            if url.endswith("/v1/heartbeats"):
                return _Response(200, {})
            return await console.post(url, json=json, headers=headers)

    monkeypatch.setattr(login_mod.httpx, "AsyncClient", _Client)


def test_the_heartbeat_sends_waiting_reports_with_no_extension_installed(monkeypatch):
    """An extension that took itself out still has its reports sent by the core."""
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", BASE)
    monkeypatch.setattr(login_mod, "_node_extensions", [])
    outbox.enqueue_report(kind="ext.downgrade", path="/v1/receipts", payload={"result": "downgraded"})
    console = _Console()
    _fake_heartbeat_client(monkeypatch, console)

    asyncio.run(ConsoleLoginService.send_heartbeat_for_session(session=SESSION, report_pending=False))
    assert console.posts == [] and len(outbox.pending_reports()) == 1

    asyncio.run(ConsoleLoginService.send_heartbeat_for_session(session=SESSION, report_pending=True))
    assert [post["json"]["result"] for post in console.posts] == ["downgraded"]
    assert outbox.pending_reports() == []


def test_outbox_follows_the_runtime_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", "")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert outbox.outbox_dir() == tmp_path / "home" / ".flocks" / "run" / "console-outbox"
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "elsewhere"))
    assert outbox.outbox_dir() == tmp_path / "elsewhere" / "run" / "console-outbox"
    assert not os.path.exists(outbox.outbox_dir())


# One delivery at a time; whoever supersedes waiting reports holds the outbox.

class _SlowConsole(_Console):
    def __init__(self, delay=0.05, **kwargs):
        super().__init__(**kwargs)
        self.delay = delay

    async def post(self, url, json=None, headers=None):
        await asyncio.sleep(self.delay)
        return await super().post(url, json=json, headers=headers)


def test_two_deliveries_at_once_send_each_report_once_in_order():
    for n in (1, 2, 3):
        outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": n})
    console = _SlowConsole()

    async def _both():
        return await asyncio.gather(
            outbox.deliver_reports(client=console, session=SESSION, console_base=BASE),
            outbox.deliver_reports(client=console, session=SESSION, console_base=BASE),
        )

    assert sorted(asyncio.run(_both())) == [0, 3]
    assert [post["json"]["n"] for post in console.posts] == [1, 2, 3]


def test_no_delivery_runs_while_the_outbox_is_held_but_the_holder_can_deliver(monkeypatch):
    monkeypatch.setattr(outbox, "_DELIVERY_WAIT_S", 0.2)
    outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": 1})
    console = _Console()

    async def _scenario():
        async with outbox.exclusive():
            # Another delivery gives up after a short wait and sends nothing.
            assert await outbox.deliver_reports(client=console, session=SESSION, console_base=BASE) == 1
            outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": 2})
            other = asyncio.create_task(outbox.deliver_reports(client=console, session=SESSION, console_base=BASE))
            assert await other == 0
        return await outbox.deliver_reports(client=console, session=SESSION, console_base=BASE)

    assert asyncio.run(_scenario()) == 1
    assert [post["json"]["n"] for post in console.posts] == [1, 2]


def test_a_withdrawal_waits_for_the_report_already_on_its_way():
    """What a downgrade does: no install receipt may reach the Console after it heard of the downgrade."""
    outbox.enqueue_report(kind="ext.install", path="/v1/receipts", payload={"result": "installed"})
    console = _SlowConsole(delay=0.1)

    async def _scenario():
        delivery = asyncio.create_task(outbox.deliver_reports(client=console, session=SESSION, console_base=BASE))
        await asyncio.sleep(0.02)  # the install receipt is on its way
        async with outbox.exclusive():
            withdrawn = outbox.withdraw_reports("ext.install", reason="downgraded")
            await console.post(BASE + "/v1/receipts", json={"result": "downgraded"})
        await delivery
        return withdrawn

    assert asyncio.run(_scenario()) == 0
    assert [post["json"]["result"] for post in console.posts] == ["installed", "downgraded"]


def test_a_report_taken_but_not_removable_stops_the_round(monkeypatch):
    first = outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": 1})
    outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": 2})
    real_unlink = type(first).unlink

    def _refuse_in_outbox(self, missing_ok=False):
        if self.parent == outbox.outbox_dir():
            raise PermissionError("read-only directory")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(first), "unlink", _refuse_in_outbox)
    console = _Console()

    assert _deliver(console) == 1
    # Taken, but the round stops there instead of failing the heartbeat.
    assert [post["json"]["n"] for post in console.posts] == [1]
    assert len(outbox.pending_reports()) == 2


def test_a_delivery_cancelled_while_waiting_for_the_file_lock_does_not_keep_it(monkeypatch):
    from filelock import FileLock

    monkeypatch.setattr(outbox, "_DELIVERY_WAIT_S", 3.0)
    outbox.enqueue_report(kind="ext.receipt", path="/v1/receipts", payload={"n": 1})
    lock_path = str(outbox.outbox_dir() / ".lock")
    other_process = FileLock(lock_path, thread_local=False)
    other_process.acquire()

    kept: list = []

    async def _scenario():
        waiting = asyncio.create_task(outbox.deliver_reports(client=_Console(), session=SESSION, console_base=BASE))
        await asyncio.sleep(0.2)  # blocked on the file lock another process holds
        waiting.cancel()
        try:
            await waiting
        except asyncio.CancelledError as exc:
            kept.append(exc)  # its traceback (and the waiting frame) stays alive
        other_process.release()
        await asyncio.sleep(0.5)  # the abandoned wait takes the lock, and must give it back

    asyncio.run(_scenario())
    probe = FileLock(lock_path, thread_local=False)
    probe.acquire(timeout=0.5)
    probe.release()
    assert kept
