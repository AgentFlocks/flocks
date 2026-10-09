"""Transactional SOC case domain for the existing Flocks plugin runtime.

Call the synchronous interface on a worker thread. External tool execution is
owned by the adapter and happens strictly between claim_action and finish_action.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4


MAX_INPUT_BYTES = 65536
MAX_RECORD_BYTES = 32768
MAX_RESPONSE_BYTES = 80 * 1024
MAX_AUDIT_BYTES = 52000
MAX_PAGE = 100
MAX_LINKS = 50
STATES = ("open", "investigating", "responding", "review", "closed")
KINDS = ("evidence", "hypotheses", "actions", "reviews", "improvements")
MUTATIONS = frozenset((
    "create", "update", "add_evidence", "add_hypothesis", "decide_hypothesis",
    "propose_action", "decide_action", "verify_action", "record_review",
    "add_improvement", "update_improvement", "add_improvement_evidence", "close", "reopen",
))
CLOSED_OPERATIONS = frozenset(("reopen", "add_improvement", "update_improvement", "add_improvement_evidence"))
HUMAN_OPERATIONS = frozenset(("decide_action", "verify_action"))
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: set[str] = set()
# Keys are compared without case or separators, so ``Set-Cookie``,
# ``req_cookie`` and ``x-api-key`` are recognized too.
_SECRET_KEY_NAMES = frozenset((
    "password", "passwd", "pwd", "secret", "clientsecret", "apikey", "accesskey", "secretkey",
    "token", "accesstoken", "refreshtoken", "authorization", "proxyauthorization", "cookie",
    "setcookie", "privatekey", "credential", "credentials",
))
_SECRET_KEY_ENDINGS = ("password", "passwd", "secret", "token", "cookie", "authorization", "apikey", "privatekey")
_SECRET_TEXT = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|"
    r"\b(?:Bearer\s+[A-Za-z0-9._~+/=-]{12,}|"
    r"(?:password|passwd|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[^\s,;]+)|"
    r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.I,
)
# A credential-bearing HTTP header in raw header text: at the start of the
# text or of a line (a real or an escaped line break), or as a quoted key.
_SECRET_HEADER = re.compile(
    r"((?:^|[\r\n]|\\[rn]|[{,\"'])[ \t]*[\"']?(?:proxy-authorization|authorization|set-cookie|cookie|"
    r"x-api-key|api-key|x-auth-token|x-access-token|x-amz-security-token)[\"']?[ \t]*:)[^\r\n\\]*",
    re.I,
)
_BASIC_AUTH = re.compile(r"\bBasic\s+([A-Za-z0-9+/]{4,}={0,2})")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid(value, field="id"):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise CaseError("invalid_input", f"{field} must be a UUID") from None


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise CaseError("invalid_input", "Input must be finite JSON data") from None


def _bounded(value, limit=MAX_INPUT_BYTES):
    encoded = _json(value)
    if len(encoded.encode("utf-8")) > limit:
        raise CaseError("input_too_large", f"JSON data exceeds {limit} bytes", 413)
    return encoded


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(data, name, *, required=False, maximum=8000, default=""):
    value = data.get(name, default)
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise CaseError("invalid_input", f"{name} must be text of at most {maximum} characters")
    value = value.strip()
    if required and not value:
        raise CaseError("invalid_input", f"{name} is required")
    return value


def _choice(data, name, values, default=None):
    value = data.get(name, default)
    if not isinstance(value, str) or value not in values:
        raise CaseError("invalid_input", f"{name} must be one of {', '.join(values)}")
    return value


def _time(value, *, name, default=None):
    if value in (None, ""):
        return default
    if not isinstance(value, str) or len(value) > 64:
        raise CaseError("invalid_input", f"{name} must be an ISO timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise CaseError("invalid_input", f"{name} must include a timezone") from None


def _secret_key(key):
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in _SECRET_KEY_NAMES or normalized.endswith(_SECRET_KEY_ENDINGS)


def _basic_credentials(match):
    """``Basic`` followed by base64 of ``user:password`` (not just any word)."""
    encoded = match.group(1)
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return False
    return ":" in decoded and decoded.isprintable()


def _inline_secret(text):
    return bool(_SECRET_TEXT.search(text) or _SECRET_HEADER.search(text)
                or any(_basic_credentials(match) for match in _BASIC_AUTH.finditer(text)))


def _redact_text(text):
    text = _SECRET_HEADER.sub(lambda match: match.group(1) + " [REDACTED]", text)
    text = _BASIC_AUTH.sub(lambda match: "Basic [REDACTED]" if _basic_credentials(match) else match.group(0), text)
    return _SECRET_TEXT.sub("[REDACTED]", text)


def _secret(value, *, redact=False, depth=0):
    if depth > 12:
        raise CaseError("invalid_input", "JSON nesting exceeds 12 levels")
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if _secret_key(key):
                if not redact:
                    raise CaseError("credentials_forbidden", "Credentials belong in the registered integration, not the case")
                result[key] = "[REDACTED]"
            else:
                result[key] = _secret(item, redact=redact, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_secret(item, redact=redact, depth=depth + 1) for item in value]
    if isinstance(value, str) and _inline_secret(value):
        if not redact:
            raise CaseError("credentials_forbidden", "Inline credentials cannot be stored in a case")
        return _redact_text(value)
    return value


def _object(row):
    if row is None:
        return None
    result = dict(row.items())
    for key, value in result.items():
        if isinstance(value, datetime):
            result[key] = value.astimezone(timezone.utc).isoformat()
        elif isinstance(value, UUID):
            result[key] = str(value)
    for key in ("data", "closure", "response"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


def _record(row):
    item = _object(row)
    if item is None:
        return None
    body = item.pop("data")
    return {**body, **item}


def _plan(action):
    return {key: action[key] for key in (
        "title", "tool_name", "parameters", "target", "reason", "evidence_ids",
        "reversible", "rollback_plan", "verification_plan",
    )}


@dataclass(frozen=True)
class Actor:
    user_id: str
    kind: str
    is_admin: bool = False


class CaseError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class CaseStore:
    def __init__(self, database=None):
        if database is None:
            from flocks.db import sync_query
            database = sync_query
        self.database = database

    def dispatch(self, operation, actor, case_id="", expected_version=None,
                 idempotency_key="", data=None):
        self._actor(actor)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise CaseError("invalid_input", "data must be an object")
        _bounded(data)
        if operation not in MUTATIONS and operation not in ("list", "get", "recall"):
            raise CaseError("invalid_operation", "Unknown SOC case operation")
        if operation in HUMAN_OPERATIONS:
            self._human(actor)
        self.ensure_schema()
        if operation in ("list", "get", "recall"):
            return self._read(operation, actor, case_id, data)
        _secret(data)
        request = {"operation": operation, "case_id": case_id,
                   "expected_version": expected_version, "data": data, "actor_kind": actor.kind}
        with self.database.connect(timeout=15) as db:
            replay = self._idempotency(db, actor, idempotency_key, request)
            if replay is not None:
                self._case(db, replay["case"]["id"], actor)
                return replay
            if operation == "create":
                case, record, changed = self._create(db, actor, data)
                if not changed:
                    result = {"case": case, "record": record}
                    self._remember(db, actor, idempotency_key, request, result)
                    return result
            else:
                case = self._case(db, case_id, actor, lock=True)
                self._version(case, expected_version)
                if case["status"] == "closed" and operation not in CLOSED_OPERATIONS:
                    raise CaseError("case_closed", "Reopen the case before making changes", 409)
                record = getattr(self, "_" + operation)(db, actor, case, data)
            return self._commit(db, actor, case, record, operation, idempotency_key, request)

    def claim_action(self, actor, case_id, action_id, expected_version,
                     idempotency_key, session_id=""):
        self._actor(actor)
        session_id = _text({"session_id": session_id}, "session_id", maximum=256)
        action_id = _uuid(action_id, "action_id")
        self.ensure_schema()
        request = {"operation": "claim_action", "case_id": case_id, "action_id": action_id,
                   "expected_version": expected_version, "session_id": session_id,
                   "actor_kind": actor.kind}
        with self.database.connect(timeout=15) as db:
            replay = self._idempotency(db, actor, idempotency_key, request)
            case = self._case(db, case_id, actor, lock=True)
            action = self._get_record(db, case["id"], action_id, "actions")
            if replay is not None:
                return {"case": case, "action": action, "claim_token": "", "claimed": False}
            self._version(case, expected_version)
            if case["status"] == "closed":
                raise CaseError("case_closed", "A closed case cannot execute an action", 409)
            if action["approval_status"] != "approved":
                raise CaseError("approval_required", "A human must approve this exact action plan", 409)
            if action["plan_hash"] != _hash(_plan(action)) or action.get("approved_plan_hash") != action["plan_hash"]:
                raise CaseError("plan_changed", "The approved action plan does not match its frozen payload", 409)
            bound = action.get("execution_session_id", "")
            if bound and bound != session_id:
                raise CaseError("session_mismatch", "This execution is bound to a different session", 403)
            if action["execution_status"] not in ("not_started", "blocked"):
                result = {"case": case, "action": action, "claim_token": "", "claimed": False}
                self._remember(db, actor, idempotency_key, request, result)
                return result
            # The preceding attempt remains in the append-only timeline. Its
            # receipt and verification cannot describe a newly claimed attempt.
            for field in ("finished_at", "receipt", "verification_summary", "verification_evidence_ids",
                          "verified_by", "verified_at", "reconciled_by", "reconciled_at",
                          "reconciliation_outcome", "reconciliation_conflict", "conflicting_receipt",
                          "conflicting_receipt_at"):
                action.pop(field, None)
            token = str(uuid4())
            action.update(execution_status="running", verification_status="unverified", claim_token=token,
                          claimed_at=_now(), execution_session_id=session_id,
                          execution_actor_user_id=actor.user_id, execution_actor_kind=actor.kind,
                          attempt=int(action.get("attempt", 0)) + 1)
            action = self._save_record(db, action)
            case["status"] = "responding"
            return self._commit(db, actor, case, action, "claim_action", idempotency_key, request,
                                extra={"action": action, "claim_token": token, "claimed": True})

    def finish_action(self, actor, case_id, action_id, claim_token, outcome, receipt):
        self._actor(actor)
        action_id = _uuid(action_id, "action_id")
        claim_token = _uuid(claim_token, "claim_token")
        outcome = _choice({"outcome": outcome}, "outcome", ("succeeded", "blocked", "unknown", "failed", "unsupported"))
        if not isinstance(receipt, dict):
            raise CaseError("invalid_input", "receipt must be an object")
        receipt = _secret(receipt, redact=True)
        encoded_receipt = _bounded(receipt)
        if len(encoded_receipt.encode("utf-8")) > 8000:
            receipt = {"truncated": True, "sha256": hashlib.sha256(encoded_receipt.encode()).hexdigest(),
                       "preview": encoded_receipt.encode()[:3000].decode("utf-8", errors="ignore")}
        self.ensure_schema()
        # One token can receive a later provider outcome. Deduplicate identical
        # receipts, while preserving new information instead of refusing it as a
        # changed client request (for example unknown followed by late success).
        key = "finish:" + claim_token + ":" + _hash({"outcome": outcome, "receipt": receipt})[:24]
        request = {"operation": "finish_action", "case_id": case_id, "action_id": action_id,
                   "claim_token": claim_token, "outcome": outcome, "receipt": receipt,
                   "actor_kind": actor.kind}
        with self.database.connect(timeout=15) as db:
            replay = self._idempotency(db, actor, key, request)
            case = self._case(db, case_id, actor, lock=True)
            action = self._get_record(db, case["id"], action_id, "actions")
            if replay is not None:
                return replay
            if action.get("claim_token") != claim_token or action.get("execution_actor_user_id") != actor.user_id or action.get("execution_actor_kind") != actor.kind:
                raise CaseError("claim_mismatch", "The execution claim does not belong to this caller", 409)
            if action["execution_status"] != "running":
                # A human may have reconciled an ambiguous execution while its
                # original caller was still waiting. A late receipt cannot undo it.
                conflict = (action.get("reconciliation_outcome") == "confirmed_not_executed"
                            and outcome == "succeeded"
                            and not (action["execution_status"] == "succeeded" and action["verification_status"] == "verified"))
                if conflict:
                    action.update(execution_status="unknown", verification_status="inconclusive",
                                  reconciliation_conflict=True, conflicting_receipt=receipt, conflicting_receipt_at=_now(),
                                  approval_status="approved")
                    action = self._save_record(db, action)
                    if case["status"] == "closed":
                        case["closure"].update(active=False, withdrawn_at=_now(),
                            withdrawal_reason="A late successful execution receipt contradicted the not-executed reconciliation")
                        case.update(status="investigating", closed_at=None)
                return self._commit(db, actor, case, action, "late_receipt", key, request,
                    audit={"late_outcome": outcome, "late_receipt": receipt, "reconciliation_conflict": conflict})
            action.update(execution_status=outcome, finished_at=_now(), receipt=receipt)
            action = self._save_record(db, action)
            return self._commit(db, actor, case, action, "finish_action", key, request)

    def ensure_schema(self):
        identity = str(self.database.identity())
        if identity in _SCHEMA_READY:
            return
        with _SCHEMA_LOCK:
            if identity in _SCHEMA_READY:
                return
            with self.database.connect(timeout=15) as db:
                db.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema() || ':soc-case-schema'))").fetchone()
                db.execute("""CREATE TABLE IF NOT EXISTS soc_cases (
                    id UUID PRIMARY KEY, owner_user_id TEXT NOT NULL, title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL,
                    status TEXT NOT NULL, version BIGINT NOT NULL DEFAULT 0,
                    assignee TEXT NOT NULL DEFAULT '', source_alert_id TEXT,
                    closure JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), closed_at TIMESTAMPTZ,
                    CHECK (status IN ('open','investigating','responding','review','closed')),
                    CHECK (severity IN ('low','medium','high','critical')))""")
                db.execute("""CREATE TABLE IF NOT EXISTS soc_case_records (
                    id UUID PRIMARY KEY, case_id UUID NOT NULL REFERENCES soc_cases(id),
                    kind TEXT NOT NULL, data JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CHECK (kind IN ('evidence','hypotheses','actions','reviews','improvements')))""")
                db.execute("""CREATE TABLE IF NOT EXISTS soc_case_events (
                    id UUID PRIMARY KEY, case_id UUID NOT NULL REFERENCES soc_cases(id),
                    version BIGINT NOT NULL, operation TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL, actor_kind TEXT NOT NULL,
                    record_id UUID, summary TEXT NOT NULL, details JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(case_id,version))""")
                db.execute("""CREATE TABLE IF NOT EXISTS soc_case_requests (
                    id UUID PRIMARY KEY, actor_user_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL, case_id UUID NOT NULL REFERENCES soc_cases(id),
                    response JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(actor_user_id,idempotency_key))""")
                for sql in (
                    "CREATE INDEX IF NOT EXISTS idx_soc_cases_owner ON soc_cases(owner_user_id,status,updated_at DESC,id)",
                    "CREATE INDEX IF NOT EXISTS idx_soc_cases_owner_seek ON soc_cases(owner_user_id,updated_at DESC,id DESC)",
                    "CREATE INDEX IF NOT EXISTS idx_soc_cases_seek ON soc_cases(updated_at DESC,id DESC)",
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_soc_cases_alert ON soc_cases(owner_user_id,source_alert_id) WHERE source_alert_id IS NOT NULL",
                    "CREATE INDEX IF NOT EXISTS idx_soc_case_records_case ON soc_case_records(case_id,created_at DESC,id)",
                    "CREATE INDEX IF NOT EXISTS idx_soc_case_records_kind ON soc_case_records(case_id,kind,created_at DESC,id)",
                    "CREATE INDEX IF NOT EXISTS idx_soc_case_events_case ON soc_case_events(case_id,version DESC)",
                ):
                    db.execute(sql)
            if len(_SCHEMA_READY) >= 256:
                _SCHEMA_READY.clear()
            _SCHEMA_READY.add(identity)

    @staticmethod
    def _actor(actor):
        if not isinstance(actor, Actor) or not isinstance(actor.user_id, str) or not actor.user_id.strip() or len(actor.user_id) > 256:
            raise CaseError("unauthenticated", "An authenticated principal is required", 401)
        if actor.kind not in ("human", "agent", "service") or not isinstance(actor.is_admin, bool):
            raise CaseError("unauthenticated", "A trusted actor kind is required", 401)

    @staticmethod
    def _human(actor):
        if actor.kind != "human":
            raise CaseError("human_required", "This decision requires an authenticated human", 403)

    @staticmethod
    def _version(case, expected):
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise CaseError("version_required", "expected_version must be a positive integer")
        if case["version"] != expected:
            raise CaseError("version_conflict", f"Case changed; current version is {case['version']}", 409)

    def _case(self, db, case_id, actor, lock=False):
        case_id = _uuid(case_id, "case_id")
        row = db.execute("SELECT * FROM soc_cases WHERE id=?::uuid" + (" FOR UPDATE" if lock else ""), (case_id,)).fetchone()
        case = _object(row)
        if case is None or (case["owner_user_id"] != actor.user_id and not actor.is_admin):
            raise CaseError("not_found", "Case not found", 404)
        return case

    def _idempotency(self, db, actor, key, request):
        if not isinstance(key, str) or not key.strip() or len(key) > 128 or "\x00" in key:
            raise CaseError("idempotency_required", "idempotency_key must contain 1 to 128 characters")
        db.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema() || ':soc-request:' || ?))", (actor.user_id + ':' + key,)).fetchone()
        row = db.execute("SELECT request_hash,response FROM soc_case_requests WHERE actor_user_id=? AND idempotency_key=?", (actor.user_id, key)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != _hash(request):
            raise CaseError("idempotency_conflict", "This idempotency key was used for a different request", 409)
        return _object(row)["response"]

    @staticmethod
    def _remember(db, actor, key, request, result):
        db.execute("""INSERT INTO soc_case_requests(id,actor_user_id,idempotency_key,request_hash,case_id,response)
            VALUES (?::uuid,?,?,?,?::uuid,?::jsonb)""", (str(uuid4()), actor.user_id, key, _hash(request), result["case"]["id"], _json(result)))

    def _commit(self, db, actor, case, record, operation, key, request, extra=None, audit=None):
        archived = record.pop("_pending_archive", None) if record else None
        _bounded({key: case[key] for key in ("title", "summary", "assignee")}, 8192)
        case = _object(db.execute("""UPDATE soc_cases SET title=?,summary=?,severity=?,status=?,assignee=?,
            closure=?::jsonb,closed_at=?::text::timestamptz,version=version+1,updated_at=clock_timestamp()
            WHERE id=?::uuid RETURNING *""", (case["title"], case["summary"], case["severity"], case["status"],
                case["assignee"], _json(case["closure"]), case.get("closed_at"), case["id"])).fetchone())
        details = {"record_id": record.get("id") if record else None}
        if record and record.get("kind") == "evidence":
            details["record"] = {key: record.get(key) for key in ("id", "kind", "title", "source", "content_hash", "supersedes_id", "purpose", "improvement_id")}
        elif record and record.get("kind"):
            # Mutable projections have an immutable snapshot per domain change:
            # revising a rationale or verification cannot erase its old basis.
            details["record"] = {key: value for key, value in record.items()
                                 if key not in ("receipt", "conflicting_receipt")}
        elif operation == "update":
            details["case"] = {key: case[key] for key in ("title", "summary", "severity", "assignee", "status")}
        if operation in ("claim_action", "finish_action", "verify_action", "decide_action"):
            details.update({k: record.get(k) for k in ("approval_status", "execution_status", "verification_status", "attempt", "claim_token", "receipt") if k in record})
        if operation in ("close", "reopen"):
            details["closure"] = case["closure"]
        elif operation == "late_receipt":
            # Full closure content is already immutable in its close event.
            # Retain only the disposition change beside a possibly large receipt.
            details["closure"] = {key: case["closure"][key] for key in
                ("active", "source_version", "withdrawn_at", "withdrawal_reason") if key in case["closure"]}
        if archived:
            details["archived_action_fields"] = archived["fields"]
        if audit:
            details.update(audit)
        db.execute("""INSERT INTO soc_case_events(id,case_id,version,operation,actor_user_id,actor_kind,record_id,summary,details)
            VALUES (?::uuid,?::uuid,?,?,?,?,?::uuid,?,?::jsonb)""", ((archived["event_id"] if archived else str(uuid4())), case["id"], case["version"], operation,
                actor.user_id, actor.kind, record.get("id") if record and record.get("kind") else None,
                operation.replace("_", " "), _bounded(details, MAX_AUDIT_BYTES)))
        result = {"case": case, "record": record}
        if extra:
            result.update(extra)
            if "action" in extra:
                result.pop("record", None)
        _bounded(result, MAX_RESPONSE_BYTES)
        self._remember(db, actor, key, request, result)
        return result

    @staticmethod
    def _pagination(data):
        limit = data.get("limit", 50)
        offset = 0 if data.get("cursor") is not None else data.get("offset", 0)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE:
            raise CaseError("invalid_input", f"limit must be between 1 and {MAX_PAGE}")
        if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 10000:
            raise CaseError("invalid_input", "offset must be between 0 and 10000")
        return limit, offset

    @staticmethod
    def _cursor(raw, scope, *, case_version=None, timeline=False):
        if raw is None:
            return None
        if not isinstance(raw, str) or not 1 <= len(raw) <= 512:
            raise CaseError("invalid_cursor", "Cursor must be a bounded opaque string")
        if raw == "end":
            return "end"
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
                raise ValueError()
            token = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True))
            expected = {"v", "scope", "position"} | ({"case_version"} if case_version is not None else set())
            if not isinstance(token, dict) or set(token) != expected or type(token["v"]) is not int or token["v"] != 1 or token["scope"] != scope:
                raise ValueError()
            position = token["position"]
            if not isinstance(position, dict):
                raise ValueError()
            if timeline:
                if set(position) != {"version"} or type(position["version"]) is not int or not 1 <= position["version"] <= 2**63 - 1:
                    raise ValueError()
            else:
                if set(position) != {"at", "id"} or not isinstance(position["id"], str):
                    raise ValueError()
                position = {"at": _time(position["at"], name="cursor time"), "id": _uuid(position["id"])}
                if position["at"] is None:
                    raise ValueError()
            if case_version is not None and (type(token["case_version"]) is not int or token["case_version"] < 1):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error):
            raise CaseError("invalid_cursor", "Cursor is malformed or belongs to a different query") from None
        if case_version is not None and token["case_version"] != case_version:
            raise CaseError("cursor_stale", "Case changed; refresh the first page", 409)
        return position

    @staticmethod
    def _cursor_token(scope, position, *, case_version=None):
        token = {"v": 1, "scope": scope, "position": position}
        if case_version is not None:
            token["case_version"] = case_version
        return base64.urlsafe_b64encode(_json(token).encode()).decode().rstrip("=")

    def _read(self, operation, actor, case_id, data):
        limit, offset = self._pagination(data)
        _, timeline_offset = self._pagination({"limit": limit, "offset": data.get("timeline_offset", offset), "cursor": data.get("timeline_cursor")})
        # Set isolation when the facade starts its transaction: it executes its
        # timeout configuration query before exposing the connection to callers.
        with self.database.connect(timeout=10, readonly=True, isolation="repeatable_read") as db:
            if operation == "get":
                case = self._case(db, case_id, actor)
                result = {"case": case, **{kind: [] for kind in KINDS}, "timeline": [],
                          "limit": limit, "offset": offset, "timeline_offset": timeline_offset,
                          "next_offset": None, "timeline_next_offset": None,
                          "next_cursor": None, "timeline_next_cursor": None}
                scope = {"operation": operation, "actor": actor.user_id, "admin": actor.is_admin, "case_id": case["id"]}
                record_scope = _hash({**scope, "stream": "records"})
                timeline_scope = _hash({**scope, "stream": "timeline"})
                record_cursor = self._cursor(data.get("cursor"), record_scope, case_version=case["version"])
                timeline_cursor = self._cursor(data.get("timeline_cursor"), timeline_scope, case_version=case["version"], timeline=True)
                result["counts"] = {kind: 0 for kind in KINDS}
                for row in db.execute("SELECT kind,count(*) AS total FROM soc_case_records WHERE case_id=?::uuid GROUP BY kind", (case["id"],)).fetchall():
                    result["counts"][row["kind"]] = row["total"]
                result["counts"]["records"] = sum(result["counts"].values())
                result["counts"]["timeline"] = case["version"]
                budget = MAX_RESPONSE_BYTES - len(_json(result).encode("utf-8")) - 1024
                record_where, record_params = "case_id=?::uuid", [case["id"]]
                record_tail = " LIMIT ? OFFSET ?"
                if isinstance(record_cursor, dict):
                    record_where += " AND (created_at,id)<(?::text::timestamptz,?::uuid)"
                    record_params.extend((record_cursor["at"], record_cursor["id"]))
                    record_tail = " LIMIT ?"
                record_params.append(limit + 1)
                if not isinstance(record_cursor, dict):
                    record_params.append(offset)
                records = [] if record_cursor == "end" else db.execute("SELECT * FROM soc_case_records WHERE " + record_where + " ORDER BY created_at DESC,id DESC" + record_tail, record_params).fetchall()
                included = 0
                last_record = None
                for row in records[:limit]:
                    item = _record(row)
                    cost = len(_json(item).encode("utf-8")) + 1
                    if cost > budget:
                        break
                    result[item["kind"]].append(item)
                    budget -= cost
                    included += 1
                    last_record = item
                if included < len(records):
                    if last_record is None:
                        raise CaseError("response_too_large", "A complete record cannot fit this case page", 413)
                    result["next_cursor"] = self._cursor_token(record_scope, {"at": last_record["created_at"], "id": last_record["id"]}, case_version=case["version"])
                    if record_cursor is None and offset + included <= 10000:
                        result["next_offset"] = offset + included
                timeline_where, timeline_params = "case_id=?::uuid", [case["id"]]
                timeline_tail = " LIMIT ? OFFSET ?"
                if isinstance(timeline_cursor, dict):
                    timeline_where += " AND version<?"
                    timeline_params.append(timeline_cursor["version"])
                    timeline_tail = " LIMIT ?"
                timeline_params.append(limit + 1)
                if not isinstance(timeline_cursor, dict):
                    timeline_params.append(timeline_offset)
                events = [] if timeline_cursor == "end" else db.execute("SELECT * FROM soc_case_events WHERE " + timeline_where + " ORDER BY version DESC" + timeline_tail, timeline_params).fetchall()
                included = 0
                last_event = None
                for row in events[:limit]:
                    event = _object(row)
                    if isinstance(event["details"], str):
                        event["details"] = json.loads(event["details"])
                    cost = len(_json(event).encode("utf-8")) + 1
                    if cost > budget:
                        break
                    result["timeline"].append(event)
                    budget -= cost
                    included += 1
                    last_event = event
                if included < len(events):
                    # Records may consume this page's budget before any event
                    # fits; retain the first pending event rather than skip it.
                    before = last_event["version"] if last_event else events[0]["version"] + 1
                    result["timeline_next_cursor"] = self._cursor_token(timeline_scope, {"version": before}, case_version=case["version"])
                    if timeline_cursor is None and timeline_offset + included <= 10000:
                        result["timeline_next_offset"] = timeline_offset + included
                    if last_event is None and last_record is None:
                        raise CaseError("response_too_large", "A complete timeline event cannot fit this case page", 413)
                _bounded(result, MAX_RESPONSE_BYTES)
                return result
            predicates, params = [], []
            if not actor.is_admin:
                predicates.append("owner_user_id=?")
                params.append(actor.user_id)
            if operation == "recall":
                predicates.append("status='closed' AND closure->>'active'='true'")
            elif data.get("status"):
                predicates.append("status=?")
                params.append(_choice(data, "status", STATES))
            query = _text(data, "query", maximum=200)
            if query:
                predicates.append("(title ILIKE ? OR summary ILIKE ?)")
                params.extend(("%" + query + "%", "%" + query + "%"))
            where = " WHERE " + " AND ".join(predicates) if predicates else ""
            scope = _hash({"operation": operation, "actor": actor.user_id, "admin": actor.is_admin,
                           "query": query, "status": data.get("status") if operation == "list" else None})
            cursor = self._cursor(data.get("cursor"), scope)
            total = db.execute("SELECT count(*) FROM soc_cases" + where, params).fetchone()[0]
            if isinstance(cursor, dict):
                predicates.append("(updated_at,id)<(?::text::timestamptz,?::uuid)")
                params.extend((cursor["at"], cursor["id"]))
                where = " WHERE " + " AND ".join(predicates)
            tail = " LIMIT ?" if isinstance(cursor, dict) else " LIMIT ? OFFSET ?"
            values = (*params, limit + 1) if isinstance(cursor, dict) else (*params, limit + 1, offset)
            rows = [] if cursor == "end" else db.execute("SELECT * FROM soc_cases" + where + " ORDER BY updated_at DESC,id DESC" + tail, values).fetchall()
            result = {"cases": [], "total": total, "limit": limit, "offset": offset, "next_offset": None, "next_cursor": None}
            budget = MAX_RESPONSE_BYTES - len(_json(result).encode("utf-8")) - 1024
            for row in rows[:limit]:
                item = _object(row)
                cost = len(_json(item).encode("utf-8")) + 1
                if cost > budget:
                    break
                result["cases"].append(item)
                budget -= cost
            if len(result["cases"]) < len(rows):
                if not result["cases"]:
                    raise CaseError("response_too_large", "A complete case cannot fit this page", 413)
                last = result["cases"][-1]
                result["next_cursor"] = self._cursor_token(scope, {"at": last["updated_at"], "id": last["id"]})
                if cursor is None and offset + len(result["cases"]) <= 10000:
                    result["next_offset"] = offset + len(result["cases"])
            _bounded(result, MAX_RESPONSE_BYTES)
            return result

    @staticmethod
    def _get_record(db, case_id, record_id, kind):
        item = _record(db.execute("SELECT * FROM soc_case_records WHERE id=?::uuid AND case_id=?::uuid AND kind=?", (_uuid(record_id), case_id, kind)).fetchone())
        if item is None:
            raise CaseError("record_not_found", f"{kind} record not found in this case", 404)
        return item

    @staticmethod
    def _insert_record(db, case_id, kind, data):
        encoded = _bounded(data, MAX_RECORD_BYTES)
        return _record(db.execute("INSERT INTO soc_case_records(id,case_id,kind,data) VALUES (?::uuid,?::uuid,?,?::jsonb) RETURNING *", (str(uuid4()), case_id, kind, encoded)).fetchone())

    @staticmethod
    def _save_record(db, record):
        data = {k: v for k, v in record.items() if k not in ("id", "case_id", "kind", "created_at", "updated_at")}
        archived, event_id = {}, str(uuid4())
        if record["kind"] == "actions":
            references = dict(data.get("audit_refs", {}))
            for field, reference in list(references.items()):
                marker = f"Full {field} is retained in audit event {reference['event_id']}."
                if data.get(field) != marker and data.get(field) != reference:
                    references.pop(field)

            def archive(field):
                value = data[field]
                encoded = _json(value).encode()
                reference = {"archived": True, "event_id": event_id,
                             "field": "archived_action_fields." + field,
                             "sha256": _hash(value), "bytes": len(encoded)}
                archived[field] = value
                references[field] = reference
                data[field] = (f"Full {field} is retained in audit event {event_id}."
                               if isinstance(value, str) else reference)
                data["audit_refs"] = references

            # Older approvals were limited in characters, not UTF-8 bytes.
            # Archive their complete text in this same transaction before a
            # real provider receipt needs the reserved projection space.
            for field in ("approval_reason", "verification_summary"):
                if field in data and len(_json(data[field]).encode()) > 4096:
                    archive(field)
            if references:
                data["audit_refs"] = references
            else:
                data.pop("audit_refs", None)
            for field in ("receipt", "conflicting_receipt", "approval_reason", "verification_summary"):
                if len(_json(data).encode()) <= MAX_RECORD_BYTES:
                    break
                if field in data and field not in archived:
                    archive(field)
        encoded = _bounded(data, MAX_RECORD_BYTES)
        saved = _record(db.execute("UPDATE soc_case_records SET data=?::jsonb,updated_at=clock_timestamp() WHERE id=?::uuid AND case_id=?::uuid RETURNING *", (encoded, record["id"], record["case_id"])).fetchone())
        if archived:
            # Transient metadata is consumed by _commit and never enters the
            # action projection or tool response. Every reference names that
            # exact immutable event; transaction rollback preserves both sides.
            saved["_pending_archive"] = {"event_id": event_id, "fields": archived}
        return saved

    @staticmethod
    def _evidence(db, case_id, data, *, required=True, fresh_after=None, improvement_id=None):
        values = data.get("evidence_ids", [])
        if not isinstance(values, list) or len(values) > MAX_LINKS or (required and not values):
            raise CaseError("invalid_evidence", f"evidence_ids must contain {'1' if required else '0'} to {MAX_LINKS} identifiers")
        ids = list(dict.fromkeys(_uuid(value, "evidence_id") for value in values))
        if not ids:
            return []
        rows = db.execute("SELECT id,created_at,data->>'observed_at' AS observed_at,data->>'purpose' AS purpose,data->>'improvement_id' AS improvement_id FROM soc_case_records WHERE case_id=?::uuid AND kind='evidence' AND id=ANY(?::uuid[])", (case_id, [UUID(item) for item in ids])).fetchall()
        if len(rows) != len(ids):
            raise CaseError("invalid_evidence", "Every evidence reference must belong to this case")
        if any(row["purpose"] == "improvement" and row["improvement_id"] != improvement_id for row in rows):
            raise CaseError("invalid_evidence_scope", "Improvement evidence can only verify the improvement it belongs to")
        if fresh_after:
            anchor = datetime.fromisoformat(fresh_after)
            for row in rows:
                observed = datetime.fromisoformat(row["observed_at"])
                if row["created_at"] < anchor or observed < anchor:
                    raise CaseError("stale_evidence", "Verification requires evidence observed and collected after execution")
        return ids

    @staticmethod
    def _gaps(data):
        gaps = data.get("gaps", [])
        if not isinstance(gaps, list) or len(gaps) > MAX_LINKS or any(not isinstance(g, str) or not g.strip() or len(g) > 1000 for g in gaps):
            raise CaseError("invalid_input", "gaps must be at most 50 nonempty strings of at most 1000 characters")
        return [g.strip() for g in gaps]

    def _create(self, db, actor, data):
        title = _text(data, "title", required=True, maximum=300)
        summary = _text(data, "summary")
        _bounded({"title": title, "summary": summary, "assignee": ""}, 8192)
        severity = _choice(data, "severity", ("low", "medium", "high", "critical"), "medium")
        alert_id = _text(data, "alert_id", maximum=256)
        snapshot = None
        if alert_id:
            if not actor.is_admin:
                raise CaseError("forbidden", "Only an administrator can attach a global alert snapshot", 403)
            if not db.table_exists("alert_records"):
                raise CaseError("alert_not_found", "Alert source is unavailable", 404)
            columns = db.columns("alert_records")
            where = "row_id=?" + (" OR record_id=?" if "record_id" in columns else "")
            rows = db.execute("SELECT row_id,octet_length(record_json::text) AS bytes,CASE WHEN octet_length(record_json::text)<=32768 THEN record_json::text ELSE NULL END AS snapshot FROM alert_records WHERE " + where + " ORDER BY row_id LIMIT 2", (alert_id, alert_id) if "record_id" in columns else (alert_id,)).fetchall()
            if not rows:
                raise CaseError("alert_not_found", "Alert not found", 404)
            if len(rows) > 1:
                raise CaseError("ambiguous_alert", "Use the unique alert row_id", 409)
            row = rows[0]
            if row["snapshot"] is None:
                raise CaseError("input_too_large", "Alert snapshot exceeds 32768 bytes", 413)
            alert_id = str(row["row_id"])
            snapshot = _secret(json.loads(row["snapshot"]), redact=True)
            db.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema() || ':soc-alert-case:' || ?))", (actor.user_id + ':' + alert_id,)).fetchone()
            existing = _object(db.execute("SELECT * FROM soc_cases WHERE owner_user_id=? AND source_alert_id=? FOR UPDATE", (actor.user_id, alert_id)).fetchone())
            if existing:
                return existing, None, False
        case = _object(db.execute("INSERT INTO soc_cases(id,owner_user_id,title,summary,severity,status,source_alert_id) VALUES (?::uuid,?,?,?,?,'open',?) RETURNING *", (str(uuid4()), actor.user_id, title, summary, severity, alert_id or None)).fetchone())
        record = None
        if snapshot is not None:
            content = _json(snapshot)
            record = self._insert_record(db, case["id"], "evidence", {"title": "Source alert", "source": "alert_records", "source_record_id": alert_id, "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest(), "observed_at": _now(), "collected_by": actor.user_id, "query": "", "entity": "", "supersedes_id": None})
        return case, record, True

    def _update(self, db, actor, case, data):
        if not set(data).intersection(("title", "summary", "assignee", "status")):
            raise CaseError("invalid_input", "No editable case fields supplied")
        for name, maximum in (("title", 300), ("summary", 8000), ("assignee", 256)):
            if name in data:
                case[name] = _text(data, name, required=name == "title", maximum=maximum)
        if "status" in data:
            status = _choice(data, "status", STATES)
            # Review and close have evidence gates of their own.
            if status not in ("open", "investigating", "responding") or STATES.index(status) < STATES.index(case["status"]) or STATES.index(status) > STATES.index(case["status"]) + 1:
                raise CaseError("invalid_transition", "Use investigation, review and close commands to advance this case", 409)
            case["status"] = status
        return None

    def _add_evidence(self, db, actor, case, data):
        return self._write_evidence(db, actor, case, data)

    def _add_improvement_evidence(self, db, actor, case, data):
        improvement = self._get_record(db, case["id"], data.get("improvement_id"), "improvements")
        return self._write_evidence(db, actor, case, data, improvement_id=improvement["id"])

    def _write_evidence(self, db, actor, case, data, improvement_id=None):
        title = _text(data, "title", required=True, maximum=300)
        source = _text(data, "source", required=True, maximum=256)
        content = _text(data, "content", required=True, maximum=24000)
        observed = _time(data.get("observed_at"), name="observed_at", default=_now())
        if datetime.fromisoformat(observed) > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise CaseError("invalid_input", "Evidence cannot be observed in the future")
        supersedes = data.get("supersedes_id") or None
        if supersedes:
            previous = self._get_record(db, case["id"], supersedes, "evidence")
            if previous.get("improvement_id") != improvement_id:
                raise CaseError("invalid_evidence_scope", "A revision must retain the original evidence scope")
            supersedes = previous["id"]
        record = self._insert_record(db, case["id"], "evidence", {"title": title, "source": source, "content": content,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(), "observed_at": observed,
            "query": _text(data, "query", maximum=4000), "entity": _text(data, "entity", maximum=500),
            "supersedes_id": supersedes, "collected_by": actor.user_id,
            "purpose": "improvement" if improvement_id else "investigation", "improvement_id": improvement_id})
        if improvement_id is None and case["status"] in ("open", "review"):
            case["status"] = "investigating"
        return record

    def _add_hypothesis(self, db, actor, case, data):
        record = self._insert_record(db, case["id"], "hypotheses", {"statement": _text(data, "statement", required=True),
            "evidence_ids": self._evidence(db, case["id"], data, required=False), "gaps": self._gaps(data),
            "status": "open", "verdict": None, "rationale": ""})
        if case["status"] in ("open", "review"):
            case["status"] = "investigating"
        return record

    def _decide_hypothesis(self, db, actor, case, data):
        record = self._get_record(db, case["id"], data.get("hypothesis_id"), "hypotheses")
        verdict = _choice(data, "verdict", ("supported", "refuted", "inconclusive"))
        gaps = self._gaps(data) if "gaps" in data else record["gaps"]
        if gaps and verdict != "inconclusive":
            raise CaseError("unresolved_gaps", "Unresolved visibility gaps cannot support a definitive verdict", 409)
        record.update(status=verdict, verdict=verdict, gaps=gaps, rationale=_text(data, "rationale", required=True),
                      evidence_ids=self._evidence(db, case["id"], data), decided_by=actor.user_id, decided_by_kind=actor.kind, decided_at=_now())
        if case["status"] in ("open", "review"):
            case["status"] = "investigating"
        return self._save_record(db, record)

    def _propose_action(self, db, actor, case, data):
        params = data.get("parameters")
        if not isinstance(params, dict):
            raise CaseError("invalid_input", "parameters must be an object")
        _bounded(params, 12000)
        if not isinstance(data.get("reversible"), bool):
            raise CaseError("invalid_input", "reversible must be a boolean")
        plan = {"title": _text(data, "title", required=True, maximum=300),
            "tool_name": _text(data, "tool_name", required=True, maximum=256), "parameters": params,
            "target": _text(data, "target", required=True, maximum=500), "reason": _text(data, "reason", required=True, maximum=4000),
            "evidence_ids": self._evidence(db, case["id"], data), "reversible": data["reversible"],
            "rollback_plan": _text(data, "rollback_plan", required=True, maximum=4000),
            "verification_plan": _text(data, "verification_plan", required=True, maximum=4000)}
        # Reserve space for provider receipt and human verification in the
        # bounded action projection; finishing a real side effect must fit.
        _bounded(plan, 12000)
        record = self._insert_record(db, case["id"], "actions", {**plan, "plan_hash": _hash(plan),
            "approval_status": "pending", "execution_status": "not_started", "verification_status": "unverified",
            "proposed_by": actor.user_id, "proposed_by_kind": actor.kind, "attempt": 0})
        case["status"] = "responding"
        return record

    def _decide_action(self, db, actor, case, data):
        record = self._get_record(db, case["id"], data.get("action_id"), "actions")
        decision = _choice(data, "decision", ("approve", "reject"))
        reason = _text(data, "reason", required=True, maximum=4000)
        _bounded({"reason": reason}, 4096)
        pending = record["approval_status"] == "pending"
        can_cancel = record["approval_status"] == "approved" and record["execution_status"] in ("not_started", "blocked", "failed", "unsupported", "not_executed")
        if not pending and not (decision == "reject" and can_cancel):
            raise CaseError("invalid_transition", "This action cannot receive that approval decision", 409)
        record.update(approval_status="approved" if decision == "approve" else "rejected", approval_reason=reason,
                      decided_by=actor.user_id, decided_at=_now())
        if decision == "approve":
            record.update(approved_by=actor.user_id, approved_at=_now(), approved_plan_hash=record["plan_hash"])
        else:
            record["rejected_by"] = actor.user_id
        return self._save_record(db, record)

    def _verify_action(self, db, actor, case, data):
        record = self._get_record(db, case["id"], data.get("action_id"), "actions")
        if record["execution_status"] not in ("succeeded", "unknown", "running"):
            raise CaseError("invalid_transition", "Verification requires executed or ambiguous execution", 409)
        verdict = _choice(data, "verdict", ("verified", "failed", "inconclusive"))
        occurred = data.get("execution_occurred")
        if occurred is not None and not isinstance(occurred, bool):
            raise CaseError("invalid_input", "execution_occurred must be a boolean")
        if occurred is False and (verdict != "failed" or record["execution_status"] not in ("unknown", "running")):
            raise CaseError("invalid_transition", "Only ambiguous execution can be confirmed not executed with a failed verification", 409)
        # Older persisted retries can still carry their previous finished_at.
        # Compare every execution boundary instead of preferring a stale finish.
        boundaries = [datetime.fromisoformat(record[field]) for field in
                      ("claimed_at", "finished_at", "conflicting_receipt_at") if record.get(field)]
        anchor = max(boundaries).isoformat() if boundaries else None
        evidence = self._evidence(db, case["id"], data, fresh_after=anchor)
        summary = _text(data, "summary", required=True, maximum=4000)
        _bounded({"summary": summary}, 4096)
        record.update(verification_status=verdict, verification_summary=summary,
                      verification_evidence_ids=evidence, verified_by=actor.user_id, verified_at=_now())
        if (verdict == "verified" or occurred is True) and record["execution_status"] in ("unknown", "running"):
            record.update(execution_status="succeeded", reconciled_by=actor.user_id, reconciled_at=_now(),
                          reconciliation_outcome="confirmed_executed", reconciliation_conflict=False)
        elif occurred is False:
            record.update(execution_status="not_executed", reconciled_by=actor.user_id,
                          reconciled_at=_now(), reconciliation_outcome="confirmed_not_executed")
        return self._save_record(db, record)

    def _record_review(self, db, actor, case, data):
        record = self._insert_record(db, case["id"], "reviews", {
            "root_cause": _text(data, "root_cause", required=True),
            "lessons_learned": _text(data, "lessons_learned", required=True),
            "recommendations": _text(data, "recommendations", required=True),
            "evidence_ids": self._evidence(db, case["id"], data), "basis_version": case["version"],
            "reviewed_by": actor.user_id, "reviewed_by_kind": actor.kind})
        case["status"] = "review"
        return record

    def _add_improvement(self, db, actor, case, data):
        return self._insert_record(db, case["id"], "improvements", {
            "title": _text(data, "title", required=True, maximum=300), "owner": _text(data, "owner", required=True, maximum=256),
            "due_at": _time(data.get("due_at"), name="due_at"),
            "acceptance_criteria": _text(data, "acceptance_criteria", required=True),
            "status": "open", "result": "", "evidence_ids": []})

    def _update_improvement(self, db, actor, case, data):
        record = self._get_record(db, case["id"], data.get("improvement_id"), "improvements")
        status = _choice(data, "status", ("open", "in_progress", "verified", "deferred"))
        result = _text(data, "result", required=status in ("verified", "deferred"))
        evidence = self._evidence(db, case["id"], data, required=status == "verified", improvement_id=record["id"])
        if status == "verified":
            self._human(actor)
        record.update(status=status, result=result, evidence_ids=evidence, updated_by=actor.user_id)
        return self._save_record(db, record)

    def _close(self, db, actor, case, data):
        category = _choice(data, "category", ("true_positive", "false_positive", "inconclusive"))
        reason = _text(data, "reason", required=True)
        accepted = _text(data, "accepted_gaps", required=category == "inconclusive")
        if category == "inconclusive":
            self._human(actor)
        pending = db.execute("""SELECT count(*) FROM soc_case_records WHERE case_id=?::uuid AND kind='actions'
            AND NOT (data->>'approval_status'='rejected' OR
                     (data->>'execution_status'='succeeded' AND data->>'verification_status' IN ('verified','failed','inconclusive')))""", (case["id"],)).fetchone()[0]
        if pending:
            raise CaseError("unresolved_actions", "Every proposed action must be rejected or successfully executed and verified", 409)
        unmet = db.execute("""SELECT id,data->>'execution_status' AS execution_status,data->>'verification_status' AS verification_status
            FROM soc_case_records WHERE case_id=?::uuid AND kind='actions' AND data->>'approval_status'<>'rejected'
            AND data->>'execution_status'='succeeded' AND data->>'verification_status' IN ('failed','inconclusive')
            ORDER BY id LIMIT 101""", (case["id"],)).fetchall()
        if unmet and category != "inconclusive":
            raise CaseError("unresolved_actions", "Failed effects require remediation or explicit human inconclusive disposition", 409)
        if len(unmet) > 100:
            raise CaseError("too_many_unmet_actions", "Review at most 100 unmet actions in one closure", 409)
        unresolved = db.execute("SELECT count(*) FROM soc_case_records WHERE case_id=?::uuid AND kind='hypotheses' AND (data->>'status' IN ('open','inconclusive') OR jsonb_array_length(data->'gaps')>0)", (case["id"],)).fetchone()[0]
        if unresolved and category != "inconclusive":
            raise CaseError("unresolved_hypotheses", "Resolve hypotheses and gaps, or explicitly conclude inconclusive", 409)
        review = _record(db.execute("SELECT * FROM soc_case_records WHERE case_id=?::uuid AND kind='reviews' ORDER BY created_at DESC,id DESC LIMIT 1", (case["id"],)).fetchone())
        if review is None:
            raise CaseError("review_required", "A case review is required before closure", 409)
        stale = db.execute("""SELECT EXISTS(SELECT 1 FROM soc_case_events WHERE case_id=?::uuid AND version>?
            AND operation IN ('add_evidence','add_hypothesis','decide_hypothesis','propose_action','decide_action','claim_action','finish_action','late_receipt','verify_action','reopen'))""", (case["id"], review["basis_version"])).fetchone()[0]
        if stale:
            raise CaseError("review_stale", "Refresh the review after the latest investigation or response change", 409)
        case["closure"] = {"active": True, "category": category, "reason": reason, "accepted_gaps": accepted,
            "review_id": review["id"], "root_cause": review["root_cause"], "lessons_learned": review["lessons_learned"],
            "recommendations": review["recommendations"], "evidence_ids": review["evidence_ids"],
            "accepted_unmet_actions": [_object(row) for row in unmet],
            "closed_by": actor.user_id, "closed_by_kind": actor.kind, "source_version": case["version"] + 1}
        _bounded(case["closure"], 16000)
        case.update(status="closed", closed_at=_now())
        return case["closure"]

    def _reopen(self, db, actor, case, data):
        if case["status"] != "closed":
            raise CaseError("invalid_transition", "Only a closed case can be reopened", 409)
        reason = _text(data, "reason", required=True)
        # A withdrawal accompanies the original bounded closure on every detail
        # page. Reject excess input atomically rather than clipping the reason.
        _bounded({"reason": reason}, 1000)
        case["closure"].update(active=False, withdrawn_at=_now(), withdrawn_by=actor.user_id, withdrawal_reason=reason)
        case.update(status="investigating", closed_at=None)
        return case["closure"]


__all__ = ["Actor", "CaseError", "CaseStore"]
