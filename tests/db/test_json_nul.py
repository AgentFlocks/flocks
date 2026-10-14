"""U+0000 escaping for PostgreSQL JSON columns round-trips exactly."""

from __future__ import annotations

import json

import pytest

from flocks.db.json_nul import (
    NUL_ESCAPED_KEY,
    NUL_ESCAPED_VALUE_KEY,
    escape_nul,
    escape_nul_for_json,
    replace_nul,
    restore_nul_from_json,
)


@pytest.mark.parametrize("value", [
    {"evidence": "observed\x00payload"},
    {"raw": "frame\x00\r\n", "nested": {"list": ["a\x00", "plain", {"deep": "\x00\x00"}]}},
    # A literal backslash and a literal "\u0000" next to a real U+0000.
    {"text": "path\\to\\u0000 and \x00 here\\"},
    ["top\x00level", 1, None],
    "only\x00string",
    {"a/b~c": "key with pointer characters\x00"},
    # Keys: one with U+0000 next to one that already reads "\u0000".
    {"a\x00b": 1, "a\\u0000b": 2, "inner": {"x\x00": {"y\x00": "z\x00"}}},
    # An object already using the reserved names keeps them.
    {NUL_ESCAPED_VALUE_KEY: "mine", "other": "y\x00"},
    {NUL_ESCAPED_KEY: ["mine"], "other": "y\x00"},
])
def test_escaped_values_survive_postgres_and_come_back_unchanged(value) -> None:
    stored = json.dumps(escape_nul_for_json(value), ensure_ascii=False)

    assert "\\u0000" not in stored.replace("\\\\", "")
    assert restore_nul_from_json(json.loads(stored)) == value


def test_values_without_nul_are_stored_as_they_are() -> None:
    value = {"text": "no nul here \\u0000 literally", "n": [1, 2]}

    assert escape_nul_for_json(value) == value
    assert restore_nul_from_json(json.loads(json.dumps(value))) == value


@pytest.mark.parametrize("value", [
    {NUL_ESCAPED_KEY: []},
    {NUL_ESCAPED_KEY: {"strings": [], "keys": []}, "x": 1},
    # Looks exactly like a marker, with text that looks escaped.
    {NUL_ESCAPED_KEY: {"strings": ["/real"], "keys": []}, "real": "literal \\u0000"},
    {NUL_ESCAPED_VALUE_KEY: "mine"},
])
def test_objects_using_the_reserved_names_read_back_unchanged(value) -> None:
    stored = json.dumps(escape_nul_for_json(value), ensure_ascii=False)

    assert restore_nul_from_json(json.loads(stored)) == value


def test_the_marker_lists_what_was_escaped() -> None:
    escaped, strings, objects = escape_nul({"_sip_raw": {"event_evidence": "a\x00b"}, "id": "x"})

    assert strings == ["/_sip_raw/event_evidence"]
    assert objects == []
    assert escaped["_sip_raw"]["event_evidence"] == "a\\u0000b"


def test_replace_nul_only_writes_the_text() -> None:
    assert replace_nul({"report": "new\x00evidence\\x", "list": ["\x00"]}) == {
        "report": "new\\u0000evidence\\x",
        "list": ["\\u0000"],
    }
