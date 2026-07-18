import asyncio
from pathlib import Path
import tempfile

import pytest

from flocks.permission.next import DeniedError, PermissionNext, PermissionRequestInfo
from flocks.storage.storage import Storage


@pytest.fixture
async def permission_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        await Storage.init(Path(tmpdir) / "permission.db")
        PermissionNext._pending = {}
        PermissionNext._session_permissions = {}
        PermissionNext._permanent_rules = {}
        PermissionNext._state_loaded = False
        PermissionNext.set_callbacks(None, None)
        yield
        PermissionNext._pending = {}
        PermissionNext._session_permissions = {}
        PermissionNext._permanent_rules = {}
        PermissionNext._state_loaded = False
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

    assert PermissionNext._permanent_rules["bash"] == "allow"
    assert await Storage.get(pending_key) is None
    assert await Storage.get(f"{PermissionNext._PERMANENT_PREFIX}bash") == "allow"


@pytest.mark.asyncio
async def test_reply_persists_session_rule_without_in_memory_future(permission_storage) -> None:
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

    assert PermissionNext._session_permissions[request.session_id]["write"] == "allow"
    assert await Storage.get(f"{PermissionNext._SESSION_PREFIX}{request.session_id}") == {"write": "allow"}


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
async def test_reply_denies_waiting_request_via_persisted_reply_when_memory_future_missing(
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

    with pytest.raises(DeniedError):
        await asyncio.wait_for(ask_task, timeout=2)

    assert await Storage.get(f"{PermissionNext._REPLY_PREFIX}{request_id}") is None


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
