"""Startup schema statements with nothing left to do, found before they take a lock.

Every process runs its schema statements when it starts. PostgreSQL takes the
table lock before it finds that an ``IF NOT EXISTS`` has nothing to do (SHARE
for CREATE INDEX, ACCESS EXCLUSIVE for ALTER TABLE) and keeps it until the
transaction ends. A process starting while another one writes those tables in
the opposite order then deadlocks with it, or stalls every reader. So the
catalog is asked first, and these statements are not sent when PostgreSQL
would skip them:

* ``CREATE [UNLOGGED] TABLE IF NOT EXISTS`` and ``CREATE [UNIQUE] INDEX IF NOT
  EXISTS`` whose name is already taken;
* ``ALTER TABLE ... ADD [COLUMN] IF NOT EXISTS`` for a column that exists;
* ``ALTER TABLE ... ALTER [COLUMN] ... TYPE`` to the built-in type the column
  already has.

Anything that cannot be read with certainty runs as written.
"""

from __future__ import annotations

import re
from typing import Any, Optional

_IDENTIFIER = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
_RELATION = rf'(?P<relation>{_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER})?)'
_LEADING_NOISE = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.S)
_CREATE_TABLE = re.compile(rf"CREATE\s+(?:UNLOGGED\s+)?TABLE\s+IF\s+NOT\s+EXISTS\s+{_RELATION}[\s(]", re.I)
_CREATE_INDEX = re.compile(
    rf"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(?P<name>{_IDENTIFIER})\s+ON\s+(?:ONLY\s+)?{_RELATION}[\s(]",
    re.I,
)
_ALTER_TABLE = re.compile(rf"ALTER\s+TABLE\s+(?:ONLY\s+)?{_RELATION}\s+(?P<action>.+?)\s*\Z", re.I | re.S)
_ADD_COLUMN = re.compile(rf"ADD\s+(?:COLUMN\s+)?IF\s+NOT\s+EXISTS\s+(?P<name>{_IDENTIFIER})\s", re.I)
_ALTER_TYPE = re.compile(
    rf"ALTER\s+(?:COLUMN\s+)?(?P<name>{_IDENTIFIER})\s+(?:SET\s+DATA\s+)?TYPE\s+(?P<type>[A-Za-z]+(?:\s+[A-Za-z]+)*)\Z",
    re.I,
)
# A tag follows PostgreSQL's identifier rules: ASCII letters, digits (not
# first) and underscores, and any character beyond ASCII.
_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_\u0080-\U0010FFFF][A-Za-z0-9_\u0080-\U0010FFFF]*)?\$")
_PARAMETER = re.compile(r"\$[0-9]")
# Built-in types a startup ALTER COLUMN ... TYPE may name, as pg_type spells them.
_TYPE_NAMES = {
    "bigint": "int8", "int8": "int8", "integer": "int4", "int": "int4", "int4": "int4",
    "smallint": "int2", "int2": "int2", "text": "text", "boolean": "bool", "bool": "bool",
    "double precision": "float8", "float8": "float8", "real": "float4", "float4": "float4",
    "json": "json", "jsonb": "jsonb", "bytea": "bytea", "uuid": "uuid", "date": "date",
    "timestamptz": "timestamptz", "timestamp with time zone": "timestamptz",
    "timestamp": "timestamp", "timestamp without time zone": "timestamp",
}
# Statements that create, drop or rename no table: a name looked up before
# them still names the same thing after them.
_NEUTRAL = re.compile(
    r"(?:SELECT|INSERT|UPDATE|DELETE|COMMENT|CREATE\s+EXTENSION"
    r"|CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE|TRIGGER))\b",
    re.I,
)
_NAME_LENGTH = 63  # NAMEDATALEN - 1: PostgreSQL truncates longer identifiers

_EXISTING = """
SELECT CASE t.kind
    WHEN 'table' THEN EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = t.name AND n.nspname = COALESCE(t.schema, current_schema()))
    WHEN 'index' THEN EXISTS (
        SELECT 1 FROM pg_class c
        WHERE c.relname = t.name
          AND c.relnamespace = (SELECT r.relnamespace FROM pg_class r WHERE r.oid = to_regclass(t.target)))
    ELSE EXISTS (
        SELECT 1 FROM pg_attribute a JOIN pg_type ty ON ty.oid = a.atttypid
        WHERE a.attrelid = to_regclass(t.target) AND a.attname = t.name
          AND a.attnum > 0 AND NOT a.attisdropped
          AND (t.kind = 'column' OR (ty.typname = t.type AND ty.typnamespace = 'pg_catalog'::regnamespace
                                     AND a.atttypmod = -1)))
END
FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[])
    WITH ORDINALITY AS t(kind, name, schema, target, type, position)
ORDER BY t.position
"""


def split_statements(script: str) -> Optional[list[str]]:
    """The script's statements in order, or None when it cannot be split for certain."""
    statements: list[str] = []
    start = index = 0
    length = len(script)
    while index < length:
        char = script[index]
        if char == "'":
            if index and script[index - 1] in "eE" and (index < 2 or not (script[index - 2].isalnum() or script[index - 2] == "_")):
                return None  # backslash escapes: not worth reading here
            end = index + 1
            while True:
                end = script.find("'", end)
                if end < 0:
                    return None
                if script.startswith("''", end):
                    end += 2
                    continue
                break
            index = end + 1
        elif char == '"':
            end = index + 1
            while True:
                end = script.find('"', end)
                if end < 0:
                    return None
                if script.startswith('""', end):
                    end += 2
                    continue
                break
            index = end + 1
        elif script.startswith("--", index):
            end = script.find("\n", index)
            index = length if end < 0 else end + 1
        elif script.startswith("/*", index):
            depth, index = 1, index + 2
            while depth:
                opening, closing = script.find("/*", index), script.find("*/", index)
                if closing < 0:
                    return None
                if 0 <= opening < closing:
                    depth, index = depth + 1, opening + 2
                else:
                    depth, index = depth - 1, closing + 2
        elif char == "$" and not (index and (script[index - 1].isalnum() or script[index - 1] in "_$")):
            tag = _DOLLAR_QUOTE.match(script, index)
            if tag is None:
                if not _PARAMETER.match(script, index):
                    return None  # neither a parameter nor a quote this reads
                index += 1
                continue
            end = script.find(tag.group(0), tag.end())
            if end < 0:
                return None
            index = end + len(tag.group(0))
        elif char == ";":
            statements.append(script[start:index])
            start = index = index + 1
        else:
            index += 1
    statements.append(script[start:])
    return [statement for statement in statements if statement.strip()]


def _fold(identifier: str) -> str:
    """The name PostgreSQL stores for an identifier as written."""
    if identifier.startswith('"'):
        name = identifier[1:-1].replace('""', '"')
    else:
        name = identifier.lower()
    return name.encode("utf-8")[:_NAME_LENGTH].decode("utf-8", errors="ignore")


def _relation_parts(relation: str) -> tuple[Optional[str], str, str]:
    """(schema or None, table, the relation as to_regclass reads it)."""
    parts = re.findall(_IDENTIFIER, relation)
    target = ".".join(parts)
    if len(parts) == 2:
        return _fold(parts[0]), _fold(parts[1]), target
    return None, _fold(parts[0]), target


def _single_action(action: str) -> bool:
    """True when an ALTER TABLE carries one subcommand (no comma outside brackets or quotes).

    Comments and dollar quotes are not read here: an action holding one is not
    taken for a single subcommand.
    """
    if "--" in action or "/*" in action or "$" in action:
        return False
    depth = 0
    quote: Optional[str] = None
    for char in action:
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            return False
    return True


def _check(statement: str) -> Optional[tuple[tuple[str, str, Optional[str], str, Optional[str]], str]]:
    """((kind, name, schema, target, type) to look up, table it concerns), or None if it must run."""
    text = _LEADING_NOISE.sub("", statement)
    match = _CREATE_TABLE.match(text)
    if match:
        schema, table, target = _relation_parts(match.group("relation"))
        return ("table", table, schema, target, None), table
    match = _CREATE_INDEX.match(text)
    if match:
        _schema, table, target = _relation_parts(match.group("relation"))
        return ("index", _fold(match.group("name")), None, target, None), table
    match = _ALTER_TABLE.match(text)
    if match and _single_action(match.group("action")):
        _schema, table, target = _relation_parts(match.group("relation"))
        action = match.group("action")
        added = _ADD_COLUMN.match(action + " ")
        if added:
            return ("column", _fold(added.group("name")), None, target, None), table
        retyped = _ALTER_TYPE.match(action)
        type_name = _TYPE_NAMES.get(" ".join(retyped.group("type").lower().split())) if retyped else None
        if type_name:
            return ("type", _fold(retyped.group("name")), None, target, type_name), table
    return None


async def schema_script_to_run(connection: Any, script: str) -> Optional[str]:
    """``script`` without the statements PostgreSQL would skip; None when none is left.

    *connection* is an asyncpg connection, inside the caller's transaction. The
    script comes back unchanged when nothing in it can be left out.
    """
    statements = split_statements(script)
    if not statements:
        return script
    checks: list[tuple[int, tuple, str]] = []
    for position, statement in enumerate(statements):
        check = _check(statement)
        if check is not None:
            checks.append((position, *check))
        elif not _NEUTRAL.match(_LEADING_NOISE.sub("", statement)):
            # It may create, drop or rename a table: what follows is looked up
            # as it stands after it, so it all runs as written.
            break
    if not checks:
        return script
    rows = await connection.fetch(
        _EXISTING,
        *[list(column) for column in zip(*(lookup for _position, lookup, _table in checks))],
    )
    skipped: set[int] = set()
    created: set[str] = set()
    retyped: set[tuple[str, str]] = set()
    for (position, (kind, name, _schema, _target, _type), table), (exists,) in zip(checks, rows):
        if table in created or (kind == "type" and (table, name) in retyped):
            # An earlier statement creates the table (which may hide another one
            # of that name further down the search path) or retypes the column.
            continue
        if exists:
            skipped.add(position)
        elif kind == "table":
            created.add(table)
        elif kind == "type":
            retyped.add((table, name))
    if not skipped:
        return script
    remaining = [statement for position, statement in enumerate(statements) if position not in skipped]
    if not any(_LEADING_NOISE.sub("", statement) for statement in remaining):
        return None
    return ";\n".join(remaining)


__all__ = ["schema_script_to_run", "split_statements"]
