from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping
from weakref import WeakKeyDictionary


class Entry(str, Enum):
    """Neutral labels for the transport that initiated an operation."""

    WEBUI = "webui"
    API = "api"
    CLI = "cli"
    TUI = "tui"
    CHANNEL = "channel"
    HEADLESS = "headless"
    ACP = "acp"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class ChannelIngressProvenance:
    """Neutral, runtime-only binding issued for a Gateway-delivered message.

    This is not an authorization decision or an identity claim.  Its opaque
    registry membership lets an extension distinguish a payload constructed by
    the Channel Gateway from a JSON/body field with the same shape.
    """

    entry: str
    channel_id: str
    account_id: str
    message_id: str
    sender_id: str
    chat_type: str


_channel_ingress_bindings: WeakKeyDictionary[ChannelIngressProvenance, tuple[object, object]] = WeakKeyDictionary()


def mint_channel_ingress_provenance(
    *,
    channel_id: str,
    account_id: str,
    message_id: str,
    sender_id: str,
    chat_type: str,
    message: object,
    evidence: object,
) -> ChannelIngressProvenance:
    """Mint the neutral gateway provenance carrier for one inbound message.

    The factory deliberately binds the exact runtime message/evidence objects;
    callers cannot reproduce that binding by serializing or embedding fields
    in a message body.  GatewayManager is its sole production caller.
    """

    provenance = ChannelIngressProvenance(
        entry=Entry.CHANNEL.value,
        channel_id=channel_id,
        account_id=account_id,
        message_id=message_id,
        sender_id=sender_id,
        chat_type=chat_type,
    )
    _channel_ingress_bindings[provenance] = (message, evidence)
    return provenance


def verify_channel_ingress_provenance(
    payload: Mapping[str, object],
) -> ChannelIngressProvenance | None:
    """Return a Gateway-issued provenance only when its payload binding holds.

    This performs mechanical capability and object-identity checks only.  It
    does not interpret the resulting fields as Flocks authentication,
    authorization, role, tenant, or policy data.
    """

    provenance = payload.get("provenance")
    if not isinstance(provenance, ChannelIngressProvenance):
        return None
    binding = _channel_ingress_bindings.get(provenance)
    if binding is None:
        return None
    message, evidence = binding
    if payload.get("message") is not message or payload.get("evidence") is not evidence:
        return None
    return provenance
