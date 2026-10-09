"""``state.time.compacted`` must survive persist → cache drop → reload.

``prune()`` and ``validate_preserved_messages()`` mark stale tool outputs
by setting ``state.time["compacted"]`` only (the output text stays on
disk).  Part normalisation on cache load used to rebuild ``time`` from
``start``/``end`` alone, so after a restart / LRU eviction every
time-marked tool result went back to the LLM in full.
"""

from __future__ import annotations

import pytest

from flocks.session.context_usage import _compacted_time_ms
from flocks.session.lifecycle.compaction.pruning import (
    prune,
    validate_preserved_messages,
)
from flocks.session.message import (
    Message,
    MessageRole,
    MessageWithParts,
    PartTime,
    ReasoningPart,
    ToolPart,
    ToolStateCompleted,
)
from flocks.storage.storage import Storage

SID = "ses_compacted_roundtrip"
MID = "msg_compacted_roundtrip"

# Large enough that a handful of pruned parts clears PRUNE_MINIMUM (20K tokens).
BIG_OUTPUT = "lorem ipsum dolor sit amet " * 6000


def _tool_part(
    session_id: str,
    message_id: str,
    call_id: str,
    output: str = BIG_OUTPUT,
    *,
    time: dict | None = None,
    metadata: dict | None = None,
) -> ToolPart:
    return ToolPart(
        id=f"part_{call_id}",
        sessionID=session_id,
        messageID=message_id,
        callID=call_id,
        tool="bash",
        state=ToolStateCompleted(
            input={"cmd": "ls"},
            output=output,
            title="bash",
            metadata=metadata or {},
            time=time or {"start": 1, "end": 2},
        ),
    )


async def _build_session(session_id: str, user_turns: int) -> None:
    """``user_turns`` user/assistant pairs, each assistant carrying one big tool result."""
    for i in range(user_turns):
        await Message.create(session_id, MessageRole.USER, f"q{i}", id=f"msg_u{i}")
        asst = await Message.create(
            session_id,
            MessageRole.ASSISTANT,
            "",
            id=f"msg_a{i}",
            providerID="test",
            modelID="test",
            agent="rex",
            mode="build",
        )
        await Message.add_part(session_id, asst.id, _tool_part(session_id, asst.id, f"call{i}"))


async def _tool_part_of(session_id: str, message_id: str) -> ToolPart:
    parts = await Message.parts(message_id, session_id)
    tool_parts = [p for p in parts if p.type == "tool"]
    assert len(tool_parts) == 1
    return tool_parts[0]


async def _stored_tool_time(session_id: str, message_id: str) -> dict:
    raw = await Storage.get(f"message_parts:{session_id}:{message_id}")
    assert isinstance(raw, list)
    stored = [p for p in raw if p.get("type") == "tool"]
    assert len(stored) == 1
    return stored[0]["state"]["time"]


def _llm_sees_placeholder(message, part: ToolPart) -> bool:
    """True when the LLM-facing conversion substitutes a placeholder.

    ``Message.to_model_message`` applies the same two checks as the runner's
    ``_get_persisted_tool_placeholder`` (``metadata.context_compact_placeholder``
    first, then ``time.compacted``).
    """
    model_msgs = Message.to_model_message([MessageWithParts(info=message, parts=[part])])
    tool_results = [
        c for m in model_msgs for c in m["content"] if c.get("type") == "tool-result"
    ]
    assert len(tool_results) == 1
    return tool_results[0]["result"] == "[Tool output compacted]"


# ---------------------------------------------------------------------------
# T1–T3: _default_part_time
# ---------------------------------------------------------------------------

class TestDefaultPartTime:
    def test_passes_through_compacted(self):
        out = Message._default_part_time({"start": 1, "end": 2, "compacted": 3})
        assert out == {"start": 1, "end": 2, "compacted": 3}

    def test_no_compacted_key_when_absent(self):
        # ToolStateCompleted.time is Dict[str, int]; a None value would fail validation.
        out = Message._default_part_time({"start": 1, "end": 2})
        assert out == {"start": 1, "end": 2}
        assert "compacted" not in Message._default_part_time(None)
        assert "compacted" not in Message._default_part_time(message_time={"created": 5})

    @pytest.mark.parametrize("bad", ["abc", None, 0, -1, 1.5, {}, []])
    def test_invalid_compacted_is_dropped(self, bad):
        out = Message._default_part_time({"start": 1, "end": 2, "compacted": bad})
        assert out == {"start": 1, "end": 2}


# ---------------------------------------------------------------------------
# T4–T6: deserialize_part round-trip
# ---------------------------------------------------------------------------

class TestDeserializeRoundTrip:
    def test_tool_part_keeps_compacted(self):
        part = _tool_part(SID, MID, "c1", "out", time={"start": 1, "end": 2, "compacted": 3})
        restored = Message.deserialize_part(part.model_dump())
        assert restored.state.time == {"start": 1, "end": 2, "compacted": 3}
        assert restored.state.output == "out"

    def test_tool_part_legacy_time_keys_keep_compacted(self):
        raw = _tool_part(SID, MID, "c2", "out").model_dump()
        raw["state"]["time"] = {"created": 1, "completed": 2, "compacted": 3}
        restored = Message.deserialize_part(raw)
        assert restored.state.time == {"start": 1, "end": 2, "compacted": 3}

    def test_reasoning_part_keeps_compacted(self):
        part = ReasoningPart(
            sessionID=SID, messageID=MID, text="think", time=PartTime(start=1, compacted=5)
        )
        restored = Message.deserialize_part(part.model_dump())
        assert restored.time.compacted == 5


# ---------------------------------------------------------------------------
# T7: prune() → drop cache → reload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prune_marker_survives_cache_reload():
    session_id = "ses_prune_reload"
    await _build_session(session_id, user_turns=6)
    await prune(session_id)

    before = await _tool_part_of(session_id, "msg_a0")
    stamp = before.state.time.get("compacted")
    assert stamp, "prune() did not mark msg_a0 — scenario setup broken"
    assert (await _stored_tool_time(session_id, "msg_a0")).get("compacted") == stamp

    Message.invalidate_cache(session_id)  # restart / LRU eviction / storage invalidation

    messages = {m.id: m for m in await Message.list(session_id)}
    after = await _tool_part_of(session_id, "msg_a0")
    assert after.state.time.get("compacted") == stamp
    assert after.state.output == BIG_OUTPUT  # text is still on disk; only the marker matters
    assert _llm_sees_placeholder(messages["msg_a0"], after)


# ---------------------------------------------------------------------------
# T8: validate_preserved_messages() → drop cache → reload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_boundary_marker_survives_cache_reload():
    session_id = "ses_boundary_reload"
    await _build_session(session_id, user_turns=2)
    all_messages = await Message.list(session_id)
    # Preserved tail starts on an assistant message → msg_a0 is the boundary.
    preserved = [m for m in all_messages if m.id in ("msg_a0", "msg_u1", "msg_a1")]
    await validate_preserved_messages(session_id, preserved)

    before = await _tool_part_of(session_id, "msg_a0")
    stamp = before.state.time.get("compacted")
    assert stamp, "validate_preserved_messages() did not mark msg_a0 — scenario setup broken"

    Message.invalidate_cache(session_id)

    messages = {m.id: m for m in await Message.list(session_id)}
    after = await _tool_part_of(session_id, "msg_a0")
    assert after.state.time.get("compacted") == stamp
    assert _llm_sees_placeholder(messages["msg_a0"], after)
    # The non-boundary assistant must remain untouched.
    untouched = await _tool_part_of(session_id, "msg_a1")
    assert "compacted" not in untouched.state.time


# ---------------------------------------------------------------------------
# T9: update_part() (runner budget path) keeps the marker in memory and on disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_part_keeps_compacted_marker():
    session_id = "ses_update_part_reload"
    await _build_session(session_id, user_turns=1)
    part = await _tool_part_of(session_id, "msg_a0")

    state = part.state.model_dump()
    state["metadata"] = {**state["metadata"], "context_compact_placeholder": "[Context compacted]"}
    state["time"] = {**state["time"], "compacted": 4242}
    await Message.update_part(session_id, "msg_a0", part.id, state=state)

    in_memory = await _tool_part_of(session_id, "msg_a0")
    assert in_memory.state.time.get("compacted") == 4242
    assert _compacted_time_ms(in_memory.state) == 4242
    assert (await _stored_tool_time(session_id, "msg_a0")).get("compacted") == 4242

    Message.invalidate_cache(session_id)
    reloaded = await _tool_part_of(session_id, "msg_a0")
    assert reloaded.state.time.get("compacted") == 4242
    assert reloaded.state.metadata.get("context_compact_placeholder") == "[Context compacted]"


# ---------------------------------------------------------------------------
# T10: a second prune() after reload leaves existing marks untouched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prune_after_reload_does_not_restamp():
    session_id = "ses_prune_twice"
    await _build_session(session_id, user_turns=6)
    await prune(session_id)
    stamps = {
        mid: (await _tool_part_of(session_id, mid)).state.time.get("compacted")
        for mid in ("msg_a0", "msg_a1", "msg_a2")
    }
    assert all(stamps.values())

    Message.invalidate_cache(session_id)
    await prune(session_id)

    for mid, stamp in stamps.items():
        assert (await _tool_part_of(session_id, mid)).state.time.get("compacted") == stamp
    for mid in ("msg_a5",):
        assert "compacted" not in (await _tool_part_of(session_id, mid)).state.time


# ---------------------------------------------------------------------------
# T11: a runner budget mark in the middle of history must not stop prune()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prune_walks_past_runner_budget_marks():
    """The runner's per-turn budget marks only some outputs of the latest turn,
    so a marked part can sit in front of older, still-unmarked ones.  Now that
    the mark survives ``update_part``/reload, prune() must skip it instead of
    treating it as "everything older is already compacted"."""
    session_id = "ses_prune_runner_mark"
    await _build_session(session_id, user_turns=1)  # turn0: expired bash

    # turn1: three big outputs; the runner compacted the first one.
    await Message.create(session_id, MessageRole.USER, "q1", id="msg_u1")
    asst = await Message.create(
        session_id, MessageRole.ASSISTANT, "", id="msg_a1",
        providerID="test", modelID="test", agent="rex", mode="build",
    )
    for k in range(3):
        await Message.add_part(session_id, asst.id, _tool_part(session_id, asst.id, f"call1_{k}"))
    first = [p for p in await Message.parts("msg_a1", session_id) if p.type == "tool"][0]
    state = first.state.model_dump()
    state["metadata"] = {
        **state["metadata"],
        "context_compacted": True,
        "context_compact_placeholder": "[Context compacted]",
    }
    state["time"] = {**state["time"], "compacted": 1700000000000}
    await Message.update_part(session_id, "msg_a1", first.id, state=state)

    # turn2: current turn keeps turn0/turn1 outside bash's 1-turn window.
    await Message.create(session_id, MessageRole.USER, "q2", id="msg_u2")
    await Message.create(
        session_id, MessageRole.ASSISTANT, "", id="msg_a2",
        providerID="test", modelID="test", agent="rex", mode="build",
    )

    await prune(session_id)

    turn1 = [p for p in await Message.parts("msg_a1", session_id) if p.type == "tool"]
    assert turn1[0].state.time.get("compacted") == 1700000000000  # runner mark untouched
    assert all(p.state.time.get("compacted") for p in turn1[1:])
    assert (await _tool_part_of(session_id, "msg_a0")).state.time.get("compacted"), (
        "older expired output behind a runner mark was never pruned"
    )
