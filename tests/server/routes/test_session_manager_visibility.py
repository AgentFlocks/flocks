from types import SimpleNamespace

import pytest

from flocks.server.routes.session import _is_hidden_from_session_manager


def test_sessions_flagged_by_their_owner_are_hidden():
    session = SimpleNamespace(agent="rex", metadata={"hideFromSessionManager": True})
    assert _is_hidden_from_session_manager(session)


@pytest.mark.parametrize(
    "agent,metadata",
    [
        ("rex", None),
        ("rex", {}),
        ("rex", {"hideFromSessionManager": False}),
        # Agent names and extension metadata alone never hide a session;
        # the extension that owns it has to set the flag.
        ("dedicated-agent", {"session_scope": "extension", "extension_run_id": "run-1"}),
    ],
)
def test_other_sessions_remain_visible(agent, metadata):
    assert not _is_hidden_from_session_manager(SimpleNamespace(agent=agent, metadata=metadata))
