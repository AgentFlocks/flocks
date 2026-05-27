"""Tests covering the ``"skipped"`` return path from ``SessionCompaction.process``.

Self-review of PR #321 surfaced that the manual + overflow paths in
``session_loop`` previously treated ``skipped_no_summary`` as a successful
compaction (updating ``last_compaction_step`` and emitting
``context.compacted``).  These tests pin the new contract:

* ``process()`` returns ``"skipped"`` (NOT ``"continue"``) when the
  anti-thrashing cooldown is active.
* ``process()`` returns ``"skipped"`` when the summary provider is in
  cooldown — without writing any summary or archiving messages.

The tests intentionally drive ``process()`` directly because the early
returns happen *before* any provider, message store, or callback code is
reached, so this gives us a deterministic, hermetic verification of the
new state machine.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.session.lifecycle.compaction import compaction as compaction_module
from flocks.session.lifecycle.compaction.compaction import (
    SessionCompaction,
    _get_compaction_history,
    _compaction_history,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_history():
    """Drop any cross-test cooldown state stashed in the module-level cache."""
    _compaction_history.clear()
    yield
    _compaction_history.clear()


def _empty_messages() -> list[dict]:
    """Minimum-viable message list — process() never touches it on the skip
    paths because they early-return before any provider call.
    """
    return [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


# ---------------------------------------------------------------------------
# Anti-thrashing cooldown — process() must return "skipped"
# ---------------------------------------------------------------------------


class TestAntiThrashingReturnsSkipped:
    @pytest.mark.asyncio
    async def test_active_cooldown_returns_skipped(self):
        session_id = "ses_skipped_thrashing"

        # Pre-seed history so the next process() invocation lands in the
        # cooldown branch on the very first line.
        history = _get_compaction_history(session_id)
        history.cooldown_remaining = 2

        result = await SessionCompaction.process(
            session_id=session_id,
            parent_id="msg_root",
            messages=_empty_messages(),
            model_id="claude-3-5-sonnet",
            provider_id="anthropic",
        )

        assert result == "skipped"

    @pytest.mark.asyncio
    async def test_cooldown_decrements_total_skipped(self):
        """The cooldown counter and the audit ``total_skipped`` should both
        advance every time we return ``"skipped"``."""
        session_id = "ses_skipped_counters"

        history = _get_compaction_history(session_id)
        history.cooldown_remaining = 3
        history.total_skipped = 0
        history.total_attempts = 0

        result = await SessionCompaction.process(
            session_id=session_id,
            parent_id="msg_root",
            messages=_empty_messages(),
            model_id="claude-3-5-sonnet",
            provider_id="anthropic",
        )

        assert result == "skipped"
        # Counters mirror the implementation: total_attempts increments
        # before the cooldown branch fires, so we expect 1 attempt +
        # 1 skip on this call.
        assert history.cooldown_remaining == 2
        assert history.total_skipped == 1
        assert history.total_attempts == 1

    @pytest.mark.asyncio
    async def test_anti_thrashing_skip_does_not_archive(self):
        """When the cooldown returns ``"skipped"``, the archive step must NOT
        run.  This guards against the original bug where the loop treated
        a skip as a successful compaction and continued downstream work.
        """
        session_id = "ses_skip_no_archive"
        history = _get_compaction_history(session_id)
        history.cooldown_remaining = 1

        archive_spy = AsyncMock(return_value="continue")
        with patch.object(
            SessionCompaction,
            "_archive_and_write_summary",
            archive_spy,
        ):
            result = await SessionCompaction.process(
                session_id=session_id,
                parent_id="msg_root",
                messages=_empty_messages(),
                model_id="claude-3-5-sonnet",
                provider_id="anthropic",
            )

        assert result == "skipped"
        archive_spy.assert_not_called()


# ---------------------------------------------------------------------------
# Summary-provider cooldown — also returns "skipped"
# ---------------------------------------------------------------------------


class TestSummaryProviderCooldownReturnsSkipped:
    @pytest.mark.asyncio
    async def test_active_summary_cooldown_returns_skipped(self):
        """When the summary-provider cooldown is in effect, ``process()``
        must short-circuit before producing or persisting a summary.

        We stub the provider so the function reaches the cooldown check,
        then assert no archive call happened and the return value is
        ``"skipped"``.
        """
        session_id = "ses_summary_cooldown"

        history = _get_compaction_history(session_id)
        history.cooldown_remaining = 0
        # Park the cooldown 10 minutes in the future on the monotonic clock.
        history.summary_cooldown_until = time.monotonic() + 600

        # Provider must exist and return a usable client so we reach the
        # cooldown-gated summarisation step.
        fake_provider = MagicMock()
        fake_chat_message_cls = MagicMock(side_effect=lambda **kwargs: kwargs)
        fake_client = MagicMock()
        fake_provider.get = MagicMock(return_value=fake_client)

        archive_spy = AsyncMock(return_value="continue")

        with (
            patch.object(
                SessionCompaction,
                "_archive_and_write_summary",
                archive_spy,
            ),
            patch(
                "flocks.provider.provider.Provider",
                fake_provider,
            ),
            patch(
                "flocks.provider.provider.ChatMessage",
                fake_chat_message_cls,
            ),
        ):
            result = await SessionCompaction.process(
                session_id=session_id,
                parent_id="msg_root",
                messages=_empty_messages(),
                model_id="claude-3-5-sonnet",
                provider_id="anthropic",
            )

        assert result == "skipped"
        # Critical assertion: the archive/summary step must NOT run when
        # we're inside the cooldown window.
        archive_spy.assert_not_called()
