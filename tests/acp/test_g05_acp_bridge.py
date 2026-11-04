"""The ACP bridge drives a running service over its HTTP API (G05).

A real server (the Flocks app under uvicorn, on a free port) stands in for the
managed service.  Through the SDK the ACP agent uses:

* a prompt is accepted by the service and the call returns when the turn is
  over -- not when the service accepted it;
* a cancel is the service's abort, and the prompt then ends "cancelled";
* events stream from the service, and a dropped stream is picked up again;
* the caller's credentials decide: a wrong token, or no service, fails at once
  and plainly, and nothing is started in its place.
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest

from flocks.server.client import FlocksClient, ServiceUnavailable

TOKEN = "acp-g05-token"  # secret-guard: allow 误报：测试用假令牌


@pytest.fixture
def api_token(monkeypatch):
    class _Secrets:
        def get(self, key):
            return TOKEN if key == "server_api_token" else None

    monkeypatch.setattr("flocks.server.auth.get_secret_manager", lambda: _Secrets())
    return TOKEN


@pytest.fixture
async def service(api_token):
    import uvicorn

    from flocks.server.app import app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


@pytest.fixture
def turn(monkeypatch):
    """The service's turns, without a model: each runs until released or aborted."""
    from flocks.server.routes import session as session_routes
    from flocks.session.session_loop import LoopResult, SessionLoop

    state = {"release": asyncio.Event(), "aborted": [], "ran": [], "gates": None}

    async def run(cls, session_id, **kwargs):
        from flocks.session.message import Message, MessageRole

        state["ran"].append(session_id)
        gate = state["release"] if state["gates"] is None else state["gates"][len(state["ran"]) - 1]
        try:
            await asyncio.wait_for(gate.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
        # What a run leaves: its reply to the user message, completed -- or
        # stopped, when it was aborted.
        asked = [item for item in await Message.list(session_id) if item.role == "user"]
        now = int(time.time() * 1000)
        if session_id in state["aborted"]:
            await Message.create(
                session_id, MessageRole.ASSISTANT, "", parentID=asked[-1].id if asked else None,
                time={"created": now}, error={"name": "MessageAbortedError", "data": {}},
            )
        else:
            await Message.create(
                session_id, MessageRole.ASSISTANT, f"done {len(state['ran'])}", parentID=asked[-1].id if asked else None,
                time={"created": now, "completed": now},
            )
        return LoopResult(action="stop")

    real_abort = session_routes._abort_session_processing

    async def abort(session_id):
        state["aborted"].append(session_id)
        state["release"].set()
        await real_abort(session_id)
        return True

    monkeypatch.setattr(SessionLoop, "run", classmethod(run))
    monkeypatch.setattr(session_routes, "_abort_session_processing", abort)
    return state


async def test_a_prompt_returns_when_the_turn_is_over(service, api_token, turn):
    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    try:
        session = await sdk.session.create(title="acp g05", directory="/tmp")
        assert session.id and (await sdk.session.get(session.id, "/tmp")).id == session.id

        async def release_later():
            await asyncio.sleep(0.6)
            turn["release"].set()

        started = time.monotonic()
        releaser = asyncio.create_task(release_later())
        stop = await sdk.session.prompt(
            session.id, {"providerID": "", "modelID": ""}, [{"type": "text", "text": "hello"}], "rex", "/tmp",
        )
        returned_after = time.monotonic() - started
        assert releaser.done(), "the prompt returned only once the turn was over"
        await releaser
        assert stop == "end_turn" and returned_after >= 0.5
        assert turn["ran"] == [session.id]
    finally:
        await sdk.close()


async def test_a_cancel_stops_the_turn_and_the_prompt_says_so(service, api_token, turn):
    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    try:
        session = await sdk.session.create(title="acp g05 cancel", directory="/tmp")
        prompt = asyncio.create_task(sdk.session.prompt(
            session.id, {}, [{"type": "text", "text": "long job"}], "rex", "/tmp",
        ))
        while session.id not in turn["ran"]:
            await asyncio.sleep(0.05)
        outcome = await sdk.session.abort(session.id, "/tmp")
        assert outcome.get("status") in {"stopped", "requested", "not_running"}
        assert await asyncio.wait_for(prompt, 10) == "cancelled"
        assert turn["aborted"] == [session.id]
    finally:
        await sdk.close()


async def test_events_stream_from_the_service_and_resume_after_a_drop(service, api_token):
    from flocks.server.routes.event import publish_event

    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    received: list[dict] = []

    async def listen():
        async for event in sdk.global_.events():
            received.append(event["payload"])
            if event["payload"].get("type") == "g05.test":
                return

    try:
        for attempt in range(2):  # the second stream is a reconnect after the first ended
            listener = asyncio.create_task(listen())
            while not any(e.get("type") == "server.connected" for e in received[attempt * 2:]):
                await asyncio.sleep(0.05)
            await publish_event("g05.test", {"attempt": attempt})
            await asyncio.wait_for(listener, 10)
        tests = [e for e in received if e.get("type") == "g05.test"]
        assert [e["properties"]["attempt"] for e in tests] == [0, 1]
    finally:
        await sdk.close()


async def test_wrong_credentials_or_no_service_fail_at_once(service):
    wrong = FlocksClient(service, api_token="not-the-token")  # secret-guard: allow 误报：测试用假令牌
    with pytest.raises(ServiceUnavailable, match="refused the credentials"):
        await wrong.check()
    await wrong.close()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    nobody = FlocksClient(f"http://127.0.0.1:{free}", api_token=TOKEN)
    with pytest.raises(ServiceUnavailable, match="no Flocks service answered"):
        await nobody.check()
    await nobody.close()


async def test_a_permission_answer_goes_to_the_service(service, api_token):
    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    try:
        import httpx

        with pytest.raises(httpx.HTTPStatusError) as unknown:
            await sdk.permission.reply("perm_does_not_exist", "once", "/tmp")
        assert unknown.value.response.status_code == 404, "asked the service, which knows no such request"
    finally:
        await sdk.close()


async def test_the_acp_agent_reports_how_the_turn_ended():
    from flocks.acp.agent import ACP, ACPConfig

    class _Session:
        async def prompt(self, **kwargs):
            return "cancelled"

    class _Sdk:
        session = _Session()

    class _Connection:
        async def session_update(self, *args, **kwargs):
            return None

    factory = await ACP.init(_Sdk())
    agent = factory["create"](_Connection(), ACPConfig(sdk=_Sdk()))
    session = type("S", (), {"cwd": "/tmp", "model": {"providerID": "p", "modelID": "m"}, "mode_id": None, "id": "ses_x"})()
    agent._session_manager.get = lambda session_id: session

    result = await agent.prompt({"sessionId": "ses_x", "prompt": [{"type": "text", "text": "hi"}]})

    assert result["stopReason"] == "cancelled"


def test_a_managed_install_lets_acp_drive_the_service_only(tmp_path, monkeypatch):
    """Without --server it would start a runtime: refused. With it, nothing starts here."""
    from typer.testing import CliRunner

    from flocks.cli.commands import acp as acp_module
    from flocks.cli.commands.acp import acp_app

    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    started = []

    async def start(self):
        started.append(self._server_url)

    monkeypatch.setattr(acp_module.ACPServer, "start", start)
    runner = CliRunner()
    refused = runner.invoke(acp_app, [])
    assert refused.exit_code == 2 and "managed install" in refused.output and started == []
    allowed = runner.invoke(acp_app, ["--server", "http://127.0.0.1:8000"])
    assert allowed.exit_code == 0 and started == ["http://127.0.0.1:8000"]



async def test_a_turn_runs_and_ends_under_its_receipt(service, api_token, turn):
    """The receipt any worker reads: running while it runs, then done, naming the turn's user message."""
    import httpx

    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    headers = {"Authorization": f"Bearer {api_token}"}
    try:
        session = await sdk.session.create(title="acp g05 receipt", directory="/tmp")
        async with httpx.AsyncClient(base_url=service, headers=headers, trust_env=False) as http:
            refused = await http.post(f"/api/session/{session.id}/prompt_async", json={"parts": [], "turnID": "../x"})
            assert refused.status_code == 400
            accepted = (await http.post(
                f"/api/session/{session.id}/prompt_async",
                json={"parts": [{"type": "text", "text": "hi"}], "turnID": "turn-one"},
            )).json()
            assert accepted["turnID"] == "turn-one"
            while session.id not in turn["ran"]:
                await asyncio.sleep(0.05)
            assert (await http.get(f"/api/session/{session.id}/turn/turn-one")).json()["state"] == "running"
            turn["release"].set()
            for _ in range(100):
                receipt = (await http.get(f"/api/session/{session.id}/turn/turn-one")).json()
                if receipt["state"] == "done":
                    break
                await asyncio.sleep(0.05)
            asked = [m for m in await sdk.session.messages(session.id, "/tmp") if m["info"]["role"] == "user"]
            assert receipt["state"] == "done" and receipt["messageID"] == asked[-1]["info"]["id"]
            assert (await http.get(f"/api/session/{session.id}/turn/no-such-turn")).status_code == 404
            again = await http.post(
                f"/api/session/{session.id}/prompt_async",
                json={"parts": [{"type": "text", "text": "again"}], "turnID": "turn-one"},
            )
            assert again.status_code == 409, "a turn id names one turn"
    finally:
        await sdk.close()


async def test_a_queued_prompt_ends_with_its_own_turn_not_the_one_before(service, api_token, turn):
    """Sent while another turn runs, it waits in the queue; the earlier turn's end is not its end."""
    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    turn["gates"] = [asyncio.Event(), asyncio.Event()]
    try:
        session = await sdk.session.create(title="acp g05 queue", directory="/tmp")
        first = asyncio.create_task(sdk.session.prompt(session.id, {}, [{"type": "text", "text": "one"}], "rex", "/tmp"))
        while len(turn["ran"]) < 1:
            await asyncio.sleep(0.05)
        second = asyncio.create_task(sdk.session.prompt(session.id, {}, [{"type": "text", "text": "two"}], "rex", "/tmp"))
        await asyncio.sleep(0.3)
        turn["gates"][0].set()
        assert await asyncio.wait_for(first, 10) == "end_turn"
        while len(turn["ran"]) < 2:
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        assert not second.done(), "the second prompt is still running its own turn"
        turn["gates"][1].set()
        assert await asyncio.wait_for(second, 10) == "end_turn"
    finally:
        await sdk.close()


async def test_a_command_still_running_is_not_over_while_the_session_reads_idle(service, api_token, monkeypatch):
    """A direct command runs without marking the session busy; its receipt says when it is done."""
    from flocks.server import client as client_module
    from flocks.server.routes import session as session_routes

    monkeypatch.setattr(client_module, "COMMAND_START_GRACE_S", 0.5, raising=False)  # the old idle grace, short
    release = asyncio.Event()
    real_dispatch = session_routes._dispatch_sse_input

    async def dispatch(session_id, session, event, working_directory):
        if (event.text or "").startswith("/slow"):
            await asyncio.wait_for(release.wait(), timeout=10)
            return None
        return await real_dispatch(session_id, session, event, working_directory)

    monkeypatch.setattr(session_routes, "_dispatch_sse_input", dispatch)
    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    real_request = sdk._service.request

    async def request(method, path, **kwargs):
        if path.endswith("/status"):  # another worker's view: nothing running, nothing queued
            return {"isProcessing": False, "pendingPromptCount": 0}
        return await real_request(method, path, **kwargs)

    monkeypatch.setattr(sdk._service, "request", request)
    try:
        session = await sdk.session.create(title="acp g05 command", directory="/tmp")
        command = asyncio.create_task(sdk.session.command(session.id, "slow", "", "", "", "/tmp"))
        await asyncio.sleep(1.5)
        assert not command.done(), "the command is still running"
        release.set()
        assert await asyncio.wait_for(command, 10) == "end_turn"
    finally:
        await sdk.close()


async def test_a_queued_turn_stays_queued_behind_a_long_one_and_a_removed_one_ends(service, api_token, turn, monkeypatch):
    """The worker holding the queue refreshes its receipts while the turn ahead runs; dropped ones end."""
    import httpx

    from flocks.session import turn_receipt
    from flocks.session.interaction_queue import InteractionQueue

    monkeypatch.setattr(turn_receipt, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(turn_receipt, "STALE_S", 1.0)
    headers = {"Authorization": f"Bearer {api_token}"}
    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    try:
        session = await sdk.session.create(title="acp g05 queued receipts", directory="/tmp")
        async with httpx.AsyncClient(base_url=service, headers=headers, trust_env=False) as http:
            async def send(turn_id: str) -> dict:
                response = await http.post(
                    f"/api/session/{session.id}/prompt_async",
                    json={"parts": [{"type": "text", "text": turn_id}], "turnID": turn_id},
                )
                return response.json()

            assert (await send("long-one"))["status"] == "accepted"
            while session.id not in turn["ran"]:
                await asyncio.sleep(0.05)
            waiting = await send("waits")
            dropped = await send("dropped")
            cleared = await send("cleared")
            assert waiting["status"] == dropped["status"] == cleared["status"] == "queued"
            await asyncio.sleep(1.6)  # longer than STALE_S
            receipt = (await http.get(f"/api/session/{session.id}/turn/waits")).json()
            assert receipt["state"] == "queued" and receipt["queueID"] == waiting["queueID"], receipt

            removed = await http.delete(f"/api/session/{session.id}/prompt_queue/{dropped['queueID']}")
            assert removed.status_code == 200
            assert (await http.get(f"/api/session/{session.id}/turn/dropped")).json()["state"] == "removed"
            # The other way a queued prompt goes without running: the queue is cleared.
            items = [item for item in await InteractionQueue.list(session.id) if item.id == cleared["queueID"]]
            await InteractionQueue.remove(session.id, cleared["queueID"])
            await turn_receipt.mark_removed(session.id, items)
            assert (await http.get(f"/api/session/{session.id}/turn/cleared")).json()["state"] == "removed"

            turn["release"].set()
            for _ in range(100):
                if (await http.get(f"/api/session/{session.id}/turn/waits")).json()["state"] == "done":
                    break
                await asyncio.sleep(0.05)
            assert (await http.get(f"/api/session/{session.id}/turn/waits")).json()["state"] == "done"
    finally:
        await sdk.close()


async def test_clearing_the_queue_ends_the_turns_in_it():
    from flocks.session import turn_receipt
    from flocks.session.interaction_queue import InteractionQueue

    session_id = "ses_receipt_clear"
    item = await InteractionQueue.enqueue(session_id, parts=[{"type": "text", "text": "x"}], execution_context={"_turnID": "in-queue"})
    await turn_receipt.create(session_id, "in-queue", turn_receipt.QUEUED, queueID=item.id)
    await InteractionQueue.clear(session_id)
    assert (await turn_receipt.get(session_id, "in-queue"))["state"] == "removed"


async def test_a_turn_handed_to_a_run_going_on_elsewhere_ends_when_that_run_answered_it(service, api_token, monkeypatch):
    """The reviewer's case: another worker holds the session, so this one leaves the message to its run."""
    from flocks.server.client import TurnFailed
    from flocks.session import ownership, turn_receipt
    from flocks.session.message import Message, MessageRole
    from flocks.session.session_loop import LoopResult, SessionLoop

    async def run_elsewhere(cls, session_id, **kwargs):
        turn_receipt.note_handed_off()  # what SessionLoop.run does when another worker has the lease
        return LoopResult(action="queued", error="Loop already running")

    held = {"elsewhere": True}

    async def is_owned(session_id):
        return held["elsewhere"]

    monkeypatch.setattr(SessionLoop, "run", classmethod(run_elsewhere))
    monkeypatch.setattr(ownership, "is_owned", is_owned)
    monkeypatch.setattr(turn_receipt, "HANDOFF_POLL_S", 0.1)
    monkeypatch.setattr(turn_receipt, "HANDOFF_IDLE_S", 0.5)
    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    try:
        session = await sdk.session.create(title="acp g05 handoff", directory="/tmp")
        prompt = asyncio.create_task(sdk.session.prompt(session.id, {}, [{"type": "text", "text": "q"}], "rex", "/tmp"))
        await asyncio.sleep(1.0)
        assert not prompt.done(), "handed off is not over"
        asked = [m for m in await sdk.session.messages(session.id, "/tmp") if m["info"]["role"] == "user"]
        now = int(time.time() * 1000)
        await Message.create(  # the other worker's run answers it
            session.id, MessageRole.ASSISTANT, "answered elsewhere", parentID=asked[-1]["info"]["id"],
            time={"created": now, "completed": now}, finish="stop",
        )
        assert await asyncio.wait_for(prompt, 10) == "end_turn"

        # A run that ended without answering: the turn failed, and says so.
        held["elsewhere"] = False
        with pytest.raises(TurnFailed, match="ended without answering"):
            await asyncio.wait_for(
                sdk.session.prompt(session.id, {}, [{"type": "text", "text": "dropped"}], "rex", "/tmp"), 10,
            )
    finally:
        await sdk.close()


async def test_an_ended_turn_never_moves_again():
    from flocks.session import turn_receipt

    session_id = "ses_receipt_terminal"
    await turn_receipt.create(session_id, "ends", turn_receipt.QUEUED, queueID="q1")
    assert await turn_receipt.advance(session_id, "ends", turn_receipt.REMOVED, queueID="q1")
    # A keeper that read the queue before the removal comes late:
    assert not await turn_receipt.refresh_queued(session_id, "ends", "q1")
    assert not await turn_receipt.advance(session_id, "ends", turn_receipt.RUNNING)
    assert (await turn_receipt.get(session_id, "ends"))["state"] == "removed"
    # Nor does a refresh for another queue item touch a queued turn.
    await turn_receipt.create(session_id, "waits", turn_receipt.QUEUED, queueID="q2")
    assert not await turn_receipt.refresh_queued(session_id, "waits", "q-other")


async def test_a_message_handed_off_with_a_later_one_is_answered_by_its_reply(tmp_path):
    """The reviewer's case: two turns handed to one run; it answers the latest, which answers both."""
    from flocks.session import turn_receipt
    from flocks.session.message import Message, MessageRole
    from flocks.session.session import Session

    session = await Session.create(project_id="receipts", directory=str(tmp_path))
    earlier = await Message.create(session.id, MessageRole.USER, "before")
    m1 = await Message.create(session.id, MessageRole.USER, "first handed off")
    now = int(time.time() * 1000) + 5
    # A reply to the message before m1, finished after m1 arrived: not m1's answer.
    await Message.create(session.id, MessageRole.ASSISTANT, "for the earlier one", parentID=earlier.id,
                         time={"created": now, "completed": now}, finish="stop")
    assert await turn_receipt._answer_of(session.id, m1.id) == (False, None)
    # Another request's direct command (/help) in between: not an answer either.
    help_asked = await Message.create(session.id, MessageRole.USER, "/help", ignored=True)
    await Message.create(session.id, MessageRole.ASSISTANT, "the commands", parentID=help_asked.id,
                         time={"created": now + 2, "completed": now + 2}, finish="stop",
                         providerID="builtin", modelID="command", ignored=True)
    assert await turn_receipt._answer_of(session.id, m1.id) == (False, None)
    m2 = await Message.create(session.id, MessageRole.USER, "second handed off")
    both = await Message.create(session.id, MessageRole.ASSISTANT, "for both", parentID=m2.id,
                                time={"created": now + 5, "completed": now + 5}, finish="stop")
    assert await turn_receipt._answer_of(session.id, m1.id) == (True, both.id)
    assert await turn_receipt._answer_of(session.id, m2.id) == (True, both.id)
    assert await turn_receipt._answer_of(session.id, "msg_gone") == (True, None), "a message that is gone has nothing left to answer"


async def test_a_queued_turn_whose_item_was_never_named_is_kept_fresh_anyway():
    from flocks.session import turn_receipt

    session_id = "ses_receipt_unnamed"
    await turn_receipt.create(session_id, "unnamed", turn_receipt.QUEUED)  # naming the item failed
    assert await turn_receipt.refresh_queued(session_id, "unnamed", "q1")
    receipt = await turn_receipt.get(session_id, "unnamed")
    assert receipt["state"] == "queued" and receipt["queueID"] == "q1"
    assert not await turn_receipt.refresh_queued(session_id, "unnamed", "q-other"), "named now: only its own item"


async def test_a_watch_that_could_not_read_once_keeps_waiting(monkeypatch):
    from flocks.session import ownership, turn_receipt

    reads = {"n": 0}

    async def answer_of(session_id, message_id):
        reads["n"] += 1
        if reads["n"] == 1:
            raise ConnectionResetError("connection reset by peer")
        return (True, "msg_reply") if reads["n"] >= 3 else (False, None)

    async def owned(session_id):
        return True

    monkeypatch.setattr(turn_receipt, "_answer_of", answer_of)
    monkeypatch.setattr(ownership, "is_owned", owned)
    monkeypatch.setattr(turn_receipt, "HANDOFF_POLL_S", 0.01)
    assert await turn_receipt._wait_for_answer("ses_watch", "msg_1") == (turn_receipt.DONE, None, "msg_reply")


async def test_a_turn_that_ran_here_is_over_only_once_its_reply_was_read(monkeypatch):
    """The reviewer's case: the read of the answering reply fails once; the receipt still names it."""
    from flocks.session import turn_receipt

    reads = {"n": 0}

    async def answer_of(session_id, message_id):
        reads["n"] += 1
        if reads["n"] == 1:
            raise ConnectionResetError("connection reset by peer")
        return True, "msg_r2"  # m1 and m2 taken together; the stopped reply is m2's

    monkeypatch.setattr(turn_receipt, "_answer_of", answer_of)
    monkeypatch.setattr(turn_receipt, "HANDOFF_POLL_S", 0.01)
    session_id = "ses_receipt_lookup"
    await turn_receipt.create(session_id, "ran-here", turn_receipt.ACCEPTED)
    async with turn_receipt.running(session_id, "ran-here"):
        turn_receipt.note_user_message("msg_m1")
    receipt = await turn_receipt.get(session_id, "ran-here")
    assert receipt["state"] == "done" and receipt.get("replyID") == "msg_r2" and reads["n"] == 2


async def test_a_queued_turn_stays_fresh_with_nothing_running_ahead_of_it(monkeypatch):
    """Queued behind a run that keeps no receipt (or none at all): the queue's own keeper refreshes it."""
    from flocks.session import turn_receipt
    from flocks.session.interaction_queue import InteractionQueue

    monkeypatch.setattr(turn_receipt, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(turn_receipt, "STALE_S", 1.0)
    session_id = "ses_receipt_keeper"
    await turn_receipt.create(session_id, "kept", turn_receipt.QUEUED)
    item = await InteractionQueue.enqueue(session_id, parts=[{"type": "text", "text": "x"}], execution_context={"_turnID": "kept"})
    await turn_receipt.name_queue_item(session_id, "kept", item.id)
    turn_receipt.ensure_queue_keeper()
    try:
        await asyncio.sleep(1.6)
        receipt = await turn_receipt.get(session_id, "kept")
        assert receipt["state"] == "queued" and not turn_receipt.is_stale(receipt)
    finally:
        await InteractionQueue.clear(session_id)
    assert (await turn_receipt.get(session_id, "kept"))["state"] == "removed"


async def test_a_turn_that_cannot_be_recorded_is_not_started(service, api_token, turn, monkeypatch):
    import httpx

    from flocks.session import turn_receipt

    async def read_only(*args, **kwargs):
        raise RuntimeError("cannot execute INSERT in a read-only transaction")

    monkeypatch.setattr(turn_receipt, "create", read_only)
    headers = {"Authorization": f"Bearer {api_token}"}
    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    try:
        session = await sdk.session.create(title="acp g05 unrecorded", directory="/tmp")
        async with httpx.AsyncClient(base_url=service, headers=headers, trust_env=False) as http:
            refused = await http.post(
                f"/api/session/{session.id}/prompt_async",
                json={"parts": [{"type": "text", "text": "x"}], "turnID": "unrecorded"},
            )
            assert refused.status_code == 503
            command = await http.post(f"/api/session/{session.id}/command", json={"command": "help", "turnID": "unrecorded-cmd"})
            assert command.status_code == 503
            await asyncio.sleep(0.3)
            assert turn["ran"] == [], "nothing was started"
            # A request that follows no turn needs no receipt.
            followed_nothing = await http.post(f"/api/session/{session.id}/prompt_async", json={"parts": [{"type": "text", "text": "x"}]})
            assert followed_nothing.status_code == 202 and "turnID" not in followed_nothing.json()
            turn["release"].set()
    finally:
        await sdk.close()


async def test_a_queued_turn_whose_preparation_fails_ends_failed(service, api_token, turn, monkeypatch):
    """Out of the queue, getting it ready fails: its receipt says failed, not queued for ever."""
    from flocks.server.client import TurnFailed
    from flocks.server.routes import session as session_routes

    real_prepare = session_routes._prepare_session_agent_before_bootstrap
    calls = {"n": 0}

    async def prepare(session, agent):
        calls["n"] += 1
        if calls["n"] == 3:  # accept the first, accept the second, then the second out of the queue
            raise ValueError("agent 'gone' is not available")
        return await real_prepare(session, agent)

    monkeypatch.setattr(session_routes, "_prepare_session_agent_before_bootstrap", prepare)
    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    try:
        session = await sdk.session.create(title="acp g05 prep", directory="/tmp")
        first = asyncio.create_task(sdk.session.prompt(session.id, {}, [{"type": "text", "text": "one"}], "rex", "/tmp"))
        while not turn["ran"]:
            await asyncio.sleep(0.05)
        second = asyncio.create_task(sdk.session.prompt(session.id, {}, [{"type": "text", "text": "two"}], "rex", "/tmp"))
        await asyncio.sleep(0.3)
        turn["release"].set()
        assert await asyncio.wait_for(first, 10) == "end_turn"
        with pytest.raises(TurnFailed, match="not available"):
            await asyncio.wait_for(second, 10)
    finally:
        await sdk.close()


async def test_a_queued_turn_that_was_removed_or_a_stale_one_reads_as_such(service, api_token):
    from flocks.session import turn_receipt

    sdk = FlocksClient(service, api_token=api_token)  # secret-guard: allow 误报：夹具传入的假令牌
    try:
        session = await sdk.session.create(title="acp g05 stale", directory="/tmp")
        await turn_receipt.create(session.id, "gone-worker", turn_receipt.RUNNING)
        stale = await turn_receipt.get(session.id, "gone-worker")
        stale["updatedAt"] -= int((turn_receipt.STALE_S + 5) * 1000)
        from flocks.storage.storage import Storage

        await Storage.set(f"session_turn:{session.id}:gone-worker", stale)
        receipt = await sdk._service.request("GET", f"/api/session/{session.id}/turn/gone-worker", directory="/tmp")
        assert receipt["state"] == "lost"
        with pytest.raises(ServiceUnavailable):
            await sdk.session._wait_for_turn(session.id, "/tmp", "gone-worker")
    finally:
        await sdk.close()


async def test_every_call_names_the_editors_project(monkeypatch):
    import httpx

    seen: list[tuple[str, str | None]] = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("x-flocks-directory")))
        if request.url.path == "/api/agent":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/provider":
            return httpx.Response(200, json={"all": []})
        return httpx.Response(200, json={"id": "ses_1"})

    sdk = FlocksClient("http://svc", api_token="t", transport=httpx.MockTransport(serve))  # secret-guard: allow 误报：测试用假令牌
    await sdk.session.create(title="x", directory="/work/project b")
    await sdk.config.get("/work/project b")
    await sdk.config.providers("/work/project b")
    await sdk.app.agents("/work/project b")
    await sdk.close()
    assert seen and all(directory == "/work/project%20b" for _, directory in seen), seen


def _turn_service(abort_status: str, reply: dict, *, done_after_polls: int = 0, gone: bool = False):
    """A service, in a MockTransport: one prompt or command, maybe an abort, and what the run left."""
    import json as json_module

    import httpx

    state = {"prompted": False, "aborted": False, "bodies": [], "polls": 0, "paths": []}

    def serve(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        state["paths"].append((path, dict(request.url.params)))
        if path.endswith("/prompt_async") or path.endswith("/command"):
            state["prompted"] = True
            body = json_module.loads(request.content)
            state["bodies"].append(body)
            return httpx.Response(202, json={"status": "accepted", "turnID": body["turnID"]})
        if path.endswith("/abort"):
            state["aborted"] = True
            return httpx.Response(200, json={"status": abort_status})
        if "/turn/" in path:
            state["polls"] += 1
            over = state["aborted"] or (done_after_polls and state["polls"] > done_after_polls)
            return httpx.Response(200, json={"state": "done", "messageID": "msg_u"} if over else {"state": "running"})
        if path.endswith("/status"):  # the session as a whole: reads idle throughout
            return httpx.Response(200, json={"isProcessing": False, "pendingPromptCount": 0})
        if path.endswith("/message"):
            if gone:
                return httpx.Response(404, json={"detail": "message not found"})
            # The turn before this one: its reply finished long ago.
            items = [
                {"info": {"id": "msg_p", "role": "user", "time": {"created": 1}}},
                {"info": {"id": "msg_pa", "role": "assistant", "parentID": "msg_p", "time": {"created": 2, "completed": 3}}},
            ]
            if request.url.params.get("after") == "msg_u":
                items = [{"info": {"id": "msg_a", "role": "assistant", "parentID": "msg_u", **reply}}]
            return httpx.Response(200, json={"items": items, "hasNewer": False})
        return httpx.Response(404)

    return httpx.MockTransport(serve), state


@pytest.mark.parametrize(("abort_status", "reply", "expected"), [
    ("unconfirmed", {"time": {"created": 2, "completed": 3}}, "end_turn"),  # the service never confirmed; the run finished
    ("not_running", {"time": {"created": 2, "completed": 3}}, "end_turn"),
    ("stopped", {"time": {"created": 2}, "error": {"name": "MessageAbortedError"}}, "cancelled"),
    ("requested", {"time": {"created": 2}, "error": {"name": "MessageAbortedError"}}, "cancelled"),
    # The reply was stopped, whatever the abort's own answer said first.
    ("unconfirmed", {"time": {"created": 2}, "error": {"name": "MessageAbortedError"}}, "cancelled"),
])
async def test_a_prompt_ends_cancelled_only_when_the_run_was_stopped(abort_status, reply, expected):
    transport, state = _turn_service(abort_status, reply)
    sdk = FlocksClient("http://svc", api_token="t", transport=transport)  # secret-guard: allow 误报：测试用假令牌
    try:
        prompt = asyncio.create_task(sdk.session.prompt("ses_1", {}, [{"type": "text", "text": "x"}], "", "/tmp"))
        while not state["prompted"]:
            await asyncio.sleep(0.01)
        await sdk.session.abort("ses_1", "/tmp")
        assert await asyncio.wait_for(prompt, 10) == expected
        assert "agent" not in state["bodies"][0] and "model" not in state["bodies"][0], "unset: the service decides"
    finally:
        await sdk.close()


async def test_the_reply_of_the_turn_before_is_not_this_turns_end():
    """The reviewer's case: a queued prompt, the earlier turn's finished reply, the session read as idle."""
    transport, state = _turn_service("", {"time": {"created": 5, "completed": 6}}, done_after_polls=4)
    sdk = FlocksClient("http://svc", api_token="t", transport=transport)  # secret-guard: allow 误报：测试用假令牌
    try:
        assert await sdk.session.prompt("ses_1", {}, [{"type": "text", "text": "x"}], "", "/tmp") == "end_turn"
        assert state["polls"] == 5, "it returned when its own turn was over"
    finally:
        await sdk.close()


async def test_a_turn_that_failed_is_reported_as_an_error():
    import httpx

    from flocks.server.client import TurnFailed

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/prompt_async"):
            return httpx.Response(202, json={"status": "accepted", "turnID": "t1"})
        if "/turn/" in request.url.path:
            return httpx.Response(200, json={"state": "failed", "error": "ProviderNotFound: provider 'x' is not configured"})
        return httpx.Response(404)

    sdk = FlocksClient("http://svc", api_token="t", transport=httpx.MockTransport(serve))  # secret-guard: allow 误报：测试用假令牌
    try:
        with pytest.raises(TurnFailed, match="ProviderNotFound"):
            await sdk.session.prompt("ses_1", {}, [{"type": "text", "text": "x"}], "", "/tmp")
    finally:
        await sdk.close()


async def test_a_turn_answered_together_with_a_later_one_ends_as_that_reply_did():
    """The reviewer's case: m1 and m2 taken together, the run stopped; the stopped reply's parent is m2."""
    import httpx

    def serve(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/prompt_async"):
            return httpx.Response(202, json={"status": "accepted", "turnID": "t1"})
        if "/turn/" in path:
            return httpx.Response(200, json={"state": "done", "messageID": "msg_m1", "replyID": "msg_r2"})
        if path.endswith("/message/msg_r2"):
            return httpx.Response(200, json={"info": {
                "id": "msg_r2", "role": "assistant", "parentID": "msg_m2",
                "time": {"created": 3}, "error": {"name": "MessageAbortedError"},
            }, "parts": []})
        if path.endswith("/message"):
            return httpx.Response(200, json={"items": [], "hasNewer": False})  # nothing has m1 as its parent
        return httpx.Response(404)

    sdk = FlocksClient("http://svc", api_token="t", transport=httpx.MockTransport(serve))  # secret-guard: allow 误报：测试用假令牌
    try:
        assert await sdk.session.prompt("ses_1", {}, [{"type": "text", "text": "x"}], "", "/tmp") == "cancelled"
    finally:
        await sdk.close()


async def test_a_command_whose_history_was_cleared_ends_normally():
    """``/clear`` leaves no message to look for; the command is over all the same."""
    transport, state = _turn_service("", {}, done_after_polls=1, gone=True)
    sdk = FlocksClient("http://svc", api_token="t", transport=transport)  # secret-guard: allow 误报：测试用假令牌
    try:
        assert await sdk.session.command("ses_1", "clear", "", "", "", "/tmp") == "end_turn"
        assert state["bodies"][0]["command"] == "clear" and state["bodies"][0]["turnID"]
    finally:
        await sdk.close()


async def test_the_acp_agent_takes_its_defaults_from_the_service():
    from flocks.acp.agent import ACP, ACPConfig, _configured_default_model

    assert _configured_default_model({"default_models": {"llm": {"provider_id": "svc", "model_id": "m1"}}}) == {"providerID": "svc", "modelID": "m1"}
    assert _configured_default_model({"model": "svc/m2"}) == {"providerID": "svc", "modelID": "m2"}
    assert _configured_default_model({}) is None

    class _Config:
        def __init__(self, config):
            self._config = config

        async def get(self, directory):
            return self._config

        async def providers(self, directory):
            return [{"id": "svc", "name": "svc", "models": {"m1": {"id": "m1"}}}]

    class _Sdk:
        def __init__(self, config):
            self.config = _Config(config)

    class _Connection:
        async def session_update(self, *args, **kwargs):
            return None

    agents = [{"name": "rex", "mode": "primary"}, {"name": "ops", "mode": "primary"}, {"name": "helper", "mode": "subagent"}]
    for config, model, agent in (
        ({"default_models": {"llm": {"provider_id": "svc", "model_id": "m1"}}, "defaultAgent": "ops"}, {"providerID": "svc", "modelID": "m1"}, "ops"),
        # Named on the client's machine but not offered by the service: not used.
        ({"model": "anthropic/claude-local", "defaultAgent": "local-only"}, {"providerID": "svc", "modelID": "m1"}, "rex"),
    ):
        sdk = _Sdk(config)
        factory = await ACP.init(sdk)
        acp_agent = factory["create"](_Connection(), ACPConfig(sdk=sdk))
        assert await acp_agent._get_default_model("/tmp") == model
        assert await acp_agent._service_default_agent("/tmp", agents) == agent


async def test_the_stdio_loop_handles_a_cancel_while_a_prompt_is_waiting(monkeypatch):
    from flocks.cli.commands import acp as acp_module

    events: list[str] = []
    released = asyncio.Event()

    class _Agent:
        def start_event_subscription(self):
            return None

        def shutdown(self):
            return None

        async def prompt(self, params):
            events.append("prompt:start")
            await asyncio.wait_for(released.wait(), 5)
            events.append("prompt:end")
            return {"stopReason": "cancelled"}

        async def cancel(self, params):
            events.append("cancel")
            released.set()

    async def init(_sdk):
        return {"create": lambda connection, config: _Agent()}

    monkeypatch.setattr("flocks.acp.agent.ACP.init", init)

    server = acp_module.ACPServer.__new__(acp_module.ACPServer)
    server._shutdown = False
    server._server_url = "http://svc"
    server._sdk = object()
    server._agent = None
    inbox: asyncio.Queue = asyncio.Queue()
    written: list[dict] = []

    class _Stream:
        async def start(self):
            return None

        async def read(self):
            return await inbox.get()

        async def write(self, message):
            written.append(message)
            if message.get("id") == 1:
                server._shutdown = True

        def close(self):
            return None

    server._stream = _Stream()
    await inbox.put({"jsonrpc": "2.0", "id": 1, "method": "session/prompt", "params": {"sessionId": "s"}})
    await inbox.put({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "s"}})
    await asyncio.wait_for(server._serve_stdio(), 10)

    assert events == ["prompt:start", "cancel", "prompt:end"]
    assert written and written[-1]["id"] == 1 and written[-1]["result"] == {"stopReason": "cancelled"}
