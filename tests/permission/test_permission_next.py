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
