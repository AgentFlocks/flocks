"""
The application for an enterprise licence from the Flocks Pro page of an open install that has none it
can go by.

The page fills a form; Console (``POST /v1/upgrade-requests``) records it for review under the
Console account signed in here. Console lists applications to its operators only, so this install
keeps the ids of the ones it sent and asks Console for each -- under the keys the open edition's page
of 2026.10.19 kept them, which brings those back on a machine updated from it.

An approved application carries the licence itself (``activate_key``, the token Console signed).
One application goes out from a machine at a time: the operations here run one after another
(two administrators sending at once would otherwise both pass the check), and the idempotency key
of a submission is kept until Console answers it, so a submission whose answer was lost is sent
again with the same key and Console hands back the application it already made.

Taking it writes the licence where the Python enterprise package kept its own
(<FLOCKS_ROOT>/flockspro/license.json, in that file's shape) and tells Console it was taken. From then
on the machine is one with a licence: the page moves it to the enterprise edition as it moves one that
had the Python package (flocks.updater.enterprise), and the enterprise core reads that file as the
Python package wrote it.

A licence no longer valid is no licence here: the machine may apply again, and the licence taken
replaces the old one. One Console renewed needs none of that: a sync with Console brings the renewal
into the file (sync_licence), as the hourly heartbeat does.

A machine that cannot reach Console applies offline: the page hands it an application to send to
whoever issues licences (offline_application: the form, and the machine the licence is to be bound to),
and imports the licence file they send back (import_licence: the license.json Console's offline issue
downloads, or the bare token), written where taking an approved application writes it.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from flocks.console.previous_licence import (
    LICENCE_FILE_LOCK,
    find_licence_file,
    licence_token,
    moment,
    terms_in_force,
    token_header,
)
from flocks.storage.storage import Storage
from flocks.updater import enterprise
from flocks.updater.enterprise import EnterpriseUpgradeError
from flocks.updater.machine_fingerprint import MachineIdUnreadable, machine_fingerprints, machine_names
from flocks.utils.log import Log

log = Log.create(service="updater.enterprise_application")

_IDS_KEY = "console:upgrade_request_ids"
_RECORD_PREFIX = "console:upgrade_request:"
#: The idempotency key of a submission Console has not answered yet.
_PENDING_KEY = "console:upgrade_request_pending"
#: Asking Console for every open application at once, the listing gives up after this long in
#: all (not counting a wait for another operation to finish) and shows what is kept.
_REFRESH_SECONDS = 8
#: Waiting for Console's review, or approved and not yet taken: asked for again on every listing.
OPEN_STATES = frozenset({"pending", "reviewing", "approved"})
#: Console holds one of these per node; an approved one takes up no such place.
REVIEW_STATES = frozenset({"pending", "reviewing"})
_RESEND = "申请可能已经交上去了，再提交一次会找回这份申请，不会重复"
#: What Console lets the applicant withdraw.
WITHDRAWABLE_STATES = frozenset({"pending", "reviewing", "approved"})


_lock = asyncio.Lock()


def _record_key(request_id: str) -> str:
    return f"{_RECORD_PREFIX}{request_id}"


async def _ids() -> list[str]:
    ids = await Storage.get(_IDS_KEY)
    return [str(i) for i in ids] if isinstance(ids, list) else []


async def _known_ids() -> list[str]:
    """The kept ids, oldest first, with any record the index lost (written before its id went in)."""
    ids = await _ids()
    stray = [key[len(_RECORD_PREFIX):] for key in await Storage.list_keys(prefix=_RECORD_PREFIX)]
    lost = [i for i in sorted(stray) if i and i not in ids]
    if lost:
        ids.extend(lost)
        await Storage.set(_IDS_KEY, ids, "json")
    return ids


async def _remember(record: dict[str, Any]) -> None:
    request_id = str(record["request_id"])
    await Storage.set(_record_key(request_id), record, "json")
    ids = await _ids()
    if request_id not in ids:
        ids.append(request_id)
        await Storage.set(_IDS_KEY, ids, "json")


def _console_base() -> str:
    base = enterprise._console_base_url()
    if not base:
        raise EnterpriseUpgradeError("这台机器没有配置 Console（FLOCKS_CONSOLE_BASE_URL），不能在线申请；请联系我们获取授权")
    return base


async def _console_session() -> dict[str, Any]:
    from flocks.console.login import ConsoleLoginService

    try:
        return await ConsoleLoginService.require_console_session()
    except ValueError as exc:
        raise EnterpriseUpgradeError("申请要先登录云账号：点「云账号登录」后再提交") from exc


def _headers(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['console_session_token']}"}


def _console_error(exc: httpx.HTTPError, doing: str) -> EnterpriseUpgradeError:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return EnterpriseUpgradeError("云账号登录已失效：点「云账号登录」后再试")
        try:
            detail = exc.response.json().get("detail")
        except ValueError:
            detail = None
        return EnterpriseUpgradeError(f"{doing}：Console 回答 HTTP {code}{f'（{detail}）' if detail else ''}")
    # A timeout has no words of its own.
    return EnterpriseUpgradeError(f"{doing}：连不上 Console（{str(exc) or type(exc).__name__}）")


async def _console_failure(exc: httpx.HTTPError, doing: str, session: dict[str, Any]) -> EnterpriseUpgradeError:
    """:func:`_console_error`, after forgetting the sign-in Console refused as not valid (401), so the
    page offers a new one. A 403 is a refusal of a sign-in Console takes: it stays."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
        await enterprise._forget_console_session(str(session.get("console_session_token") or ""))
    return _console_error(exc, doing)


def _status(record: dict[str, Any]) -> str:
    return str(record.get("status") or "").strip().lower()


def _merge(record: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Console's word on an application over what this install kept of it."""
    merged = dict(record)
    for key in ("status", "reason", "suggestion", "activate_key", "license_id", "license_status",
                "max_admins", "max_members", "expires_at", "created_at", "updated_at"):
        if key in data and data[key] is not None:
            merged[key] = data[key]
    if isinstance(data.get("form_data"), dict):
        merged["details"] = {**(record.get("details") or {}), **data["form_data"]}
    return merged


def _on_machine() -> tuple[str, str]:
    """The licence this machine goes by, and the token in its licence file, whatever that one is worth."""
    found = find_licence_file()
    return enterprise.standing_licence_id(), licence_token(found[1])[0] if found else ""


def _can_take(record: dict[str, Any], standing: str, held_token: str) -> bool:
    """Approved with its licence, and this machine has none it can go by. Not the token it already
    holds: that gains nothing. Its date is no reason against: a renewal Console signed does not show on
    the application, and once the token is on the machine a sync brings it in."""
    token = str(record.get("activate_key") or "").strip()  # secret-guard: allow 误报：变量名，取值来自 Console 的审批结果
    return (
        _status(record) in {"approved", "activated"} and bool(token) and bool(record.get("license_id"))
        and not standing and token != held_token
    )


def _licence_over(record: dict[str, Any]) -> bool:
    return enterprise.licence_lapsed(record.get("license_status"), record.get("expires_at"))


def _in_the_way(record: dict[str, Any], standing: str, held_token: str) -> bool:
    """One application at a time: one under review, or a licence to take that is good by its own
    terms. One over by them may be dead or renewed, nobody here can tell, and the machine must be able
    to apply past it."""
    return _status(record) in REVIEW_STATES or (_can_take(record, standing, held_token) and not _licence_over(record))


def public(record: dict[str, Any], licence_id: str = "", held_token: str = "") -> dict[str, Any]:
    """What the page shows of an application: everything but the licence token."""
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    status = _status(record)
    issued = str(record.get("license_id") or "").strip()
    return {
        "request_id": record.get("request_id"),
        "status": status,
        "license_type": details.get("license_type"),
        "company": details.get("company") or details.get("enterprise_name"),
        "applicant_name": details.get("applicant_name"),
        "applicant_email": details.get("applicant_email"),
        "reason": record.get("reason"),
        "suggestion": record.get("suggestion"),
        "license_id": issued or None,
        "license_status": record.get("license_status"),
        "expires_at": record.get("expires_at"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        # Approved and not taken here can still be withdrawn, as Console allows.
        "can_withdraw": status in WITHDRAWABLE_STATES and not (licence_id and licence_id == issued),
        "can_take": _can_take(record, licence_id, held_token),
        # Over by the terms it was issued with: it may be taken, and stands in no new application's way.
        "licence_over": _licence_over(record),
    }


async def _fetch(client: httpx.AsyncClient, base: str, session: dict[str, Any], request_id: str) -> dict[str, Any]:
    resp = await client.get(f"{base}/v1/upgrade-requests/{request_id}", headers=_headers(session))
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise EnterpriseUpgradeError("Console 的回答格式不对")
    return data


async def applications() -> dict[str, Any]:
    """This install's applications, newest first, the open ones as Console has them now (asked
    all at once, given up on after _REFRESH_SECONDS in all). When Console cannot be asked (no
    address, not signed in, unreachable, too slow) the kept ones, with why; and whether a
    submission still waits for an answer."""
    async with _lock:
        licence_id, held_token = await asyncio.to_thread(_on_machine)
        ids = list(reversed(await _known_ids()))
        records = [r for r in [await Storage.get(_record_key(i)) for i in ids] if isinstance(r, dict)]
        note: str | None = None
        open_ones = [i for i, r in enumerate(records) if _status(r) in OPEN_STATES]
        if open_ones:
            try:
                base, session = _console_base(), await _console_session()
                async with httpx.AsyncClient(timeout=_REFRESH_SECONDS) as client:
                    answers = await asyncio.wait_for(asyncio.gather(
                        *(_fetch(client, base, session, str(records[i]["request_id"])) for i in open_ones),
                        return_exceptions=True,
                    ), timeout=_REFRESH_SECONDS)
                for index, answer in zip(open_ones, answers):
                    if isinstance(answer, BaseException):
                        # Each refusal is looked at (a 401 among them forgets the sign-in);
                        # the first one is what the page says.
                        failure = answer if isinstance(answer, EnterpriseUpgradeError) else (
                            await _console_failure(answer, "刷新申请状态失败", session)
                            if isinstance(answer, httpx.HTTPError) else answer)
                        if note is None:
                            note = str(failure)
                        continue
                    if not answer.get("not_modified"):
                        records[index] = _merge(records[index], answer)
                        await _remember(records[index])
            except EnterpriseUpgradeError as exc:
                note = str(exc)
            except TimeoutError:
                note = f"Console 回答太慢（超过 {_REFRESH_SECONDS} 秒），先显示这台机器记下的状态"
        pending = isinstance(await Storage.get(_PENDING_KEY), dict)
    return {"items": [public(r, licence_id, held_token) for r in records], "note": note, "pending_submission": pending}


async def apply(form: dict[str, Any], applied_by: str) -> dict[str, Any]:
    """Send an application to Console for review and keep its id."""
    async with _lock:
        return await _apply(form, applied_by)


async def _apply(form: dict[str, Any], applied_by: str) -> dict[str, Any]:
    if not enterprise.available():
        raise EnterpriseUpgradeError("这台机器不能在这里申请：它已经是企业版，或者不是 Linux / macOS")
    standing, held_token = await asyncio.to_thread(_on_machine)
    if standing:
        raise EnterpriseUpgradeError("这台机器已经有企业版授权，直接升级即可")
    for kept in [await Storage.get(_record_key(i)) for i in await _known_ids()]:
        if isinstance(kept, dict) and _in_the_way(kept, standing, held_token):
            raise EnterpriseUpgradeError("已经有一份申请在审核或等着启用，处理完再申请")
    base, session = _console_base(), await _console_session()
    try:
        machine, machine_kind, devid = await asyncio.to_thread(machine_names)
    except MachineIdUnreadable as exc:
        # Sent without it, the licence would not be bound to this machine although it could be.
        raise EnterpriseUpgradeError(f"读不到这台机器的标识，先处理好再申请：{exc}") from exc
    # A submission whose answer never came may be at Console already: the same key gets it back.
    pending = await Storage.get(_PENDING_KEY)
    kept_key = str(pending.get("idempotency_key") or "") if isinstance(pending, dict) else ""
    request_id = kept_key or str(uuid4())
    account = session.get("user_display") or session.get("user_email") or session.get("passport_uid")
    details = {
        "product": "Flocks Pro",
        "license_type": form["license_type"],
        "request_kind": "new",
        # The licence no longer valid this one is to take the place of, for whoever reviews it.
        "replaces_license_id": enterprise.previous_licence_id() or None,
        "company": form["company"],
        "enterprise_name": form["company"],
        "applicant_name": form["applicant_name"],
        "applicant_email": form["applicant_email"],
        "applicant_phone": form["applicant_phone"],
        "sales_rep_name": form.get("sales_rep_name") or None,
        "notes": form.get("notes") or None,
        "idempotency_key": request_id,
        "console_account_name": account,
        "cloud_account": account,
        "passport_uid": session.get("passport_uid"),
        "applied_by": applied_by,
        # The host as the enterprise core names it: Console binds the licence it issues to it, and the
        # core runs on that licence on this machine only. None for a host that keeps no identifier,
        # which Console shows the operator. machine_kind says which fingerprint it is, for whoever
        # reviews: the machine-id digest, which every enterprise core reads, or the device id of a
        # host that keeps no machine id. devid is the device id beside it, for Console's records.
        "machine": machine or None,
        "machine_kind": machine_kind or None,
        "devid": devid or None,
    }
    payload = {
        # The machine, as Console knows it from the sign-in: Console holds one open application
        # per node, and answers the same node and key with the application it already made.
        "node_id": session.get("install_id"),
        "console_login_id": session.get("console_login_id"),
        "fingerprint": session.get("fingerprint"),
        "install_id": session.get("install_id"),
        "passport_uid": session.get("passport_uid"),
        "company_name": form["company"],
        "enterprise_name": form["company"],
        "contact_email": form["applicant_email"],
        "idempotency_key": request_id,
        "form_data": details,
    }
    from flocks.console.login import ConsoleLoginService

    channel = ConsoleLoginService.update_channel()
    if channel:
        payload["channel"] = channel
    await Storage.set(_PENDING_KEY, {"idempotency_key": request_id, "at": datetime.now(UTC).isoformat()}, "json")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{base}/v1/upgrade-requests", json=payload, headers=_headers(session))
    except httpx.HTTPError as exc:
        # No answer: it may have been made. The key stays for the next submission.
        raise EnterpriseUpgradeError(f"提交后没有收到 Console 的回答（{exc}）：{_RESEND}") from exc
    if 400 <= resp.status_code < 500:
        # Refused: nothing was made with a new key, so the next submission is a new one. A key
        # kept from a submission whose answer never came stays: that one may be at Console, and
        # this refusal (a sign-in run out, say) says nothing about it.
        if not kept_key:
            await Storage.delete(_PENDING_KEY)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise await _console_failure(exc, "提交申请失败", session) from exc
    # Anything else that is not a readable application (a 5xx from a proxy in front of Console, a
    # body that does not read) may still have been made there: the key stays.
    if resp.status_code >= 300:
        raise EnterpriseUpgradeError(f"提交申请时 Console 回答 HTTP {resp.status_code}：{_RESEND}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise EnterpriseUpgradeError(f"Console 的回答读不懂：{_RESEND}") from exc
    if not isinstance(data, dict) or not data.get("request_id"):
        raise EnterpriseUpgradeError(f"Console 的回答格式不对：{_RESEND}")
    now = datetime.now(UTC).isoformat()
    record = _merge({"request_id": data["request_id"], "status": "pending", "details": details,
                     "created_at": now, "updated_at": now}, data)
    await _remember(record)
    # Kept here: only now is the key done with.
    await Storage.delete(_PENDING_KEY)
    log.info("enterprise.application_sent", {"request_id": record["request_id"]})
    return public(record, "")


async def _kept(request_id: str) -> dict[str, Any]:
    record = await Storage.get(_record_key(request_id))
    if not isinstance(record, dict) or str(record.get("request_id") or "") != request_id:
        raise EnterpriseUpgradeError("这台机器没有发过这份申请")
    return record


async def withdraw(request_id: str) -> dict[str, Any]:
    """Withdraw an application that has not been taken."""
    async with _lock:
        return await _withdraw(request_id)


async def _withdraw(request_id: str) -> dict[str, Any]:
    record = await _kept(request_id)
    base, session = _console_base(), await _console_session()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{base}/v1/upgrade-requests/{request_id}/withdraw", json={},
                                     headers=_headers(session))
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise await _console_failure(exc, "撤回失败", session) from exc
    record = _merge(record, data if isinstance(data, dict) else {})
    await _remember(record)
    return public(record, *await asyncio.to_thread(_on_machine))


def licence_file() -> Path:
    """Where the Python enterprise package kept its licence, and the enterprise installer finds it."""
    return enterprise.runtime_root() / "flockspro" / "license.json"


def _write_licence(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".license.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(content, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _keep_replaced_licence(path: Path, now: int) -> None:
    """A copy of the file a licence is about to replace (one no longer valid, or an older token of
    the same licence), beside it and never over an earlier copy: the patches and the record of how it
    came are in no other place. Created private, as the licence is."""
    if not path.exists():
        return
    fd, copy = tempfile.mkstemp(dir=path.parent, prefix=f"license.replaced-{now}-", suffix=".json")
    os.close(fd)
    try:
        shutil.copyfile(path, copy)
    except BaseException:
        Path(copy).unlink(missing_ok=True)
        raise


async def take(request_id: str) -> dict[str, Any]:
    """Write the licence of an approved application on this machine and tell Console it was taken."""
    async with _lock:
        return await _take(request_id)


async def _take(request_id: str) -> dict[str, Any]:
    record = await _kept(request_id)
    base, session = _console_base(), await _console_session()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            data = await _fetch(client, base, session, request_id)
    except httpx.HTTPError as exc:
        raise await _console_failure(exc, "取授权失败", session) from exc
    record = _merge(record, data)
    await _remember(record)
    token = str(record.get("activate_key") or "").strip()  # secret-guard: allow 误报：变量名，取值来自 Console 的审批结果
    issued = str(record.get("license_id") or "").strip()
    if _status(record) not in {"approved", "activated"} or not token or not issued:
        raise EnterpriseUpgradeError("这份申请还没有审核通过，没有可启用的授权")
    present, held_token = await asyncio.to_thread(_on_machine)
    if present and present != issued:
        raise EnterpriseUpgradeError(f"这台机器已经有授权 {present}，不覆盖")
    # This very token on the machine already (taken twice, or a retry): the file stands, with the
    # patches it has gathered; Console still hears of it below.
    if token != held_token:
        # Under the lock the heartbeat writes under: it must not put the file it read back over this one.
        with LICENCE_FILE_LOCK:
            now = int(time.time())
            _keep_replaced_licence(licence_file(), now)
            _write_licence(licence_file(), {
                "license_id": issued,
                "key": token,
                "payload": {},
                "patches": [],
                "activated_at": now,
                "install_id": session.get("install_id"),
                "fingerprint": session.get("fingerprint"),
                "request_id": request_id,
            })
        log.info("enterprise.licence_taken", {"request_id": request_id, "license_id": issued})
    if _status(record) == "approved":
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{base}/v1/upgrade-requests/{request_id}/activate", headers=_headers(session))
                resp.raise_for_status()
                answer = resp.json()
            record = _merge(record, answer if isinstance(answer, dict) else {"status": "activated"})
        except httpx.HTTPError as exc:
            # The licence is on the machine; Console hears of it the next time it is taken or listed.
            log.warning("enterprise.licence_taken_unreported", {"request_id": request_id, "error": str(exc)})
        await _remember(record)
    return public(record, issued, token)


async def sync_licence() -> dict[str, bool]:
    """Ask Console for this machine's licence now, with the page waiting: what the hourly heartbeat
    does (its answer carries the latest patch Console signed: a renewal, new quotas, a revocation,
    and the file keeps it), with what went wrong said; and whether the terms changed."""
    async with _lock:
        return await _sync_licence()


async def _sync_licence() -> dict[str, bool]:
    from flocks.console.login import ConsoleLoginService, ConsoleSessionRefused, keep_previous_licence_current

    if not enterprise.available():
        raise EnterpriseUpgradeError("这台机器已经是企业版")
    before = await asyncio.to_thread(enterprise.previous_edition)
    if not before or not before["license_id"]:
        raise EnterpriseUpgradeError("这台机器上没有授权，不用同步")
    if not enterprise._console_base_url():
        raise EnterpriseUpgradeError("这台机器没有配置 Console（FLOCKS_CONSOLE_BASE_URL），同步不了授权")
    try:
        session = await ConsoleLoginService.require_console_session()
    except ValueError as exc:
        raise EnterpriseUpgradeError("同步授权要先登录云账号：点「云账号登录」后再试") from exc
    try:
        answer = await ConsoleLoginService.send_heartbeat()
    except ConsoleSessionRefused as exc:
        if exc.status_code == 401:
            await enterprise._forget_console_session(str(session.get("console_session_token") or ""))
            raise EnterpriseUpgradeError("云账号登录已失效：点「云账号登录」后再试") from exc
        raise EnterpriseUpgradeError("Console 不接受这台机器的云账号登录（HTTP 403）：退出登录后重新登录再试") from exc
    except httpx.HTTPError as exc:
        raise _console_error(exc, "同步授权失败") from exc
    except ValueError as exc:
        raise EnterpriseUpgradeError("同步授权失败：Console 的回答读不懂") from exc
    # The heartbeat wrote this and only logged a failure (a heartbeat goes on without the file); here
    # someone is waiting to hear of it.
    try:
        keep_previous_licence_current(answer)
    except OSError as exc:
        raise EnterpriseUpgradeError(f"Console 答复了，但写不进这台机器的授权文件：{exc}") from exc
    after = await asyncio.to_thread(enterprise.previous_edition)
    terms = ("license_status", "expires_at", "max_admins", "max_members")
    changed = after is not None and any(before.get(term) != after.get(term) for term in terms)
    log.info("enterprise.licence_synced", {"license_id": before["license_id"], "changed": changed})
    return {"changed": changed}


#: A licence file is a few kilobytes; anything far bigger is not one.
MAX_LICENCE_CHARS = 256 * 1024
#: The bare token, as an operator may copy it out of Console instead of downloading the file.
_BARE_TOKEN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")
#: What the application file says it is, for whoever reads it.
OFFLINE_APPLICATION_FORMAT = "flocks-licence-application/1"


def _licence_document(content: str) -> dict[str, Any]:
    """The licence file as sent: the license.json Console's offline issue downloads, or its bare token."""
    text = content.strip().lstrip("\ufeff")
    if not text:
        raise EnterpriseUpgradeError("授权文件是空的")
    if len(text) > MAX_LICENCE_CHARS:
        raise EnterpriseUpgradeError("这不是授权文件：内容太大")
    if _BARE_TOKEN.match(text):
        return {"key": text}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise EnterpriseUpgradeError("这不是授权文件：内容既不是 JSON，也不是授权令牌") from exc
    if not isinstance(data, dict):
        raise EnterpriseUpgradeError("这不是授权文件：内容不是 JSON 对象")
    return data


async def import_licence(content: str) -> dict[str, Any]:
    """Write a licence file sent back for an offline application on this machine, after the checks that
    can be made here; the enterprise core verifies its signature when the machine moves."""
    async with _lock:
        return await _import_licence(content)


async def _import_licence(content: str) -> dict[str, Any]:
    if not enterprise.available():
        raise EnterpriseUpgradeError("这台机器已经是企业版：授权在企业版的 Flocks Pro 页导入")
    data = _licence_document(content)
    token, claims = licence_token(data)
    if not token:
        raise EnterpriseUpgradeError("这个文件里没有授权令牌，不是 Console 签发的授权文件")
    header = token_header(token) or {}
    if str(header.get("alg") or "").upper() != "ES256":
        raise EnterpriseUpgradeError("授权令牌不是 Console 签发的格式")
    issued = str(claims.get("license_id") or "").strip()
    if not issued:
        raise EnterpriseUpgradeError("授权令牌里没有授权编号")
    named = str(data.get("license_id") or "").strip()
    if named and named != issued:
        raise EnterpriseUpgradeError(f"文件写的授权编号 {named} 和令牌里的 {issued} 对不上")
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    # The core takes the file's payload only as a copy of the token's own (go/internal/license/legacy.go).
    if payload and payload != claims:
        raise EnterpriseUpgradeError("文件里的 payload 和授权令牌对不上，文件可能被改过")
    patches = [item for item in data.get("patches") or [] if isinstance(item, str)] if isinstance(data.get("patches"), list) else []
    record = {"license_id": issued, "key": token, "payload": payload, "patches": patches}  # secret-guard: allow 误报：key 字段存的是上传文件里的令牌变量
    # A token bound to another machine is refused before anything is written, the same token already on
    # this machine too (a data directory moved from that machine, say).
    machine = str(claims.get("machine") or "").strip().lower()
    if machine:
        ours = await asyncio.to_thread(machine_fingerprints)
        # A host whose fingerprints cannot be read now is not refused here: the core judges the binding.
        if ours and machine not in ours:
            raise EnterpriseUpgradeError("这份授权绑定的是另一台机器：请用这台机器的申请信息重新申请")
    again = await asyncio.to_thread(_import_again, token, patches)
    if again is not None:
        # This very token is on the machine already (imported twice, or taken before): the file stands, with
        # the patches it has gathered and the ones it lacked, and is judged as it now is -- a renewal it holds
        # keeps an expired token good, a revocation it holds keeps it revoked.
        _refuse_if_lapsed(again)
        log.info("enterprise.licence_imported_again", {"license_id": issued})
        return _imported(again, issued)
    _refuse_if_lapsed(record)
    standing = await asyncio.to_thread(enterprise.standing_licence_id)
    if standing and standing != issued:
        raise EnterpriseUpgradeError(f"这台机器已经有授权 {standing}，不覆盖；它失效后再导入新的")
    with LICENCE_FILE_LOCK:
        now = int(time.time())
        path = licence_file()
        _keep_replaced_licence(path, now)
        _write_licence(path, {
            **record,
            "activated_at": now,
            "imported_at": now,
            # What the file says of the install it was issued for; the core holds a bound token to it.
            **{field: data[field] for field in ("install_id", "fingerprint", "request_id") if data.get(field)},
        })
    log.info("enterprise.licence_imported", {"license_id": issued})
    return _imported(record, issued)


def _import_again(token: str, patches: list[str]) -> dict[str, Any] | None:
    """The file holding *token*, with the patches it lacked added (and written), read and written under the
    lock the heartbeat writes under; None when the file on the machine holds another token, or none."""
    with LICENCE_FILE_LOCK:
        found = find_licence_file()
        if not found or licence_token(found[1])[0] != token:
            return None
        path, held = found
        known = [item for item in held.get("patches") or [] if isinstance(item, str)]
        fresh = [item for item in patches if item not in known]
        merged = {**held, "patches": known + fresh}
        if fresh:
            _write_licence(path, merged)
        return merged


def _licence_terms(record: dict[str, Any]) -> tuple[Any, Any]:
    """A licence file's status and expiry as the core holds it to them: the token's, or the patch's in force."""
    claims = licence_token(record)[1]
    terms = {**claims, **terms_in_force(record)}
    return terms.get("status"), terms.get("expires_at")


def _refuse_if_lapsed(record: dict[str, Any]) -> None:
    status, expires_at = _licence_terms(record)
    state = str(status or "").strip().lower()
    if state in enterprise.LAPSED_STATUSES:
        said = {"revoked": "已吊销", "expired": "已过期", "superseded": "已被新的授权替换"}[state]
        raise EnterpriseUpgradeError(f"这份授权{said}，不能导入；请重新申请")
    if enterprise.licence_lapsed(status, expires_at):
        ended = datetime.fromtimestamp(moment(expires_at) or 0, UTC).astimezone().strftime("%Y-%m-%d")
        raise EnterpriseUpgradeError(f"这份授权已在 {ended} 到期，不能导入；续期后重新下载授权文件，或者重新申请")


def _imported(record: dict[str, Any], issued: str) -> dict[str, Any]:
    status, expires_at = _licence_terms(record)
    return {"license_id": issued, "license_status": status, "expires_at": expires_at}


async def offline_application(form: dict[str, Any], applied_by: str) -> dict[str, Any]:
    """The application to hand to whoever issues licences when Console cannot be reached from here: the
    form, and the machine a licence issued for it is to be bound to. Nothing is sent or kept."""
    if not enterprise.available():
        raise EnterpriseUpgradeError("这台机器不能在这里申请：它已经是企业版，或者不是 Linux / macOS / Windows")
    if await asyncio.to_thread(enterprise.standing_licence_id):
        raise EnterpriseUpgradeError("这台机器已经有企业版授权，直接升级即可")
    try:
        machine, machine_kind, devid = await asyncio.to_thread(machine_names)
    except MachineIdUnreadable as exc:
        raise EnterpriseUpgradeError(f"读不到这台机器的标识，先处理好再申请：{exc}") from exc
    from flocks import __version__
    from flocks.console.login import ConsoleLoginService

    return {
        "format": OFFLINE_APPLICATION_FORMAT,
        "product": "Flocks Pro",
        "license_type": form["license_type"],
        "company": form["company"],
        "applicant_name": form["applicant_name"],
        "applicant_email": form["applicant_email"],
        "applicant_phone": form["applicant_phone"],
        "sales_rep_name": form.get("sales_rep_name") or None,
        "notes": form.get("notes") or None,
        "replaces_license_id": enterprise.previous_licence_id() or None,
        # Console's offline issue binds the licence to this fingerprint (32 hex digits), and keeps the
        # device id beside it for its records.
        "machine": machine or None,
        "machine_kind": machine_kind or None,
        "devid": devid or None,
        "install_id": await ConsoleLoginService._get_install_id(),
        "hostname": platform.node() or None,
        "platform": enterprise.platform_id(),
        "flocks_version": __version__,
        "applied_by": applied_by,
        "created_at": datetime.now(UTC).isoformat(),
    }
