"""Who a model or tool call is made for, as extensions see it.

Every hook stage that runs for a session -- the prompt going out, the response
coming back, a tool about to run -- carries the same caller.  An extension that
scopes state to the caller (a redaction mapping minted for the request and used
again for the response, say) has to see one caller for all of them, so the
value is derived from one place: the session's execution profile, which is the
same record a tool payload's actor is built from.

The value is ``"<type>:<id>"``: ``human:<user id>`` for a person,
``service:<name>`` for anything acting on its own -- the API token clients
(``service:api-token-service``) and work with no subject at all
(``service:flocks``).  It describes the call; it grants nothing.  An extension
that authorises on it validates it against its own records.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from flocks.auth.context import API_TOKEN_SERVICE_USER_ID

#: The caller of work that no subject asked for: schedulers, background jobs,
#: a workflow started without a session.
SYSTEM_CALLER_ID = "service:flocks"


@dataclass(frozen=True)
class CallerIdentity:
    id: str
    type: str

    def as_metadata(self) -> Dict[str, str]:
        return {"callerID": self.id, "callerType": self.type}


SYSTEM_CALLER = CallerIdentity(id=SYSTEM_CALLER_ID, type="service")


def resolve_actor_subject(profile: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The subject id and type a session's execution profile names, if any."""

    subject_id = (
        str(
            profile.get("subject_id")
            or profile.get("owner_user_id")
            or profile.get("owner_username")
            or ""
        )
        .strip()
        or None
    )
    explicit_type = str(profile.get("subject_type") or "").strip().lower()
    if explicit_type:
        return subject_id, explicit_type
    if subject_id is None:
        return None, None
    if subject_id == API_TOKEN_SERVICE_USER_ID:
        return subject_id, "service"
    return subject_id, "human"


def caller_from_subject(subject_id: Optional[str], subject_type: Optional[str]) -> CallerIdentity:
    """The caller for a resolved subject; no subject is the system caller."""

    subject = str(subject_id or "").strip()
    if not subject:
        return SYSTEM_CALLER
    kind = str(subject_type or "").strip().lower() or "human"
    return CallerIdentity(id=f"{kind}:{subject}", type=kind)


def caller_from_profile(profile: Optional[Mapping[str, Any]]) -> CallerIdentity:
    if not isinstance(profile, Mapping):
        return SYSTEM_CALLER
    return caller_from_subject(*resolve_actor_subject(profile))


def caller_for_user(user: Any) -> CallerIdentity:
    """The caller for an authenticated request user (an ``AuthUser``)."""

    user_id = str(getattr(user, "id", "") or "").strip()
    if not user_id:
        return SYSTEM_CALLER
    if user_id == API_TOKEN_SERVICE_USER_ID:
        return caller_from_subject(user_id, "service")
    return caller_from_subject(user_id, "human")


async def session_caller(session_id: Optional[str]) -> CallerIdentity:
    """The caller of every model and tool call made for ``session_id``."""

    sid = str(session_id or "").strip()
    if not sid:
        return SYSTEM_CALLER
    from flocks.session.execution_profile import get_session_execution_profile

    try:
        profile = await get_session_execution_profile(sid)
    except Exception:
        # A session that cannot be read has no subject anyone vouched for; the
        # system caller is the honest answer, not a guess at the owner.
        return SYSTEM_CALLER
    return caller_from_profile(profile)


__all__ = [
    "CallerIdentity",
    "SYSTEM_CALLER",
    "SYSTEM_CALLER_ID",
    "caller_for_user",
    "caller_from_profile",
    "caller_from_subject",
    "resolve_actor_subject",
    "session_caller",
]
