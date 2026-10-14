"""Keep U+0000 out of PostgreSQL ``text`` and ``jsonb``.

PostgreSQL stores neither form of it: ``text`` rejects the character and
``jsonb`` rejects the ``\\u0000`` escape JSON writes for it, so one such string
fails the whole statement.  Device payloads do carry it (Sangfor SE frames end
with it, binary evidence holds it).  Values headed for a JSON column are
escaped here first, reversibly and with an explicit marker.

The marker is ``{"_pg_nul_escaped": {"strings": [...], "keys": [...]}}`` on the
top-level object: JSON Pointers (RFC 6901, written with the escaped keys) of
the strings that were escaped, and of the objects whose keys were.  A value
that is not an object, or an object already using one of the two reserved
keys, is stored as ``{"_pg_nul_escaped": ..., "_pg_nul_value": value}``.
"""

from __future__ import annotations

import re
from typing import Any

NUL_ESCAPED_KEY = "_pg_nul_escaped"
NUL_ESCAPED_VALUE_KEY = "_pg_nul_value"

_UNESCAPE = re.compile(r"\\(\\|u0000)")


def escape_nul(value: Any) -> tuple[Any, list[str], list[str]]:
    """A copy of ``value`` with U+0000 escaped, and where it was.

    In a string that held U+0000, backslashes are doubled and each U+0000 is
    written as the six characters ``\\u0000``.  An object with such a key has
    all its keys escaped that way, so an escaped key cannot meet a key that
    already read ``\\u0000``.  Returns the copy, the pointers of the escaped
    strings and the pointers of the objects whose keys were escaped; a value
    without U+0000 comes back equal with both lists empty.
    """
    strings: list[str] = []
    objects: list[str] = []

    def walk(item: Any, pointer: str) -> Any:
        if isinstance(item, str):
            if "\x00" not in item:
                return item
            strings.append(pointer)
            return _escape(item)
        if isinstance(item, dict):
            escape_keys = any(isinstance(key, str) and "\x00" in key for key in item)
            if escape_keys:
                objects.append(pointer)
            result = {}
            for key, child in item.items():
                if escape_keys and isinstance(key, str):
                    key = _escape(key)
                result[key] = walk(child, f"{pointer}/{_pointer_token(str(key))}")
            return result
        if isinstance(item, (list, tuple)):
            return [walk(child, f"{pointer}/{index}") for index, child in enumerate(item)]
        return item

    return walk(value, ""), strings, objects


def escape_nul_for_json(value: Any) -> Any:
    """``value`` ready for a PostgreSQL JSON column, marked when anything changed.

    An object that already uses a reserved key is always wrapped, even without
    U+0000, so reading it back never mistakes its own key for the marker.
    """
    escaped, strings, objects = escape_nul(value)
    reserved = isinstance(escaped, dict) and bool({NUL_ESCAPED_KEY, NUL_ESCAPED_VALUE_KEY} & set(escaped))
    if not strings and not objects and not reserved:
        return escaped
    marker = {"strings": strings, "keys": objects}
    if isinstance(escaped, dict) and not {NUL_ESCAPED_KEY, NUL_ESCAPED_VALUE_KEY} & set(escaped):
        escaped[NUL_ESCAPED_KEY] = marker
        return escaped
    return {NUL_ESCAPED_KEY: marker, NUL_ESCAPED_VALUE_KEY: escaped}


def restore_nul_from_json(value: Any) -> Any:
    """The value ``escape_nul_for_json`` was given, from what it stored."""
    if not isinstance(value, dict):
        return value
    marker = value.get(NUL_ESCAPED_KEY)
    wrapped = set(value) == {NUL_ESCAPED_KEY, NUL_ESCAPED_VALUE_KEY}
    if not (
        isinstance(marker, dict)
        and isinstance(marker.get("strings"), list)
        and isinstance(marker.get("keys"), list)
        and (marker["strings"] or marker["keys"] or wrapped)
    ):
        return value
    if wrapped:
        value = value[NUL_ESCAPED_VALUE_KEY]
    else:
        value = {key: child for key, child in value.items() if key != NUL_ESCAPED_KEY}
    for pointer in marker["strings"]:
        value = _replace_at(value, pointer, lambda text: _unescape(text) if isinstance(text, str) else text)
    # Deepest first: a pointer is written with the escaped keys above it.
    for pointer in sorted(marker["keys"], key=lambda item: item.count("/"), reverse=True):
        value = _replace_at(
            value,
            pointer,
            lambda item: {
                (_unescape(key) if isinstance(key, str) else key): child for key, child in item.items()
            } if isinstance(item, dict) else item,
        )
    return value


def replace_nul(value: Any) -> Any:
    """``value`` with each U+0000 written as the text ``\\u0000``, not restorable.

    For fields replaced wholesale on every write, where a marker could not
    follow them: the text reads the same and nothing turns it back.
    """
    if isinstance(value, str):
        return value.replace("\x00", "\\u0000")
    if isinstance(value, dict):
        return {replace_nul(key) if isinstance(key, str) else key: replace_nul(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [replace_nul(child) for child in value]
    return value


def _replace_at(value: Any, pointer: str, change) -> Any:
    if pointer == "":
        return change(value)
    steps = [_pointer_unescape(step) for step in pointer.split("/")[1:]]
    parent = value
    try:
        for step in steps[:-1]:
            parent = parent[int(step)] if isinstance(parent, list) else parent[step]
        last = int(steps[-1]) if isinstance(parent, list) else steps[-1]
        parent[last] = change(parent[last])
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return value


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\x00", "\\u0000")


def _unescape(text: str) -> str:
    return _UNESCAPE.sub(lambda match: "\\" if match.group(1) == "\\" else "\x00", text)


def _pointer_token(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _pointer_unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")
