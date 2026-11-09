"""
The licence file of the Python enterprise package, as this open release reads it: for the heartbeat,
which keeps the file current with what Console says, and for the Flocks Pro page, which shows its terms.

Read, not verified: the enterprise core judges the licence when the machine moves. What is read here
goes the way it reads it (go/internal/license/legacy.go in flockspro), so the page shows the terms the
core will hold the licence to: those of the token, or those of the patch in force (renewals, new
quotas and revocations come as patches Console signs).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: What a patch counts for when it names no time of its own.
_PATCH_SECONDS = 60 * 86400
#: Console signs its suite licences with the same key; one names the licence and is no patch.
_SUITE_GRANT = "flocks-console-suite-grant/1"
_TOKEN_FIELDS = ("key", "token", "activate_key")


def licence_files() -> list[Path]:
    """Where the Python enterprise package kept its licence: under FLOCKS_ROOT (its cloud checker),
    or under the home's .flocks whatever FLOCKS_ROOT says (its offline checker)."""
    override = (os.environ.get("FLOCKS_ROOT") or "").strip()
    files = [(Path(override).expanduser() if override else Path.home() / ".flocks") / "flockspro" / "license.json"]
    home = Path.home() / ".flocks" / "flockspro" / "license.json"
    if home not in files:
        files.append(home)
    return files


def read_licence_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def token_claims(token: Any) -> dict[str, Any] | None:
    """What a signed token says of itself, None for what does not read as one."""
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    return claims if isinstance(claims, dict) else None


def licence_token(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The file's licence token and what it says; empty ones when it has none that reads."""
    for field in _TOKEN_FIELDS:
        token = str(data.get(field) or "").strip()  # secret-guard: allow 误报：变量名，不是凭证
        claims = token_claims(token)
        if claims is not None:
            return token, claims
    return "", {}


def licence_id_of(data: dict[str, Any]) -> str:
    """The licence a file is of. Some keep the id only inside the token."""
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    found = data.get("license_id") or payload.get("license_id") or licence_token(data)[1].get("license_id")
    return str(found or "").strip()


def find_licence_file() -> tuple[Path, dict[str, Any]] | None:
    """The first file that names a licence, and what is in it."""
    for path in licence_files():
        data = read_licence_file(path)
        if data and licence_id_of(data):
            return path, data
    return None


def moment(value: Any) -> float | None:
    """A time a licence names, in seconds since the epoch, as the core reads it (legacyTime): a
    number, the same written out, or an ISO time; 0 for none, None for what does not read."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return 0
    if text.isdigit():
        return float(text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()


def _whole(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _signed_for_token(patch: dict[str, Any], token: str, token_issued: float) -> bool:
    """A patch for the licence as Console issued it before under the same id says nothing of this
    token: one names the token it was signed for; one from before they named it goes by time."""
    if "token_binding" in patch:
        signed = token.rsplit(".", 1)[0]
        return patch["token_binding"] == hashlib.sha256(signed.encode("utf-8")).hexdigest()[:32]
    issued = moment(patch.get("issued_at")) or 0
    return not (issued and token_issued and issued < token_issued)


def _patch_seconds(patch: dict[str, Any]) -> int:
    seconds = _whole(patch.get("ttl_seconds"))
    if seconds > 0:
        return seconds
    days = _whole(patch.get("patch_ttl_days"))
    return days * 86400 if days > 0 else _PATCH_SECONDS


def patch_in_force(data: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
    """The patch in the file that holds now: for this licence and this token, begun and not lapsed,
    the one with the highest serial (the later one on a tie), as the core picks it (inForce)."""
    token, claims = licence_token(data)
    patches = data.get("patches")
    if not token or not isinstance(patches, list):
        return None
    moment_now = time.time() if now is None else now
    token_issued = moment(claims.get("issued_at")) or 0  # secret-guard: allow 误报：变量名，不是凭证
    in_force: dict[str, Any] | None = None
    highest = -1
    for item in patches:
        patch = token_claims(item)
        if patch is None or patch.get("license_id") != claims.get("license_id") or patch.get("typ") == _SUITE_GRANT:
            continue
        if not _signed_for_token(patch, token, token_issued):
            continue
        issued = moment(patch.get("issued_at"))
        if issued is None or moment(patch.get("dynamic_expires_at")) is None:
            continue
        begins = max(_whole(patch.get("nbf")), _whole(patch.get("iat")))
        lapsed = bool(issued) and moment_now > issued + _patch_seconds(patch)
        serial = _whole(patch.get("serial"))
        if begins <= moment_now and not lapsed and serial >= highest:
            in_force, highest = patch, serial
    return in_force


def terms_in_force(data: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """What the patch in force says over the token: only what it names."""
    patch = patch_in_force(data, now=now)
    if patch is None:
        return {}
    terms: dict[str, Any] = {}
    status = str(patch.get("dynamic_status") or "").strip().lower()
    if status:
        terms["status"] = status
    if (moment(patch.get("dynamic_expires_at")) or 0) > 0:
        terms["expires_at"] = patch["dynamic_expires_at"]
    for field, named in (("max_admins", "dynamic_max_admins"), ("max_members", "dynamic_max_members")):
        if patch.get(named) is not None:
            terms[field] = patch[named]
    return terms
