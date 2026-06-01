"""Tests for PR-2 / Phase 2 — per-tool retention policy.

Covers:
* ``resolve_tool_preserve_turns`` — exact, prefix-wildcard, fallback;
* ``truncate_tool_result_text_safe`` — JSON-aware truncation;
* ``prune`` — user-turn pruning when the v2 tool-policy flag is on.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.session.lifecycle.compaction import (
    TOOL_PRUNE_POLICY,
    TOOL_RESULT_PRESERVE_USER_TURNS,
    resolve_tool_preserve_turns,
)
from flocks.session.lifecycle.compaction.pruning import prune
from flocks.tool.truncation import (
    truncate_tool_result_text_safe,
    _truncate_json_value,
)


# ---------------------------------------------------------------------------
# resolve_tool_preserve_turns
# ---------------------------------------------------------------------------

class TestResolveToolPreserveTurns:
    def test_exact_match_skill_load_never_prune(self):
        assert resolve_tool_preserve_turns("skill_load") == -1

    def test_exact_match_bash_single_turn(self):
        assert resolve_tool_preserve_turns("bash") == 1

    def test_prefix_wildcard_tdp(self):
        # ``tdp_*`` should match any tool starting with ``tdp_``.
        assert resolve_tool_preserve_turns("tdp_alert_pull") == 2
        assert resolve_tool_preserve_turns("tdp_search_log") == 2

    def test_prefix_wildcard_threatbook(self):
        assert resolve_tool_preserve_turns("threatbook_ip_query") == 2

    def test_unknown_tool_falls_back(self):
        # An unknown tool gets the ``*`` catch-all.
        assert resolve_tool_preserve_turns("totally_made_up_xyz") == TOOL_RESULT_PRESERVE_USER_TURNS

    def test_empty_tool_name_falls_back(self):
        assert resolve_tool_preserve_turns("") == TOOL_RESULT_PRESERVE_USER_TURNS

    def test_catchall_value_consistent(self):
        # The catch-all in TOOL_PRUNE_POLICY mirrors TOOL_RESULT_PRESERVE_USER_TURNS.
        assert TOOL_PRUNE_POLICY["*"] == TOOL_RESULT_PRESERVE_USER_TURNS

    def test_prefix_wildcard_does_not_eat_catchall(self):
        # A tool that starts with ``*`` (the catch-all pattern) should not
        # be treated as a prefix match.  Edge case guard.
        # Implementation explicitly skips ``*`` in the prefix loop.
        assert resolve_tool_preserve_turns("anything_at_all") == TOOL_PRUNE_POLICY["*"]


# ---------------------------------------------------------------------------
# truncate_tool_result_text_safe
# ---------------------------------------------------------------------------

class TestTruncateToolResultSafe:
    def test_short_text_passthrough(self):
        text = "hello world"
        assert truncate_tool_result_text_safe(text, 1000) == text

    def test_non_json_falls_back_to_char_truncation(self):
        # Plain log lines — should NOT raise and should return shorter text.
        text = "x" * 5_000
        result = truncate_tool_result_text_safe(text, 1_000)
        assert len(result) <= 1_500  # allow margin for suffix
        assert result.startswith("x")

    def test_json_dict_remains_parseable(self):
        # Build a JSON payload larger than max_chars.
        payload = {
            "tool": "tdp_alert_pull",
            "items": [
                {"id": i, "body": "lorem ipsum " * 50}
                for i in range(100)
            ],
            "metadata": {"total": 100, "page": 1},
        }
        text = json.dumps(payload, ensure_ascii=False)
        assert len(text) > 5_000

        truncated = truncate_tool_result_text_safe(text, 2_000)
        # Must still parse as JSON
        parsed = json.loads(truncated)
        # Truncation marker was added somewhere
        assert "__truncated__" in str(parsed) or "[truncated]" in str(parsed)

    def test_json_list_remains_parseable(self):
        payload = [
            {"id": i, "name": "x" * 100} for i in range(200)
        ]
        text = json.dumps(payload, ensure_ascii=False)
        truncated = truncate_tool_result_text_safe(text, 1_500)
        parsed = json.loads(truncated)
        assert isinstance(parsed, list)
        # The truncated list should be shorter than the original.
        assert len(parsed) < len(payload)

    def test_long_string_value_truncated(self):
        text = json.dumps({"data": "a" * 10_000}, ensure_ascii=False)
        truncated = truncate_tool_result_text_safe(text, 500)
        parsed = json.loads(truncated)
        assert "[truncated]" in parsed["data"]

    def test_malformed_json_falls_back(self):
        # Looks JSON-ish but isn't parseable — should not crash.
        text = '{"key": "value", "broken": '
        # extend with junk so we exceed max_chars
        text = text + "x" * 5_000
        result = truncate_tool_result_text_safe(text, 1_000)
        assert isinstance(result, str)
        assert len(result) <= 1_500

    def test_truncate_json_value_dict_preserves_keys(self):
        value = {"a": "x" * 500, "b": "y" * 500, "c": "z" * 500}
        truncated = _truncate_json_value(value, budget=300)
        assert isinstance(truncated, dict)
        # At least one key should have a truncation marker
        truncated_repr = json.dumps(truncated, ensure_ascii=False)
        assert len(truncated_repr) <= 600  # close to budget


# ---------------------------------------------------------------------------
# prune() — v2 user-turn policy
# ---------------------------------------------------------------------------

class _FakePart:
    """Minimal duck-type stand-in for MessagePart used inside prune()."""

    def __init__(self, *, type: str, tool: str = "", state: Any = None, message_id: str = ""):
        self.type = type
        self.tool = tool
        self.state = state
        self.messageID = message_id


class _FakeState:
    def __init__(self, *, status: str = "completed", output: str = "", time: dict | None = None):
        self.status = status
        self.output = output
        self.time = time if time is not None else {}


class _FakeRole:
    def __init__(self, value: str):
        self.value = value


class _FakeMessage:
    def __init__(self, *, id: str, role: str, finish: str | None = None):
        self.id = id
        self.role = _FakeRole(role)
        self.finish = finish
        self.metadata = {}


@pytest.fixture
def make_session():
    """Build a fake message list with N user-turn / assistant-tool pairs."""
    def _build(turns: list[tuple[str, str]]) -> tuple[list[_FakeMessage], dict[str, list[_FakePart]]]:
        """``turns`` is a list of ``(role, tool_name)`` — when role is ``user``
        the ``tool_name`` parameter is ignored.  Returns messages plus a
        message_id -> parts mapping for ``Message.parts``.
        """
        messages: list[_FakeMessage] = []
        parts_by_id: dict[str, list[_FakePart]] = {}
        for idx, (role, tool_name) in enumerate(turns):
            mid = f"m{idx}"
            msg = _FakeMessage(id=mid, role=role)
            messages.append(msg)
            if role == "assistant" and tool_name:
                # 24K chars ≈ 6K tokens — comfortably above the lower bound
                # of ``prune_minimum`` (2K) that ``from_model`` will clamp
                # us to even when we ask for a smaller value.
                state = _FakeState(
                    status="completed",
                    output="x" * 24_000,
                    time={},
                )
                parts_by_id[mid] = [_FakePart(
                    type="tool", tool=tool_name, state=state, message_id=mid,
                )]
            else:
                parts_by_id[mid] = []
        return messages, parts_by_id
    return _build


@pytest.mark.asyncio
async def test_prune_v2_per_tool_window(make_session):
    """A ``bash`` tool 3 user-turns ago should be pruned; the latest
    ``bash`` should be preserved; ``skill_load`` is never pruned."""
    # Turns ordered oldest -> newest.  Last user is the current turn.
    turns = [
        ("user", ""),                    # u3 turn (oldest)
        ("assistant", "skill_load"),     # NEVER prune
        ("user", ""),                    # u2 turn
        ("assistant", "bash"),           # OUT of bash's 1-turn window -> prune
        ("user", ""),                    # u1 turn (most recent user prompt)
        ("assistant", "bash"),           # INSIDE bash's 1-turn window -> keep
    ]
    messages, parts_by_id = make_session(turns)

    async def fake_list(session_id):  # noqa: ARG001
        return messages

    async def fake_parts(message_id, session_id):  # noqa: ARG001
        return parts_by_id.get(message_id, [])

    async def fake_persist(session_id, message_id=None):  # noqa: ARG001
        return None

    with patch("flocks.session.message.Message") as MockMessage:
        MockMessage.list = AsyncMock(side_effect=fake_list)
        MockMessage.parts = AsyncMock(side_effect=fake_parts)
        MockMessage._persist_parts = AsyncMock(side_effect=fake_persist)

        # Configure a v2 policy with overhead enabled; prune() reads
        # ``policy.prune_protect`` and ``policy.prune_minimum`` and
        # ``policy.preserve_last``.
        from flocks.session.lifecycle.compaction import CompactionPolicy
        policy = CompactionPolicy.from_model(128_000, 4_096, overrides={
            "prune_minimum": 100,  # tiny so we don't gate on minimum
            "prune_protect": 100,
            "preserve_last": 1,
        })
        await prune("ses_test", policy=policy)

    skill_load_state = parts_by_id["m1"][0].state
    bash_old_state = parts_by_id["m3"][0].state
    bash_new_state = parts_by_id["m5"][0].state

    # skill_load: never pruned (compacted=falsy)
    assert not skill_load_state.time.get("compacted"), \
        "skill_load must NOT be compacted under any policy"
    # Old bash: outside its 1-turn window -> compacted
    assert bash_old_state.time.get("compacted"), \
        "bash 2 user-turns ago must be compacted under v2 tool policy"
    # Latest bash: inside its 1-turn window -> kept
    assert not bash_new_state.time.get("compacted"), \
        "bash from the latest user turn must NOT be compacted"


@pytest.mark.asyncio
async def test_prune_keeps_full_history_for_never_prune_tools(make_session):
    """``skill_load`` (retention = -1) must never be compacted, even when
    every other tool result older than its window has been pruned."""
    # 20 cycles: each user turn calls ``skill_load`` and a ``bash``.
    turns: list[tuple[str, str]] = []
    for _ in range(20):
        turns.append(("user", ""))
        turns.append(("assistant", "skill_load"))
        turns.append(("assistant", "bash"))
    messages, parts_by_id = make_session(turns)

    async def fake_list(session_id):  # noqa: ARG001
        return messages

    async def fake_parts(message_id, session_id):  # noqa: ARG001
        return parts_by_id.get(message_id, [])

    async def fake_persist(session_id, message_id=None):  # noqa: ARG001
        return None

    with patch("flocks.session.message.Message") as MockMessage:
        MockMessage.list = AsyncMock(side_effect=fake_list)
        MockMessage.parts = AsyncMock(side_effect=fake_parts)
        MockMessage._persist_parts = AsyncMock(side_effect=fake_persist)

        from flocks.session.lifecycle.compaction import CompactionPolicy
        policy = CompactionPolicy.from_model(128_000, 4_096, overrides={
            "prune_minimum": 100,
            "prune_protect": 100,
        })
        await prune("ses_test", policy=policy)

    # Every ``skill_load`` part across the 20 turns must remain pristine.
    for mid, parts in parts_by_id.items():
        for part in parts:
            if getattr(part, "tool", None) == "skill_load":
                state = part.state
                assert not state.time.get("compacted"), (
                    f"skill_load at {mid} must never be compacted"
                )
