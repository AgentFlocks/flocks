from flocks.auth.context import AuthUser
from flocks.session.policy import SessionPolicy
from flocks.session.session import SessionInfo, SessionTime


def _session_with_shared(shared_user_id: str) -> SessionInfo:
    return SessionInfo(
        id="s1",
        slug="s1",
        project_id="p1",
        directory="/tmp",
        title="t",
        version="1.0.0",
        time=SessionTime(created=1, updated=1),
        owner_user_id="owner-1",
        metadata={"shared_read_access_user_ids": [shared_user_id]},
    )


def test_shared_user_can_read_but_cannot_write():
    shared_user = AuthUser(
        id="member-1",
        username="member",
        role="member",
        status="active",
        must_reset_password=False,
    )
    session = _session_with_shared(shared_user.id)

    assert SessionPolicy.can_read(session, shared_user) is True
    assert SessionPolicy.can_write(session, shared_user) is False
