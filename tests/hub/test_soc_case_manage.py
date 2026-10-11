import asyncio
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.tool.registry import ToolResult

PLUGIN = Path(__file__).resolve().parents[2] / ".flocks/flockshub/plugins/tools/python/soc_case_manage/soc_case_manage.py"
CTX = SimpleNamespace(session_id="session-1", aborted=False)
ACTOR = SimpleNamespace(user_id="analyst", kind="human")


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("soc_case_manage_unit_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Store:
    """Stands in for CaseStore: a claim always succeeds, finishes are recorded."""

    def __init__(self, hold_claim: threading.Event | None = None) -> None:
        self.hold_claim = hold_claim
        self.finished: list[tuple[str, dict]] = []

    def claim_action(self, actor, case_id, action_id, expected_version, idempotency_key, session_id=""):
        if self.hold_claim is not None:
            self.hold_claim.wait(5)
        plan = {"id": action_id, "case_id": case_id, "plan_hash": "plan-hash", "tool_name": "isolate_host",
                "parameters": {"device_id": "device-1", "host": "10.0.0.9"}}
        return {"case": {"id": case_id}, "action": plan, "claim_token": "claim-1", "claimed": True}

    def finish_action(self, actor, case_id, action_id, claim_token, outcome, receipt):
        self.finished.append((outcome, receipt))
        return {"case": {"id": case_id}, "record": {"id": action_id, "execution_status": outcome}}


@pytest.fixture
def dispatches(tool, monkeypatch):
    calls: list[str] = []

    async def execute(tool_name, ctx=None, **kwargs):
        calls.append(tool_name)
        return ToolResult(success=True, output="isolated")

    monkeypatch.setattr(tool, "_child_context", lambda ctx, session, plan: SimpleNamespace(call_id="call-1"))
    monkeypatch.setattr(tool, "_device_tool", lambda plan: None)
    monkeypatch.setattr(tool.ToolRegistry, "execute", execute)
    return calls


def _execute(tool, store):
    return tool._execute(store, ACTOR, CTX, None, "case-1", 3, "key-1", {"action_id": "action-1"})


def test_alert_snapshots_keep_no_http_credentials(tool) -> None:
    snapshot = {
        "req_header": "GET /login HTTP/1.1\r\nHost: portal.test\r\nAuthorization: Basic dXNlcjpwYXNz\r\nCookie: session=abc123",
        "rsp_header": "HTTP/1.1 302 Found\r\nSet-Cookie: sid=xyz789; HttpOnly\r\nLocation: /home",
        "req_cookie": "session=abc123",
        "headers": {"X-Api-Key": "key-456", "Accept": "*/*"},
        "escaped_header": "Host: portal.test\\r\\nCookie: session=abc123",
        "payload": "user=admin&hint=Basic dXNlcjpwYXNz",
        "referer": "http://admin:hunter2@10.0.0.5/login",  # secret-guard: allow (fake credential the test expects to be redacted)
    }
    redacted = tool._domain._secret(snapshot, redact=True)
    flat = json.dumps(redacted)
    for secret in ("dXNlcjpwYXNz", "abc123", "xyz789", "key-456", "hunter2"):
        assert secret not in flat
    assert "Host: portal.test" in redacted["req_header"]
    assert "Location: /home" in redacted["rsp_header"]
    assert redacted["headers"]["Accept"] == "*/*"


@pytest.mark.parametrize("data", [
    {"content": "Cookie: session=abc123"},
    {"content": "GET / HTTP/1.1\nAuthorization: Basic dXNlcjpwYXNz"},
    {"content": "Set-Cookie: sid=xyz789"},
    {"content": "the capture shows Basic dXNlcjpwYXNz"},
    {"content": "http://admin:hunter2@10.0.0.5/"},  # secret-guard: allow (fake credential the test expects to be redacted)
    {"parameters": {"req_cookie": "session=abc123"}},
])
def test_case_data_carrying_http_credentials_is_refused(tool, data) -> None:
    with pytest.raises(tool.CaseError) as refused:
        tool._domain._secret(data)
    assert refused.value.code == "credentials_forbidden"


def test_prose_about_credentials_is_not_mistaken_for_one(tool) -> None:
    text = "Basic authentication was enabled; the session cookie: rotated after the incident"
    assert tool._domain._secret({"content": text}) == {"content": text}


async def test_a_claim_committed_after_cancellation_is_finished_as_never_dispatched(tool, dispatches) -> None:
    release = threading.Event()
    store = _Store(hold_claim=release)
    task = asyncio.create_task(_execute(tool, store))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.sleep(0.05)
    release.set()  # the claim commits in its thread after the caller is gone
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dispatches == []
    assert [outcome for outcome, _ in store.finished] == ["blocked"]
    assert store.finished[0][1]["dispatch_started"] is False


async def test_cancellation_while_recording_the_outcome_still_finishes_the_claim(tool, dispatches, monkeypatch) -> None:
    hold = threading.Event()
    receipt = tool._receipt

    def slow_receipt(*args):
        hold.wait(5)
        return receipt(*args)

    monkeypatch.setattr(tool, "_receipt", slow_receipt)
    store = _Store()
    task = asyncio.create_task(_execute(tool, store))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.sleep(0.05)
    hold.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dispatches == ["isolate_host"]
    assert [outcome for outcome, _ in store.finished] == ["succeeded"]


async def test_arguments_refused_before_the_handler_are_recorded_as_not_executed(tool, dispatches, monkeypatch) -> None:
    async def refuse(tool_name, ctx=None, **kwargs):
        return ToolResult(success=False, error="Missing required parameter: host",
                          metadata={"schema_precheck": {"tool": tool_name}})

    monkeypatch.setattr(tool.ToolRegistry, "execute", refuse)
    store = _Store()
    value = await _execute(tool, store)
    assert value["execution_outcome"] == "unsupported"
    assert [outcome for outcome, _ in store.finished] == ["unsupported"]


def test_plans_must_use_the_device_tool_arguments_exactly(tool) -> None:
    schema = SimpleNamespace(
        properties={"device_id": {"type": "string"}, "host": {"type": "string"}, "minutes": {"type": "integer"}},
        required=["host"],
    )
    device_tool = SimpleNamespace(info=SimpleNamespace(name="isolate_host", get_schema=lambda: schema))
    tool._check_schema(device_tool, {"device_id": "device-1", "host": "10.0.0.9", "minutes": 30})
    for params, message in (
        ({"device_id": "device-1", "hostname": "10.0.0.9"}, "Unknown parameters"),
        ({"device_id": "device-1"}, "Missing parameters"),
        ({"device_id": "device-1", "host": "10.0.0.9", "minutes": "30"}, "minutes"),
        ({"device_id": "device-1", "host": "10.0.0.9", "minutes": True}, "minutes"),
    ):
        with pytest.raises(tool.CaseError, match=message) as refused:
            tool._check_schema(device_tool, params)
        assert refused.value.code == "invalid_parameters"


@pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="Requires isolated PostgreSQL")
async def test_a_case_opened_from_an_alert_stores_the_snapshot_without_credentials(tool) -> None:
    from flocks.db.connect import open_transaction

    handle = await open_transaction()
    try:
        await handle.executescript("CREATE TABLE alert_records (row_id TEXT PRIMARY KEY, record_json JSONB)")
        await handle.execute("INSERT INTO alert_records(row_id, record_json) VALUES (?, ?::jsonb)", (
            "row-1",
            json.dumps({
                "threat_name": "brute force",
                "req_header": "POST /login HTTP/1.1\r\nHost: portal.test\r\nCookie: session=abc123",
                "rsp_header": "HTTP/1.1 200 OK\r\nSet-Cookie: sid=xyz789",
                "req_cookie": "session=abc123",
            }),
        ))
    finally:
        await handle.close()
    store = tool.CaseStore()
    admin = tool.Actor(user_id="admin", kind="human", is_admin=True)
    created = await asyncio.to_thread(
        store.dispatch, "create", admin, idempotency_key="create-1",
        data={"title": "Brute force on portal", "alert_id": "row-1"},
    )
    detail = await asyncio.to_thread(store.dispatch, "get", admin, case_id=created["case"]["id"], data={})
    [evidence] = detail["evidence"]
    assert evidence["source_record_id"] == "row-1"
    assert "Host: portal.test" in evidence["content"] and "brute force" in evidence["content"]
    for secret in ("abc123", "xyz789"):
        assert secret not in evidence["content"]


@pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="Requires isolated PostgreSQL")
async def test_an_alert_row_id_is_matched_before_any_record_id(tool) -> None:
    from flocks.db.connect import open_transaction

    handle = await open_transaction()
    try:
        await handle.executescript("CREATE TABLE alert_records (row_id TEXT PRIMARY KEY, record_id TEXT, record_json JSONB)")
        for row_id, record_id in (("row-1", "row-2"), ("row-2", "shared"), ("row-3", "shared"), ("row-4", "only-4")):
            await handle.execute("INSERT INTO alert_records(row_id, record_id, record_json) VALUES (?, ?, ?::jsonb)",
                                 (row_id, record_id, json.dumps({"threat_name": "alert " + row_id})))
    finally:
        await handle.close()
    store = tool.CaseStore()
    admin = tool.Actor(user_id="admin", kind="human", is_admin=True)

    def open_case(alert_id):
        return store.dispatch("create", admin, idempotency_key="create-" + alert_id,
                              data={"title": "Alert " + alert_id, "alert_id": alert_id})

    # row-1's record_id is also "row-2": the primary key decides, without a table scan.
    assert (await asyncio.to_thread(open_case, "row-2"))["case"]["source_alert_id"] == "row-2"
    assert (await asyncio.to_thread(open_case, "only-4"))["case"]["source_alert_id"] == "row-4"
    with pytest.raises(tool.CaseError) as ambiguous:
        await asyncio.to_thread(open_case, "shared")
    assert ambiguous.value.code == "ambiguous_alert"


@pytest.fixture
def sessions(tool, monkeypatch):
    """Sessions _actor_for can look up; the caller is an active analyst account."""
    known: dict[str, object] = {}
    principal = SimpleNamespace(id="analyst", status="active", must_reset_password=False, role="member")

    async def get_session(session_id):
        return known.get(session_id)

    async def get_message(session_id, message_id):
        return {"id": message_id}

    monkeypatch.setattr(tool, "get_current_auth_user", lambda: principal)
    monkeypatch.setattr(tool, "Session", SimpleNamespace(get_by_id=get_session))
    monkeypatch.setattr(tool, "Message", SimpleNamespace(get=get_message))
    monkeypatch.setattr(tool, "SessionPolicy", SimpleNamespace(can_read=lambda s, p: True, can_write=lambda s, p: True))
    known["investigation"] = SimpleNamespace(agent="soc-investigator", owner_user_id="analyst")
    known["chat"] = SimpleNamespace(agent="rex", owner_user_id="analyst")
    return known


def _caller(session_id: str, agent: str = "", *, http: bool = False) -> SimpleNamespace:
    extra = {"http_permission_denied": {}} if http else {"agent_execution_session": True}
    return SimpleNamespace(extra=extra, session_id=session_id, message_id="message-1", agent=agent)


@pytest.mark.parametrize("operation", ["execute_action", "close", "reopen"])
async def test_the_investigation_agent_cannot_execute_close_or_reopen(tool, sessions, operation) -> None:
    with pytest.raises(tool.CaseError) as refused:
        await tool._actor_for(_caller("investigation", "soc-investigator"), operation)
    assert (refused.value.code, refused.value.status_code) == ("operator_required", 403)
    # The session's own agent decides even when the context does not name one.
    with pytest.raises(tool.CaseError, match="investigation agent"):
        await tool._actor_for(_caller("investigation"), operation)


@pytest.mark.parametrize("operation", ["get", "add_evidence", "decide_hypothesis", "propose_action", "record_review"])
async def test_the_investigation_agent_still_investigates_and_proposes(tool, sessions, operation) -> None:
    actor, _ = await tool._actor_for(_caller("investigation", "soc-investigator"), operation)
    assert actor.kind == "agent"


async def test_operators_and_other_agents_keep_execute_and_close(tool, sessions) -> None:
    operator, _ = await tool._actor_for(_caller("investigation", http=True), "execute_action")
    assert operator.kind == "human"
    for operation in ("execute_action", "close", "reopen"):
        agent, _ = await tool._actor_for(_caller("chat", "rex"), operation)
        assert agent.kind == "agent"
        # The user switched an investigation session to another agent: the
        # running agent decides, not the one the session was created with.
        switched, _ = await tool._actor_for(_caller("investigation", "rex"), operation)
        assert switched.kind == "agent"
