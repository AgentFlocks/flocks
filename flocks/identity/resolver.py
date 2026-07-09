from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flocks.auth.context import AuthUser
from flocks.identity.entry import DEFAULT_PERMISSION_MODE_BY_ENTRY, Entry
from flocks.identity.subject import Subject


@dataclass(slots=True)
class RequestIdentityContext:
    entry: Entry
    auth_source: str
    auth_user: Optional[AuthUser] = None
    subject_type: str = "human"
    verified: bool = True


class IdentityResolver:
    """Small resolver used by B1a to unify AuthUser -> Subject."""

    @staticmethod
    def resolve(context: RequestIdentityContext) -> Optional[Subject]:
        if context.auth_user is None:
            return None
        return Subject.from_auth_user(
            context.auth_user,
            entry=context.entry.value,
            auth_source=context.auth_source,
            permission_mode=DEFAULT_PERMISSION_MODE_BY_ENTRY[context.entry],
            subject_type=context.subject_type,
            verified=context.verified,
        )
