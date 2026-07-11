"""Per-webhook security context handed from the HTTP ingress to a channel plugin.

The context variable deliberately has channel-only scope.  It is set only
when a registered Pro ingress hook supplied a validated subject, so an OSS
deployment with no such hook keeps its historical channel behaviour.
"""

from __future__ import annotations

import contextvars
from typing import Optional

from flocks.identity.subject import Subject


_ingress_subject: contextvars.ContextVar[Optional[Subject]] = contextvars.ContextVar(
    "channel_ingress_subject",
    default=None,
)


def set_ingress_subject(subject: Subject) -> contextvars.Token:
    return _ingress_subject.set(subject)


def reset_ingress_subject(token: contextvars.Token) -> None:
    _ingress_subject.reset(token)


def get_ingress_subject() -> Optional[Subject]:
    return _ingress_subject.get()
