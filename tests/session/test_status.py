"""
Tests for flocks/session/core/status.py

Covers:
- SessionStatus get/set/clear/clear_all
- All status types: idle, busy, retry, compacting, dreaming
- Default idle behavior
- Instance-scoped state isolation
"""

import pytest

from flocks.session.core.status import (
    SessionStatus,
    SessionStatusBusy,
    SessionStatusCompacting,
    SessionStatusDreaming,
    SessionStatusIdle,
    SessionStatusRetry,
)


@pytest.fixture(autouse=True)
def clean_status():
    """Clear all session statuses before each test."""
    SessionStatus.clear_all()
    yield
    SessionStatus.clear_all()


# ---------------------------------------------------------------------------
# Default behavior
# ---------------------------------------------------------------------------

class TestSessionStatusDefaults:
    def test_unknown_session_returns_idle(self):
        status = SessionStatus.get("nonexistent_session")
        assert isinstance(status, SessionStatusIdle)
        assert status.type == "idle"

    def test_list_empty_initially(self):
        result = SessionStatus.list()
        assert isinstance(result, dict)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Set / Get
# ---------------------------------------------------------------------------

class TestSessionStatusSetGet:
    def test_set_busy_and_get(self):
        SessionStatus.set("ses_1", SessionStatusBusy())
        status = SessionStatus.get("ses_1")
        assert isinstance(status, SessionStatusBusy)
        assert status.type == "busy"

    def test_set_retry_and_get(self):
        retry = SessionStatusRetry(attempt=2, message="Rate limited", next=1700000000000)
        SessionStatus.set("ses_2", retry)
        status = SessionStatus.get("ses_2")
        assert isinstance(status, SessionStatusRetry)
        assert status.attempt == 2
        assert status.message == "Rate limited"

    def test_set_compacting_and_get(self):
        SessionStatus.set("ses_3", SessionStatusCompacting())
        status = SessionStatus.get("ses_3")
        assert isinstance(status, SessionStatusCompacting)
        assert status.type == "compacting"

    def test_set_compacting_custom_message(self):
        SessionStatus.set("ses_4", SessionStatusCompacting(message="Summarizing..."))
        status = SessionStatus.get("ses_4")
        assert status.message == "Summarizing..."

    def test_set_dreaming_and_get(self):
        SessionStatus.set("ses_dream", SessionStatusDreaming(message="Dreaming..."))
        status = SessionStatus.get("ses_dream")
        assert isinstance(status, SessionStatusDreaming)
        assert status.message == "Dreaming..."

    def test_set_idle_removes_from_state(self):
        SessionStatus.set("ses_5", SessionStatusBusy())
        # Setting to idle should clean up the entry
        SessionStatus.set("ses_5", SessionStatusIdle())
        status = SessionStatus.get("ses_5")
        assert isinstance(status, SessionStatusIdle)
        # Should not appear in list
        all_statuses = SessionStatus.list()
        assert "ses_5" not in all_statuses

    def test_overwrite_status(self):
        SessionStatus.set("ses_6", SessionStatusBusy())
        SessionStatus.set("ses_6", SessionStatusCompacting())
        status = SessionStatus.get("ses_6")
        assert isinstance(status, SessionStatusCompacting)


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------

class TestSessionStatusClear:
    def test_clear_single_session(self):
        SessionStatus.set("ses_a", SessionStatusBusy())
        SessionStatus.set("ses_b", SessionStatusBusy())
        SessionStatus.clear("ses_a")
        assert isinstance(SessionStatus.get("ses_a"), SessionStatusIdle)
        # ses_b unaffected
        assert isinstance(SessionStatus.get("ses_b"), SessionStatusBusy)

    def test_clear_nonexistent_does_not_raise(self):
        SessionStatus.clear("never_existed")  # should not raise

    def test_clear_all(self):
        SessionStatus.set("s1", SessionStatusBusy())
        SessionStatus.set("s2", SessionStatusCompacting())
        SessionStatus.set("s3", SessionStatusRetry(attempt=1, message="err", next=0))
        SessionStatus.clear_all()
        assert len(SessionStatus.list()) == 0


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

class TestSessionStatusList:
    def test_list_shows_non_idle_sessions(self):
        SessionStatus.set("ses_x", SessionStatusBusy())
        SessionStatus.set("ses_y", SessionStatusCompacting())
        SessionStatus.set("ses_z", SessionStatusDreaming(message="Dreaming..."))
        result = SessionStatus.list()
        assert "ses_x" in result
        assert "ses_y" in result
        assert "ses_z" in result

    def test_dreaming_session_is_reported_as_busy(self):
        SessionStatus.set("ses_dream", SessionStatusDreaming(message="Dreaming..."))

        assert "ses_dream" in SessionStatus.get_busy_session_ids()

    def test_list_returns_copy(self):
        SessionStatus.set("ses_x", SessionStatusBusy())
        result = SessionStatus.list()
        result["injected"] = SessionStatusBusy()
        # Original state unaffected
        assert "injected" not in SessionStatus.list()

    def test_multiple_sessions_independent(self):
        SessionStatus.set("s1", SessionStatusBusy())
        SessionStatus.set("s2", SessionStatusRetry(attempt=1, message="retry", next=0))
        result = SessionStatus.list()
        assert result["s1"].type == "busy"
        assert result["s2"].type == "retry"


# ---------------------------------------------------------------------------
# Status model validation
# ---------------------------------------------------------------------------

class TestStatusModels:
    def test_idle_type_literal(self):
        idle = SessionStatusIdle()
        assert idle.type == "idle"

    def test_busy_type_literal(self):
        busy = SessionStatusBusy()
        assert busy.type == "busy"

    def test_retry_required_fields(self):
        retry = SessionStatusRetry(attempt=3, message="Overloaded", next=9999)
        assert retry.attempt == 3
        assert retry.message == "Overloaded"
        assert retry.next == 9999

    def test_compacting_default_message(self):
        from flocks.session.core.status import COMPACTING_DEFAULT_MESSAGE
        comp = SessionStatusCompacting()
        assert comp.message == COMPACTING_DEFAULT_MESSAGE

    def test_dreaming_requires_message(self):
        with pytest.raises(Exception):
            SessionStatusDreaming()

    def test_retry_missing_fields_raises(self):
        with pytest.raises(Exception):
            SessionStatusRetry()  # missing attempt, message, next
