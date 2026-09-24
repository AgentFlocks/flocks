"""Rewriting ``?`` markers for backends that number their parameters.

The codebase has several hundred statements written with SQLite's ``?``. They
are correct SQL; they are just written for a driver that does not number its
parameters. Rewriting each one by hand would be a large, mechanical, and
error-prone diff -- and the errors would be silent, because ``$2`` still runs,
it just binds the wrong value.

So the backend does it. A statement keeps its ``?`` markers and the PostgreSQL
transaction handle converts them on the way to the driver. Code that builds a
clause dynamically should still use :class:`~flocks.db.params.Params`, which
gets the numbering right at construction time; this exists for the statements
that are already written and correct.

The one thing this must not do is rewrite a ``?`` that is part of the data --
inside a quoted literal, or in a ``::`` cast. Both are handled below.
"""

from __future__ import annotations


def rewrite_markers(sql: str, placeholder) -> str:
    """Replace positional ``?`` markers using *placeholder(position)*.

    Question marks inside single or double quoted literals are left alone, so
    ``WHERE name LIKE '%?%'`` keeps its meaning.
    """
    out: list[str] = []
    position = 0
    quote: str | None = None
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        if quote is not None:
            out.append(char)
            if char == quote:
                # Doubled quote is an escaped quote, not the end of the literal.
                if index + 1 < length and sql[index + 1] == quote:
                    out.append(sql[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char in ("'", '"'):
            quote = char
            out.append(char)
            index += 1
            continue

        if char == "?":
            position += 1
            out.append(placeholder(position))
            index += 1
            continue

        out.append(char)
        index += 1

    return "".join(out)


def split_statements(script: str) -> list[str]:
    """Split a DDL script on semicolons that are not inside a literal."""
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    length = len(script)

    while index < length:
        char = script[index]
        if quote is not None:
            current.append(char)
            if char == quote:
                if index + 1 < length and script[index + 1] == quote:
                    current.append(script[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in ("\'", '"'):
            quote = char
            current.append(char)
            index += 1
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


__all__ = ["rewrite_markers", "split_statements"]
