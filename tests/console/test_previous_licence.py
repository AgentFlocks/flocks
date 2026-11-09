"""flocks.console.previous_licence: the terms a licence file holds, as the enterprise core reads them.

Case list (编号 | 场景):
  PL1 a patch in force goes over the token: status, expiry, quotas, only what it names
  PL2 which patch holds now: the highest serial begun and not lapsed; a lapsed one or one not begun
      says nothing, whatever its serial; on a tie the later one
  PL3 a patch of something else says nothing: another licence, another token of this licence (named,
      or signed before it), a suite licence, what does not read
  PL4 how long a patch lasts: its own seconds, its days, 60 days when it names neither, for ever
      when it names no issue time
  PL5 a time goes as the core reads it: a number, the same written out, an ISO time, none, nonsense
  PL6 the licence's id: at the top of the file, in the copy of the payload, or only in the token
  PL7 where it is found: under FLOCKS_ROOT first, then the home's .flocks; a file with no licence
      does not hide the one after it
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path

import pytest

from flocks.console import previous_licence as licence

NOW = 1_800_000_000
DAY = 86400


def signed(claims: dict) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'ES256', 'kid': 'flocks-license-test'})}.{part(claims)}.c2ln"


TOKEN = signed({"license_id": "zj-remote login", "status": "poc", "expires_at": NOW - 30 * DAY, "max_admins": 3, "max_members": 20, "issued_at": NOW - 120 * DAY})  # secret-guard: allow 误报：变量名，不是凭证


def binding(token: str = TOKEN) -> str:
    return hashlib.sha256(token.rsplit(".", 1)[0].encode()).hexdigest()[:32]


def patch(serial: int = 1, **claims) -> str:
    return signed({
        "serial": serial, "license_id": "zj-remote login", "dynamic_status": "poc", "dynamic_expires_at": NOW + 90 * DAY,
        "issued_at": NOW - DAY, "iat": NOW - DAY, "patch_ttl_days": 120, "token_binding": binding(), **claims,
    })


def file_with(*patches, token: str = TOKEN) -> dict:
    return {"license_id": "zj-remote login", "key": token, "patches": list(patches)}  # secret-guard: allow 误报：变量名，不是凭证


def test_pl1_a_patch_in_force_goes_over_the_token():
    terms = licence.terms_in_force(file_with(patch(dynamic_max_admins=9, dynamic_max_members=90)), now=NOW)
    assert terms == {"status": "poc", "expires_at": NOW + 90 * DAY, "max_admins": 9, "max_members": 90}

    # Only what it names: the token's stand for the rest.
    assert licence.terms_in_force(file_with(patch(dynamic_status="", dynamic_expires_at=0)), now=NOW) == {}
    assert licence.terms_in_force(file_with(patch(dynamic_status=" Revoked ")), now=NOW)["status"] == "revoked"
    assert licence.terms_in_force(file_with(), now=NOW) == {}
    assert licence.terms_in_force({"key": TOKEN, "patches": "not a list"}, now=NOW) == {}  # secret-guard: allow 误报：变量名，不是凭证


def test_pl2_the_patch_that_holds_now():
    older = patch(1, dynamic_expires_at=NOW + 10 * DAY)
    newer = patch(2, dynamic_expires_at=NOW + 20 * DAY)
    assert licence.patch_in_force(file_with(newer, older), now=NOW)["serial"] == 2

    lapsed = patch(3, issued_at=NOW - 200 * DAY, iat=NOW - 200 * DAY, patch_ttl_days=60, dynamic_expires_at=NOW + 30 * DAY)
    assert licence.patch_in_force(file_with(older, lapsed), now=NOW)["serial"] == 1
    # Its own last moment still counts.
    assert licence.patch_in_force(file_with(lapsed), now=(NOW - 200 * DAY) + 60 * DAY)["serial"] == 3

    for not_begun in (patch(3, nbf=NOW + 60), patch(3, iat=NOW + 60)):
        assert licence.patch_in_force(file_with(older, not_begun), now=NOW)["serial"] == 1

    tie_first, tie_last = patch(5, dynamic_expires_at=NOW + 1), patch(5, dynamic_expires_at=NOW + 2)
    assert licence.patch_in_force(file_with(tie_first, tie_last), now=NOW)["dynamic_expires_at"] == NOW + 2


@pytest.mark.parametrize("other", [
    patch(license_id="another licence"),
    patch(token_binding="0" * 32),
    patch(token_binding=None),
    signed({"serial": 1, "license_id": "zj-remote login", "dynamic_expires_at": NOW + DAY, "issued_at": NOW - 121 * DAY}),
    patch(typ="flocks-console-suite-grant/1"),
    patch(dynamic_expires_at="the day after"),
    patch(issued_at="some time"),
    "not.a.token",
    "two.parts",
    {"dynamic_expires_at": NOW + DAY},
    None,
])
def test_pl3_a_patch_of_something_else_says_nothing(other):
    assert licence.patch_in_force(file_with(other), now=NOW) is None


def test_pl3b_one_from_before_patches_named_the_token_goes_by_time():
    unnamed = {"serial": 1, "license_id": "zj-remote login", "dynamic_expires_at": NOW + DAY, "patch_ttl_days": 365}
    assert licence.patch_in_force(file_with(signed({**unnamed, "issued_at": NOW - 120 * DAY})), now=NOW) is not None
    assert licence.patch_in_force(file_with(signed({**unnamed, "issued_at": NOW - 121 * DAY})), now=NOW) is None


def test_pl3c_a_file_with_no_token_has_no_patch_in_force():
    assert licence.patch_in_force({"license_id": "zj-remote login", "patches": [patch()]}, now=NOW) is None


@pytest.mark.parametrize("named, lasts", [
    ({"ttl_seconds": 10 * DAY, "patch_ttl_days": 99}, 10),
    ({"patch_ttl_days": 20}, 20),
    ({"patch_ttl_days": None}, 60),
    ({"ttl_seconds": -1, "patch_ttl_days": 30}, 30),
    ({"ttl_seconds": 0, "patch_ttl_days": -5}, 60),
])
def test_pl4_how_long_a_patch_lasts(named, lasts):
    issued = NOW - DAY
    item = patch(issued_at=issued, iat=issued, **{"patch_ttl_days": None, **named})
    assert licence.patch_in_force(file_with(item), now=issued + lasts * DAY) is not None
    assert licence.patch_in_force(file_with(item), now=issued + lasts * DAY + 1) is None


def test_pl4b_a_patch_that_names_no_issue_time_does_not_lapse():
    item = signed({"serial": 1, "license_id": "zj-remote login", "dynamic_expires_at": NOW + DAY, "token_binding": binding()})
    assert licence.patch_in_force(file_with(item), now=NOW + 1000 * DAY) is not None


@pytest.mark.parametrize("value, seconds", [
    (None, 0), ("", 0), ("  ", 0),
    (1825321652, 1825321652), (1825321652.5, 1825321652.5), ("1825321652", 1825321652),
    ("2026-09-30T03:47:25+00:00", 1790740045), ("2026-09-30T11:47:25+08:00", 1790740045),
    ("2026-09-30T03:47:25", 1790740045), ("2026-09-30T03:47:25Z", 1790740045),
    ("nonsense", None), ("-5", None), (True, None), ({}, None),
])
def test_pl5_a_time_goes_as_the_core_reads_it(value, seconds):
    assert licence.moment(value) == seconds


def test_pl6_the_licence_id_wherever_the_file_keeps_it():
    assert licence.licence_id_of({"license_id": "top", "payload": {"license_id": "kept"}, "key": TOKEN}) == "top"
    assert licence.licence_id_of({"payload": {"license_id": "kept"}, "key": TOKEN}) == "kept"
    assert licence.licence_id_of({"license_id": None, "key": TOKEN}) == "zj-remote login"
    assert licence.licence_id_of({"token": TOKEN}) == "zj-remote login"
    assert licence.licence_id_of({"key": "only.two"}) == ""
    assert licence.licence_id_of({"key": "a.%%%.c"}) == ""
    assert licence.licence_id_of({}) == ""


def test_pl7_where_the_file_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, home = tmp_path / "root", tmp_path / "home"
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("HOME", str(home))
    assert licence.find_licence_file() is None

    at_home = home / ".flocks" / "flockspro" / "license.json"
    at_home.parent.mkdir(parents=True)
    at_home.write_text(json.dumps({"license_id": "home"}))
    assert licence.find_licence_file()[0] == at_home

    at_root = root / "flockspro" / "license.json"
    at_root.parent.mkdir(parents=True)
    at_root.write_text("{}")
    assert licence.find_licence_file()[0] == at_home  # names none: the one after it

    at_root.write_text("not json")
    assert licence.find_licence_file()[0] == at_home

    at_root.write_text(json.dumps({"license_id": "root"}))
    path, data = licence.find_licence_file()
    assert path == at_root and data["license_id"] == "root"

    monkeypatch.delenv("FLOCKS_ROOT")
    assert licence.licence_files() == [at_home]


def test_now_defaults_to_the_clock():
    assert licence.patch_in_force(file_with(patch(issued_at=time.time() - 60, iat=int(time.time()) - 60))) is not None
