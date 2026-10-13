import asyncio
from pathlib import Path
import tempfile

import pytest

from flocks.permission.next import PermissionNext, PermissionRequestInfo
from flocks.storage.storage import Storage


@pytest.fixture
async def permission_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        await Storage.init(Path(tmpdir) / "permission.db")
        PermissionNext._pending = {}
        PermissionNext.set_callbacks(None, None)
        yield
        PermissionNext._pending = {}
        PermissionNext.set_callbacks(None, None)
        await Storage.clear()


@pytest.mark.asyncio
async def test_reply_restores_persisted_pending_request_without_memory(permission_storage) -> None:
    request = PermissionRequestInfo(
        id="per_testpending00000000000001",
        sessionID="ses_testpending0000000001",
        permission="bash",
        patterns=["*"],
        metadata={"messageID": "msg_1"},
        always=["*"],
        tool={"name": "bash"},
    )
    pending_key = f"{PermissionNext._PENDING_PREFIX}{request.id}"

    await Storage.set(pending_key, request.model_dump(by_alias=True), "permission_pending")

    await PermissionNext.reply(request.id, "always", session_id=request.session_id)

    assert await Storage.get(pending_key) is None


@pytest.mark.asyncio
async def test_reply_persists_transport_reply_without_in_memory_future(permission_storage) -> None:
    request = PermissionRequestInfo(
        id="per_testsession0000000000001",
        sessionID="ses_testsession0000000001",
        permission="write",
        patterns=["notes.md"],
        metadata={"messageID": "msg_2"},
        always=[],
        tool={"name": "write"},
    )

    await Storage.set(
        f"{PermissionNext._PENDING_PREFIX}{request.id}",
        request.model_dump(by_alias=True),
        "permission_pending",
    )

    await PermissionNext.reply(request.id, "allow_session", session_id=request.session_id)

    stored = await Storage.get(f"{PermissionNext._REPLY_PREFIX}{request.id}")
    assert stored["reply"] == "allow_session"
    assert stored["sessionID"] == request.session_id


@pytest.mark.asyncio
async def test_reply_persists_session_denial_without_in_memory_future(permission_storage) -> None:
    request = PermissionRequestInfo(
        id="per_testsessiondeny000000001",
        sessionID="ses_testsessiondeny00000001",
        permission="bash",
        patterns=["*"],
    )
    await Storage.set(
        f"{PermissionNext._PENDING_PREFIX}{request.id}",
        request.model_dump(by_alias=True),
        "permission_pending",
    )

    await PermissionNext.reply(request.id, "deny_session", session_id=request.session_id)

    assert PermissionNext._session_permissions[request.session_id]["bash"] == "deny"
    assert await Storage.get(f"{PermissionNext._SESSION_PREFIX}{request.session_id}") == {"bash": "deny"}


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["allow", "once"])
async def test_reply_unblocks_waiting_request_via_persisted_reply_when_memory_future_missing(
    permission_storage,
    reply: str,
) -> None:
    request_id = f"per_waiting_{reply}"
    ask_task = asyncio.create_task(
        PermissionNext.ask(
            session_id="ses_waiting_allow",
            permission="bash",
            patterns=["*"],
            ruleset=[],
            metadata={"messageID": "msg_waiting_allow"},
            request_id=request_id,
        )
    )

    while request_id not in PermissionNext._pending:
        await asyncio.sleep(0)

    PermissionNext._pending.pop(request_id, None)

    await PermissionNext.reply(request_id, reply, session_id="ses_waiting_allow")
    await asyncio.wait_for(ask_task, timeout=2)

    assert await Storage.get(f"{PermissionNext._REPLY_PREFIX}{request_id}") is None


@pytest.mark.asyncio
async def test_reply_is_returned_raw_when_memory_future_missing(
    permission_storage,
) -> None:
    request_id = "per_waiting_deny"
    ask_task = asyncio.create_task(
        PermissionNext.ask(
            session_id="ses_waiting_deny",
            permission="write",
            patterns=["notes.md"],
            ruleset=[],
            metadata={"messageID": "msg_waiting_deny"},
            request_id=request_id,
        )
    )

    while request_id not in PermissionNext._pending:
        await asyncio.sleep(0)

    PermissionNext._pending.pop(request_id, None)

    await PermissionNext.reply(request_id, "deny", session_id="ses_waiting_deny")

    assert await asyncio.wait_for(ask_task, timeout=2) == "deny"

    assert await Storage.get(f"{PermissionNext._REPLY_PREFIX}{request_id}") is None


@pytest.mark.asyncio
async def test_session_denial_applies_to_active_request_without_global_rule(permission_storage) -> None:
    request_id = "per_waiting_session_deny"
    session_id = "ses_waiting_session_deny"
    ask_task = asyncio.create_task(
        PermissionNext.ask(
            session_id=session_id,
            permission="bash",
            patterns=["*"],
            ruleset=[],
            request_id=request_id,
        )
    )

    while request_id not in PermissionNext._pending:
        await asyncio.sleep(0)

    await PermissionNext.reply(request_id, "deny_session", session_id=session_id)

    with pytest.raises(DeniedError):
        await asyncio.wait_for(ask_task, timeout=2)

    assert PermissionNext._session_permissions[session_id]["bash"] == "deny"
    assert "bash" not in PermissionNext._permanent_rules


@pytest.mark.asyncio
async def test_state_load_retries_after_transient_storage_failure(permission_storage, monkeypatch: pytest.MonkeyPatch) -> None:
    await Storage.set(f"{PermissionNext._PERMANENT_PREFIX}bash", "allow", "permission_rule")


    original_list_entries = Storage.list_entries
    call_count = 0

    async def flaky_list_entries(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("temporary storage failure")
        return await original_list_entries(*args, **kwargs)

    monkeypatch.setattr(Storage, "list_entries", flaky_list_entries)

    await PermissionNext._ensure_persisted_state_loaded()
    assert PermissionNext._state_loaded is False
    assert PermissionNext._permanent_rules == {}

    await PermissionNext._ensure_persisted_state_loaded()
    assert PermissionNext._state_loaded is True
    assert PermissionNext._permanent_rules["bash"] == "allow"


@pytest.mark.asyncio
async def test_ask_returns_allow_when_auto_approve_env_set(
    permission_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOCKS_AUTO_APPROVE", "true")

    reply = await PermissionNext.ask(
        session_id="ses_auto_approve",
        permission="bash",
        patterns=["*"],
        ruleset=[],
        metadata={},
        request_id="per_auto_approve",
    )

    assert reply == "allow"
    assert "per_auto_approve" not in PermissionNext._pending


@pytest.mark.asyncio
async def test_ask_times_out_as_denyable_timeout_and_cleans_pending_request(
    permission_storage,
) -> None:
    request_id = "per_confirm_timeout"

    with pytest.raises(asyncio.TimeoutError, match="timed out after 0s"):
        await PermissionNext.ask(
            session_id="ses_confirm_timeout",
            permission="ssh_host_cmd",
            patterns=["ssh_host_cmd:canonical:hash"],
            ruleset=[],
            metadata={},
            request_id=request_id,
            timeout_seconds=0,
        )

    assert request_id not in PermissionNext._pending
    assert await Storage.get(f"{PermissionNext._PENDING_PREFIX}{request_id}") is None
    assert await Storage.get(f"{PermissionNext._REPLY_PREFIX}{request_id}") is None


@pytest.mark.asyncio
async def test_request_is_persisted_before_callback_can_reply(permission_storage) -> None:
    request_id = "per_persist_before_expose"

    async def reply_immediately(request: PermissionRequestInfo) -> None:
        stored = await Storage.get(f"{PermissionNext._PENDING_PREFIX}{request.id}")
        assert stored is not None
        await PermissionNext.reply(request.id, "always", session_id=request.session_id)

    PermissionNext.set_callbacks(reply_immediately, None)

    assert await PermissionNext.ask(
        session_id="ses_persist_before_expose",
        permission="bash",
        patterns=["*"],
        ruleset=[],
        metadata={},
        request_id=request_id,
    ) == "always"
    assert await Storage.get(f"{PermissionNext._PENDING_PREFIX}{request_id}") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["replied", "timed_out", "cancelled"])
async def test_settled_request_is_announced_and_dropped(
    permission_storage,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    # The session and every session that delegated to it show the request;
    # each needs to hear once nobody waits on it any more.
    published: list[tuple[str, dict]] = []

    async def _publish(event_type: str, properties: dict = None, directory: str = None):
        published.append((event_type, properties or {}))

    monkeypatch.setattr("flocks.server.routes.event.publish_event", _publish)
    request_id = f"per_settled_{ending}"
    ask_task = asyncio.create_task(
        PermissionNext.ask(
            session_id="ses_settled",
            permission="edit",
            patterns=["notes.md"],
            ruleset=[],
            metadata={},
            request_id=request_id,
            timeout_seconds=0 if ending == "timed_out" else None,
        )
    )
    if ending != "timed_out":
        while request_id not in PermissionNext._pending:
            await asyncio.sleep(0)
    if ending == "replied":
        await PermissionNext.reply(request_id, "allow", session_id="ses_settled")
        assert await asyncio.wait_for(ask_task, timeout=2) == "allow"
    elif ending == "cancelled":
        ask_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ask_task
    else:
        with pytest.raises(asyncio.TimeoutError):
            await ask_task

    settled = [props for event_type, props in published if event_type == "permission.replied"]
    assert settled == [{
        "requestID": request_id,
        "sessionID": "ses_settled",
        "reply": "allow" if ending == "replied" else None,
    }]
    assert request_id not in PermissionNext._pending
    assert await Storage.get(f"{PermissionNext._PENDING_PREFIX}{request_id}") is None


@pytest.mark.asyncio
async def test_every_request_names_the_sessions_that_delegated_its_session(
    permission_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Confirmations that reach PermissionNext.ask directly (Pro policy, tool
    # routes) must show in the delegating chat just like runner requests.
    from types import SimpleNamespace

    sessions = {
        "ses_root": SimpleNamespace(id="ses_root", category="user", parent_id=None),
        "ses_child": SimpleNamespace(id="ses_child", category="task", parent_id="ses_root"),
        "ses_grandchild": SimpleNamespace(id="ses_grandchild", category="task", parent_id="ses_child"),
    }

    async def _get_by_id(session_id: str):
        return sessions.get(session_id)

    published: list[tuple[str, dict]] = []

    async def _publish(event_type: str, properties: dict = None, directory: str = None):
        published.append((event_type, properties or {}))

    monkeypatch.setattr("flocks.session.session.Session.get_by_id", _get_by_id)
    monkeypatch.setattr("flocks.server.routes.event.publish_event", _publish)

    for session_id, metadata in (("ses_grandchild", {}), ("ses_root", {}), ("ses_child", {"delegationChain": ["kept"]})):
        with pytest.raises(asyncio.TimeoutError):
            await PermissionNext.ask(
                session_id=session_id,
                permission="network",
                patterns=["example.com"],
                ruleset=[],
                metadata=metadata,
                timeout_seconds=0,
            )

    chains = [
        props["metadata"].get("delegationChain")
        for event_type, props in published
        if event_type == "permission.request"
    ]
    assert chains == [["ses_child", "ses_root"], None, ["kept"]]


@pytest.mark.asyncio
async def test_request_stopped_while_being_announced_is_still_dropped(
    permission_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[tuple[str, dict]] = []

    async def _publish(event_type: str, properties: dict = None, directory: str = None):
        published.append((event_type, properties or {}))

    announcing = asyncio.Event()

    async def _slow_listener(_request: PermissionRequestInfo) -> None:
        announcing.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("flocks.server.routes.event.publish_event", _publish)
    PermissionNext.set_callbacks(_slow_listener, None)
    request_id = "per_stopped_while_announced"
    ask_task = asyncio.create_task(PermissionNext.ask(
        session_id="ses_announce",
        permission="edit",
        patterns=["notes.md"],
        ruleset=[],
        metadata={},
        request_id=request_id,
    ))
    await asyncio.wait_for(announcing.wait(), timeout=2)

    ask_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ask_task

    assert request_id not in PermissionNext._pending
    assert await Storage.get(f"{PermissionNext._PENDING_PREFIX}{request_id}") is None
    assert ("permission.replied", {"requestID": request_id, "sessionID": "ses_announce", "reply": None}) in published
